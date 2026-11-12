# coding: utf-8
"""
    sickle.consumer
    ~~~~~~~~~~~~~~~

    Resumable, per-record consumption on top of a :class:`~sickle.app.Sickle`
    harvest.

    :class:`RecordConsumer` downloads one OAI page at a time, hands the
    records to the caller and only moves forward once every record of the
    current page has been explicitly acknowledged. Progress is persisted in a
    :class:`Checkpoint`, which is bound to the endpoint, the request
    parameters and the metadata format and distinguishes three separate
    facts:

    * the page has been **downloaded** (its fingerprints are stored),
    * which records of that page have already been **consumed/acknowledged**,
    * whether the whole page has been **confirmed** and the next resumption
      token may be used.

    If the process exits before a page is fully acknowledged, the same
    records are delivered again on restart. The next-page resumption token is
    never treated as progress until the current page is confirmed. If the
    content of an already downloaded page changes between runs, a
    :class:`~sickle.oaiexceptions.CheckpointConflict` is raised instead of
    guessing records by their position in the page.

    :copyright: Copyright 2015 Mathias Loesch
"""

import hashlib
import json
import os
import tempfile

from lxml import etree

from . import oaiexceptions
from ._compat import string_types
from .iterator import VERBS_ELEMENTS
from .oaiexceptions import (CheckpointConflict, CheckpointMismatch,
                            IncompletePage, UnknownRecord)
from .response import XMLParser

#: Version written into every checkpoint; bump when the on-disk layout
#: changes in an incompatible way.
CHECKPOINT_VERSION = 1

# Page-level lifecycle states stored in the checkpoint.
STATE_PAGE_DOWNLOADED = 'page_downloaded'
STATE_PAGE_CONFIRMED = 'page_confirmed'
STATE_COMPLETED = 'completed'


def _canonical_bytes(element):
    """Return a context-independent canonical serialization of an element.

    The element is first serialized and re-parsed so that namespace
    declarations inherited from an ancestor element are materialized on the
    element itself. Canonical XML (C14N) then produces identical bytes for
    identical markup regardless of attribute order, whitespace or namespace
    prefix placement. If C14N is unavailable for the element, the plain
    serialization of the re-parsed element is used instead.
    """
    standalone = etree.XML(etree.tostring(element), parser=XMLParser)
    try:
        return etree.tostring(standalone, method='c14n')
    except (etree.C14NError, ValueError):
        return etree.tostring(standalone)


def fingerprint(element):
    """Return a stable sha256 digest for an XML element's content."""
    return hashlib.sha256(_canonical_bytes(element)).hexdigest()


class Checkpoint(object):
    """Serializable progress marker for one harvest.

    A checkpoint is bound to the exact harvest it belongs to: the OAI
    endpoint, the verb, all request parameters (including the metadata
    format) and the OAI element that is being iterated. It can therefore be
    rejected on restart instead of silently continuing a different harvest.

    Three pieces of information are kept apart:

    * :attr:`page_fingerprints` prove that a page has been **downloaded** and
      still contains the same records;
    * :attr:`acked_fingerprints` record how far the caller has **consumed**
      the current page;
    * :attr:`state` says whether the whole page has been **confirmed** and
      the next resumption token may be used.

    Checkpoint objects are plain data holders. Use
    :class:`CheckpointStore` to persist them.
    """

    def __init__(self, version=CHECKPOINT_VERSION, endpoint=None, verb=None,
                 params=None, element=None, state=STATE_PAGE_DOWNLOADED,
                 request_token=None, next_token=None,
                 page_fingerprints=None, acked_fingerprints=None):
        self.version = version
        self.endpoint = endpoint
        self.verb = verb
        self.params = dict(params or {})
        self.element = element
        self.state = state
        #: Resumption token used to fetch the current page (``None`` for the
        #: first request).
        self.request_token = request_token
        #: Token that will fetch the next page; never used before the current
        #: page has been confirmed.
        self.next_token = next_token
        self.page_fingerprints = list(page_fingerprints or [])
        self.acked_fingerprints = list(acked_fingerprints or [])

    @property
    def is_completed(self):
        return self.state == STATE_COMPLETED

    @property
    def is_page_confirmed(self):
        return self.state == STATE_PAGE_CONFIRMED

    def to_dict(self):
        return {
            'version': self.version,
            'endpoint': self.endpoint,
            'verb': self.verb,
            'params': self.params,
            'element': self.element,
            'state': self.state,
            'request_token': self.request_token,
            'next_token': self.next_token,
            'page_fingerprints': self.page_fingerprints,
            'acked_fingerprints': self.acked_fingerprints,
        }

    @classmethod
    def from_dict(cls, data):
        if data.get('version') != CHECKPOINT_VERSION:
            raise CheckpointMismatch(
                "Checkpoint version %r is not supported by this version of "
                "sickle (expected %r)."
                % (data.get('version'), CHECKPOINT_VERSION))
        return cls(
            version=data['version'],
            endpoint=data['endpoint'],
            verb=data['verb'],
            params=data.get('params', {}),
            element=data['element'],
            state=data['state'],
            request_token=data.get('request_token'),
            next_token=data.get('next_token'),
            page_fingerprints=data.get('page_fingerprints', []),
            acked_fingerprints=data.get('acked_fingerprints', []),
        )


