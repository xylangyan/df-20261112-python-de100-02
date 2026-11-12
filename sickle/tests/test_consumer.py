# coding: utf-8
"""
    sickle.tests.test_consumer
    ~~~~~~~~~~~~~~~~~~~~~~~~~~

    Tests for the resumable per-record consumption interface.

    :copyright: Copyright 2015 Mathias Loesch
"""
import os
import shutil
import tempfile
import unittest

from lxml import etree

from sickle import (CheckpointConflict, CheckpointMismatch, CheckpointStore,
                    IncompletePage, RecordConsumer, Sickle, UnknownRecord)
from sickle.response import OAIResponse

OAI_NS = 'http://www.openarchives.org/OAI/2.0/'
OAI_DC_NS = 'http://www.openarchives.org/OAI/2.0/oai_dc/'
DC_NS = 'http://purl.org/dc/elements/1.1/'
XSI_NS = 'http://www.w3.org/2001/XMLSchema-instance'
NSMAP = {None: OAI_NS, 'xsi': XSI_NS, 'oai_dc': OAI_DC_NS, 'dc': DC_NS}
ENDPOINT = 'http://localhost/oai'


class StubHTTPResponse(object):
    def __init__(self, text):
        self.text = text
        self.content = text.encode('utf-8')


def make_record(number, deleted=False, title=None):
    identifier = 'oai:test:%04d' % number
    record = etree.Element('{%s}record' % OAI_NS, nsmap=NSMAP)
    header = etree.SubElement(record, '{%s}header' % OAI_NS)
    if deleted:
        header.set('status', 'deleted')
    etree.SubElement(header, '{%s}identifier' % OAI_NS).text = identifier
    etree.SubElement(header, '{%s}datestamp' % OAI_NS).text = \
        '2026-11-01T00:00:%02dZ' % number
    if not deleted:
        metadata = etree.SubElement(record, '{%s}metadata' % OAI_NS)
        dc = etree.SubElement(metadata, '{%s}dc' % OAI_DC_NS)
        etree.SubElement(dc, '{%s}title' % DC_NS).text = \
            title or ('Title %d' % number)
    return record


def make_page(records, next_token=None, cursor=0):
    root = etree.Element('{%s}OAI-PMH' % OAI_NS, nsmap=NSMAP)
    etree.SubElement(root, '{%s}responseDate' % OAI_NS).text = \
        '2026-11-01T09:00:00Z'
    request = etree.SubElement(root, '{%s}request' % OAI_NS,
                               metadataPrefix='oai_dc', verb='ListRecords')
    request.text = ENDPOINT
    list_records = etree.SubElement(root, '{%s}ListRecords' % OAI_NS)
    for record in records:
        list_records.append(record)
    if next_token is not None:
        token = etree.SubElement(
            list_records, '{%s}resumptionToken' % OAI_NS,
            completeListSize='9', cursor=str(cursor))
        token.text = next_token
    return etree.tostring(root, xml_declaration=True,
                          encoding='UTF-8', pretty_print=True).decode('utf-8')


def make_error(code, message):
    root = etree.Element('{%s}OAI-PMH' % OAI_NS, nsmap=NSMAP)
    error = etree.SubElement(root, '{%s}error' % OAI_NS, code=code)
    error.text = message
    return etree.tostring(root, encoding='unicode')


class FakeRepository(object):
    """In-process OAI repository keyed by resumption token."""

    def __init__(self, pages):
        self.pages = dict(pages)
        self.request_log = []

    def harvest(self, *args, **kwargs):
        token = kwargs.get('resumptionToken')
        self.request_log.append(token)
        return OAIResponse(StubHTTPResponse(self.pages[token]), kwargs)

    def replace(self, token, page):
        self.pages[token] = page


def default_pages():
    return {
        None: make_page([make_record(1), make_record(2, deleted=True),
                         make_record(3)], next_token='t2', cursor=0),
        't2': make_page([make_record(4), make_record(5), make_record(6)],
                        next_token='t3', cursor=3),
        't3': make_page([make_record(7), make_record(8), make_record(9)],
                        next_token='t4', cursor=6),
        't4': make_page([], next_token=None, cursor=9),
    }


