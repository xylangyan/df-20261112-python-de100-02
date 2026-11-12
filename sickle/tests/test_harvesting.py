# coding: utf-8
"""
    sickle.tests.test_harvesting
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~

    :copyright: Copyright 2015 Mathias Loesch
"""
import datetime
import os
import unittest

from lxml import etree
from nose.tools import raises
import mock

from sickle import Sickle
from sickle._compat import binary_type, string_types, text_type, to_unicode
from sickle.interval import (GranularityError, POLICY_STRICT, POLICY_WIDEN)
from sickle.response import OAIResponse
from sickle.iterator import OAIResponseIterator
from sickle.oaiexceptions import BadArgument, CannotDisseminateFormat, \
    IdDoesNotExist, NoSetHierarchy, BadResumptionToken, NoRecordsMatch, \
    OAIError

this_dir, this_filename = os.path.split(__file__)


class NamedResponse(object):
    """Mimics an HTTP response built from a file in ``sample_data``."""

    def __init__(self, filename):
        with open(os.path.join(this_dir, 'sample_data', filename), 'r') as fp:
            text = to_unicode(fp.read())
        self.text = text
        self.content = text.encode('utf-8')


class MockResponse(object):
    """Mimics the response object returned by HTTP requests."""

    def __init__(self, text):
        # request's response object carry an attribute 'text' which contains
        # the server's response data encoded as unicode.
        self.text = text
        self.content = text.encode('utf-8')


