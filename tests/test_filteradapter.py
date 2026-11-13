"""Tests for capabilities-aware filter request building."""

from copy import deepcopy

import pytest

from owslib import fes, fes2
from owslib.etree import etree
from owslib.filteradapter import (
    AdaptedFilter,
    FilterCapabilityError,
    FilterRewriter,
    build_filter,
)
from owslib.gml import Point
from owslib.wfs import WebFeatureService

from tests.utils import compare_xml, resource_file

CUSTOM_NAMESPACES = {"acme": "https://example.org/acme/1.0",
                     "ext": "https://example.org/ext/2.0"}

FES_NS = "http://www.opengis.net/fes/2.0"
GML32_NS = "http://www.opengis.net/gml/3.2"


@pytest.fixture
def full_service():
    xml = open(resource_file("wfs_filter_caps_full_200.xml"), "rb").read()
    return WebFeatureService("http://example.org/wfs", xml=xml, version="2.0.0")


@pytest.fixture
def limited_service():
    xml = open(resource_file("wfs_filter_caps_limited_200.xml"), "rb").read()
    return WebFeatureService("http://example.org/wfs", xml=xml, version="2.0.0")


def _expression_tree():
    """A reusable tree with nested Not plus spatial and comparison nodes."""
    point = Point(id="p1", srsName="urn:ogc:def:crs:EPSG::4326", pos=[4.0, 51.0])
    return fes2.And([
        fes2.Or([
            fes2.PropertyIsEqualTo("acme:name", "gold"),
            fes2.Not([fes2.PropertyIsLike("ext:kind", "%x%")]),
        ]),
        fes2.Not([fes2.Intersects("acme:geom", point)]),
    ])


def test_full_capabilities_builds_parseable_filter(full_service, monkeypatch):
    """The supported tree yields parseable XML and never contacts the service."""
    expression = _expression_tree()

    def fail(*args, **kwargs):
        raise AssertionError("the request must not be sent")

    monkeypatch.setattr("owslib.feature.wfs200.openURL", fail)

    adapted = full_service.getfilterrequest(expression)
    xml = adapted.tostring()

    root = etree.fromstring(xml)
    assert root.tag == "{%s}Filter" % FES_NS
    assert root.nsmap["acme"] == CUSTOM_NAMESPACES["acme"]
    assert root.nsmap["ext"] == CUSTOM_NAMESPACES["ext"]


def test_full_capabilities_output_structure(full_service):
    expression = _expression_tree()
    root = full_service.getfilterrequest(expression).toXML()

    assert root.nsmap["acme"] == CUSTOM_NAMESPACES["acme"]
    values = [node.text for node in root.iter("{%s}ValueReference" % FES_NS)]
    assert values == ["acme:name", "ext:kind", "acme:geom"]
    # nested Not is preserved rather than flattened or dropped
    assert len(root.findall(".//{%s}Not" % FES_NS)) == 2


def test_limited_capabilities_fail_before_building(limited_service, monkeypatch):
    """A service missing a required operator fails with path and gap."""
    expression = _expression_tree()

    def fail(*args, **kwargs):
        raise AssertionError("the request must not be sent")

    monkeypatch.setattr("owslib.feature.wfs200.openURL", fail)

    with pytest.raises(FilterCapabilityError) as excinfo:
        limited_service.getfilterrequest(expression)

    error = excinfo.value
    assert error.node_path == "And[1]/Not[0]/Intersects"
    assert any("Intersects" in gap for gap in error.gaps)
    assert error.rewrites == []


def test_unsupported_node_is_not_dropped(limited_service):
    tree = fes2.Or([
        fes2.PropertyIsEqualTo("acme:name", "gold"),
        fes2.PropertyIsGreaterThan("acme:rank", 10),
    ])
    with pytest.raises(FilterCapabilityError) as excinfo:
        limited_service.getfilterrequest(tree)
    assert excinfo.value.node_path == "Or[1]/PropertyIsGreaterThan"
    assert "PropertyIsGreaterThan" in excinfo.value.gaps[0]


def test_input_expression_is_not_modified(full_service, limited_service):
    expression = _expression_tree()
    before = etree.tostring(expression.toXML())
    child_before = etree.tostring(expression.operations[0].toXML())

    full_service.getfilterrequest(expression)
    with pytest.raises(FilterCapabilityError):
        limited_service.getfilterrequest(expression)

    assert etree.tostring(expression.toXML()) == before
    assert etree.tostring(expression.operations[0].toXML()) == child_before


def test_adaptation_returns_independent_copy(full_service):
    expression = _expression_tree()
    adapted = full_service.getfilterrequest(expression)
    assert isinstance(adapted, AdaptedFilter)
    assert adapted.expression is not expression
    expression.operations[1].operations[0].propertyname = "acme:changed"
    assert adapted.expression.operations[1].operations[0].propertyname == "acme:geom"


