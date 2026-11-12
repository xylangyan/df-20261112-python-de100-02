=====================
Interval Harvesting
=====================

Some OAI-PMH repositories only accept day-granularity dates
(``YYYY-MM-DD``), others accept full UTC seconds
(``YYYY-MM-DDThh:mm:ssZ``).  When callers assemble ``from``/``until``
slices by hand, inclusive endpoints cause overlapping boundary records
and rounding mistakes cause gaps or silently truncated ranges.

:meth:`sickle.app.Sickle.HarvestInterval` reads the repository's
``Identify`` response first to determine its granularity, then splits a
timezone-aware, inclusive time range into legal slices no wider than a
given maximum span.

Basic usage
-----------

::

    import datetime
    from sickle import Sickle

    sickle = Sickle('http://example.org/oai')
    harvester = sickle.HarvestInterval(
        start=datetime.datetime(2024, 1, 1, 22, 0,
                                tzinfo=datetime.timezone.utc),
        end=datetime.datetime(2024, 1, 31, 21, 59, 59,
                              tzinfo=datetime.timezone.utc),
        metadata_prefix='oai_dc',
        max_span=datetime.timedelta(days=7),
    )
    records = harvester.harvest()

The request parameters that were actually sent and the status of every
slice are available for inspection::

    for slice_result in harvester.slices:
        print(slice_result.status, slice_result.params)

Each slice is independent: a failed slice is marked ``failed`` with its
exception stored in ``error`` and never prevents the remaining slices
from running.  An empty slice (an OAI ``noRecordsMatch`` error) is a
successful slice with zero records.

Granularity policies
--------------------

If the repository only supports day granularity, a sub-day range cannot
be expressed exactly.  Sickle never truncates the range silently; the
caller must choose explicitly:

``policy='strict'`` (default)
    Raise :class:`sickle.interval.GranularityError` if either bound is
    not a whole UTC day.
``policy='widen'``
    Widen the request to the whole days containing the bounds and filter
    the returned records by their header datestamp, so the final record
    sequence still matches the requested range exactly.

Overlapping boundaries
----------------------

OAI-PMH ``from`` and ``until`` are inclusive, so adjacent slices share
their boundary instant and the repository may return the same record
version from both slices.  Interval harvesting de-duplicates by the
``(identifier, datestamp)`` pair of the record header: an identical
record version appears only once, while successive versions of the same
identifier (different datestamps) are all preserved.

Reference
---------

.. autoclass:: sickle.interval.DateRange
    :members:

.. autoclass:: sickle.interval.GranularityError
    :members:

.. autoclass:: sickle.harvest.IntervalHarvester
    :members:

.. autoclass:: sickle.harvest.SliceResult
    :members:
