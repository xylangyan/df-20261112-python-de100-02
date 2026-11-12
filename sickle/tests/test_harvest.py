# coding: utf-8
"""
    sickle.tests.test_harvest
    ~~~~~~~~~~~~~~~~~~~~~~~~~

    Tests for the two-phase ListIdentifiers/GetRecord harvest pipeline.

    :copyright: Copyright 2015 Mathias Loesch
"""
import os
import unittest

import mock

from sickle import Sickle
from sickle.harvest import (
    Harvester, HeaderMismatchError, harvest,
    STATUS_UNCHANGED, STATUS_UPDATED, STATUS_DELETED, STATUS_FAILED,
    ACTION_ACCEPT, ACTION_CONFLICT, ACTION_SKIP,
)
from sickle._compat import to_unicode
from sickle.response import OAIResponse

this_dir, this_filename = os.path.split(__file__)


class MockResponse(object):
    """Mimics the response object returned by HTTP requests."""

    def __init__(self, text):
        self.text = text
        self.content = text.encode('utf-8')


def mock_harvest(*args, **kwargs):
    """Serve harvest fixtures instead of contacting an OAI interface.

    ``ListIdentifiers`` is answered with ``HarvestListIdentifiers.xml``.
    ``GetRecord`` is answered per identifier with
    ``HarvestGetRecord_<suffix>.xml`` where ``<suffix>`` is the part of the
    identifier after the last colon.
    """
    verb = kwargs.get('verb')
    if verb == 'GetRecord':
        suffix = kwargs['identifier'].rsplit(':', 1)[1]
        filename = 'HarvestGetRecord_%s.xml' % suffix
    else:
        filename = 'HarvestListIdentifiers.xml'
    with open(os.path.join(this_dir, 'sample_data', filename), 'r') as fp:
        response = MockResponse(to_unicode(fp.read()))
        return OAIResponse(response, kwargs)


