# -*- coding: utf-8 -*-
# =============================================================================
# OWSLib. Copyright (C) 2024 Tom Kralidis
#
# Contact email: tomkralidis@gmail.com
# =============================================================================
"""
Capabilities-aware filter request building.

WFS services advertise different subsets of the OGC Filter Encoding (FE/FES)
operators.  The helpers in this module walk an expression tree made of the
existing :mod:`owslib.fes` (FE 1.1) or :mod:`owslib.fes2` (FES 2.0) objects,
verify that every node can be expressed against the target service's
``Filter_Capabilities`` and only then serialise the request.

Unsupported nodes are never silently dropped: the first unsupported node
raises :class:`FilterCapabilityError` carrying the tree path and the
capability gap.  The only degradations performed are rewrites that can be
proven semantically equivalent (see :data:`SAFE_REWRITES`); every applied
rewrite is recorded in the result's diagnostics.

The input expression tree is never modified.  Adaptation produces new
expression objects and the original objects are still serialised exactly as
before the call.
"""

from copy import deepcopy

from owslib.etree import etree
from owslib import fes, fes2
from owslib.namespaces import Namespaces


NAMESPACE_REGISTRY = Namespaces()


class FilterCapabilityError(Exception):
    """Raised when an expression node cannot be expressed by the service.

    Attributes
    ----------

    - node_path: position of the offending node in the expression tree,
      e.g. ``Not[0]/And[1]/Contains``.
    - node: the offending FES expression object.
    - capability: capability name the service is missing.
    - gaps: list of human readable capability gap descriptions, in case the
      node has more than one gap.
    - rewrites: rewrites already applied before the failure was reached.
    """

    def __init__(self, node_path, node, capability, gaps=None, rewrites=None):
        self.node_path = node_path
        self.node = node
        self.capability = capability
        self.gaps = gaps or [capability]
        self.rewrites = list(rewrites or [])
        label = _node_label(node)
        detail = "; ".join(self.gaps)
        message = "filter node '%s' (%s) cannot be expressed by the target service: %s" % (
            node_path, label, detail)
        super(FilterCapabilityError, self).__init__(message)


# Semantically equivalent rewrites.  Only operator substitutions proven to
# preserve semantics may be registered here; anything that changes the result
# set must instead raise FilterCapabilityError.
SAFE_REWRITES = {
    "1.0.0": {
        "PropertyIsNotEqualTo": ("PropertyIsEqualTo", "Not"),
    },
    "1.1.0": {
        # FE 1.1 services advertise the short scalar operator names.
        # PropertyIsNotEqualTo negates equality; equivalent only when the
        # service also supports logical Not, otherwise no rewrite happens.
        "PropertyIsNotEqualTo": ("PropertyIsEqualTo", "Not"),
    },
    "2.0.0": {},
}


# Node classes grouped by role for both filter encoding versions.
COMPARISON_CLASSES = (
    fes.BinaryComparisonOpType, fes.PropertyIsLike, fes.PropertyIsNull,
    fes.PropertyIsBetween,
    fes2.BinaryComparisonOpType, fes2.PropertyIsLike, fes2.PropertyIsNull,
    fes2.PropertyIsBetween,
)

SPATIAL_CLASSES = (fes.BBox, fes2.TopologicalOpType, fes2.BBox)

LOGIC_CLASSES = (
    fes.BinaryLogicOpType, fes.UnaryLogicOpType,
    fes2.BinaryLogicOpType, fes2.UnaryLogicOpType,
)


def _node_label(node):
    if isinstance(node, (fes.Filter, fes2.Filter)):
        return "Filter"
    name = node.__class__.__name__
    if isinstance(node, (fes.And, fes2.And)):
        return "And"
    if isinstance(node, (fes.Or, fes2.Or)):
        return "Or"
    if isinstance(node, (fes.Not, fes2.Not)):
        return "Not"
    if isinstance(node, (fes.BBox, fes2.BBox)):
        return "BBOX"
    return name


def _walk_path(path, index):
    if path:
        return "%s[%s]" % (path, index)
    return "[%s]" % index


def _semantic_operator(node):
    """Return the canonical operator name for a leaf expression."""
    if isinstance(node, (fes.BBox, fes2.BBox)):
        return "BBOX"
    if isinstance(node, fes2.TopologicalOpType):
        return node.operation
    return node.__class__.__name__


def _is_logic(node):
    return isinstance(node, LOGIC_CLASSES)


