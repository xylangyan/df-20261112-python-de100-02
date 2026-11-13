"""Offline tests for the explicit WFS spatial extent adaption layer.

No map rendering and no public WFS traffic: capabilities come from local
fixtures and every HTTP call is intercepted by a request capturer.
"""

from io import BytesIO
from urllib.parse import parse_qs, urlparse

import pytest

from owslib.crs import Crs
from owslib.etree import etree
from owslib.feature import wfs100, wfs110, wfs200
from owslib.feature.spatial import (
    SpatialExtent,
    SpatialExtentError,
    extract_feature_ids,
    merge_gml_feature_collections,
)
from owslib.namespaces import Namespaces
from owslib.wfs import WebFeatureService
from tests.utils import resource_file

NAMESPACES = Namespaces()
GML32 = NAMESPACES.get_namespace("gml32")
WFS20 = NAMESPACES.get_namespace("wfs20")
FES = NAMESPACES.get_namespace("fes")
GML = NAMESPACES.get_namespace("gml")
OGC = NAMESPACES.get_namespace("ogc")

CAPS = {
    "1.0.0": "wfs_dov_getcapabilities_100_verbOptions.xml",
    "1.1.0": "wfs_HSRS_GetCapabilities_1_1_0.xml",
    "2.0.0": "wfs_CUZK_GetCapabilities_2_0_0.xml",
}
TYPENAMES = {
    "1.0.0": "gw_meetnetten:meetnetten",
    "1.1.0": "okresy",
    "2.0.0": "CP:CadastralBoundary",
}


def make_wfs(version):
    """Build a WebFeatureService from the offline capabilities fixture."""
    xml = open(resource_file(CAPS[version]), "rb").read()
    return WebFeatureService(
        "http://example.test/wfs", xml=xml, version=version)


class RequestCapturer:
    """Intercepts openURL, records requests, serves canned GML responses."""

    def __init__(self, monkeypatch, version, response_factory=None):
        self.calls = []
        self.version = version
        self.response_factory = response_factory or (lambda: b"<empty/>")
        module = {
            "1.0.0": wfs100,
            "1.1.0": wfs110,
            "2.0.0": wfs200,
        }[version]
        monkeypatch.setattr(module, "openURL", self._capture)
        # the spatial multi-request path calls openURL from the base module
        import owslib.feature
        monkeypatch.setattr(owslib.feature, "openURL", self._capture)

    def _capture(self, url, data=None, method="Get", **kwargs):
        self.calls.append({"url": url, "data": data, "method": method})
        return BytesIO(self.response_factory())

    def kvp(self, index=-1):
        """Query-string mapping of the captured GET request."""
        query = urlparse(self.calls[index]["url"]).query
        return {key: values[0] for key, values in parse_qs(query).items()}

    def post_envelope(self, index=-1):
        """(srsName, lowerCorner, upperCorner) of the captured POST body."""
        root = etree.fromstring(self.calls[index]["data"])
        if self.version == "2.0.0":
            envelope = root.find(
                ".//{%s}BBOX/{%s}Envelope" % (FES, GML32))
            lower = envelope.find("{%s}lowerCorner" % GML32)
            upper = envelope.find("{%s}upperCorner" % GML32)
        else:
            envelope = root.find(
                ".//{%s}BBOX/{%s}Envelope" % (OGC, GML))
            lower = envelope.find("{%s}lowerCorner" % GML)
            upper = envelope.find("{%s}upperCorner" % GML)
        return envelope.get("srsName"), lower.text, upper.text


# ---------------------------------------------------------------------------
# Caller-declared axis order and CRS spelling rules
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("version", ["1.1.0", "2.0.0"])
def test_epsg4326_urn_is_latlon(monkeypatch, version):
    wfs = make_wfs(version)
    capturer = RequestCapturer(monkeypatch, version)
    extent = SpatialExtent.from_bbox(
        [15, 49, 16, 51], axis_order="lonlat",
        crs=Crs("urn:ogc:def:crs:EPSG::4326"))
    wfs.getfeature(typename=TYPENAMES[version], spatial_extent=extent)

    bbox = capturer.kvp()["bbox"].split(",")
    assert bbox[:4] == ["49", "15", "51", "16"]
    if version == "1.1.0":
        assert bbox[4] == "urn:ogc:def:crs:EPSG::4326"
    else:
        assert bbox[4] == "http://www.opengis.net/def/crs/EPSG/0/4326"