class CheckpointStore(object):
    """Persistent storage for one :class:`Checkpoint`.

    The default implementation keeps the checkpoint in a JSON file and writes
    atomically (a temporary file in the same directory, then
    :func:`os.replace`) so a crash while saving can never leave a truncated
    checkpoint behind. Subclass or replace this class to store checkpoints in
    a database or any other durable system.
    """

    def __init__(self, path):
        self.path = path

    def load(self):
        """Return the stored checkpoint, or ``None`` if none exists yet."""
        if not os.path.exists(self.path):
            return None
        with open(self.path, 'r') as fp:
            return Checkpoint.from_dict(json.load(fp))

    def save(self, checkpoint):
        directory = os.path.dirname(os.path.abspath(self.path))
        if not os.path.isdir(directory):
            os.makedirs(directory)
        fd, tmp_path = tempfile.mkstemp(dir=directory, suffix='.tmp')
        try:
            with os.fdopen(fd, 'w') as fp:
                json.dump(checkpoint.to_dict(), fp, indent=2, sort_keys=True)
                fp.write('\n')
            os.replace(tmp_path, self.path)
        except Exception:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise

    def clear(self):
        if os.path.exists(self.path):
            os.unlink(self.path)


class RecordConsumer(object):
    """Resumable, per-record consumption of a ListRecords harvest.

    Typical usage::

        consumer = RecordConsumer(
            sickle, CheckpointStore('harvest.json'),
            metadataPrefix='oai_dc')
        while not consumer.finished:
            for record in consumer.pending_records():
                persist(record)          # write to the database
                consumer.ack(record)     # confirm success
            consumer.confirm_page()      # mark the page as fully consumed
            if not consumer.finished:
                consumer.next_page()     # fetch the next page

    The consumer downloads one page at a time. Records are identified by a
    digest of their canonical XML, never by their position in the page, so
    out-of-order and duplicate acknowledgements are both safe. If the process
    dies before :meth:`confirm_page`, the unacknowledged records are
    redelivered on the next run; a page whose content has changed raises
    :class:`~sickle.oaiexceptions.CheckpointConflict`.

    :param sickle: The :class:`~sickle.app.Sickle` client used for requests.
    :param store: A :class:`CheckpointStore` (or compatible object exposing
                  ``load``/``save``).
    :param ignore_deleted: Skip records flagged as deleted. If used, it must
                           be used consistently across restarts.
    :param kwargs: OAI arguments for the verb (e.g. ``metadataPrefix``).
    """

    verb = 'ListRecords'

    def __init__(self, sickle, store, ignore_deleted=False, **kwargs):
        self.sickle = sickle
        self.store = store
        self.ignore_deleted = ignore_deleted
        self.params = dict(kwargs)
        self.params['verb'] = self.verb
        self.element = VERBS_ELEMENTS[self.verb]
        self.namespace = sickle.oai_namespace
        self.mapper = sickle.class_mapping[self.verb]
        self._page = None
        self._checkpoint = self._resume_or_start()

    # -- scope handling ---------------------------------------------------

    def _expected_scope(self):
        return {
            'endpoint': self.sickle.endpoint,
            'verb': self.verb,
            'params': self.params,
            'element': self.element,
        }

    def _assert_scope(self, checkpoint):
        expected = self._expected_scope()
        actual = {
            'endpoint': checkpoint.endpoint,
            'verb': checkpoint.verb,
            'params': checkpoint.params,
            'element': checkpoint.element,
        }
        differences = [
            key for key in expected
            if actual.get(key) != expected[key]
        ]
        if differences:
            raise CheckpointMismatch(
                "The checkpoint does not belong to this harvest. Conflicting "
                "scope fields: %s. Checkpoint=%r, requested=%r"
                % (', '.join(differences), actual, expected))

    # -- page lifecycle ----------------------------------------------------

    def _resume_or_start(self):
        checkpoint = self.store.load()
        if checkpoint is None:
            checkpoint = Checkpoint(**self._expected_scope())
            self._download_page(checkpoint, request_token=None)
        else:
            self._assert_scope(checkpoint)
            if checkpoint.is_completed:
                return checkpoint
            if checkpoint.is_page_confirmed:
                # The previous page was fully acknowledged; its next token
                # is safe to use now.
                self._download_page(checkpoint,
                                    request_token=checkpoint.next_token)
            else:
                # The page was downloaded but not fully acknowledged:
                # re-download it and compare content before redelivering.
                self._redownload_page(checkpoint)
        return checkpoint

    def _request_params(self, request_token):
        if request_token:
            return {'verb': self.verb, 'resumptionToken': request_token}
        return dict(self.params)

    def _download_page(self, checkpoint, request_token):
        """Fetch a page and store its fingerprints in the checkpoint."""
        response = self.sickle.harvest(**self._request_params(request_token))
        self._page = _Page(self, response, request_token)
        checkpoint.request_token = request_token
        checkpoint.next_token = self._page.next_token
        checkpoint.page_fingerprints = self._page.fingerprints
        checkpoint.acked_fingerprints = []
        checkpoint.state = STATE_PAGE_DOWNLOADED
        self.store.save(checkpoint)

    def _redownload_page(self, checkpoint):
        """Re-fetch the page of an interrupted run and verify its content."""
        response = self.sickle.harvest(
            **self._request_params(checkpoint.request_token))
        page = _Page(self, response, checkpoint.request_token)
        if page.fingerprints != checkpoint.page_fingerprints:
            raise CheckpointConflict(
                "The page requested with resumptionToken=%r changed since it "
                "was downloaded. %d record(s) had been acknowledged before "
                "the interruption and %d record(s) were still pending; "
                "refusing to match records by position. Resolve the conflict "
                "manually (e.g. start a fresh harvest or reconcile the "
                "already delivered records) and delete the checkpoint."
                % (checkpoint.request_token,
                   len(checkpoint.acked_fingerprints),
                   len(checkpoint.page_fingerprints)
                   - len(checkpoint.acked_fingerprints)))
        self._page = page
        # Keep the stored acks; only the token of the *next* page may have
        # changed on the server.
        checkpoint.next_token = page.next_token
        self.store.save(checkpoint)

    def _ensure_page_loaded(self):
        if self._page is None:
            self._download_page(self._checkpoint,
                                request_token=self._checkpoint.request_token)
        return self._page

    # -- public state ------------------------------------------------------

    @property
    def checkpoint(self):
        """The current :class:`Checkpoint` (a copy owned by the caller)."""
        return Checkpoint.from_dict(self._checkpoint.to_dict())

    @property
    def finished(self):
        return self._checkpoint.is_completed

    @property
    def pending_fingerprints(self):
        if self._checkpoint.is_completed:
            return []
        return [f for f in self._checkpoint.page_fingerprints
                if f not in self._checkpoint.acked_fingerprints]

    @property
    def resume_token(self):
        """The token that a restart would use, or ``None`` when no further
        page is known yet.

        While the current page is unconfirmed this is still the token used
        to fetch *that* page, never the next-page token.
        """
        if self._checkpoint.is_completed:
            return None
        if self._checkpoint.is_page_confirmed:
            return self._checkpoint.next_token
        return self._checkpoint.request_token

    def pending_records(self):
        """Return the records of the current page that are still pending.

        Pending records keep their original page order. Calling this after a
        restart yields exactly the records that were not acknowledged before
        the interruption. Each returned record carries a ``fingerprint``
        attribute with the digest used for acknowledgements.
        """
        if self._checkpoint.is_completed:
            return []
        page = self._ensure_page_loaded()
        acked = set(self._checkpoint.acked_fingerprints)
        return [record for record in page.records
                if record.fingerprint not in acked]

    @staticmethod
    def _fingerprint_of(record):
        if isinstance(record, string_types):
            return record
        fingerprint = getattr(record, 'fingerprint', None)
        if fingerprint is None:
            raise UnknownRecord(
                "ack() expects a record returned by pending_records() or its "
                "fingerprint string; got %r" % type(record))
        return fingerprint

    def ack(self, record):
        """Acknowledge that one record has been successfully processed.

        Acknowledgements may arrive in any order. Duplicate acknowledgements
        are allowed and simply return :obj:`False`; the method never raises
        for an already acknowledged record. The checkpoint is persisted on
        every successful acknowledgement so a crash only redelivers records
        that were never confirmed.

        :param record: A record returned by :meth:`pending_records` or its
                       ``fingerprint`` string.
        :rtype: bool
        :returns: :obj:`True` if this was the first acknowledgement of the
                  record, :obj:`False` if it was already acknowledged.
        """
        if self._checkpoint.is_completed:
            raise UnknownRecord(
                "The harvest is finished; there are no records to "
                "acknowledge.")
        fingerprint = self._fingerprint_of(record)
        if fingerprint not in self._checkpoint.page_fingerprints:
            raise UnknownRecord(
                "Record %r is not part of the active page (requested with "
                "resumptionToken=%r)."
                % (fingerprint, self._checkpoint.request_token))
        if fingerprint in self._checkpoint.acked_fingerprints:
            return False
        self._checkpoint.acked_fingerprints.append(fingerprint)
        self.store.save(self._checkpoint)
        return True

    def confirm_page(self):
        """Confirm that the current page is fully acknowledged.

        Only after this call may the next-page resumption token be used.
        Calling it while records are still pending raises
        :class:`~sickle.oaiexceptions.IncompletePage` so that unacknowledged
        records can never be skipped silently. When the last page (no
        resumption token) is confirmed, the checkpoint is marked completed;
        otherwise the checkpoint is left in the ``page_confirmed`` state and
        :meth:`next_page` must be called to download the following page.
        """
        if self._checkpoint.is_completed or \
                self._checkpoint.is_page_confirmed:
            return
        pending = self.pending_fingerprints
        if pending:
            raise IncompletePage(
                "%d record(s) of the current page (resumptionToken=%r) have "
                "not been acknowledged; refusing to advance. Acknowledge "
                "them or restart the consumer to have them redelivered."
                % (len(pending), self._checkpoint.request_token))
        if self._checkpoint.next_token:
            self._checkpoint.state = STATE_PAGE_CONFIRMED
        else:
            self._checkpoint.state = STATE_COMPLETED
        self.store.save(self._checkpoint)

    def next_page(self):
        """Download the page following a confirmed page.

        :raises :class:`~sickle.oaiexceptions.IncompletePage`: if the current
            page has not been fully acknowledged and confirmed.
        :rtype: list
        :returns: The pending records of the newly downloaded page.
        """
        if self._checkpoint.is_completed:
            return []
        if not self._checkpoint.is_page_confirmed:
            raise IncompletePage(
                "The current page (resumptionToken=%r) has not been fully "
                "acknowledged and confirmed; call confirm_page() before "
                "requesting the next page."
                % self._checkpoint.request_token)
        self._download_page(self._checkpoint,
                            request_token=self._checkpoint.next_token)
        return self.pending_records()

    def reset(self):
        """Discard the checkpoint and start the harvest from scratch."""
        self.store.clear()
        self._page = None
        self._checkpoint = self._resume_or_start()

    def __iter__(self):
        return self

    def __next__(self):
        return self.next()

    def next(self):
        """Return the next pending record, downloading pages as needed.

        This is a convenience for code that prefers iterator semantics;
        records still have to be acknowledged with :meth:`ack` and pages are
        confirmed with :meth:`confirm_page` before the iterator moves on.
        """
        while True:
            if self._checkpoint.is_completed:
                raise StopIteration
            pending = self.pending_records()
            if pending:
                return pending[0]
            self.confirm_page()
            if not self._checkpoint.is_completed:
                self.next_page()


