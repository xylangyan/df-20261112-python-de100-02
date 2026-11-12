Resumable harvest demo
======================

This directory contains a self-contained demonstration of
``sickle.RecordConsumer``: records are fetched page by page, written to
SQLite and acknowledged individually; the process is then restarted to show
that only unacknowledged records are delivered again.

Files:

``oai_stub.py``
    A local OAI-PMH response stand-in. It serves the XML pages in
    ``fixtures/`` and is plugged into ``Sickle.harvest`` with ``mock.patch``,
    so no server is needed.

``fixtures/``
    OAI-PMH ``ListRecords`` pages (four pages; the last one is empty) plus a
    modified first page used for the conflict demonstration.

``build_fixtures.py``
    Regenerates ``fixtures/`` from code (the XML layout follows the sample
    data shipped in ``sickle/tests/sample_data``).

``demo.py``
    The end-to-end walkthrough. It prints every delivery, acknowledgement,
    restart and conflict, and finally the database contents and the request
    log of the stand-in.

Run it from the repository root::

    .venv/bin/python -B examples/resumable_harvest/demo.py

Expected highlights:

* Phase 1 acknowledges two of the three records on page 1 and then "crashes"
  without confirming the page.
* Phase 2 restarts and shows that **only** the unacknowledged record is
  delivered again; the next-page token was not used prematurely.
* Phase 3 acknowledges records out of order, acknowledges one twice and
  acknowledges one by its fingerprint string.
* Phase 4 confirms the empty last page and finishes the harvest.
* Phase 5 restarts against a changed page and raises
  ``CheckpointConflict``.
