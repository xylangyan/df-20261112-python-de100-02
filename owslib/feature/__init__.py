# =============================================================================
# OWSLib. Copyright (C) 2012 Jachym Cepicky
#
# Contact email: jachym.cepicky@gmail.com
#
# =============================================================================

import logging

import copy

from urllib.parse import urlencode
from owslib.crs import Crs
from owslib.util import Authentication, build_get_url, openURL
from owslib.feature.schema import get_schema
from owslib.feature.postrequest import PostRequest_1_1_0, PostRequest_2_0_0
from owslib.feature.spatial import (
    SpatialExtent,
    SpatialExtentError,
    adapt_extent,
    extract_feature_ids,
    merge_gml_feature_collections,
    normalise_version,
)

LOGGER = logging.getLogger(__name__)


class WebFeatureService_(object):
    """Base class for WebFeatureService implementations"""

    def __init__(self, auth=None):
        self.auth = auth or Authentication()

    def _default_crs(self, typename):
        """Default CRS declared in capabilities for ``typename``."""
        if isinstance(typename, (list, tuple)):
            typename = typename[0] if typename else None
        if typename is None or typename not in self.contents:
            return None
        options = self.contents[typename].crsOptions
        return options[0] if options else None

    def make_spatial_extent(self, bbox, axis_order=None, crs=None):
        """Build a :class:`SpatialExtent` from a legacy bbox sequence.

        Five-element bboxes keep the historical east,north convention; pass
        ``axis_order`` explicitly to declare latitude,longitude input or to
        use 3D extents.
        """
        return SpatialExtent.from_bbox(bbox, axis_order=axis_order, crs=crs)

    def resolve_spatial_extent(self, extent, split=True, typename=None):
        """Resolve a caller extent for this service version.

        Returns a tuple of :class:`RequestExtent`; antimeridian-crossing
        geographic extents are split into exactly two non-overlapping parts.
        Each part carries the CRS identifier and transmitted axis order that
        will actually be put on the wire.  ``typename`` selects the default
        CRS from capabilities when the extent does not name one.
        """
        default_crs = None
        if extent.crs is None and self.contents:
            if typename is None:
                typename = next(iter(self.contents))
            default_crs = self._default_crs(typename)
        return adapt_extent(
            extent, self.version, default_crs=default_crs,
            split_antimeridian_crossing=split,
        )

    def getBBOXKVP(self, bbox, typename, axis_order=None):
        """Formate bounding box for KVP request type (HTTP GET)

        @param bbox: (minx,miny,maxx,maxy[,srs]) or 3D 6/7 element sequence
        @type bbox: List
        @param typename:  feature name
        @type typename: String
        @param axis_order: caller-declared order of the input coordinates,
            one of 'xy'/'lonlat' (default for 5-element bboxes) or
            'yx'/'latlon'
        @returns: String properly formated according to version and
            coordinate reference system
        """
        return self._bbox_request_extents(
            bbox, typename, axis_order, split=False)[0].kvp_value()

    def getBBOXPost(self, bbox, typename, axis_order=None):
        """Format bounding box for Post requests

        @param bbox: (minx,miny,maxx,maxy[,srs]) or 3D 6/7 element sequence
        @type bbox: List
        @param typename:  feature name
        @type typename: String
        @param axis_order: caller-declared order of the input coordinates
        @returns: List of corner values plus the on-wire srsName
        """
        request_extent = self._bbox_request_extents(
            bbox, typename, axis_order, split=False)[0]
        lower, upper = request_extent.gml_corners()
        formatted_bbox = lower.split(" ") + upper.split(" ")
        if request_extent.srs_name:
            formatted_bbox.append(request_extent.srs_name)
        return formatted_bbox

    def _bbox_request_extents(self, bbox, typename, axis_order, split=True):
        extent = SpatialExtent.from_bbox(bbox, axis_order=axis_order)
        default_crs = self._default_crs(typename) if typename else None
        return adapt_extent(
            extent, self.version, default_crs=default_crs,
            split_antimeridian_crossing=split,
        )

    def create_post_request(self):
        """Creates an xml POST request according to WFS version."""

        if self.version in ['1.1.0']:
            return PostRequest_1_1_0()

        if self.version in ['2.0', '2.0.0']:
            return PostRequest_2_0_0()

    def getSRS(self, srsname, typename):
        """Returns None or Crs object for given name

        @param typename:  feature name
        @type typename: String
        """
        if not isinstance(srsname, Crs):
            srs = Crs(srsname)
        else:
            srs = srsname

        try:
            index = self.contents[typename].crsOptions.index(srs)
            # Return the Crs string that was pulled directly from the
            # GetCaps document (the 'id' attribute in the Crs object).
            return self.contents[typename].crsOptions[index]
        except ValueError:
            options = ", ".join([crs.id for crs in self.contents[typename].crsOptions])
            LOGGER.warning(
                "Requested srsName %r is not declared as being "
                "allowed for requested typename %r. "
                "Options are: %r.",
                srs.getcode(),
                typename,
                options,
            )
            return None

    def getGETGetFeatureRequest(
        self,
        typename=None,
        filter=None,
        bbox=None,
        featureid=None,
        featureversion=None,
        propertyname=None,
        maxfeatures=None,
        srsname=None,
        storedQueryID=None,
        storedQueryParams=None,
        outputFormat=None,
        method="Get",
        startindex=None,
        sortby=None,
        spatial_extent=None,
        bbox_axis_order=None,
    ):
        """Formulate proper GetFeature request using KVP encoding
        ----------
        typename : list
            List of typenames (string)
        filter : string
            XML-encoded OGC filter expression.
        bbox : tuple
            (left, bottom, right, top) in the feature type's coordinates == (minx, miny, maxx, maxy)
        featureid : list
            List of unique feature ids (string)
        featureversion : string
            Default is most recent feature version.
        propertyname : list
            List of feature property names. '*' matches all.
        maxfeatures : int
            Maximum number of features to be returned.
        srsname: string
            EPSG code to request the data in
        method : string
            Qualified name of the HTTP DCP method to use.
        outputFormat: string (optional)
            Requested response format of the request.
        startindex: int (optional)
            Start position to return feature set (paging in combination with maxfeatures)
        sortby: list (optional)
            List of property names whose values should be used to order
            (upon presentation) the set of feature instances that
            satify the query.
        spatial_extent : SpatialExtent (optional)
            Explicit extent with a caller-declared axis order.  Cannot be
            combined with ``bbox``; antimeridian-crossing extents issue one
            KVP request per split part.
        bbox_axis_order : string (optional)
            Axis order of the ``bbox`` coordinates ('xy'/'lonlat' or
            'yx'/'latlon').  Only single-request bboxes are supported here;
            use ``spatial_extent`` for splitting.

        There are 3 different modes of use

        1) typename and bbox (simple spatial query)
        2) typename and filter (==query) (more expressive)
        3) featureid (direct access to known features)
        """
        if spatial_extent is not None and bbox is not None:
            raise ValueError(
                "Pass either bbox or spatial_extent, not both")
        if bbox_axis_order is not None and spatial_extent is not None:
            raise ValueError(
                "bbox_axis_order belongs to bbox, not spatial_extent")
        storedQueryParams = storedQueryParams or {}

        base_url = next(
            (
                m.get("url")
                for m in self.getOperationByName("GetFeature").methods
                if m.get("type").lower() == method.lower()
            )
        )

        request = {"service": "WFS", "version": self.version, "request": "GetFeature"}

        # check featureid
        if featureid:
            request["featureid"] = ",".join(featureid)
        elif spatial_extent is not None:
            pairs = self.getSpatialGETRequests(
                spatial_extent, typename, propertyname=propertyname,
                sortby=sortby, featureversion=featureversion,
                maxfeatures=maxfeatures, srsname=srsname,
                startindex=startindex, storedQueryID=storedQueryID,
                storedQueryParams=storedQueryParams,
                outputFormat=outputFormat)
            if len(pairs) > 1:
                raise SpatialExtentError(
                    "spatial_extent crosses the antimeridian and needs more "
                    "than one GET request; use getSpatialGETRequests()")
            return pairs[0][0]
        elif bbox:
            request["bbox"] = self.getBBOXKVP(
                bbox, typename, axis_order=bbox_axis_order)
        elif filter:
            request["query"] = str(filter)
        if typename:
            typename = (
                [typename] if isinstance(typename, str) else typename
            )  # noqa: E721
            if int(self.version.split(".")[0]) >= 2:
                request["typenames"] = ",".join(typename)
            else:
                request["typename"] = ",".join(typename)
        if propertyname:
            request["propertyname"] = ",".join(propertyname)
        if sortby:
            request["sortby"] = ",".join(sortby)
        if featureversion:
            request["featureversion"] = str(featureversion)
        if maxfeatures:
            if int(self.version.split(".")[0]) >= 2:
                request["count"] = str(maxfeatures)
            else:
                request["maxfeatures"] = str(maxfeatures)
        if srsname:
            request["srsname"] = str(srsname)

            # Check if desired SRS is supported by the service for each
            # typename. Warning will be thrown if that SRS is not allowed.
            for name in typename:
                _ = self.getSRS(srsname, name)
        if startindex:
            request["startindex"] = str(startindex)
        if storedQueryID:
            request["storedQuery_id"] = str(storedQueryID)
            for param in storedQueryParams:
                request[param] = storedQueryParams[param]
        if outputFormat is not None:
            request["outputFormat"] = outputFormat

        return build_get_url(base_url, request)

    def getSpatialGETRequests(
        self, spatial_extent, typename, propertyname=None, sortby=None,
        featureversion=None, maxfeatures=None, srsname=None, startindex=None,
        storedQueryID=None, storedQueryParams=None, outputFormat=None,
    ):
        """Build one KVP ``(url, RequestExtent)`` pair per request extent.

        Antimeridian-crossing extents yield two pairs; nothing is widened to
        cover the gap between the parts.
        """
        base_request = {
            "service": "WFS", "version": self.version,
            "request": "GetFeature",
        }
        try:
            base_url = next(
                (
                    m.get("url")
                    for m in self.getOperationByName("GetFeature").methods
                    if m.get("type").lower() == "get"
                )
            )
        except StopIteration:
            base_url = self.url
        storedQueryParams = storedQueryParams or {}
        return self._spatial_extent_kvp_urls(
            base_url, base_request, spatial_extent, typename, propertyname,
            sortby, featureversion, maxfeatures, srsname, startindex,
            storedQueryID, storedQueryParams, outputFormat,
        )

    def _spatial_extent_kvp_urls(
        self, base_url, base_request, spatial_extent, typename, propertyname,
        sortby, featureversion, maxfeatures, srsname, startindex,
        storedQueryID, storedQueryParams, outputFormat,
    ):
        """Build one KVP URL per resolved (possibly split) request extent."""
        typename = (
            [typename] if isinstance(typename, str) else typename
        )  # noqa: E721
        request_extents = self.resolve_spatial_extent(
            spatial_extent, split=True, typename=typename)
        urls = []
        for request_extent in request_extents:
            request = copy.deepcopy(base_request)
            request["bbox"] = request_extent.kvp_value()
            if typename:
                if int(self.version.split(".")[0]) >= 2:
                    request["typenames"] = ",".join(typename)
                else:
                    request["typename"] = ",".join(typename)
            if propertyname:
                request["propertyname"] = ",".join(propertyname)
            if sortby:
                request["sortby"] = ",".join(sortby)
            if featureversion:
                request["featureversion"] = str(featureversion)
            if maxfeatures:
                if int(self.version.split(".")[0]) >= 2:
                    request["count"] = str(maxfeatures)
                else:
                    request["maxfeatures"] = str(maxfeatures)
            if srsname:
                request["srsname"] = str(srsname)
                if typename:
                    for name in typename:
                        _ = self.getSRS(srsname, name)
            if startindex:
                request["startindex"] = str(startindex)
            if storedQueryID:
                request["storedQuery_id"] = str(storedQueryID)
                for param in storedQueryParams:
                    request[param] = storedQueryParams[param]
            if outputFormat is not None:
                request["outputFormat"] = outputFormat
            urls.append((build_get_url(base_url, request), request_extent))
        return urls

    def getPOSTGetFeatureRequest(
        self,
        typename=None,
        filter=None,
        bbox=None,
        featureid=None,
        featureversion=None,
        propertyname=None,
        maxfeatures=None,
        storedQueryID=None,
        storedQueryParams=None,
        outputFormat=None,
        method="Post",
        startindex=None,
        sortby=None,
        spatial_extent=None,
    ):
        """Formulate proper GetFeature request using KVP encoding
        ----------
        typename : list
            List of typenames (string)
        filter : string
            XML-encoded OGC filter expression.
        bbox : tuple
            (left, bottom, right, top) in the feature type's coordinates == (minx, miny, maxx, maxy)
        featureid : list
            List of unique feature ids (string)
        featureversion : string
            Default is most recent feature version.
        propertyname : list
            List of feature property names. Leave blank (None) to get all properties.
        maxfeatures : int
            Maximum number of features to be returned.
        method : string
            Qualified name of the HTTP DCP method to use.
        outputFormat: string (optional)
            Requested response format of the request.
        startindex: int (optional)
            Start position to return feature set (paging in combination with maxfeatures)
        storedQueryID : string
            A name identifying a prepared set available in WFS-service.
            WFS version 2.0.0 and above only.
        storedQueryParams : dict
            Variable amount of extra information sent to server related to
            storedQueryID to further define the requested data.
            WFS version 2.0.0 and above only.
            {'parameter_name': parameter_value}
        sortby: list (optional)
            List of property names whose values should be used to order
            (upon presentation) the set of feature instances that
            satify the query.
        spatial_extent : SpatialExtent (optional)
            Explicit extent with a caller-declared axis order.  Cannot be
            combined with ``bbox``; antimeridian-crossing extents produce
            one POST body per split part.

        There are 5 different modes of use

        1) typename and bbox (simple spatial query)
        2) typename and filter (==query) (more expressive)
        3) featureid (direct access to known features)
        4) storedQueryID and optional storedQueryParams
        5) filter only via Post method
        """

        try:
            base_url = next(
                (
                    m.get("url")
                    for m in self.getOperationByName("GetFeature").methods
                    if m.get("type").lower() == method.lower()
                )
            )
        except StopIteration:
            base_url = self.url

        if not typename and filter:
            return base_url, filter

        request = self.create_post_request()

        if storedQueryID:
            if self.version in ["1.0.0", "1.1.0"]:
                LOGGER.warning("Stored queries are only supported in version 2.0.0 and above.")
                return None

            storedQueryParams = storedQueryParams or {}
            request.create_storedquery(storedQueryID, storedQueryParams)
            data = request.to_string()
            return base_url, data

        typename = (
            [typename] if isinstance(typename, str) else typename
        )  # noqa: E721
        typenames = ",".join(typename)

        request.create_query(typenames)

        if featureid:
            featureid = (
                [featureid] if isinstance(featureid, str) else featureid
            )
            request.set_featureid(featureid)
        elif spatial_extent is not None:
            post_requests = self.getPOSTSpatialRequests(
                spatial_extent, typename, propertyname=propertyname,
                maxfeatures=maxfeatures, outputFormat=outputFormat,
                startindex=startindex, sortby=sortby,
                featureversion=featureversion)
            if len(post_requests) > 1:
                raise SpatialExtentError(
                    "spatial_extent crosses the antimeridian and needs more "
                    "than one POST request; use getPOSTSpatialRequests()")
            return post_requests[0][0], post_requests[0][1]
        elif bbox:
            request.set_bbox(self.getBBOXPost(bbox, typename))
        elif filter is not None:
            request.set_filter(filter)

        if featureversion:
            request.set_featureversion(str(featureversion))
        if maxfeatures:
            request.set_maxfeatures(maxfeatures)
        if outputFormat:
            request.set_outputformat(outputFormat)
        if propertyname:
            propertyname = (
                [propertyname] if isinstance(propertyname, str) else propertyname
            )
            request.set_propertyname(propertyname)
        if sortby:
            sortby = (
                [sortby] if isinstance(sortby, str) else sortby
            )
            request.set_sortby(sortby)
        if startindex:
            request.set_startindex(startindex)

        data = request.to_string()
        return base_url, data

    def getPOSTSpatialRequests(
        self, spatial_extent, typename, propertyname=None, maxfeatures=None,
        outputFormat=None, startindex=None, sortby=None, featureversion=None,
    ):
        """Build one POST payload per resolved (possibly split) request.

        Returns a list of ``(url, payload, request_extent)`` tuples.  Each
        payload carries the same extent semantics as the matching KVP
        request; the :class:`RequestExtent` records the CRS identifier and
        axis order actually written into the envelope.
        """
        typename = (
            [typename] if isinstance(typename, str) else typename
        )  # noqa: E721
        request_extents = self.resolve_spatial_extent(
            spatial_extent, split=True, typename=typename)
        typenames = ",".join(typename)
        try:
            base_url = next(
                (
                    m.get("url")
                    for m in self.getOperationByName("GetFeature").methods
                    if m.get("type").lower() == "post"
                )
            )
        except StopIteration:
            base_url = self.url

        post_requests = []
        for request_extent in request_extents:
            request = self.create_post_request()
            request.create_query(typenames)
            lower, upper = request_extent.gml_corners()
            bbox_values = lower.split(" ") + upper.split(" ")
            if request_extent.srs_name:
                bbox_values.append(request_extent.srs_name)
            request.set_bbox(bbox_values)
            if featureversion:
                request.set_featureversion(str(featureversion))
            if maxfeatures:
                request.set_maxfeatures(maxfeatures)
            if outputFormat:
                request.set_outputformat(outputFormat)
            if propertyname:
                propertyname = (
                    [propertyname]
                    if isinstance(propertyname, str)
                    else propertyname
                )  # noqa: E721
                request.set_propertyname(propertyname)
            if sortby:
                sortby = (
                    [sortby] if isinstance(sortby, str) else sortby
                )  # noqa: E721
                request.set_sortby(sortby)
            if startindex:
                request.set_startindex(startindex)
            post_requests.append(
                (base_url, request.to_string(), request_extent))
        return post_requests

    def get_schema(self, typename):
        """
        Get layer schema compatible with :class:`fiona` schema object
        """
        return get_schema(self.url, typename, self.version, auth=self.auth)

    # Spatial multi-request execution -------------------------------------

    def _send_getfeature_request(self, url, data, method):
        """Perform one GetFeature request; caller handles the payload."""
        return openURL(
            url, data, method, timeout=self.timeout,
            headers=self.headers, auth=self.auth,
        )

    def _spatial_getfeature(self, requests, method="Get"):
        """Run one request per resolved extent and merge/deduplicate GML.

        ``requests`` are ``(url, data, RequestExtent)`` triples (``data`` is
        None for GET, the XML payload for POST).  Returns a
        :class:`SpatialFeatureResponse` whose ``content`` is a file-like
        object like a classic ``getfeature`` result.  The response records the
        CRS actually requested and every extent sent.
        """
        from owslib.feature.spatial import SpatialFeatureResponse

        payloads = []
        for url, data, request_extent in requests:
            response = self._send_getfeature_request(url, data, method)
            payloads.append(response.read())
        content = merge_gml_feature_collections(payloads)
        return SpatialFeatureResponse(
            content, [request[2] for request in requests], self.version)

    def _run_spatial_getfeature(
        self, typename, spatial_extent=None, bbox=None, bbox_axis_order=None,
        method="Get", propertyname=None, maxfeatures=None,
        outputFormat=None, startindex=None, sortby=None, featureversion=None,
        srsname=None, storedQueryID=None, storedQueryParams=None,
    ):
        """Shared spatial path used by every WFS version."""
        if spatial_extent is not None and bbox is not None:
            raise ValueError("Pass either bbox or spatial_extent, not both")
        if spatial_extent is None:
            spatial_extent = SpatialExtent.from_bbox(
                bbox, axis_order=bbox_axis_order)
        verb = str(method).split("}")[-1].lower()
        if verb == "post" and normalise_version(self.version) == "1.0.0":
            raise SpatialExtentError(
                "WFS 1.0.0 has no XML POST GetFeature builder; use GET")
        if verb == "get":
            pairs = self.getSpatialGETRequests(
                spatial_extent, typename, propertyname=propertyname,
                sortby=sortby, featureversion=featureversion,
                maxfeatures=maxfeatures, srsname=srsname,
                startindex=startindex, storedQueryID=storedQueryID,
                storedQueryParams=storedQueryParams, outputFormat=outputFormat)
            requests = [(url, None, extent) for url, extent in pairs]
        else:
            post_requests = self.getPOSTSpatialRequests(
                spatial_extent, typename, propertyname=propertyname,
                maxfeatures=maxfeatures, outputFormat=outputFormat,
                startindex=startindex, sortby=sortby,
                featureversion=featureversion)
            requests = [
                (url, payload, extent)
                for url, payload, extent in post_requests]
        return self._spatial_getfeature(requests, method=method)


    def get_spatial_feature_ids(self, typename=None, spatial_extent=None,
                                method="Get", **kwargs):
        """Return sorted feature ids touched by the resolved requests.

        Small, offline-checkable proof that split extents cover exactly the
        union of their parts: each returned id was delivered by at least one
        of the per-part requests.
        """
        response = self.getfeature(
            typename=typename, spatial_extent=spatial_extent, method=method,
            **kwargs)
        return extract_feature_ids(response.content)
