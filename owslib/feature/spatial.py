"""Explicit spatial extent adaption for WFS GetFeature requests.

Callers state the axis order of their coordinates; this module turns that
declaration plus the service version and CRS encoding conventions into the
ordering each request dialect expects.  KVP ``bbox=`` and XML ``Filter/BBOX``
are produced from the same normalised extent so both keep identical semantics.
"""

from dataclasses import dataclass
from io import BytesIO
from typing import List, Optional, Sequence, Tuple

from owslib.crs import Crs
from owslib.etree import etree
from owslib.namespaces import Namespaces

_NAMESPACES = Namespaces()
_GML_NS = _NAMESPACES.get_namespace("gml")
_GML32_NS = _NAMESPACES.get_namespace("gml32")

# feature container local names: GML 2/3.1 collections and WFS 2.0 members
_MEMBER_TAGS = ("featureMember", "member")

# Axis order as declared by the caller when handing in coordinates.
XY = "xy"
YX = "yx"
AXIS_ORDERS = (XY, YX)

# Name used when the caller does not name an axis order.
LONLAT = "lonlat"
LATLON = "latlon"

CRS84_CODE = "CRS84"
CRS84_URN = "urn:ogc:def:crs:OGC:1.3:CRS84"
CRS84_URI = "http://www.opengis.net/def/crs/OGC/1.3/CRS84"

_CRS84_SPELLINGS = frozenset({
    "CRS84",
    "CRS:84",
    "OGC:CRS84",
    CRS84_URN,
    CRS84_URI,
})


class SpatialExtentError(ValueError):
    """Raised when an extent or axis-order declaration is unusable."""


@dataclass(frozen=True)
class SpatialExtent:
    """A caller-declared spatial extent.

    Coordinates are kept exactly as handed in.  ``axis_order`` states how to
    read them: ``'xy'`` means longitude/easting first, ``'yx'`` means
    latitude/northing first.  ``'lonlat'`` / ``'latlon'`` are accepted as
    aliases and normalised to ``'xy'`` / ``'yx'``.

    A third dimension (elevation/depth) is preserved and never silently
    truncated: provide six values for a 3D extent and read
    :meth:`is_three_dimensional` / :meth:`z_range`.
    """

    min_1: float
    min_2: float
    max_1: float
    max_2: float
    axis_order: str = XY
    crs: Optional[Crs] = None
    min_3: Optional[float] = None
    max_3: Optional[float] = None

    def __post_init__(self):
        axis_order = _normalise_axis_order(self.axis_order)
        object.__setattr__(self, "axis_order", axis_order)
        if self.crs is not None and not isinstance(self.crs, Crs):
            raise SpatialExtentError("crs must be an owslib.crs.Crs instance")
        if (self.min_3 is None) != (self.max_3 is None):
            raise SpatialExtentError(
                "3D extents require both min_3 and max_3")

    @classmethod
    def from_bbox(cls, bbox, axis_order=None, crs=None):
        """Build an extent from a 4/5/6 element bbox sequence.

        The five-element form ``(minx, miny, maxx, maxy, srsname)`` keeps the
        historical OWSLib convention of east,north coordinates; the CRS is
        taken from the fifth element unless ``crs`` overrides it.  A
        six-element form carries the third dimension and requires an explicit
        ``axis_order``.
        """
        values = list(bbox)
        if len(values) == 5:
            bbox_crs = Crs(values[4])
            coords = values[:4]
            if axis_order is None:
                axis_order = XY
        elif len(values) in (4, 6):
            bbox_crs = None
            coords = values
        else:
            raise SpatialExtentError(
                "bbox must have 4, 5 or 6 elements, got %d" % len(values))

        if crs is None:
            crs = bbox_crs
        elif not isinstance(crs, Crs):
            crs = Crs(crs)

        if len(coords) == 6:
            if axis_order is None:
                raise SpatialExtentError(
                    "3D bbox requires an explicit axis_order")
            min_1, min_2, min_3, max_1, max_2, max_3 = coords
        else:
            min_1, min_2, max_1, max_2 = coords
            min_3 = max_3 = None

        return cls(
            float(min_1), float(min_2), float(max_1), float(max_2),
            axis_order or XY, crs,
            None if min_3 is None else float(min_3),
            None if max_3 is None else float(max_3),
        )

    @property
    def dimensions(self) -> int:
        """Number of coordinate dimensions (2 or 3)."""
        return 3 if self.is_three_dimensional() else 2

    def is_three_dimensional(self) -> bool:
        return self.min_3 is not None

    def z_range(self) -> Optional[Tuple[float, float]]:
        if self.min_3 is None:
            return None
        return self.min_3, self.max_3

    @property
    def lonlat_bounds(self) -> Tuple[float, float, float, float]:
        """Horizontal bounds in (minlon, minlat, maxlon, maxlat)."""
        if self.axis_order == XY:
            return self.min_1, self.min_2, self.max_1, self.max_2
        return self.min_2, self.min_1, self.max_2, self.max_1

    def axis1_axis2_bounds(self) -> Tuple[float, float, float, float]:
        """Horizontal bounds in the caller's declared order."""
        return self.min_1, self.min_2, self.max_1, self.max_2