def mock_harvest(*args, **kwargs):
    """Read test data from files instead of from an OAI interface.

    The data is read from the ``xml`` directory by using the provided
    :attr:`verb` as file name. The following returns an OAIResponse created
    from the file ``ListRecords.xml``::

        fake_harvest(verb='ListRecords', metadataPrefix='oai_dc')

    The file names for consecutive resumption responses are expected in the
    resumptionToken parameter::

        fake_harvest(verb='ListRecords', resumptionToken='ListRecords2.xml')

    The parameter :attr:`error` can be used to invoke a specific OAI error
    response. For instance, the following returns a ``badArgument`` error
    response::

        fake_harvest(verb='ListRecords', error='badArgument')

    :param kwargs: OAI arguments that would normally be passed to
                   :meth:`sickle.app.Sickle.harvest`.
    :rtype: :class:`sickle.response.OAIResponse`.
    """
    verb = kwargs.get('verb')
    resumption_token = kwargs.get('resumptionToken')
    error = kwargs.get('error')
    if resumption_token is not None:
        filename = resumption_token
    elif error is not None:
        filename = '%s.xml' % error
    else:
        filename = '%s.xml' % verb

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

    def test_OAIResponse(self):
        response = self.sickle.harvest(verb='ListRecords',
                                       metadataPrefix='oai_dc')
        self.assertIsInstance(response.xml, etree._Element)
        self.assertIsInstance(response.raw, string_types)

    def test_broken_XML(self):
        response = self.sickle.harvest(
            verb='ListRecords', resumptionToken='ListRecordsBroken.xml')
        self.assertEqual(response.xml, None)
        self.assertIsInstance(response.raw, string_types)

    def test_ListRecords(self):
        records = self.sickle.ListRecords(metadataPrefix='oai_dc')
        assert len([r for r in records]) == 8

    def test_ListRecords_ignore_deleted(self):
        records = self.sickle.ListRecords(metadataPrefix='oai_dc',
                                          ignore_deleted=True)
        num_records = len([r for r in records])
        assert num_records == 4

    def test_ListSets(self):
        set_iterator = self.sickle.ListSets()
        sets = [s for s in set_iterator]
        self.assertEqual(131, len(sets))
        dict(sets[0])

    def test_ListMetadataFormats(self):
        mdf_iterator = self.sickle.ListMetadataFormats()
        mdfs = [mdf for mdf in mdf_iterator]
        self.assertEqual(5, len(mdfs))
        dict(mdfs[0])

    def test_ListIdentifiers(self):
        records = self.sickle.ListIdentifiers(metadataPrefix='oai_dc')
        self.assertEqual(len([r for r in records]), 4)

    def test_ListIdentifiers_ignore_deleted(self):
        records = self.sickle.ListIdentifiers(
            metadataPrefix='oai_dc', ignore_deleted=True)
        # There are 2 deleted headers in the test data
        num_records = len([r for r in records])
        self.assertEqual(num_records, 2)

    def test_Identify(self):
        identify = self.sickle.Identify()
        self.assertTrue(hasattr(identify, 'repositoryName'))
        self.assertTrue(hasattr(identify, 'baseURL'))
        self.assertTrue(hasattr(identify, 'adminEmail'))
        self.assertTrue(hasattr(identify, 'earliestDatestamp'))
        self.assertTrue(hasattr(identify, 'deletedRecord'))
        self.assertTrue(hasattr(identify, 'granularity'))
        self.assertTrue(hasattr(identify, 'description'))
        self.assertTrue(hasattr(identify, 'oai_identifier'))
        self.assertTrue(hasattr(identify, 'sampleIdentifier'))
        dict(identify)

    def test_GetRecord(self):
        oai_id = 'oai:test.example.com:1996652'
        record = self.sickle.GetRecord(identifier=oai_id)
        self.assertEqual(record.header.identifier, oai_id)
        self.assertIn(oai_id, record.raw)
        self.assertEqual(record.header.datestamp, '2011-09-05T12:51:52Z')
        self.assertIsInstance(record.xml, etree._Element)
        binary_type(record)
        text_type(record)
        dict(record.header)
        self.assertEqual(dict(record), record.metadata)

    # Test OAI-specific exceptions

    @raises(BadArgument)
    def test_badArgument(self):
        self.sickle.ListRecords(metadataPrefix='oai_dc',
                                error='badArgument')

    @raises(CannotDisseminateFormat)
    def test_cannotDisseminateFormat(self):
        self.sickle.ListRecords(
            metadataPrefix='oai_dc', error='cannotDisseminateFormat')

    @raises(IdDoesNotExist)
    def test_idDoesNotExist(self):
        self.sickle.GetRecord(
            metadataPrefix='oai_dc', error='idDoesNotExist')

    @raises(NoSetHierarchy)
    def test_noSetHierarchy(self):
        self.sickle.ListSets(
            metadataPrefix='oai_dc', error='noSetHierarchy')

    @raises(BadResumptionToken)
    def test_badResumptionToken(self):
        self.sickle.ListRecords(
            metadataPrefix='oai_dc', error='badResumptionToken')

    @raises(NoRecordsMatch)
    def test_noRecordsMatch(self):
        self.sickle.ListRecords(
            metadataPrefix='oai_dc', error='noRecordsMatch')

    @raises(OAIError)
    def test_undefined_OAI_error_XML(self):
        self.sickle.ListRecords(
            metadataPrefix='oai_dc', error='undefinedError')

    def test_OAIResponseIterator(self):
        sickle = Sickle('fake_url', iterator=OAIResponseIterator)
        records = [r for r in sickle.ListRecords(metadataPrefix='oai_dc')]
        self.assertEqual(len(records), 4)


def mock_get(*args, **kwargs):
    class MockResponseWrongEncoding(object):
        """Mimics a case where the requests library misidentifies the text encoding.

        If http headers do not specify the correct encoding, requests will default
        to 'ISO-8859-1', even though the oai-pmh document might specify e.g.
        <xml encoding='utf-8'>.

        Parameters
        ----------
        filename : str
            Name of a utf8-encoded test file in the sample_data directory.

        Attributes
        ----------
        content : bytes (py3) / str (py2)
            Raw utf8-encoded bytestream
        text : str (py3) / unicode (py2)
            Unicode string from self.content, but wrongly decoded as ISO-8859-1
        status_code : int
            = 200
        """

        def __init__(self, filename='GetRecord_utf8test.xml'):
            with open(os.path.join(this_dir, 'sample_data', filename), 'rb') as fp:
                self.content = fp.read()
            self.text = self.content.decode('ISO-8859-1')
            self.status_code = 200

        def raise_for_status(self):
            pass

    return MockResponseWrongEncoding()


