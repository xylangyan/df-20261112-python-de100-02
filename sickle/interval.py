# coding: utf-8
"""
    sickle.interval
    ~~~~~~~~~~~~~~~

    Time-range handling for interval-based harvesting.

    :copyright: Copyright 2015 Mathias Loesch
"""
import datetime

#: The two date granularities defined by the OAI-PMH specification.
DAY_GRANULARITY = 'YYYY-MM-DD'
SECOND_GRANULARITY = 'YYYY-MM-DDThh:mm:ssZ'

#: Policy: reject ranges that cannot be expressed exactly at the
#: repository's granularity.
POLICY_STRICT = 'strict'
#: Policy: widen the requested range to whole days and filter the
#: harvested items by their header datestamps.
POLICY_WIDEN = 'widen'

POLICIES = (POLICY_STRICT, POLICY_WIDEN)

_ONE_DAY = datetime.timedelta(days=1)
_ONE_SECOND = datetime.timedelta(seconds=1)


class GranularityError(ValueError):
    """Raised when a requested time range cannot be expressed exactly
    at the date granularity supported by the repository."""


class DateRange(object):
    """A time range requested by the caller.

    Both bounds are inclusive and must be timezone-aware.

    :param start: The start of the range (inclusive).
    :type start: :class:`datetime.datetime`
    :param end: The end of the range (inclusive).
    :type end: :class:`datetime.datetime`
    """

    def __init__(self, start, end):
        if start.tzinfo is None or start.tzinfo.utcoffset(start) is None:
            raise ValueError("start must be timezone-aware")
        if end.tzinfo is None or end.tzinfo.utcoffset(end) is None:
            raise ValueError("end must be timezone-aware")
        if end < start:
            raise ValueError("end must not be earlier than start")
        #: The start of the range, normalized to UTC.
        self.start = start.astimezone(datetime.timezone.utc)
        #: The end of the range, normalized to UTC.
        self.end = end.astimezone(datetime.timezone.utc)

    def __repr__(self):
        return '<DateRange %s - %s>' % (
            self.start.isoformat(), self.end.isoformat())


def _is_whole_day(moment):
    return (moment.hour, moment.minute, moment.second,
            moment.microsecond) == (0, 0, 0, 0)


def _format(moment, granularity):
    if granularity == DAY_GRANULARITY:
        return moment.strftime('%Y-%m-%d')
    return moment.strftime('%Y-%m-%dT%H:%M:%SZ')


def _slice_bounds(date_range, granularity, max_span, policy):
    """Compute the inclusive ``(from, until)`` datetime pairs for a range.

    The pairs cover the requested range exactly at the repository's
    granularity.  For day granularity, sub-day bounds are either
    rejected (``strict`` policy) or widened to whole days (``widen``
    policy).  Adjacent slices overlap on their shared boundary because
    OAI-PMH ``from``/``until`` arguments are inclusive.
    """
    if policy not in POLICIES:
        raise ValueError(
            "Invalid policy: %r! Must be one of %s." % (policy, POLICIES))
    if max_span < _ONE_DAY:
        raise ValueError("max_span must be at least one day")
    if max_span.microseconds:
        raise ValueError("max_span must not contain microseconds")
    if granularity == DAY_GRANULARITY and max_span.seconds:
        raise ValueError(
            "max_span must be a whole number of days for a "
            "day-granularity repository; got %s" % (max_span,))

    start, end = date_range.start, date_range.end
    if granularity == DAY_GRANULARITY:
        if policy == POLICY_STRICT and \
                not (_is_whole_day(start) and _is_whole_day(end)):
            raise GranularityError(
                "The repository only supports day granularity "
                "(%s); the requested range %s - %s cannot be "
                "expressed exactly. Use policy='widen' to harvest "
                "whole days and filter by header datestamp."
                % (granularity, start.isoformat(), end.isoformat()))
        start = start.replace(hour=0, minute=0, second=0, microsecond=0)
        # A day-granularity ``until`` covers the whole day, so the
        # widened end is the day containing the requested end.
        end = end.replace(hour=0, minute=0, second=0, microsecond=0)
        step = datetime.timedelta(days=max_span.days)
        boundary = _ONE_DAY
    elif granularity == SECOND_GRANULARITY:
        if start.microsecond or end.microsecond:
            raise GranularityError(
                "The repository supports second granularity (%s); the "
                "requested range %s - %s contains sub-second values."
                % (granularity, start.isoformat(), end.isoformat()))
        step = max_span
        boundary = _ONE_SECOND
    else:
        raise GranularityError(
            "Unsupported granularity reported by the repository: %r! "
            "Must be %r or %r."
            % (granularity, DAY_GRANULARITY, SECOND_GRANULARITY))

    slices = []
    cursor = start
    while cursor <= end:
        slice_end = min(cursor + step - boundary, end)
        slices.append((cursor, slice_end))
        cursor = slice_end + boundary
    return slices


def slice_params(date_range, granularity, max_span, policy=POLICY_STRICT):
    """Compute the OAI ``from``/``until`` parameter pairs for a range.

    :param date_range: The requested time range.
    :type date_range: :class:`DateRange`
    :param granularity: The granularity reported by the repository's
                        Identify response.
    :type granularity: str
    :param max_span: The maximum time span covered by a single slice.
    :type max_span: :class:`datetime.timedelta`
    :param policy: How to handle ranges that cannot be expressed
                   exactly at the repository's granularity
                   (``'strict'`` or ``'widen'``).
    :type policy: str
    :rtype: list of (str, str) tuples with formatted ``from``/``until``
            values.
    """
    return [(_format(start, granularity), _format(end, granularity))
            for start, end in
            _slice_bounds(date_range, granularity, max_span, policy)]