def _normalise_axis_order(axis_order: str) -> str:
    if axis_order is None:
        return XY
    aliases = {XY: XY, YX: YX, LONLAT: XY, LATLON: YX}
    try:
        return aliases[axis_order.lower()]
    except (AttributeError, KeyError):
        raise SpatialExtentError(
            "axis_order must be one of %r, got %r"
            % (AXIS_ORDERS + (LONLAT, LATLON), axis_order))


def _number_text(value) -> str:
    """Render numbers without an artificial trailing ``.0``."""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


_VERSION_ALIASES = {"1.0": "1.0.0", "1.1": "1.1.0", "2.0": "2.0.0"}


def normalise_version(version: str) -> str:
    """Map short service versions ('2.0') onto the canonical spelling."""
    return _VERSION_ALIASES.get(str(version), str(version))


def is_crs84(crs: Optional[Crs]) -> bool:
    """True for every spelling of the WGS84 lon,lat 2D/3D CRS."""
    if crs is None:
        return False
    if str(crs.code) == CRS84_CODE and str(crs.authority).upper() == "OGC":
        return True
    return crs.id in _CRS84_SPELLINGS


@dataclass(frozen=True)
class RequestExtent:
    """A normalised extent ready to encode for one WFS version.

    ``transmitted_order`` states the order in which the horizontal
    coordinates must be written; ``srs_name`` is the identifier to place on
    the wire for the requested dialect.
    """

    source: SpatialExtent
    version: str
    transmitted_order: str
    srs_name: str
    crs: Optional[Crs]

    @property
    def dimensions(self) -> int:
        return self.source.dimensions

    def horizontal_coordinates(
        self, order: Optional[str] = None
    ) -> Tuple[float, float, float, float]:
        """Horizontal bounds in ``order`` (defaults to transmitted order)."""
        order = order or self.transmitted_order
        lon1, lat1, lon2, lat2 = self.source.lonlat_bounds
        if order == XY:
            return lon1, lat1, lon2, lat2
        return lat1, lon1, lat2, lon2

    def corners(self) -> Tuple[Tuple[float, ...], Tuple[float, ...]]:
        """Lower and upper corners in transmitted order, 3D values kept."""
        h1, h2, h3, h4 = self.horizontal_coordinates()
        if self.source.is_three_dimensional():
            return (h1, h2, self.source.min_3), (h3, h4, self.source.max_3)
        return (h1, h2), (h3, h4)

    def kvp_value(self) -> str:
        """KVP ``bbox`` parameter value for this extent."""
        lower, upper = self.corners()
        values = [_number_text(v) for v in lower + upper]
        if self.srs_name:
            return ",".join(values + [self.srs_name])
        return ",".join(values)

    def gml_corners(self) -> Tuple[str, str]:
        """Space-separated lowerCorner / upperCorner text for XML filters."""
        lower, upper = self.corners()
        return (
            " ".join(_number_text(v) for v in lower),
            " ".join(_number_text(v) for v in upper),
        )


def _srs_name_for(crs: Optional[Crs], version: str) -> str:
    """Identifier placed on the wire for ``crs`` in ``version``."""
    if crs is None:
        return ""
    if version == "1.0.0":
        return crs.getcode() or crs.id
    if version == "2.0.0":
        if is_crs84(crs):
            return CRS84_URI
        return crs.getcodeuri1()
    # 1.1.0 uses the OGC URN style for axis-aware CRS identifiers.
    if is_crs84(crs):
        return CRS84_URN
    return crs.getcodeurn()


def resolve_extent(
    extent: SpatialExtent, version: str, default_crs: Optional[Crs] = None
) -> RequestExtent:
    """Resolve one extent into the ordering/identifier ``version`` requires.

    ``default_crs`` is the feature type's default CRS from capabilities, used
    when the caller did not attach one.  The rule set:

    * WFS 1.0.0 has no axis-order rules: coordinates pass through in the
      caller's order.
    * WFS 1.1.0/2.0.0 follow the CRS definition: EPSG:4326 (and its URN /
      URI spellings) are latitude,longitude, while OGC CRS84 / CRS:84 stay
      longitude,latitude.
    """
    version = normalise_version(version)
    crs = extent.crs or default_crs
    transmitted_order = _transmitted_order(extent, crs, version)
    srs_name = _srs_name_for(crs, version)
    return RequestExtent(extent, version, transmitted_order, srs_name, crs)


def _transmitted_order(
    extent: SpatialExtent, crs: Optional[Crs], version: str
) -> str:
    if version == "1.0.0":
        return extent.axis_order
    if is_crs84(crs):
        return XY
    if crs is not None and crs.axisorder == YX:
        return YX
    return XY


# Antimeridian handling -------------------------------------------------------

_LON_MIN = -180.0
_LON_MAX = 180.0


def crosses_antimeridian(extent: SpatialExtent) -> bool:
    """True when the caller's longitude range wraps past ±180°.

    Only meaningful for longitude/latitude CRS; projected CRS extents are
    never split.
    """
    crs = extent.crs
    if crs is not None and not _is_geographic(crs):
        return False
    minlon, _, maxlon, _ = extent.lonlat_bounds
    return minlon > maxlon