class TestCaseWrongEncoding(unittest.TestCase):

    def __init__(self, methodName='runTest'):
        super(TestCaseWrongEncoding, self).__init__(methodName)
        self.patch = mock.patch('sickle.app.requests.get', mock_get)

    def setUp(self):
        self.patch.start()
        self.sickle = Sickle('http://localhost')

    def tearDown(self):
        self.patch.stop()

    def test_GetRecord(self):
        oai_id = 'oai:test.example.com:1996652'
        record = self.sickle.GetRecord(identifier=oai_id)
        self.assertEqual(record.header.identifier, oai_id)
        self.assertIn(oai_id, record.raw)
        self.assertEqual(record.header.datestamp, '2011-09-05T12:51:52Z')
        self.assertIsInstance(record.xml, etree._Element)
        binary_type(record)
        text_type(record)
        dict(record.header)
        self.assertEqual(dict(record), record.metadata)
        self.assertIn(u'某人', record.metadata['creator'])


UTC = datetime.timezone.utc


def interval_mock_harvest(*args, **kwargs):
    """Serve interval harvesting fixtures based on the request params.

    The ``Identify`` fixture is selected by the ``_identify_file``
    attribute of the Sickle instance (default
    ``IdentifySeconds.xml``).  ``ListRecords`` requests are dispatched
    on their ``from``/``until`` pair; the final pair raises to simulate
    a failed slice.
    """
    if kwargs.get('verb') == 'Identify':
        identify_file = getattr(args[0], '_identify_file',
                                'IdentifySeconds.xml')
        return OAIResponse(NamedResponse(identify_file), kwargs)
    if kwargs.get('verb') == 'ListRecords':
        pair = (kwargs.get('from'), kwargs.get('until'))
        if pair == ('2024-01-04T22:00:00Z', '2024-01-05T21:59:59Z'):
            raise RuntimeError('simulated HTTP failure')
        files = {
            ('2024-01-01T22:00:00Z', '2024-01-02T21:59:59Z'):
                'IntervalSliceA.xml',
            ('2024-01-02T22:00:00Z', '2024-01-03T21:59:59Z'):
                'IntervalSliceB.xml',
            ('2024-01-03T22:00:00Z', '2024-01-04T21:59:59Z'):
                'noRecordsMatch.xml',
            ('2024-01-01', '2024-01-01'): 'IntervalDay1.xml',
            ('2024-01-02', '2024-01-02'): 'IntervalDay2.xml',
            ('2024-01-03', '2024-01-03'): 'IntervalDay3.xml',
        }
        return OAIResponse(NamedResponse(files[pair]), kwargs)
    raise ValueError('unexpected request: %r' % (kwargs,))


