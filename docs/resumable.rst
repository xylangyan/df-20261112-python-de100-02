=========================
Resumable Record Consumption
=========================

Sickle's iterators transparently page through a repository, but iterating
over records does not mean they have been processed: if the process dies
half-way through a page, the already delivered records are gone. The
:class:`sickle.consumer.RecordConsumer` adds an explicit acknowledgement
step on top of ``ListRecords`` so a harvest can be resumed exactly where it
stopped.

Checkpoints
===========

Progress is persisted in a :class:`sickle.consumer.Checkpoint` which is
bound to the exact harvest it belongs to: the OAI endpoint, the OAI verb,
all request parameters (including the metadata format) and the OAI element
being iterated. A checkpoint that does not match the current harvest raises
:class:`sickle.oaiexceptions.CheckpointMismatch` on restart.

The checkpoint distinguishes three facts:

* **page downloaded** — the fingerprints of the page's records are stored;
* **page consumed** — the fingerprints of acknowledged records are stored
  individually;
* **page confirmed** — the whole page has been acknowledged and the
  next-page resumption token may be used.

The next-page resumption token is never treated as progress before the
current page is confirmed, so a crash can never silently skip
unacknowledged records.

Usage
=====

::

    from sickle import Sickle, RecordConsumer, CheckpointStore

    sickle = Sickle('http://elis.da.ulcc.ac.uk/cgi/oai2')
    consumer = RecordConsumer(
        sickle, CheckpointStore('harvest.json'), metadataPrefix='oai_dc')

    while not consumer.finished:
        for record in consumer.pending_records():
            persist(record)          # write to the database
            consumer.ack(record)     # confirm success
        consumer.confirm_page()      # mark the page as fully consumed
        if not consumer.finished:
            consumer.next_page()     # fetch the next page

Acknowledgements may arrive in any order and duplicate acknowledgements are
allowed (``ack`` returns :obj:`False` for them). Records are identified by a
digest of their canonical XML, never by their position in the page.

If the process exits before a page is confirmed, the unacknowledged records
are delivered again on restart. If the content of an already downloaded
page has changed on the server, a
:class:`sickle.oaiexceptions.CheckpointConflict` is raised instead of
guessing records by their position.

A runnable end-to-end example (fetch → persist → acknowledge → restart,
including an empty last page and a page-conflict demonstration) lives in
``examples/resumable_harvest/`` and uses a local OAI-PMH response stand-in
built from XML fixtures.

API
===

.. autoclass:: sickle.consumer.RecordConsumer
    :members:

.. autoclass:: sickle.consumer.Checkpoint
    :members:

.. autoclass:: sickle.consumer.CheckpointStore
    :members:

.. autoexception:: sickle.oaiexceptions.CheckpointError
.. autoexception:: sickle.oaiexceptions.CheckpointConflict
.. autoexception:: sickle.oaiexceptions.CheckpointMismatch
.. autoexception:: sickle.oaiexceptions.IncompletePage
.. autoexception:: sickle.oaiexceptions.UnknownRecord
