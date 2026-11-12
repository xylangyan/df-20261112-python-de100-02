# coding: utf-8
"""
    sickle.harvest
    ~~~~~~~~~~~~~~

    Interval-based harvesting.

    :copyright: Copyright 2015 Mathias Loesch
"""
import datetime

from .interval import (DateRange, GranularityError,
                       DAY_GRANULARITY, SECOND_GRANULARITY,
                       POLICY_STRICT, POLICY_WIDEN, slice_params)
from .iterator import IntervalItemIterator
from .oaiexceptions import NoRecordsMatch

STATUS_PENDING = 'pending'
STATUS_COMPLETED = 'completed'
STATUS_FAILED = 'failed'


def parse_datestamp(datestamp):
    """Parse an OAI datestamp into an aware UTC datetime.

    Supports both granularities defined by the OAI-PMH specification.
    Returns :obj:`None` if the datestamp is missing or cannot be parsed.
    """
    if not datestamp:
        return None
    try:
        return datetime.datetime.strptime(
            datestamp, '%Y-%m-%dT%H:%M:%SZ').replace(
            tzinfo=datetime.timezone.utc)
    except ValueError:
        try:
            return datetime.datetime.strptime(
                datestamp, '%Y-%m-%d').replace(
                tzinfo=datetime.timezone.utc)
        except ValueError:
            return None


class SliceResult(object):
    """The status of one time slice of an interval harvest.

    Slices are independent: a failed slice never prevents later slices
    from being harvested, and every slice keeps its own state.

    :ivar index: Zero-based position of the slice in the harvest.
    :ivar params: The OAI parameters actually sent to the endpoint.
    :ivar status: One of ``'pending'``, ``'completed'`` or ``'failed'``.
    :ivar records: The records returned for this slice (after header
                   filtering when the request range was widened).
    :ivar error: The exception that caused the slice to fail, if any.
    """

    def __init__(self, index, params):
        self.index = index
        self.params = params
        self.status = STATUS_PENDING
        self.records = []
        self.error = None

    def __repr__(self):
        return '<SliceResult #%d %s from=%s until=%s>' % (
            self.index, self.status,
            self.params.get('from'), self.params.get('until'))

    @property
    def completed(self):
        return self.status == STATUS_COMPLETED

    @property
    def failed(self):
        return self.status == STATUS_FAILED


class IntervalHarvester(object):
    """Harvest an OAI repository in explicit time slices.

    The harvester first reads the repository's ``Identify`` response to
    determine its date granularity, then splits the requested time
    range into legal ``from``/``until`` parameter pairs.  Each slice
    keeps an independent status in :attr:`slices`.

    OAI-PMH ``from``/``until`` arguments are inclusive, so adjacent
    slices share their boundary instant.  Records returned by more
    than one slice are de-duplicated by the ``(identifier, datestamp)``
    pair from their headers: the exact same record version therefore
    appears only once while successive versions of the same identifier
    are preserved.

    :param sickle: The Sickle client used for the requests.
    :type sickle: :class:`sickle.app.Sickle`
    :param date_range: The inclusive, timezone-aware time range.
    :type date_range: :class:`sickle.interval.DateRange`
    :param metadata_prefix: The OAI ``metadataPrefix`` argument.
    :type metadata_prefix: str
    :param max_span: Maximum time span covered by a single slice.
    :type max_span: :class:`datetime.timedelta`
    :param policy: ``'strict'`` to reject ranges that cannot be
                   expressed exactly at the repository's granularity,
                   or ``'widen'`` to widen day-granularity requests to
                   whole days and filter by header datestamp.
    :type policy: str
    :param ignore_deleted: Skip records flagged as deleted.
    :type ignore_deleted: bool
    :param set_spec: Optional OAI ``set`` argument.
    :type set_spec: str
    """

    def __init__(self, sickle, date_range, metadata_prefix, max_span,
                 policy=POLICY_STRICT, ignore_deleted=False,
                 set_spec=None):
        self.sickle = sickle
        self.date_range = date_range if isinstance(
            date_range, DateRange) else DateRange(
            date_range[0], date_range[1])
        self.metadata_prefix = metadata_prefix
        self.max_span = max_span
        self.policy = policy
        self.ignore_deleted = ignore_deleted
        self.set_spec = set_spec

        identify = sickle.Identify()
        #: The granularity reported by the repository.
        self.granularity = getattr(identify, 'granularity', None)
        if self.granularity not in (DAY_GRANULARITY,
                                    SECOND_GRANULARITY):
            raise GranularityError(
                "Unsupported granularity reported by the repository: "
                "%r! Must be %r or %r."
                % (self.granularity, DAY_GRANULARITY,
                   SECOND_GRANULARITY))

        self._pairs = slice_params(
            self.date_range, self.granularity,
            self.max_span, self.policy)
        #: One :class:`SliceResult` per planned slice, in request order.
        self.slices = [SliceResult(index, self._params(pair))
                       for index, pair in enumerate(self._pairs)]

    def _params(self, pair):
        params = {
            'verb': 'ListRecords',
            'metadataPrefix': self.metadata_prefix,
            'from': pair[0],
            'until': pair[1],
        }
        if self.set_spec is not None:
            params['set'] = self.set_spec
        return params

    def _in_range(self, record):
        datestamp = parse_datestamp(record.header.datestamp)
        if datestamp is None:
            return True
        return self.date_range.start <= datestamp <= self.date_range.end

    def harvest(self):
        """Harvest all slices and return the combined records.

        Each slice is requested independently.  A failure (including an
        empty slice reported as ``noRecordsMatch``) is recorded on the
        corresponding :class:`SliceResult` and does not stop the
        remaining slices.  Records are de-duplicated across slices by
        ``(identifier, datestamp)``.

        :rtype: list of :class:`sickle.models.Record`
        """
        iterators = []
        for slice_result in self.slices:
            try:
                iterator = self.sickle.ListRecords(
                    ignore_deleted=self.ignore_deleted,
                    **slice_result.params)
                records = list(iterator)
                if self.policy == POLICY_WIDEN:
                    records = [record for record in records
                               if self._in_range(record)]
                slice_result.records = records
                slice_result.status = STATUS_COMPLETED
                iterators.append(iter(records))
            except NoRecordsMatch:
                slice_result.records = []
                slice_result.status = STATUS_COMPLETED
            except Exception as error:
                slice_result.error = error
                slice_result.status = STATUS_FAILED
        return list(IntervalItemIterator(iterators))

    def __iter__(self):
        return iter(self.harvest())