def _is_geographic(crs: Crs) -> bool:
    if is_crs84(crs):
        return True
    return isinstance(crs.code, int) and crs.code in (
        4326, 4258, 4269, 4979, 4267, 4268, 4290, 4283, 4284,
    )


def split_antimeridian(extent: SpatialExtent) -> Tuple[SpatialExtent, ...]:
    """Split an antimeridian-crossing extent into west/east parts.

    The split never widens the requested area: the union of the parts covers
    exactly the caller's range.  Non-crossing extents come back unchanged.
    """
    if not crosses_antimeridian(extent):
        return (extent,)
    minlon, minlat, maxlon, maxlat = extent.lonlat_bounds
    parts = []
    for lon1, lon2 in ((minlon, _LON_MAX), (_LON_MIN, maxlon)):
        parts.append(_rebuild(extent, lon1, minlat, lon2, maxlat))
    return tuple(parts)


def _rebuild(
    extent: SpatialExtent,
    minlon: float, minlat: float, maxlon: float, maxlat: float,
) -> SpatialExtent:
    if extent.axis_order == XY:
        min_1, min_2, max_1, max_2 = minlon, minlat, maxlon, maxlat
    else:
        min_1, min_2, max_1, max_2 = minlat, minlon, maxlat, maxlon
    return SpatialExtent(
        min_1, min_2, max_1, max_2, extent.axis_order, extent.crs,
        extent.min_3, extent.max_3,
    )


def adapt_extent(
    extent: SpatialExtent,
    version: str,
    default_crs: Optional[Crs] = None,
    split_antimeridian_crossing: bool = True,
) -> Tuple[RequestExtent, ...]:
    """Resolve ``extent`` for ``version``, splitting at the antimeridian.

    Returns one or two :class:`RequestExtent` objects.  Splitting is the
    caller's opt-out via ``split_antimeridian_crossing=False``; without it a
    crossing extent is never inflated into a near-global request.
    """
    effective_crs = extent.crs or default_crs
    can_split = effective_crs is None or _is_geographic(effective_crs)
    parts = (
        split_antimeridian(extent)
        if split_antimeridian_crossing and can_split
        else (extent,)
    )
    return tuple(resolve_extent(part, version, default_crs) for part in parts)


# Response handling -----------------------------------------------------------

@dataclass
class SpatialFeatureResponse:
    """Result of one or more spatial GetFeature requests.

    ``content`` is the merged, deduplicated GML payload as a file-like object;
    ``requests`` pairs every :class:`RequestExtent` with the request that was
    sent.  ``crs`` is the CRS identifier actually written to the wire, and
    ``request_bboxes`` are the coordinate sequences in transmitted order.
    """

    content: object
    request_extents: List[RequestExtent]
    version: str

    @property
    def crs(self) -> str:
        """On-wire CRS identifier, empty string when none was declared."""
        return self.request_extents[0].srs_name

    @property
    def request_bboxes(self) -> List[Tuple[float, ...]]:
        bboxes = []
        for request_extent in self.request_extents:
            lower, upper = request_extent.gml_corners()
            bboxes.append(tuple(
                float(value) for value in lower.split(" ") + upper.split(" ")))
        return bboxes

    def read(self):
        """File-like delegation so callers can treat this like getfeature()."""
        return self.content.read()


def extract_feature_ids(content) -> List[str]:
    """Return sorted, deduplicated GML feature identifiers from GML bytes."""
    if hasattr(content, "read"):
        data = content.read()
    else:
        data = content
    if isinstance(data, str):
        data = data.encode("utf-8")
    tree = etree.fromstring(data)
    identifiers = set()
    for element in tree.iter():
        tag = etree.QName(element).localname
        if tag in _MEMBER_TAGS:
            for child in element:
                feature_id = child.get(
                    "{%s}id" % _GML32_NS) or child.get("{%s}id" % _GML_NS)
                if feature_id:
                    identifiers.add(feature_id)
    return sorted(identifiers)


def merge_gml_feature_collections(payloads: Sequence[bytes]) -> BytesIO:
    """Merge per-request GML collections, dropping duplicate feature ids.

    The first collection supplies the document frame; members of later
    collections are appended once each.  A single payload is returned as-is.
    """
    if len(payloads) == 1:
        return BytesIO(payloads[0])

    base_tree = etree.fromstring(payloads[0])
    seen = set(extract_feature_ids(payloads[0]))
    for payload in payloads[1:]:
        tree = etree.fromstring(payload)
        members = [
            member for member in tree.iter()
            if etree.QName(member).localname in _MEMBER_TAGS
        ]
        for member in members:
            for child in list(member):
                feature_id = child.get(
                    "{%s}id" % _GML32_NS) or child.get("{%s}id" % _GML_NS)
                if feature_id is not None and feature_id in seen:
                    continue
                if feature_id is not None:
                    seen.add(feature_id)
                base_tree.append(member)
                break
    return BytesIO(etree.tostring(base_tree))
