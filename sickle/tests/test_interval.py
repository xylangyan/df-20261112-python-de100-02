# coding: utf-8
"""
    sickle.tests.test_interval
    ~~~~~~~~~~~~~~~~~~~~~~~~~~

    :copyright: Copyright 2015 Mathias Loesch
"""
import datetime
import unittest

from sickle.harvest import parse_datestamp
from sickle.interval import (DateRange, GranularityError,
                             DAY_GRANULARITY, SECOND_GRANULARITY,
                             POLICY_STRICT, POLICY_WIDEN, slice_params)
from sickle.iterator import IntervalItemIterator

UTC = datetime.timezone.utc


def dt(*args):
    return datetime.datetime(*args, tzinfo=UTC)


class TestDateRange(unittest.TestCase):

    def test_naive_bounds_rejected(self):
        with self.assertRaises(ValueError):
            DateRange(datetime.datetime(2024, 1, 1), dt(2024, 1, 2))
        with self.assertRaises(ValueError):
            DateRange(dt(2024, 1, 1), datetime.datetime(2024, 1, 2))

    def test_end_before_start_rejected(self):
        with self.assertRaises(ValueError):
            DateRange(dt(2024, 1, 2), dt(2024, 1, 1))

    def test_bounds_normalized_to_utc(self):
        plus_two = datetime.timezone(datetime.timedelta(hours=2))
        date_range = DateRange(
            datetime.datetime(2024, 1, 1, 2, 0, tzinfo=plus_two),
            datetime.datetime(2024, 1, 1, 3, 0, tzinfo=plus_two))
        self.assertEqual(date_range.start, dt(2024, 1, 1, 0, 0))
        self.assertEqual(date_range.end, dt(2024, 1, 1, 1, 0))


class TestSliceParams(unittest.TestCase):

    def test_day_granularity_whole_days(self):
        date_range = DateRange(dt(2024, 1, 1), dt(2024, 1, 12))
        self.assertEqual(
            slice_params(date_range, DAY_GRANULARITY,
                         datetime.timedelta(days=10)),
            [('2024-01-01', '2024-01-10'),
             ('2024-01-11', '2024-01-12')])

    def test_second_granularity_slices(self):
        date_range = DateRange(dt(2024, 1, 1, 10, 0),
                               dt(2024, 1, 15, 10, 0))
        self.assertEqual(
            slice_params(date_range, SECOND_GRANULARITY,
                         datetime.timedelta(days=7)),
            [('2024-01-01T10:00:00Z', '2024-01-08T09:59:59Z'),
             ('2024-01-08T10:00:00Z', '2024-01-15T09:59:59Z'),
             ('2024-01-15T10:00:00Z', '2024-01-15T10:00:00Z')])

    def test_single_instant(self):
        date_range = DateRange(dt(2024, 1, 1, 10, 0),
                               dt(2024, 1, 1, 10, 0))
        self.assertEqual(
            slice_params(date_range, SECOND_GRANULARITY,
                         datetime.timedelta(days=1)),
            [('2024-01-01T10:00:00Z', '2024-01-01T10:00:00Z')])

    def test_strict_rejects_subday_range(self):
        date_range = DateRange(dt(2024, 1, 1, 10, 0), dt(2024, 1, 2))
        with self.assertRaises(GranularityError):
            slice_params(date_range, DAY_GRANULARITY,
                         datetime.timedelta(days=1))

    def test_widen_expands_to_whole_days(self):
        date_range = DateRange(dt(2024, 1, 1, 10, 0),
                               dt(2024, 1, 3, 12, 0))
        self.assertEqual(
            slice_params(date_range, DAY_GRANULARITY,
                         datetime.timedelta(days=2),
                         policy=POLICY_WIDEN),
            [('2024-01-01', '2024-01-02'),
             ('2024-01-03', '2024-01-03')])

    def test_unsupported_granularity_rejected(self):
        date_range = DateRange(dt(2024, 1, 1), dt(2024, 1, 2))
        with self.assertRaises(GranularityError):
            slice_params(date_range, 'YYYY-MM',
                         datetime.timedelta(days=1))

    def test_invalid_policy_rejected(self):
        date_range = DateRange(dt(2024, 1, 1), dt(2024, 1, 2))
        with self.assertRaises(ValueError):
            slice_params(date_range, DAY_GRANULARITY,
                         datetime.timedelta(days=1), policy='truncate')

    def test_max_span_smaller_than_a_day_rejected(self):
        date_range = DateRange(dt(2024, 1, 1), dt(2024, 1, 2))
        with self.assertRaises(ValueError):
            slice_params(date_range, SECOND_GRANULARITY,
                         datetime.timedelta(hours=12))

    def test_fractional_day_span_rejected_at_day_granularity(self):
        date_range = DateRange(dt(2024, 1, 1), dt(2024, 1, 2))
        with self.assertRaises(ValueError):
            slice_params(date_range, DAY_GRANULARITY,
                         datetime.timedelta(days=1, hours=12))


class TestParseDatestamp(unittest.TestCase):

    def test_seconds(self):
        self.assertEqual(parse_datestamp('2024-01-02T03:04:05Z'),
                         dt(2024, 1, 2, 3, 4, 5))

    def test_days(self):
        self.assertEqual(parse_datestamp('2024-01-02'),
                         dt(2024, 1, 2))

    def test_invalid(self):
        self.assertIsNone(parse_datestamp('not-a-date'))
        self.assertIsNone(parse_datestamp(None))


class HeaderStub(object):
    def __init__(self, identifier, datestamp):
        self.identifier = identifier
        self.datestamp = datestamp


class RecordStub(object):
    def __init__(self, identifier, datestamp):
        self.header = HeaderStub(identifier, datestamp)

    @property
    def key(self):
        return self.header.identifier, self.header.datestamp


class TestIntervalItemIterator(unittest.TestCase):

    def test_dedup_keeps_identifier_versions(self):
        items = [
            iter([RecordStub('a', '2024-01-01'),
                  RecordStub('b', '2024-01-01')]),
            iter([RecordStub('b', '2024-01-01'),
                  RecordStub('b', '2024-01-02')]),
        ]
        self.assertEqual(
            [r.key for r in IntervalItemIterator(items)],
            [('a', '2024-01-01'), ('b', '2024-01-01'),
             ('b', '2024-01-02')])

    def test_headers_without_header_attribute(self):
        items = [iter([HeaderStub('a', '2024-01-01'),
                       HeaderStub('a', '2024-01-01')])]
        self.assertEqual(
            [(r.identifier, r.datestamp)
             for r in IntervalItemIterator(items)],
            [('a', '2024-01-01')])

    def test_items_without_header_values_are_never_deduped(self):
        items = [iter([HeaderStub(None, None), HeaderStub(None, None)])]
        self.assertEqual(len(list(IntervalItemIterator(items))), 2)

    def test_empty(self):
        self.assertEqual(list(IntervalItemIterator([])), [])