class _Page(object):
    """One downloaded OAI page with per-record fingerprints."""

    def __init__(self, consumer, response, request_token):
        self.request_token = request_token
        xml = response.xml
        namespace = consumer.namespace
        self._raise_on_error(xml, namespace)
        record_elements = xml.findall(
            './/' + namespace + consumer.element)
        self.fingerprints = []
        self.records = []
        for element in record_elements:
            mapped = consumer.mapper(element)
            if consumer.ignore_deleted and mapped.deleted:
                continue
            record_fingerprint = fingerprint(element)
            mapped.fingerprint = record_fingerprint
            self.fingerprints.append(record_fingerprint)
            self.records.append(mapped)
        self.next_token = self._extract_next_token(xml, namespace)

    @staticmethod
    def _raise_on_error(xml, namespace):
        error_element = xml.find('.//' + namespace + 'error')
        if error_element is None:
            return
        code = error_element.attrib.get('code', 'UNKNOWN')
        description = error_element.text or ''
        # An empty ListRecords result is a valid, completed harvest.
        if code.lower() == 'norecordsmatch':
            return
        exception_class = getattr(
            oaiexceptions, code[0].upper() + code[1:], oaiexceptions.OAIError)
        raise exception_class(description)

    @staticmethod
    def _extract_next_token(xml, namespace):
        token_element = xml.find('.//' + namespace + 'resumptionToken')
        if token_element is None:
            return None
        token = token_element.text
        # An empty resumptionToken body marks the end of the list.
        if token is None or not token.strip():
            return None
        return token