def test_full_capabilities_wrapped_filter_tree(full_service):
    expression = fes2.Filter(_expression_tree())
    adapted = full_service.getfilterrequest(expression)
    root = adapted.toXML()
    assert root.find("{%s}And" % FES_NS) is not None
    assert compare_xml(root, adapted.toXML())


def test_safe_rewrite_not_equal_to_not_equal():
    caps = fes.FilterCapabilities(None)
    caps.logical_operators = True
    caps.scalar_comparison_operators = ["EqualTo"]
    caps.spatial_operators = ["BBOX"]
    caps.spatial_operands = ["gml:Envelope"]

    node = fes.PropertyIsNotEqualTo("acme:status", "closed")
    adapted = build_filter(node, caps, version="1.1.0",
                           namespaces={"acme": CUSTOM_NAMESPACES["acme"]})

    assert len(adapted.rewrites) == 1
    rewrite = adapted.rewrites[0]
    assert "PropertyIsNotEqualTo" in rewrite
    assert "Not(PropertyIsEqualTo)" in rewrite

    root = adapted.toXML()
    assert root.findall(".//{http://www.opengis.net/ogc}Not")
    equal = root.find(".//{http://www.opengis.net/ogc}PropertyIsEqualTo")
    assert equal is not None
    etree.fromstring(adapted.tostring())
    # the original node still serialises as PropertyIsNotEqualTo
    assert node.propertyoperator == "ogc:PropertyIsNotEqualTo"


def test_rewrite_without_logical_operators_is_rejected():
    caps = fes.FilterCapabilities(None)
    caps.logical_operators = False
    caps.scalar_comparison_operators = ["EqualTo"]

    node = fes.PropertyIsNotEqualTo("acme:status", "closed")
    with pytest.raises(FilterCapabilityError) as excinfo:
        build_filter(node, caps, version="1.1.0",
                     namespaces={"acme": CUSTOM_NAMESPACES["acme"]})
    assert excinfo.value.rewrites == []
    assert "PropertyIsNotEqualTo" in excinfo.value.gaps[0]


def test_unsupported_logic_is_reported(limited_service):
    caps = fes2.FilterCapabilities(None)
    caps.logical_operators = False
    caps.scalar_comparison_operators = ["PropertyIsEqualTo"]
    tree = fes2.And([
        fes2.PropertyIsEqualTo("acme:a", 1),
        fes2.PropertyIsEqualTo("acme:b", 2),
    ])
    with pytest.raises(FilterCapabilityError) as excinfo:
        build_filter(tree, caps, version="2.0.0",
                     namespaces={"acme": CUSTOM_NAMESPACES["acme"]})
    assert excinfo.value.node_path == "And"
    assert "logical" in excinfo.value.gaps[0].lower()


def test_spatial_geometry_operand_gap():
    caps = fes2.FilterCapabilities(None)
    caps.logical_operators = True
    caps.spatial_operators = ["Contains"]
    caps.spatial_operands = ["gml:Envelope"]
    point = Point(id="p1", srsName="urn:ogc:def:crs:EPSG::4326", pos=[4.0, 51.0])
    tree = fes2.Contains("acme:geom", point)
    with pytest.raises(FilterCapabilityError) as excinfo:
        build_filter(tree, caps, version="2.0.0",
                     namespaces={"acme": CUSTOM_NAMESPACES["acme"]})
    assert excinfo.value.node_path == "Contains"
    assert any("geometry operand" in gap for gap in excinfo.value.gaps)


def test_custom_prefix_requires_binding():
    caps = fes2.FilterCapabilities(None)
    caps.logical_operators = True
    caps.scalar_comparison_operators = ["PropertyIsEqualTo"]
    node = fes2.PropertyIsEqualTo("weirdprefix:name", "gold")
    with pytest.raises(ValueError):
        build_filter(node, caps, version="2.0.0").toXML()


def test_custom_prefix_is_bound_in_output(full_service):
    point = Point(id="p1", srsName="urn:ogc:def:crs:EPSG::4326", pos=[4.0, 51.0])
    tree = fes2.Intersects("weirdprefix:geom", point)
    adapted = full_service.getfilterrequest(
        tree, namespaces={"weirdprefix": "https://example.org/weird/9.9"})
    root = adapted.toXML()
    assert root.nsmap["weirdprefix"] == "https://example.org/weird/9.9"
    etree.fromstring(adapted.tostring())


def test_capability_parser_logical_flag(full_service, limited_service):
    assert full_service.filters.logical_operators is True
    assert limited_service.filters.logical_operators is True


def test_unknown_expression_type_is_rejected():
    class Bogus(fes2.OgcExpression):
        pass

    caps = fes2.FilterCapabilities(None)
    with pytest.raises(FilterCapabilityError) as excinfo:
        FilterRewriter(caps, "2.0.0").adapt(Bogus())
    assert "unsupported expression type" in excinfo.value.gaps[0]


