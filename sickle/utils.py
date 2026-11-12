# coding: utf-8
"""
    sickle.utils
    ~~~~~~~~~~~~

    Collects utility functions.

    :copyright: Copyright 2015 Mathias Loesch
"""

import re
from collections import defaultdict


def get_namespace(element):
    """Return the namespace of an XML element.

    :param element: An XML element.
    """
    return re.search('(\{.*\})', element.tag).group(1)


def xml_to_dict(tree, paths=None, nsmap=None, strip_ns=False):
    """Convert an XML tree to a dictionary.

    :param tree: etree Element
    :type tree: :class:`lxml.etree._Element`
    :param paths: An optional list of XPath expressions applied on the XML tree.
    :type paths: list[basestring]
    :param nsmap: An optional prefix-namespace mapping for conciser spec of paths.
    :type nsmap: dict
    :param strip_ns: Flag for whether to remove the namespaces from the tags.
    :type strip_ns: bool
    """
    paths = paths or ['.//']
    nsmap = nsmap or {}
    fields = defaultdict(list)
    for path in paths:
        elements = tree.findall(path, nsmap)
        for element in elements:
            tag = re.sub(
                r'\{.*\}', '', element.tag) if strip_ns else element.tag
            fields[tag].append(element.text)
    return dict(fields)


class MetadataField(object):
    """A metadata field identified by namespace URI and element path."""

    def __init__(self, name, path, attribute=None):
        self.name = name
        self.path = path
        self.attribute = attribute
        self.path_parts = _parse_clark_path(path)


class MetadataValue(object):
    """A projected value and the location of its XML source."""

    def __init__(self, value, source_path, source_line):
        self.value = value
        self.source_path = source_path
        self.source_line = source_line

    def __iter__(self):
        return iter((
            ('value', self.value),
            ('source_path', self.source_path),
            ('source_line', self.source_line),
        ))

    def as_dict(self):
        return dict(self)

    def __eq__(self, other):
        return isinstance(other, MetadataValue) and list(self) == list(other)

    def __ne__(self, other):
        return not self == other

    def __repr__(self):
        return '<MetadataValue %r at %s:%s>' % (
            self.value, self.source_path, self.source_line)


def project_metadata(tree, fields):
    """Project metadata elements onto an explicit, namespace-aware field set.

    Paths use Clark notation, for example ``/{urn:example}record/{urn:example}title``.
    This makes the selection independent of the namespace prefix used by the
    source document. Element text is selected unless ``attribute`` is supplied.

    Missing elements produce an empty value list. Empty leaf elements have an
    empty-string text value, while elements containing only children have
    ``None`` because they have no direct text. Mixed content returns only the
    direct text of the selected element; descendant text and tail text after
    children are not joined into the value. Values retain XML document order.
    """
    if hasattr(fields, 'items'):
        fields = fields.items()
    projected = defaultdict(list)
    field_order = [_coerce_field(field) for field in fields]
    for field in field_order:
        values = projected[field.name]
        for element in _iter_path_elements(tree, field.path_parts):
            if field.attribute is None:
                value = element.text
                if value is None and len(element) == 0:
                    value = ''
                source_path = _clark_path(element)
            else:
                value = element.attrib.get(field.attribute)
                source_path = '%s/@%s' % (
                    _clark_path(element), field.attribute)
            values.append(
                MetadataValue(value, source_path, element.sourceline))
    return dict(projected)


def _coerce_field(field):
    if isinstance(field, MetadataField):
        return field
    name, spec = field
    if isinstance(spec, dict):
        return MetadataField(name, spec['path'],
                             attribute=spec.get('attribute'))
    return MetadataField(name, spec)


def _parse_clark_path(path):
    if not path.startswith('/'):
        raise ValueError("Metadata path must be absolute: %s" % path)
    raw_parts = [part for part in path.split('/') if part]
    if not raw_parts:
        raise ValueError("Metadata path must contain an element: %s" % path)
    parsed_parts = []
    for part in raw_parts:
        match = re.match(r'^\{(?P<namespace>[^}]*)}(?P<name>.+)$', part)
        if match is None:
            raise ValueError(
                "Metadata path elements must use Clark notation: %s" % path)
        parsed_parts.append((match.group('namespace'), match.group('name')))
    return parsed_parts


def _iter_path_elements(tree, path_parts):
    namespace, local_name = path_parts[0]
    root_tag = _tag(namespace, local_name)
    candidates = [tree] if tree.tag == root_tag else []
    for namespace, local_name in path_parts[1:]:
        tag = _tag(namespace, local_name)
        next_candidates = []
        for candidate in candidates:
            next_candidates.extend(
                element for element in candidate
                if element.tag == tag)
        candidates = next_candidates
    return candidates


def _tag(namespace, local_name):
    if namespace:
        return '{%s}%s' % (namespace, local_name)
    return local_name


def _clark_path(element):
    parts = []
    for ancestor in reversed(list(element.iterancestors())):
        parts.append(_canonical_tag(ancestor.tag))
    parts.append(_canonical_tag(element.tag))
    return '/' + '/'.join(parts)


def _canonical_tag(tag):
    if tag.startswith('{'):
        return tag
    return '{}%s' % tag