class TestCase(unittest.TestCase):

    def __init__(self, methodName='runTest'):
        super(TestCase, self).__init__(methodName)
        self.patch = mock.patch('sickle.app.Sickle.harvest', mock_harvest)

    def setUp(self):
        self.patch.start()
        self.sickle = Sickle('http://localhost')

    def tearDown(self):
        self.patch.stop()

    def _results(self, **kwargs):
        return dict((r.identifier, r)
                    for r in self.sickle_harvest(**kwargs))

    def sickle_harvest(self, **kwargs):
        return harvest(self.sickle, metadataPrefix='oai_dc', **kwargs)

    def test_statuses_are_distinguishable(self):
        results = self._results()
        base = 'oai:test.example.com:%s'
        self.assertEqual(results[base % 'unchanged'].status, STATUS_UNCHANGED)
        self.assertEqual(results[base % 'updated'].status, STATUS_UPDATED)
        self.assertEqual(results[base % 'deleted'].status, STATUS_DELETED)
        self.assertEqual(results[base % 'failed'].status, STATUS_FAILED)
        # A record deleted at listing time but live again at GetRecord time
        # is reported as updated.
        self.assertEqual(results[base % 'reappeared'].status, STATUS_UPDATED)

    def test_single_failure_keeps_other_results(self):
        results = list(self.sickle_harvest())
        self.assertEqual(len(results), 5)
        failed = [r for r in results if r.status == STATUS_FAILED]
        self.assertEqual(len(failed), 1)
        self.assertIsNotNone(failed[0].error)
        self.assertIsNone(failed[0].record)
        ok = [r for r in results if r.ok]
        self.assertEqual(len(ok), 3)

    def test_listed_and_actual_headers_kept(self):
        results = self._results()
        updated = results['oai:test.example.com:updated']
        self.assertEqual(updated.listed_header.datestamp,
                         '2011-07-18T16:31:00Z')
        self.assertEqual(updated.actual_header.datestamp,
                         '2011-09-05T12:51:52Z')
        # The accepted record carries the actual (newer) header, never the
        # stale listed datestamp.
        self.assertEqual(updated.record.header.datestamp,
                         '2011-09-05T12:51:52Z')
        self.assertEqual(updated.action, ACTION_ACCEPT)

    def test_unchanged_result(self):
        results = self._results()
        unchanged = results['oai:test.example.com:unchanged']
        self.assertTrue(unchanged.ok)
        self.assertEqual(unchanged.action, ACTION_ACCEPT)
        self.assertEqual(unchanged.record.metadata['title'],
                         ['Unchanged Title'])
        self.assertEqual(unchanged.listed_header.datestamp,
                         unchanged.actual_header.datestamp)

    def test_deleted_default_skipped(self):
        results = self._results()
        deleted = results['oai:test.example.com:deleted']
        self.assertEqual(deleted.status, STATUS_DELETED)
        self.assertEqual(deleted.action, ACTION_SKIP)
        self.assertIsNone(deleted.record)
        # Both header versions remain available for the ingestion layer.
        self.assertFalse(deleted.listed_header.deleted)
        self.assertTrue(deleted.actual_header.deleted)

    def test_deleted_accept_keeps_marker(self):
        results = self._results(on_deleted=ACTION_ACCEPT)
        deleted = results['oai:test.example.com:deleted']
        self.assertEqual(deleted.action, ACTION_ACCEPT)
        self.assertIsNotNone(deleted.record)
        self.assertTrue(deleted.record.deleted)

    def test_updated_conflict_defers_record(self):
        results = self._results(on_updated=ACTION_CONFLICT)
        updated = results['oai:test.example.com:updated']
        self.assertEqual(updated.status, STATUS_UPDATED)
        self.assertEqual(updated.action, ACTION_CONFLICT)
        self.assertFalse(updated.ok)
        # The conflicting record is stashed with the result, not lost.
        self.assertIsNotNone(updated.record)
        reappeared = results['oai:test.example.com:reappeared']
        self.assertEqual(reappeared.action, ACTION_CONFLICT)

    def test_reappeared_record(self):
        results = self._results()
        reappeared = results['oai:test.example.com:reappeared']
        self.assertTrue(reappeared.listed_header.deleted)
        self.assertFalse(reappeared.actual_header.deleted)
        self.assertEqual(reappeared.record.metadata['title'],
                         ['Reappeared Title'])

    def test_ignore_deleted_skips_listed_deleted(self):
        results = list(self.sickle_harvest(ignore_deleted=True))
        identifiers = [r.identifier for r in results]
        self.assertNotIn('oai:test.example.com:reappeared', identifiers)
        self.assertEqual(len(results), 4)

    def test_header_mismatch_is_failed(self):
        harvester = Harvester(self.sickle)
        listed = [h for h in self.sickle.ListIdentifiers(
            metadataPrefix='oai_dc')
            if h.identifier == 'oai:test.example.com:updated'][0]
        # Rewrite the fixture lookup so GetRecord answers with a record
        # whose header carries a different identifier.
        def wrong_id_harvest(*args, **kw):
            if kw.get('verb') == 'GetRecord':
                kw = dict(kw, identifier='oai:test.example.com:wrongid')
            return mock_harvest(*args, **kw)
        with mock.patch('sickle.app.Sickle.harvest', wrong_id_harvest):
            result = harvester.harvest_header(
                listed, metadataPrefix='oai_dc')
        self.assertEqual(result.status, STATUS_FAILED)
        self.assertEqual(result.action, ACTION_SKIP)
        self.assertIsInstance(result.error, HeaderMismatchError)
        self.assertEqual(result.actual_header.identifier,
                         'oai:test.example.com:someother')

    def test_result_repr(self):
        results = self._results()
        text = repr(results['oai:test.example.com:unchanged'])
        self.assertIn('oai:test.example.com:unchanged', text)
        self.assertIn(STATUS_UNCHANGED, text)
        self.assertIn(ACTION_ACCEPT, text)

    def test_harvester_class_api(self):
        harvester = Harvester(self.sickle, on_updated=ACTION_CONFLICT,
                              on_deleted=ACTION_ACCEPT)
        results = dict((r.identifier, r)
                       for r in harvester.harvest(metadataPrefix='oai_dc'))
        self.assertEqual(results['oai:test.example.com:updated'].action,
                         ACTION_CONFLICT)
        self.assertEqual(results['oai:test.example.com:deleted'].action,
                         ACTION_ACCEPT)