@pytest.mark.parametrize("version", ["1.1.0", "2.0.0"])
def test_crs84_stays_lonlat(monkeypatch, version):
    wfs = make_wfs(version)
    capturer = RequestCapturer(monkeypatch, version)
    extent = SpatialExtent.from_bbox(
        [15, 49, 16, 51], axis_order="lonlat",
        crs=Crs("urn:ogc:def:crs:OGC:1.3:CRS84"))
    wfs.getfeature(typename=TYPENAMES[version], spatial_extent=extent)

    bbox = capturer.kvp()["bbox"].split(",")
    assert bbox[:4] == ["15", "49", "16", "51"]
    if version == "1.1.0":
        assert bbox[4] == "urn:ogc:def:crs:OGC:1.3:CRS84"
    else:
        assert bbox[4] == "http://www.opengis.net/def/crs/OGC/1.3/CRS84"


def test_code_style_eps4326_and_crs84_are_distinct(monkeypatch):
    wfs = make_wfs("1.1.0")
    capturer = RequestCapturer(monkeypatch, "1.1.0")

    epsg_extent = SpatialExtent.from_bbox(
        [15, 49, 16, 51], axis_order="lonlat", crs=Crs("EPSG:4326"))
    wfs.getfeature(typename=TYPENAMES["1.1.0"], spatial_extent=epsg_extent)
    assert capturer.kvp()["bbox"] == (
        "49,15,51,16,urn:ogc:def:crs:EPSG::4326")

    crs84_extent = SpatialExtent.from_bbox(
        [15, 49, 16, 51], axis_order="lonlat", crs=Crs("CRS:84"))
    wfs.getfeature(typename=TYPENAMES["1.1.0"], spatial_extent=crs84_extent)
    assert capturer.kvp()["bbox"] == (
        "15,49,16,51,urn:ogc:def:crs:OGC:1.3:CRS84")


def test_latlon_declaration_swaps_back_for_epsg4326(monkeypatch):
    wfs = make_wfs("1.1.0")
    capturer = RequestCapturer(monkeypatch, "1.1.0")
    extent = SpatialExtent.from_bbox(
        [49, 15, 51, 16], axis_order="latlon", crs=Crs("EPSG:4326"))
    wfs.getfeature(typename=TYPENAMES["1.1.0"], spatial_extent=extent)
    assert capturer.kvp()["bbox"] == (
        "49,15,51,16,urn:ogc:def:crs:EPSG::4326")


def test_wfs100_preserves_caller_order(monkeypatch):
    wfs = make_wfs("1.0.0")
    capturer = RequestCapturer(monkeypatch, "1.0.0")
    extent = SpatialExtent.from_bbox(
        [49, 15, 51, 16], axis_order="latlon", crs=Crs("EPSG:4326"))
    wfs.getfeature(typename=TYPENAMES["1.0.0"], spatial_extent=extent)
    bbox = capturer.kvp()["bbox"].split(",")
    assert bbox[:4] == ["49", "15", "51", "16"]
    assert bbox[4] == "EPSG:4326"


# ---------------------------------------------------------------------------
# KVP bbox and XML Filter must encode the same extent semantics
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("version", ["1.1.0", "2.0.0"])
def test_kvp_and_post_carry_identical_semantics(monkeypatch, version):
    wfs = make_wfs(version)
    capturer = RequestCapturer(monkeypatch, version)
    extent = SpatialExtent.from_bbox(
        [15, 49, 16, 51], axis_order="lonlat",
        crs=Crs("urn:ogc:def:crs:EPSG::4326"))

    wfs.getfeature(
        typename=TYPENAMES[version], spatial_extent=extent, method="Get")
    kvp_bbox = capturer.kvp()["bbox"].split(",")

    wfs.getfeature(
        typename=TYPENAMES[version], spatial_extent=extent, method="Post")
    srs_name, lower, upper = capturer.post_envelope()

    assert " ".join(kvp_bbox[:2]) == lower
    assert " ".join(kvp_bbox[2:4]) == upper
    assert kvp_bbox[4] == srs_name
    assert lower.split(" ") == ["49", "15"]
    assert upper.split(" ") == ["51", "16"]


# ---------------------------------------------------------------------------
# Third dimension must survive request construction
# ---------------------------------------------------------------------------

def test_three_dimensional_extent_kept_in_kvp(monkeypatch):
    wfs = make_wfs("2.0.0")
    capturer = RequestCapturer(monkeypatch, "2.0.0")
    extent = SpatialExtent(
        15, 49, 16, 51, axis_order="lonlat",
        crs=Crs("http://www.opengis.net/def/crs/EPSG/0/4979"),
        min_3=100, max_3=200)
    wfs.getfeature(typename=TYPENAMES["2.0.0"], spatial_extent=extent)
    bbox = capturer.kvp()["bbox"].split(",")
    assert len(bbox) == 7
    assert bbox == [
        "49", "15", "100", "51", "16", "200",
        "http://www.opengis.net/def/crs/EPSG/0/4979"]


def test_three_dimensional_extent_kept_in_post(monkeypatch):
    wfs = make_wfs("2.0.0")
    capturer = RequestCapturer(monkeypatch, "2.0.0")
    extent = SpatialExtent(
        15, 49, 16, 51, axis_order="lonlat",
        crs=Crs("http://www.opengis.net/def/crs/EPSG/0/4979"),
        min_3=100, max_3=200)
    wfs.getfeature(
        typename=TYPENAMES["2.0.0"], spatial_extent=extent, method="Post")
    _, lower, upper = capturer.post_envelope()
    assert lower == "49 15 100"
    assert upper == "51 16 200"


def test_partial_3d_extent_rejected():
    with pytest.raises(SpatialExtentError):
        SpatialExtent(
            15, 49, 16, 51, axis_order="lonlat",
            crs=Crs("EPSG:4326"), min_3=100)


# ---------------------------------------------------------------------------
# Antimeridian crossing: split, never widen
# ---------------------------------------------------------------------------

def _gml_collection(feature_ids):
    members = "".join(
        '<gml:featureMember><t:F gml:id="{0}"/></gml:featureMember>'.format(fid)
        for fid in feature_ids)
    return (
        '<wfs:FeatureCollection xmlns:wfs="http://www.opengis.net/wfs" '
        'xmlns:gml="http://www.opengis.net/gml" '
        'xmlns:t="urn:test">' + members + '</wfs:FeatureCollection>'
    ).encode("utf-8")


def test_antimeridian_split_issues_two_small_requests(monkeypatch):
    wfs = make_wfs("2.0.0")
    capturer = RequestCapturer(monkeypatch, "2.0.0")
    extent = SpatialExtent.from_bbox(
        [170, 10, -170, 20], axis_order="lonlat",
        crs=Crs("urn:ogc:def:crs:OGC:1.3:CRS84"))
    response = wfs.getfeature(
        typename=TYPENAMES["2.0.0"], spatial_extent=extent, method="Get")

    assert len(capturer.calls) == 2
    west = capturer.kvp(0)["bbox"].split(",")
    east = capturer.kvp(1)["bbox"].split(",")
    assert west[:4] == ["170", "10", "180", "20"]
    assert east[:4] == ["-180", "10", "-170", "20"]

    widths = float(west[2]) - float(west[0]), float(east[2]) - float(east[0])
    assert widths == (10.0, 10.0)
    # a 20-degree band must not turn into a near-global request
    assert sum(widths) == 20.0

    assert len(response.request_bboxes) == 2
    assert response.crs == "http://www.opengis.net/def/crs/OGC/1.3/CRS84"


def test_antimeridian_split_post_issues_two_envelopes(monkeypatch):
    wfs = make_wfs("2.0.0")
    capturer = RequestCapturer(monkeypatch, "2.0.0")
    extent = SpatialExtent.from_bbox(
        [170, 10, -170, 20], axis_order="lonlat", crs=Crs("EPSG:4326"))
    requests = wfs.getPOSTSpatialRequests(
        extent, TYPENAMES["2.0.0"])
    assert len(requests) == 2
    capturer.calls = [
        {"url": url, "data": payload, "method": "Post"}
        for url, payload, _ in requests]
    assert capturer.post_envelope(0)[1:] == ("10 170", "20 180")
    assert capturer.post_envelope(0)[0] == (
        "http://www.opengis.net/def/crs/EPSG/0/4326")
    assert capturer.post_envelope(1)[1:] == ("10 -180", "20 -170")