class ConsumerTestCase(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.store_path = os.path.join(self.tmpdir, 'checkpoint.json')
        self.pages = default_pages()

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def make_consumer(self, pages=None):
        repository = FakeRepository(pages or self.pages)
        sickle = Sickle(ENDPOINT)
        sickle.harvest = repository.harvest
        consumer = RecordConsumer(
            sickle, CheckpointStore(self.store_path),
            metadataPrefix='oai_dc')
        consumer.repository = repository
        return consumer

    @staticmethod
    def identifiers(records):
        return [record.header.identifier for record in records]

    def test_checkpoint_is_bound_to_endpoint_params_and_format(self):
        consumer = self.make_consumer()
        checkpoint = CheckpointStore(self.store_path).load()
        self.assertEqual(checkpoint.endpoint, ENDPOINT)
        self.assertEqual(checkpoint.verb, 'ListRecords')
        self.assertEqual(checkpoint.params['metadataPrefix'], 'oai_dc')
        self.assertEqual(checkpoint.params['verb'], 'ListRecords')
        self.assertEqual(checkpoint.element, 'record')

    def test_full_happy_path_and_empty_last_page(self):
        consumer = self.make_consumer()
        seen = []
        while not consumer.finished:
            records = consumer.pending_records()
            for record in records:
                seen.append(record.header.identifier)
                self.assertTrue(consumer.ack(record))
            consumer.confirm_page()
            if not consumer.finished:
                consumer.next_page()
        self.assertTrue(consumer.finished)
        # 9 records (including one deleted), then an empty final page.
        self.assertEqual(seen, ['oai:test:%04d' % i for i in range(1, 10)])
        checkpoint = CheckpointStore(self.store_path).load()
        self.assertEqual(checkpoint.state, 'completed')
        self.assertEqual(checkpoint.page_fingerprints, [])
        # Only the first page plus three resumption-token requests are made.
        self.assertEqual(consumer.repository.request_log,
                         [None, 't2', 't3', 't4'])

    def test_mid_page_crash_redelivers_only_unacknowledged_records(self):
        consumer = self.make_consumer()
        records = consumer.pending_records()
        consumer.ack(records[0])
        consumer.ack(records[1])
        # Process dies without confirm_page(); record 3 was never acked.

        restarted = self.make_consumer()
        redelivered = restarted.pending_records()
        self.assertEqual(self.identifiers(redelivered), ['oai:test:0003'])
        # The first two records are not delivered again.
        self.assertEqual(
            len(restarted.checkpoint.acked_fingerprints), 2)
        # The next-page token was never used for the interrupted page:
        # the restart re-requests the first page (token None).
        self.assertEqual(restarted.repository.request_log, [None])

    def test_duplicate_ack_is_idempotent(self):
        consumer = self.make_consumer()
        record = consumer.pending_records()[0]
        self.assertTrue(consumer.ack(record))
        self.assertFalse(consumer.ack(record))
        self.assertFalse(consumer.ack(record.fingerprint))
        self.assertEqual(len(consumer.checkpoint.acked_fingerprints), 1)

    def test_out_of_order_acks_are_supported(self):
        consumer = self.make_consumer()
        records = consumer.pending_records()
        consumer.ack(records[2])
        consumer.ack(records[0])
        self.assertEqual(
            self.identifiers(consumer.pending_records()),
            ['oai:test:0002'])
        consumer.ack(records[1])
        self.assertEqual(consumer.pending_records(), [])
        consumer.confirm_page()
        self.assertNotIn('t2', consumer.repository.request_log)
        consumer.next_page()
        # Advancing only happens after every record has been acked.
        self.assertEqual(consumer.repository.request_log[-1], 't2')

    def test_confirm_page_refuses_to_skip_pending_records(self):
        consumer = self.make_consumer()
        records = consumer.pending_records()
        consumer.ack(records[0])
        with self.assertRaises(IncompletePage):
            consumer.confirm_page()
        # No next-page request was issued while records are pending.
        self.assertEqual(consumer.repository.request_log, [None])

    def test_next_page_requires_confirmation(self):
        consumer = self.make_consumer()
        records = consumer.pending_records()
        consumer.ack(records[0])
        with self.assertRaises(IncompletePage):
            consumer.next_page()
        self.assertEqual(consumer.repository.request_log, [None])

    def test_unknown_record_is_rejected(self):
        consumer = self.make_consumer()
        with self.assertRaises(UnknownRecord):
            consumer.ack('not-a-fingerprint')
        with self.assertRaises(UnknownRecord):
            consumer.ack(object())

    def test_next_page_token_is_not_progress_before_confirmation(self):
        consumer = self.make_consumer()
        records = consumer.pending_records()
        consumer.ack(records[0])
        # resume_token points at the current page, never the next one.
        self.assertIsNone(consumer.resume_token)
        self.assertEqual(
            consumer.checkpoint.next_token, 't2')
        restarted = self.make_consumer()
        # Restart still requests the first page; t2 was not consumed.
        self.assertEqual(restarted.repository.request_log, [None])

    def test_confirmed_page_is_resumed_from_the_next_token(self):
        consumer = self.make_consumer()
        for record in consumer.pending_records():
            consumer.ack(record)
        consumer.confirm_page()
        # The confirmed state is persisted on disk before any next-page
        # request happens.
        on_disk = CheckpointStore(self.store_path).load()
        self.assertEqual(on_disk.state, 'page_confirmed')
        self.assertEqual(on_disk.next_token, 't2')
        # A restart now uses the confirmed next token.
        restarted = self.make_consumer()
        self.assertEqual(restarted.repository.request_log, ['t2'])
        self.assertEqual(
            self.identifiers(restarted.pending_records()),
            ['oai:test:0004', 'oai:test:0005', 'oai:test:0006'])

    def test_changed_page_on_restart_raises_conflict(self):
        consumer = self.make_consumer()
        records = consumer.pending_records()
        consumer.ack(records[0])
        # The repository now returns different content for the first page.
        changed = dict(self.pages)
        changed[None] = make_page(
            [make_record(1), make_record(99), make_record(3)],
            next_token='t2', cursor=0)
        with self.assertRaises(CheckpointConflict):
            self.make_consumer(pages=changed)

    def test_conflict_is_not_raised_when_page_is_identical(self):
        consumer = self.make_consumer()
        records = consumer.pending_records()
        consumer.ack(records[0])
        # Identical bytes with different whitespace must still match.
        parsed = etree.XML(self.pages[None].encode('utf-8'))
        same_content = etree.tostring(
            parsed, xml_declaration=True, encoding='UTF-8')
        pages = dict(self.pages)
        pages[None] = same_content.decode('utf-8')
        restarted = self.make_consumer(pages=pages)
        self.assertEqual(
            self.identifiers(restarted.pending_records()),
            ['oai:test:0002', 'oai:test:0003'])

    def test_checkpoint_scope_mismatch_is_rejected(self):
        consumer = self.make_consumer()
        # Restart against a different endpoint / metadata format.
        repository = FakeRepository(self.pages)
        sickle = Sickle('http://other-host/oai')
        sickle.harvest = repository.harvest
        with self.assertRaises(CheckpointMismatch):
            RecordConsumer(sickle, CheckpointStore(self.store_path),
                           metadataPrefix='marc21')

    def test_same_endpoint_different_params_are_rejected(self):
        self.make_consumer()
        repository = FakeRepository(self.pages)
        sickle = Sickle(ENDPOINT)
        sickle.harvest = repository.harvest
        with self.assertRaises(CheckpointMismatch):
            RecordConsumer(sickle, CheckpointStore(self.store_path),
                           metadataPrefix='oai_dc', set='other-set')

    def test_checkpoint_save_is_atomic_and_reloadable(self):
        consumer = self.make_consumer()
        consumer.ack(consumer.pending_records()[0])
        checkpoint = CheckpointStore(self.store_path).load()
        self.assertEqual(
            checkpoint.acked_fingerprints,
            consumer.checkpoint.acked_fingerprints)
        self.assertEqual(checkpoint.endpoint, ENDPOINT)
        leftovers = [name for name in os.listdir(self.tmpdir)
                     if name.endswith('.tmp')]
        self.assertEqual(leftovers, [])

    def test_ignore_deleted_excludes_deleted_records(self):
        repository = FakeRepository(self.pages)
        sickle = Sickle(ENDPOINT)
        sickle.harvest = repository.harvest
        consumer = RecordConsumer(
            sickle, CheckpointStore(self.store_path),
            ignore_deleted=True, metadataPrefix='oai_dc')
        records = consumer.pending_records()
        self.assertEqual(
            self.identifiers(records),
            ['oai:test:0001', 'oai:test:0003'])
        for record in records:
            consumer.ack(record)
        # All deliverable records of the page are acked -> no IncompletePage.
        consumer.confirm_page()

    def test_iterator_interface_delivers_until_finished(self):
        consumer = self.make_consumer()
        seen = []
        for record in consumer:
            seen.append(record.header.identifier)
            consumer.ack(record)
        self.assertEqual(len(seen), 9)
        self.assertTrue(consumer.finished)

    def test_oai_errors_are_surfaced(self):
        from sickle.oaiexceptions import BadResumptionToken
        pages = default_pages()
        pages['t2'] = make_error('badResumptionToken', 'expired')
        consumer = self.make_consumer(pages=pages)
        for record in consumer.pending_records():
            consumer.ack(record)
        consumer.confirm_page()
        with self.assertRaises(BadResumptionToken):
            consumer.next_page()

    def test_restarting_a_completed_harvest_does_not_fetch(self):
        consumer = self.make_consumer()
        while not consumer.finished:
            for record in consumer.pending_records():
                consumer.ack(record)
            consumer.confirm_page()
            if not consumer.finished:
                consumer.next_page()
        restarted = self.make_consumer()
        self.assertTrue(restarted.finished)
        self.assertEqual(restarted.repository.request_log, [])
        self.assertEqual(restarted.pending_records(), [])

    def test_reset_starts_from_scratch(self):
        consumer = self.make_consumer()
        records = consumer.pending_records()
        consumer.ack(records[0])
        consumer.reset()
        self.assertEqual(
            self.identifiers(consumer.pending_records()),
            ['oai:test:0001', 'oai:test:0002', 'oai:test:0003'])
        self.assertEqual(consumer.checkpoint.acked_fingerprints, [])

    def test_checkpoint_persists_every_ack(self):
        consumer = self.make_consumer()
        record = consumer.pending_records()[0]
        consumer.ack(record)
        on_disk = CheckpointStore(self.store_path).load()
        self.assertEqual(on_disk.acked_fingerprints,
                         consumer.checkpoint.acked_fingerprints)
        self.assertEqual(on_disk.state, 'page_downloaded')

    def test_fingerprints_are_stable_across_reparsing(self):
        from sickle.consumer import fingerprint
        element = make_record(1)
        first = fingerprint(element)
        reparsed = etree.XML(etree.tostring(element))
        self.assertEqual(first, fingerprint(reparsed))