def test_fes100_capabilities_are_parsed():
    xml = open(resource_file("wfs_mapserver_demo_getcapabilities_100.xml"), "rb").read()
    wfs = WebFeatureService("http://example.org/wfs", xml=xml, version="1.0.0")
    assert wfs.filters.logical_operators is True
    assert "EqualTo" in wfs.filters.scalar_comparison_operators
    assert "Intersects" in wfs.filters.spatial_operators


def test_wfs110_capabilities_adaptation():
    xml = open(resource_file("wfs_dov_getcapabilities_110.xml"), "rb").read()
    wfs = WebFeatureService("http://example.org/wfs", xml=xml, version="1.1.0")
    tree = fes.And([
        fes.PropertyIsEqualTo("acme:name", "gold"),
        fes.Not([fes.PropertyIsLike("acme:kind", "%x%")]),
    ])
    adapted = wfs.getfilterrequest(tree, namespaces={"acme": CUSTOM_NAMESPACES["acme"]})
    root = etree.fromstring(adapted.tostring())
    assert root.tag == "{http://www.opengis.net/ogc}Filter"
    assert root.nsmap["acme"] == CUSTOM_NAMESPACES["acme"]


def test_getfeature_fails_before_sending(limited_service, monkeypatch):
    expression = _expression_tree()

    def fail(*args, **kwargs):
        raise AssertionError("openURL must not be called")

    monkeypatch.setattr("owslib.feature.wfs200.openURL", fail)

    with pytest.raises(FilterCapabilityError):
        limited_service.getfeature(typename=["acme:sites"], filter=expression, method="POST")


def test_successful_filter_flows_into_post_request(full_service):
    expression = _expression_tree()
    url, data = full_service.getPOSTGetFeatureRequest(
        typename=["acme:sites"],
        filter=full_service.getfilterrequest(expression).tostring(),
    )
    root = etree.fromstring(data)
    assert root.findall(".//{%s}Intersects" % FES_NS)


def test_rewrite_diagnostics_are_complete():
    caps = fes.FilterCapabilities(None)
    caps.logical_operators = True
    caps.scalar_comparison_operators = ["EqualTo"]
    tree = fes.Not([fes.PropertyIsNotEqualTo("acme:status", "closed")])
    adapted = build_filter(tree, caps, version="1.1.0",
                           namespaces={"acme": CUSTOM_NAMESPACES["acme"]})
    assert adapted.rewrites == [
        "Not[0]/PropertyIsNotEqualTo: PropertyIsNotEqualTo rewritten as "
        "Not(PropertyIsEqualTo) (semantically equivalent)"]


def test_deepcopy_leaves_original_tree_intact(full_service):
    expression = _expression_tree()
    original = deepcopy(expression)
    full_service.getfilterrequest(expression)
    assert etree.tostring(expression.toXML()) == etree.tostring(original.toXML())


def test_fes100_filter_build():
    xml = open(resource_file("wfs_mapserver_demo_getcapabilities_100.xml"), "rb").read()
    wfs = WebFeatureService("http://example.org/wfs", xml=xml, version="1.0.0")
    tree = fes.Or([
        fes.PropertyIsEqualTo("acme:name", "gold"),
        fes.PropertyIsLike("acme:kind", "%x%"),
    ])
    adapted = build_filter(tree, wfs.filters, version="1.0.0",
                           namespaces={"acme": CUSTOM_NAMESPACES["acme"]})
    root = etree.fromstring(adapted.tostring())
    assert root.tag == "{http://www.opengis.net/ogc}Filter"
    assert root.find("{http://www.opengis.net/ogc}Or") is not None


def test_wfs100_getfilterrequest_entry():
    xml = open(resource_file("wfs_mapserver_demo_getcapabilities_100.xml"), "rb").read()
    wfs = WebFeatureService("http://example.org/wfs", xml=xml, version="1.0.0")
    tree = fes.PropertyIsEqualTo("acme:name", "gold")
    adapted = wfs.getfilterrequest(tree, namespaces={"acme": CUSTOM_NAMESPACES["acme"]})
    assert adapted.version == "1.0.0"
    root = etree.fromstring(adapted.tostring())
    assert root.tag == "{http://www.opengis.net/ogc}Filter"


def test_wfs100_unsupported_operator_fails():
    xml = open(resource_file("wfs_mapserver_demo_getcapabilities_100.xml"), "rb").read()
    wfs = WebFeatureService("http://example.org/wfs", xml=xml, version="1.0.0")
    tree = fes.PropertyIsNull("acme:name")
    with pytest.raises(FilterCapabilityError) as excinfo:
        wfs.getfilterrequest(tree, namespaces={"acme": CUSTOM_NAMESPACES["acme"]})
    assert excinfo.value.node_path == "PropertyIsNull"


def test_capabilities_object_entry_point(full_service):
    expression = fes2.PropertyIsEqualTo("acme:name", "gold")
    adapted = full_service.filters.getfilterrequest(
        expression, namespaces={"acme": CUSTOM_NAMESPACES["acme"]})
    assert adapted.rewrites == []
    assert etree.fromstring(adapted.tostring()) is not None