def _is_filter(node):
    return isinstance(node, (fes.Filter, fes2.Filter))


_OPERATOR_ALIASES = {
    "EqualTo": "PropertyIsEqualTo",
    "NotEqualTo": "PropertyIsNotEqualTo",
    "LessThan": "PropertyIsLessThan",
    "GreaterThan": "PropertyIsGreaterThan",
    "LessThanEqualTo": "PropertyIsLessThanOrEqualTo",
    "GreaterThanEqualTo": "PropertyIsGreaterThanOrEqualTo",
    "Like": "PropertyIsLike",
    "Between": "PropertyIsBetween",
    "NullCheck": "PropertyIsNull",
    "Intersect": "Intersects",
}


def _capability_names(caps):
    """Normalise advertised operator names to canonical FES names."""
    names = set()
    for operator in getattr(caps, "scalar_comparison_operators", []) or []:
        if operator is None:
            continue
        names.add(operator)
        names.add(_OPERATOR_ALIASES.get(operator, "PropertyIs%s" % operator))
    for operator in getattr(caps, "spatial_operators", []) or []:
        if operator is None:
            continue
        names.add(operator)
        names.add(_OPERATOR_ALIASES.get(operator, operator))
    return names


def _logical_supported(caps):
    """Whether the service supports And/Or/Not composition."""
    if getattr(caps, "logical_operators", False):
        return True
    conformance = getattr(caps, "conformance", {}) or {}
    if conformance.get("ImplementsStandardFilter") == "TRUE":
        return True
    return False


def _conformance(caps, name):
    conformance = getattr(caps, "conformance", {}) or {}
    return conformance.get(name)


def _operator_supported(caps, operator, kind):
    names = _capability_names(caps)
    if operator in names:
        return True
    # FE 1.1 BBOX support is commonly advertised through the Envelope operand
    if kind == "spatial" and operator == "BBOX":
        operands = getattr(caps, "spatial_operands", []) or []
        if "gml:Envelope" in operands or "gml32:Envelope" in operands:
            return True
    # fall back to conformance flags for documents without operator lists
    if kind == "spatial":
        flag = _conformance(caps, "ImplementsSpatialFilter")
        if flag == "TRUE":
            return True
        if flag == "FALSE":
            return False
    if kind == "comparison":
        flag = _conformance(caps, "ImplementsStandardFilter")
        if flag == "TRUE":
            return True
        if flag == "FALSE":
            return False
    return False


def _spatial_operand_supported(caps, operand):
    operands = getattr(caps, "spatial_operands", []) or []
    if not operands:
        # no operand list advertised; rely on operator/conformance checks
        return True
    if operand in operands:
        return True
    # GML 3.1 and GML 3.2 geometric concepts share the same local names,
    # even when capabilities advertise them with the legacy gml: prefix.
    local = operand.split(":", 1)[-1]
    return any(item.split(":", 1)[-1] == local for item in operands)


def _geometry_operand(node):
    """Geometry operand QName used by a spatial node, or None."""
    if isinstance(node, (fes.BBox, fes2.BBox)):
        return "gml:Envelope"
    geometry = getattr(node, "geometry", None)
    if geometry is None:
        return None
    tag = getattr(geometry.toXML(), "tag", "")
    if tag.startswith("{"):
        namespace, _, local = tag[1:].partition("}")
        if namespace == NAMESPACE_REGISTRY.get_namespace("gml32"):
            return "gml32:%s" % local
        if namespace == NAMESPACE_REGISTRY.get_namespace("gml"):
            return "gml:%s" % local
        return local
    return tag


def _property_reference(node):
    if isinstance(node, (fes.BinaryComparisonOpType, fes.PropertyIsLike,
                         fes.PropertyIsNull, fes.PropertyIsBetween,
                         fes2.BinaryComparisonOpType, fes2.PropertyIsLike,
                         fes2.PropertyIsNull, fes2.PropertyIsBetween)):
        return node.propertyname
    if isinstance(node, fes2.TopologicalOpType):
        return node.propertyname
    if isinstance(node, (fes.BBox, fes2.BBox)):
        return "ows:BoundingBox"
    return None


def _collect_prefixes(value, prefixes):
    """Collect CURIE prefixes used in a property reference string."""
    if not isinstance(value, str):
        return
    token = value.strip()
    if token.startswith("{"):
        return
    if ":" in token and "/" not in token.split(":", 1)[0]:
        prefixes.add(token.split(":", 1)[0])