def test_projected_extent_not_split_but_follows_crs_axis_order():
    from owslib.feature.spatial import adapt_extent

    # EPSG:2065 is a northing,easting CRS, so a caller declaring xy input
    # still gets a yx request; no antimeridian split applies to projected CRS.
    extent = SpatialExtent.from_bbox(
        [-685336, -993518, -684996, -993285], axis_order="xy",
        crs=Crs("EPSG:2065"))
    parts = adapt_extent(extent, "1.1.0")
    assert len(parts) == 1
    assert parts[0].kvp_value() == (
        "-993518,-685336,-993285,-684996,urn:ogc:def:crs:EPSG::2065")


def test_non_crossing_geographic_extent_single_request(monkeypatch):
    wfs = make_wfs("1.1.0")
    capturer = RequestCapturer(monkeypatch, "1.1.0")
    extent = SpatialExtent.from_bbox(
        [-10, 40, 30, 60], axis_order="lonlat", crs=Crs("EPSG:4326"))
    wfs.getfeature(typename=TYPENAMES["1.1.0"], spatial_extent=extent)
    assert len(capturer.calls) == 1


# ---------------------------------------------------------------------------
# Merging and feature-id deduplication across split requests
# ---------------------------------------------------------------------------

def test_split_responses_merged_and_deduplicated(monkeypatch):
    payloads = iter([
        _gml_collection(["pt-west-1", "pt-west-2"]),
        _gml_collection(["pt-west-2", "pt-east-1"]),
    ])
    wfs = make_wfs("1.1.0")
    capturer = RequestCapturer(
        monkeypatch, "1.1.0", response_factory=lambda: next(payloads))
    extent = SpatialExtent.from_bbox(
        [170, 10, -170, 20], axis_order="lonlat", crs=Crs("EPSG:4326"))
    response = wfs.getfeature(
        typename=TYPENAMES["1.1.0"], spatial_extent=extent, method="Get")

    assert extract_feature_ids(response.content) == [
        "pt-east-1", "pt-west-1", "pt-west-2"]
    assert response.crs == "urn:ogc:def:crs:EPSG::4326"
    assert response.request_bboxes == [
        (10.0, 170.0, 20.0, 180.0), (10.0, -180.0, 20.0, -170.0)]


def test_get_spatial_feature_ids_uses_tagged_points(monkeypatch):
    payloads = iter([
        _gml_collection(["pt-west-1", "pt-west-2"]),
        _gml_collection(["pt-east-1", "pt-east-2"]),
    ])
    wfs = make_wfs("1.1.0")
    RequestCapturer(
        monkeypatch, "1.1.0", response_factory=lambda: next(payloads))
    extent = SpatialExtent.from_bbox(
        [170, 10, -170, 20], axis_order="lonlat", crs=Crs("EPSG:4326"))
    ids = wfs.get_spatial_feature_ids(
        typename=TYPENAMES["1.1.0"], spatial_extent=extent)
    assert ids == ["pt-east-1", "pt-east-2", "pt-west-1", "pt-west-2"]


def test_merge_single_payload_passthrough():
    payload = _gml_collection(["a", "b"])
    merged = merge_gml_feature_collections([payload])
    assert extract_feature_ids(merged) == ["a", "b"]


# ---------------------------------------------------------------------------
# Response annotation: actual CRS and requested extents
# ---------------------------------------------------------------------------

def test_response_annotates_crs_and_requested_extent(monkeypatch):
    wfs = make_wfs("2.0.0")
    RequestCapturer(monkeypatch, "2.0.0")
    extent = SpatialExtent.from_bbox(
        [15, 49, 16, 51], axis_order="lonlat",
        crs=Crs("urn:ogc:def:crs:EPSG::4326"))
    response = wfs.getfeature(
        typename=TYPENAMES["2.0.0"], spatial_extent=extent)
    assert response.crs == "http://www.opengis.net/def/crs/EPSG/0/4326"
    assert response.request_bboxes == [(49.0, 15.0, 51.0, 16.0)]
    assert response.read() == b"<empty/>"