class TestIntervalHarvesting(unittest.TestCase):
    """Interval harvesting against local fixture data."""

    def __init__(self, methodName='runTest'):
        super(TestIntervalHarvesting, self).__init__(methodName)
        self.patch = mock.patch('sickle.app.Sickle.harvest',
                                interval_mock_harvest)

    def setUp(self):
        self.patch.start()
        self.sickle = Sickle('http://localhost')

    def tearDown(self):
        self.patch.stop()

    def test_seconds_granularity_utc_day_boundary(self):
        """Slices cross the UTC day boundary; the record updated on the
        boundary appears in both versions, the duplicate boundary record
        only once, and the empty slice completes without records."""
        harvester = self.sickle.HarvestInterval(
            start=datetime.datetime(2024, 1, 1, 22, 0, tzinfo=UTC),
            end=datetime.datetime(2024, 1, 4, 21, 59, 59, tzinfo=UTC),
            metadata_prefix='oai_dc',
            max_span=datetime.timedelta(days=1),
        )
        self.assertEqual(harvester.granularity,
                         'YYYY-MM-DDThh:mm:ssZ')
        # The exact parameters sent to the endpoint, for verification.
        self.assertEqual(
            [(s.params['from'], s.params['until']) for s in
             harvester.slices],
            [('2024-01-01T22:00:00Z', '2024-01-02T21:59:59Z'),
             ('2024-01-02T22:00:00Z', '2024-01-03T21:59:59Z'),
             ('2024-01-03T22:00:00Z', '2024-01-04T21:59:59Z')])

        records = harvester.harvest()

        # The final record sequence has no gaps and no duplicates.
        self.assertEqual(
            [(r.header.identifier, r.header.datestamp) for r in
             records],
            [('oai:test.example.com:101', '2024-01-01T22:00:00Z'),
             ('oai:test.example.com:102', '2024-01-02T00:30:00Z'),
             ('oai:test.example.com:103', '2024-01-02T12:00:00Z'),
             ('oai:test.example.com:105', '2024-01-02T22:00:00Z'),
             ('oai:test.example.com:104', '2024-01-02T23:15:00Z'),
             ('oai:test.example.com:103', '2024-01-03T08:45:30Z')])
        # The boundary record 105 is returned by both adjacent slices
        # (inclusive from/until) but kept only once.
        self.assertEqual(
            [r.header.identifier for r in harvester.slices[0].records],
            ['oai:test.example.com:101', 'oai:test.example.com:102',
             'oai:test.example.com:103', 'oai:test.example.com:105'])
        self.assertEqual(
            [r.header.identifier for r in harvester.slices[1].records],
            ['oai:test.example.com:105', 'oai:test.example.com:104',
             'oai:test.example.com:103'])
        # The updated version of 103 (new datestamp) is preserved.
        self.assertEqual(
            [r.header.datestamp for r in records
             if r.header.identifier == 'oai:test.example.com:103'],
            ['2024-01-02T12:00:00Z', '2024-01-03T08:45:30Z'])
        # Every slice reports its own status; the empty slice
        # (noRecordsMatch) completes with zero records.
        self.assertEqual([s.status for s in harvester.slices],
                         ['completed', 'completed', 'completed'])
        self.assertEqual(harvester.slices[2].records, [])

    def test_day_granularity_strict_rejects_subday_range(self):
        """A day-granularity repository must not silently truncate a
        sub-day range: the strict policy rejects it."""
        self.sickle._identify_file = 'Identify.xml'
        with self.assertRaises(GranularityError):
            self.sickle.HarvestInterval(
                start=datetime.datetime(2024, 1, 1, 22, 0, tzinfo=UTC),
                end=datetime.datetime(2024, 1, 3, 12, 0, tzinfo=UTC),
                metadata_prefix='oai_dc',
                max_span=datetime.timedelta(days=1),
                policy=POLICY_STRICT,
            )

    def test_day_granularity_widen_filters_by_header(self):
        """With the widen policy the request is expanded to whole days
        and the results are filtered by their header datestamps."""
        self.sickle._identify_file = 'Identify.xml'
        harvester = self.sickle.HarvestInterval(
            start=datetime.datetime(2024, 1, 1, 22, 0, tzinfo=UTC),
            end=datetime.datetime(2024, 1, 3, 8, 45, 30, tzinfo=UTC),
            metadata_prefix='oai_dc',
            max_span=datetime.timedelta(days=1),
            policy=POLICY_WIDEN,
        )
        self.assertEqual(harvester.granularity, 'YYYY-MM-DD')
        # The widened day-granularity parameters actually sent.
        self.assertEqual(
            [(s.params['from'], s.params['until']) for s in
             harvester.slices],
            [('2024-01-01', '2024-01-01'),
             ('2024-01-02', '2024-01-02'),
             ('2024-01-03', '2024-01-03')])

        records = harvester.harvest()

        # Records before the start (110) and after the end (112) are
        # filtered out; 111 lies inside the requested range.  Both
        # versions of 103 on the boundary day are kept.
        self.assertEqual(
            [(r.header.identifier, r.header.datestamp) for r in
             records],
            [('oai:test.example.com:101', '2024-01-01T22:00:00Z'),
             ('oai:test.example.com:102', '2024-01-02T00:30:00Z'),
             ('oai:test.example.com:111', '2024-01-02T18:00:00Z'),
             ('oai:test.example.com:103', '2024-01-03T08:00:00Z'),
             ('oai:test.example.com:103', '2024-01-03T08:45:30Z')])
        self.assertEqual([s.status for s in harvester.slices],
                         ['completed', 'completed', 'completed'])

    def test_failed_slice_does_not_stop_other_slices(self):
        """A failing slice is marked failed; the other slices still
        complete and their records are returned."""
        harvester = self.sickle.HarvestInterval(
            start=datetime.datetime(2024, 1, 1, 22, 0, tzinfo=UTC),
            end=datetime.datetime(2024, 1, 5, 21, 59, 59, tzinfo=UTC),
            metadata_prefix='oai_dc',
            max_span=datetime.timedelta(days=1),
        )
        records = harvester.harvest()
        self.assertEqual([s.status for s in harvester.slices],
                         ['completed', 'completed', 'completed',
                          'failed'])
        failed = harvester.slices[3]
        self.assertEqual(failed.params['from'],
                         '2024-01-04T22:00:00Z')
        self.assertIsInstance(failed.error, RuntimeError)
        self.assertEqual(failed.records, [])
        self.assertFalse(failed.completed)
        self.assertTrue(failed.failed)
        # Records from the successful slices are still delivered.
        self.assertEqual(
            [r.header.identifier for r in records],
            ['oai:test.example.com:101', 'oai:test.example.com:102',
             'oai:test.example.com:103', 'oai:test.example.com:105',
             'oai:test.example.com:104', 'oai:test.example.com:103'])

    def test_unsupported_granularity_rejected(self):
        """A repository reporting an illegal granularity is rejected."""
        self.sickle._identify_file = \
            'IdentifyUnsupportedGranularity.xml'
        with self.assertRaises(GranularityError):
            self.sickle.HarvestInterval(
                start=datetime.datetime(2024, 1, 1, tzinfo=UTC),
                end=datetime.datetime(2024, 1, 2, tzinfo=UTC),
                metadata_prefix='oai_dc',
                max_span=datetime.timedelta(days=1),
            )

    def test_naive_datetimes_rejected(self):
        """Range bounds must be timezone-aware."""
        with self.assertRaises(ValueError):
            self.sickle.HarvestInterval(
                start=datetime.datetime(2024, 1, 1),
                end=datetime.datetime(2024, 1, 2),
                metadata_prefix='oai_dc',
                max_span=datetime.timedelta(days=1),
            )

    def test_set_argument_is_forwarded(self):
        """The optional OAI ``set`` argument appears in every slice."""
        harvester = self.sickle.HarvestInterval(
            start=datetime.datetime(2024, 1, 1, 22, 0, tzinfo=UTC),
            end=datetime.datetime(2024, 1, 2, 21, 59, 59, tzinfo=UTC),
            metadata_prefix='oai_dc',
            max_span=datetime.timedelta(days=1),
            set_spec='col1:set2',
        )
        for slice_result in harvester.slices:
            self.assertEqual(slice_result.params['set'], 'col1:set2')
            self.assertEqual(
                slice_result.params['metadataPrefix'], 'oai_dc')
            self.assertEqual(slice_result.params['verb'], 'ListRecords')

    def test_timezone_conversion(self):
        """Bounds given in another timezone are converted to UTC."""
        plus_eight = datetime.timezone(datetime.timedelta(hours=8))
        harvester = self.sickle.HarvestInterval(
            start=datetime.datetime(2024, 1, 2, 6, 0, tzinfo=plus_eight),
            end=datetime.datetime(2024, 1, 3, 4, 0, tzinfo=plus_eight),
            metadata_prefix='oai_dc',
            max_span=datetime.timedelta(days=1),
        )
        self.assertEqual(
            [(s.params['from'], s.params['until']) for s in
             harvester.slices],
            [('2024-01-01T22:00:00Z', '2024-01-02T20:00:00Z')])