class FilterRewriter(object):
    """Validate and adapt an FES expression tree to a service's capabilities.

    Parameters
    ----------

    - capabilities: a :class:`owslib.fes.FilterCapabilities` or
      :class:`owslib.fes2.FilterCapabilities` instance.
    - version: target filter encoding version, ``'1.0.0'``, ``'1.1.0'`` or
      ``'2.0.0'``.
    """

    def __init__(self, capabilities, version):
        if version not in ("1.0.0", "1.1.0", "2.0.0"):
            raise ValueError("unsupported filter encoding version '%s'" % version)
        self.capabilities = capabilities
        self.version = version
        self.rewrites = []
        self.prefixes = set()

    def adapt(self, expression):
        """Return a validated, adapted copy of ``expression``.

        Raises :class:`FilterCapabilityError` before any request is built if
        a node cannot be expressed by the target service.
        """
        if _is_filter(expression):
            adapted = deepcopy(expression)
            adapted.filter = self._adapt_node(expression.filter, "")
        else:
            adapted = self._adapt_node(expression, "")
        return adapted

    def _adapt_node(self, node, path):
        if _is_filter(node):
            adapted = deepcopy(node)
            adapted.filter = self._adapt_node(node.filter, path)
            return adapted

        if _is_logic(node):
            return self._adapt_logic(node, path)
        return self._adapt_leaf(node, path)

    def _adapt_logic(self, node, path):
        label = _node_label(node)
        node_path = "%s/%s" % (path, label) if path else label
        if not _logical_supported(self.capabilities):
            raise FilterCapabilityError(
                node_path, node, "LogicalOperators",
                ["service does not advertise logical operators (And/Or/Not)"],
                self.rewrites)

        adapted_children = []
        for index, child in enumerate(node.operations):
            adapted_children.append(
                self._adapt_node(child, _walk_path(node_path, index)))

        adapted = deepcopy(node)
        adapted.operations = adapted_children
        return adapted

    def _adapt_leaf(self, node, path):
        label = _node_label(node)
        node_path = "%s/%s" % (path, label) if path else label

        reference = _property_reference(node)
        _collect_prefixes(reference, self.prefixes)

        if isinstance(node, COMPARISON_CLASSES):
            return self._adapt_comparison(node, node_path)
        if isinstance(node, SPATIAL_CLASSES):
            self._collect_geometry_prefixes(node)
            return self._adapt_spatial(node, node_path)

        raise FilterCapabilityError(
            node_path, node, node.__class__.__name__,
            ["unsupported expression type '%s'" % node.__class__.__name__],
            self.rewrites)

    def _adapt_comparison(self, node, node_path):
        operator = _semantic_operator(node)
        if _operator_supported(self.capabilities, operator, "comparison"):
            return deepcopy(node)

        rewrite = SAFE_REWRITES.get(self.version, {}).get(operator)
        if rewrite is not None:
            replacement = rewrite[0]
            if (_operator_supported(self.capabilities, replacement, "comparison")
                    and _logical_supported(self.capabilities)):
                adapted = deepcopy(node)
                adapted.propertyoperator = self._rewrite_operator_name(
                    node, replacement)
                wrapped = self._wrap_not(adapted)
                self.rewrites.append(
                    "%s: %s rewritten as Not(%s) (semantically equivalent)" % (
                        node_path, operator, replacement))
                return wrapped

        raise FilterCapabilityError(
            node_path, node, operator,
            ["service does not support comparison operator '%s'" % operator],
            self.rewrites)

    def _rewrite_operator_name(self, node, replacement):
        """Return the prefixed operator name for the node's encoding."""
        if isinstance(node, fes.BinaryComparisonOpType):
            return "ogc:%s" % replacement
        return "fes:%s" % replacement

    def _wrap_not(self, node):
        if isinstance(node, fes2.OgcExpression):
            return fes2.Not([node])
        return fes.Not([node])

    def _collect_geometry_prefixes(self, node):
        """Collect namespace prefixes used by a geometry's XML tree."""
        geometry = getattr(node, "geometry", None)
        if geometry is None:
            return
        element = geometry.toXML()
        prefixes = [prefix for prefix in element.nsmap if prefix]
        self.prefixes.update(prefixes)

    def _adapt_spatial(self, node, node_path):
        operator = _semantic_operator(node)
        gaps = []
        if not _operator_supported(self.capabilities, operator, "spatial"):
            gaps.append("service does not support spatial operator '%s'" % operator)

        operand = _geometry_operand(node)
        if operand is not None and not _spatial_operand_supported(self.capabilities, operand):
            gaps.append("service does not support geometry operand '%s'" % operand)

        if gaps:
            raise FilterCapabilityError(node_path, node, operator, gaps, self.rewrites)
        return deepcopy(node)


