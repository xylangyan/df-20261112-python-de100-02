# coding: utf-8
"""
    sickle.harvest
    ~~~~~~~~~~~~~~

    Two-phase harvesting: chain ``ListIdentifiers`` and ``GetRecord`` into a
    single collection pipeline and treat data changes between the two
    requests as first-class results.

    :copyright: Copyright 2015 Mathias Loesch
"""

#: The header seen at GetRecord time matches the listed header.
STATUS_UNCHANGED = 'unchanged'
#: The record was updated between ListIdentifiers and GetRecord.
STATUS_UPDATED = 'updated'
#: The record was deleted between ListIdentifiers and GetRecord.
STATUS_DELETED = 'deleted'
#: The record could not be retrieved (OAI error or request failure).
STATUS_FAILED = 'failed'

#: Accept the record as returned by GetRecord.
ACTION_ACCEPT = 'accept'
#: Keep the listed header as the reference and defer the record.
ACTION_CONFLICT = 'conflict'
#: Skip the item (deleted or failed).
ACTION_SKIP = 'skip'

#: Statuses for which the caller may choose how to proceed.
DECIDABLE_STATUSES = (STATUS_UPDATED, STATUS_DELETED)


class HarvestResult(object):
    """The outcome of harvesting a single identifier.

    Keeps the header as listed by ``ListIdentifiers`` next to the header
    actually returned by ``GetRecord`` so that downstream ingestion code can
    reconcile both versions.

    :param listed_header: The header as returned by ``ListIdentifiers``.
    :type listed_header: :class:`sickle.models.Header`
    :param status: One of ``unchanged``, ``updated``, ``deleted`` or
                   ``failed``.
    :param action: How the caller decided to handle the item (``accept``,
                   ``conflict`` or ``skip``).
    :param record: The record returned by ``GetRecord`` (if any).
    :param actual_header: The header as returned by ``GetRecord`` (if any).
    :param error: The exception raised while fetching the record (if any).
    """

    def __init__(self, listed_header, status, action,
                 record=None, actual_header=None, error=None):
        self.listed_header = listed_header
        self.status = status
        self.action = action
        self.record = record
        self.actual_header = actual_header
        self.error = error

    @property
    def identifier(self):
        """The identifier of the harvested item."""
        return self.listed_header.identifier

    @property
    def ok(self):
        """Whether the item was accepted with a record attached."""
        return self.action == ACTION_ACCEPT and self.record is not None

    def __repr__(self):
        return '<HarvestResult %s [%s/%s]>' % (
            self.identifier, self.status, self.action)


class HeaderMismatchError(Exception):
    """Raised when the GetRecord header does not match the listed header.

    Carries both header versions so that the mismatch can be recorded
    instead of silently dropping the item.
    """

    def __init__(self, message, listed_header, actual_header=None):
        super(HeaderMismatchError, self).__init__(message)
        self.listed_header = listed_header
        self.actual_header = actual_header


class Harvester(object):
    """Chain ``ListIdentifiers`` and ``GetRecord`` into a harvest pipeline.

    Each listed header is verified against the header returned by
    ``GetRecord``. Changes that happened between the two requests (updated
    datestamp, deletion, temporary failure) are reported as distinguishable
    statuses instead of being silently merged.

    :param sickle: The Sickle client used for issuing requests.
    :type sickle: :class:`sickle.app.Sickle`
    :param on_updated: How to handle records whose datestamp changed between
                       listing and fetching: ``accept`` (default) keeps the
                       newer version, ``conflict`` defers it for later
                       reconciliation.
    :param on_deleted: How to handle records deleted between listing and
                       fetching: ``skip`` (default) drops them, ``accept``
                       keeps the deletion marker, ``conflict`` defers them.
    """

    def __init__(self, sickle, on_updated=ACTION_ACCEPT,
                 on_deleted=ACTION_SKIP):
        self.sickle = sickle
        self.on_updated = on_updated
        self.on_deleted = on_deleted

    def harvest(self, ignore_deleted=False, **kwargs):
        """Harvest records by listing identifiers first.

        :param ignore_deleted: If set to :obj:`True`, headers flagged as
                               deleted in the ``ListIdentifiers`` response
                               are skipped without issuing ``GetRecord``
                               requests for them.
        :param kwargs: OAI arguments passed to both requests (e.g.
                       ``metadataPrefix``, ``set``, ``from``, ``until``).
        :rtype: iterator of :class:`HarvestResult`
        """
        headers = self.sickle.ListIdentifiers(
            ignore_deleted=ignore_deleted, **kwargs)
        for listed_header in headers:
            yield self.harvest_header(listed_header, **kwargs)

    def harvest_header(self, listed_header, **kwargs):
        """Fetch and verify a single listed header.

        A failure to fetch one item yields a ``failed`` result and does not
        affect the remaining items.
        """
        try:
            record = self.sickle.GetRecord(
                identifier=listed_header.identifier, **kwargs)
        except Exception as error:
            return HarvestResult(
                listed_header, STATUS_FAILED, ACTION_SKIP, error=error)
        return self.reconcile(listed_header, record)

    def reconcile(self, listed_header, record):
        """Compare the listed header with the fetched record's header."""
        actual_header = record.header
        if actual_header.identifier != listed_header.identifier:
            return HarvestResult(
                listed_header, STATUS_FAILED, ACTION_SKIP,
                actual_header=actual_header,
                error=HeaderMismatchError(
                    'GetRecord returned identifier %r, expected %r' % (
                        actual_header.identifier, listed_header.identifier),
                    listed_header, actual_header))
        if actual_header.deleted:
            return self._decide(
                listed_header, STATUS_DELETED, self.on_deleted,
                record=record, actual_header=actual_header)
        if actual_header.datestamp != listed_header.datestamp:
            return self._decide(
                listed_header, STATUS_UPDATED, self.on_updated,
                record=record, actual_header=actual_header)
        return HarvestResult(
            listed_header, STATUS_UNCHANGED, ACTION_ACCEPT,
            record=record, actual_header=actual_header)

    def _decide(self, listed_header, status, decision, **result_kwargs):
        action = decision if decision in (ACTION_ACCEPT, ACTION_CONFLICT) \
            else ACTION_SKIP
        if action == ACTION_SKIP:
            result_kwargs['record'] = None
        return HarvestResult(
            listed_header, status, action, **result_kwargs)


def harvest(sickle, on_updated=ACTION_ACCEPT, on_deleted=ACTION_SKIP,
            ignore_deleted=False, **kwargs):
    """Convenience wrapper around :class:`Harvester`.

    :rtype: iterator of :class:`HarvestResult`
    """
    return Harvester(
        sickle, on_updated=on_updated, on_deleted=on_deleted
    ).harvest(ignore_deleted=ignore_deleted, **kwargs)