def test_bbox_and_spatial_extent_are_mutually_exclusive(monkeypatch):
    wfs = make_wfs("1.1.0")
    RequestCapturer(monkeypatch, "1.1.0")
    extent = SpatialExtent.from_bbox(
        [15, 49, 16, 51], axis_order="lonlat", crs=Crs("EPSG:4326"))
    with pytest.raises(ValueError):
        wfs.getfeature(
            typename=TYPENAMES["1.1.0"], spatial_extent=extent,
            bbox=[15, 49, 16, 51])


def test_legacy_bbox_with_declared_axis_order(monkeypatch):
    wfs = make_wfs("1.1.0")
    capturer = RequestCapturer(monkeypatch, "1.1.0")
    wfs.getfeature(
        typename=TYPENAMES["1.1.0"], bbox=[49, 15, 51, 16],
        bbox_axis_order="latlon")
    assert capturer.kvp()["bbox"] == (
        "49,15,51,16,urn:ogc:def:crs:EPSG::4326")


def test_single_request_builder_refuses_crossing_extent(monkeypatch):
    wfs = make_wfs("1.1.0")
    RequestCapturer(monkeypatch, "1.1.0")
    extent = SpatialExtent.from_bbox(
        [170, 10, -170, 20], axis_order="lonlat", crs=Crs("EPSG:4326"))
    with pytest.raises(SpatialExtentError):
        wfs.getGETGetFeatureRequest(
            typename=TYPENAMES["1.1.0"], spatial_extent=extent)
    with pytest.raises(SpatialExtentError):
        wfs.getPOSTGetFeatureRequest(
            typename=TYPENAMES["1.1.0"], spatial_extent=extent)


def test_request_builder_exposes_two_get_urls_for_crossing_extent():
    wfs = make_wfs("2.0.0")
    extent = SpatialExtent.from_bbox(
        [170, 10, -170, 20], axis_order="lonlat",
        crs=Crs("urn:ogc:def:crs:OGC:1.3:CRS84"))
    pairs = wfs.getSpatialGETRequests(extent, TYPENAMES["2.0.0"])
    assert len(pairs) == 2
    for url, request_extent in pairs:
        assert "bbox=" in url
        assert request_extent.srs_name == (
            "http://www.opengis.net/def/crs/OGC/1.3/CRS84")


def test_short_version_spelling_uses_canonical_rules(monkeypatch):
    xml = open(resource_file(CAPS["2.0.0"]), "rb").read()
    wfs = WebFeatureService(
        "http://example.test/wfs", xml=xml, version="2.0")
    capturer = RequestCapturer(monkeypatch, "2.0.0")
    extent = SpatialExtent.from_bbox(
        [15, 49, 16, 51], axis_order="lonlat",
        crs=Crs("urn:ogc:def:crs:EPSG::4326"))
    response = wfs.getfeature(
        typename=TYPENAMES["2.0.0"], spatial_extent=extent)
    assert response.crs == "http://www.opengis.net/def/crs/EPSG/0/4326"
    assert capturer.kvp()["bbox"].startswith("49,15,51,16,")


def test_post_spatial_getfeature_merges_across_split(monkeypatch):
    payloads = iter([
        _gml_collection(["pt-west-1"]),
        _gml_collection(["pt-east-1", "pt-east-2"]),
    ])
    wfs = make_wfs("1.1.0")
    capturer = RequestCapturer(
        monkeypatch, "1.1.0", response_factory=lambda: next(payloads))
    extent = SpatialExtent.from_bbox(
        [170, 10, -170, 20], axis_order="lonlat", crs=Crs("EPSG:4326"))
    response = wfs.getfeature(
        typename=TYPENAMES["1.1.0"], spatial_extent=extent, method="Post")

    assert len(capturer.calls) == 2
    assert all(call["method"].lower() == "post" for call in capturer.calls)
    assert extract_feature_ids(response.content) == [
        "pt-east-1", "pt-east-2", "pt-west-1"]
    assert response.crs == "urn:ogc:def:crs:EPSG::4326"
    for call in capturer.calls:
        assert b"Envelope" in call["data"]