def _fes_module(version):
    if version in ("1.0.0", "1.1.0"):
        return fes
    return fes2


def _filter_root_tag(version):
    module = _fes_module(version)
    prefix = "fes" if version == "2.0.0" else "ogc"
    return "{%s}Filter" % module.namespaces[prefix]


def _resolve_namespaces(prefixes, extra_namespaces):
    """Build an nsmap preserving prefix bindings for property references."""
    nsmap = {}
    for prefix in sorted(prefixes):
        if prefix in extra_namespaces:
            nsmap[prefix] = extra_namespaces[prefix]
            continue
        uri = NAMESPACE_REGISTRY.get_namespace(prefix)
        if uri is None:
            raise ValueError(
                "no namespace binding known for prefix '%s'; "
                "pass namespaces={'%s': '...'} to bind it" % (prefix, prefix))
        nsmap[prefix] = uri
    for prefix, uri in extra_namespaces.items():
        nsmap.setdefault(prefix, uri)
    return nsmap


class AdaptedFilter(object):
    """Result of a capabilities-aware filter build.

    Attributes
    ----------

    - expression: the adapted expression tree (a copy of the input).
    - rewrites: list of semantically equivalent rewrites that were applied.
    - version: filter encoding version of the generated XML.
    """

    def __init__(self, expression, rewrites, version, prefixes, namespaces):
        self.expression = expression
        self.rewrites = list(rewrites)
        self.version = version
        self._prefixes = prefixes
        self._namespaces = namespaces or {}

    def toXML(self):
        """Serialise the adapted filter to an lxml Element.

        The returned ``fes:Filter``/``ogc:Filter`` root carries namespace
        declarations for every prefix used by property references, including
        custom prefixes, so the document is parseable and self-contained.
        """
        nsmap = _resolve_namespaces(self._prefixes, self._namespaces)
        root = etree.Element(_filter_root_tag(self.version), nsmap=nsmap)
        expression = self.expression
        if _is_filter(expression):
            root.append(expression.filter.toXML())
        else:
            root.append(expression.toXML())
        return root

    def tostring(self, encoding=None, xml_declaration=False):
        """Serialise the adapted filter to a text string.

        Returns a ``str`` by default.  When ``xml_declaration`` is requested
        the output is encoded with ``encoding`` (default ``utf-8``) and
        returned as ``bytes``.
        """
        if xml_declaration:
            encoding = encoding or "utf-8"
            return etree.tostring(
                self.toXML(), encoding=encoding, xml_declaration=True)
        return etree.tostring(self.toXML(), encoding="unicode")


def is_expression_tree(value):
    """Return True when ``value`` is an FES expression object tree."""
    return isinstance(value, (fes.OgcExpression, fes2.OgcExpression))


def build_filter(expression, capabilities, version="2.0.0", namespaces=None):
    """Check ``expression`` against ``capabilities`` and build a filter.

    Parameters
    ----------

    - expression: an expression tree made of :mod:`owslib.fes` or
      :mod:`owslib.fes2` objects (optionally wrapped in ``fes.Filter`` /
      ``fes2.Filter``).  The tree is never modified.
    - capabilities: parsed filter capabilities of the target service
      (``fes.FilterCapabilities`` / ``fes2.FilterCapabilities``).
    - version: filter encoding version to generate, ``'1.0.0'``, ``'1.1.0'``
      or ``'2.0.0'`` (default ``'2.0.0'``).
    - namespaces: optional ``{prefix: uri}`` bindings for custom prefixes
      used in property references.  Well-known prefixes (``csw``, ``gml``,
      ``ows``, ...) are resolved automatically.

    Returns an :class:`AdaptedFilter` whose ``rewrites`` list documents every
    semantically equivalent rewrite applied.  Raises
    :class:`FilterCapabilityError` before generating any request if a node
    cannot be expressed by the target service.
    """
    rewriter = FilterRewriter(capabilities, version)
    adapted = rewriter.adapt(expression)
    return AdaptedFilter(adapted, rewriter.rewrites, version,
                         rewriter.prefixes, namespaces)
