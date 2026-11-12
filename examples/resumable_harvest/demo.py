# coding: utf-8
"""End-to-end demonstration: fetch -> persist -> acknowledge -> restart.

The script uses the local OAI-PMH stand-in (see ``oai_stub.py``) instead of a
real server and a SQLite file instead of a production database. It shows:

1. fetching records page by page with :class:`sickle.RecordConsumer`;
2. persisting each record into SQLite and acknowledging it individually;
3. an artificial mid-page crash (the process exits before the page is
   confirmed) and the restart, where unacknowledged records are delivered
   again while acknowledged ones are not;
4. duplicate and out-of-order acknowledgements;
5. the empty last page that marks the harvest completed;
6. a separate run demonstrating :class:`sickle.CheckpointConflict` when an
   already-downloaded page changes on the server.

Run from the repository root::

    .venv/bin/python -B examples/resumable_harvest/demo.py
"""

import os
import sqlite3
import sys

import mock

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from sickle import (CheckpointConflict, CheckpointStore, RecordConsumer,  # noqa: E402
                    Sickle)
from oai_stub import OAIStub  # noqa: E402

ENDPOINT = 'http://localhost:8000/oai'
WORKDIR = os.path.join(HERE, 'work')
CHECKPOINT_PATH = os.path.join(WORKDIR, 'checkpoint.json')
DATABASE_PATH = os.path.join(WORKDIR, 'harvest.sqlite')


def heading(title):
    print('\n' + '=' * 72)
    print(title)
    print('=' * 72)


def init_database():
    connection = sqlite3.connect(DATABASE_PATH)
    connection.execute(
        'CREATE TABLE IF NOT EXISTS records ('
        'identifier TEXT PRIMARY KEY, datestamp TEXT, title TEXT, '
        'deleted INTEGER, fingerprint TEXT)')
    connection.commit()
    return connection


def store_record(connection, record):
    """Pretend to write the record into a database and return its key."""
    title = None if record.deleted else record.metadata.get('title', [None])[0]
    connection.execute(
        'INSERT OR REPLACE INTO records '
        '(identifier, datestamp, title, deleted, fingerprint) '
        'VALUES (?, ?, ?, ?, ?)',
        (record.header.identifier, record.header.datestamp, title,
         int(record.deleted), record.fingerprint))
    connection.commit()
    return record.header.identifier


def build_consumer():
    sickle = Sickle(ENDPOINT)
    return RecordConsumer(sickle, CheckpointStore(CHECKPOINT_PATH),
                          metadataPrefix='oai_dc')


def phase_first_run_interrupted(connection):
    """Fetch page 1, persist two records, then crash mid-page."""
    heading('Phase 1: first run, interrupted inside page 1')
    consumer = build_consumer()
    records = consumer.pending_records()
    print('delivered on page 1: %s'
          % [r.header.identifier for r in records])
    for record in records[:2]:
        identifier = store_record(connection, record)
        first = consumer.ack(record)
        print('  persisted + acknowledged %s (new ack: %s)'
              % (identifier, first))
    pending = [r.header.identifier for r in consumer.pending_records()]
    print('unacknowledged before the crash: %s' % pending)
    print('checkpoint state on disk: %s (next token is NOT used yet)'
          % consumer.checkpoint.state)
    # Dropping the consumer without confirm_page() simulates the process
    # being killed. The unacknowledged record is not lost: it will be
    # redelivered on restart.
    return pending


def phase_restart_and_redelivery(connection):
    heading('Phase 2: restart after the crash')
    consumer = build_consumer()
    redelivered = consumer.pending_records()
    print('records delivered again after restart: %s'
          % [r.header.identifier for r in redelivered])
    for record in redelivered:
        identifier = store_record(connection, record)
        consumer.ack(record)
        print('  persisted + acknowledged %s' % identifier)
    consumer.confirm_page()
    print('page 1 confirmed; next token on disk: %s'
          % consumer.checkpoint.next_token)
    consumer.next_page()
    print('page 2 downloaded, requested with token: %s'
          % consumer.checkpoint.request_token)
    return consumer


def phase_duplicate_and_unordered_acks(consumer, connection):
    heading('Phase 3: duplicate and out-of-order acknowledgements')
    records = consumer.pending_records()
    print('delivered on page 2: %s'
          % [r.header.identifier for r in records])
    # Persist and acknowledge the last record first.
    last = records[-1]
    store_record(connection, last)
    print('persisted + ack out of order: %s -> %s'
          % (last.header.identifier, consumer.ack(last)))
    # Acknowledge the same record again: allowed, returns False.
    print('ack the same record again: %s' % consumer.ack(last))
    # Fingerprint strings are accepted as well.
    middle = records[1]
    store_record(connection, middle)
    print('persisted + ack by fingerprint string: %s -> %s'
          % (middle.header.identifier, consumer.ack(middle.fingerprint)))
    first = records[0]
    store_record(connection, first)
    print('persisted + ack: %s -> %s'
          % (first.header.identifier, consumer.ack(first)))
    print('pending on page 2 now: %s'
          % [r.header.identifier for r in consumer.pending_records()])
    consumer.confirm_page()
    consumer.next_page()
    print('page 2 confirmed; page 3 requested with token: %s'
          % consumer.checkpoint.request_token)


def phase_finish_through_empty_page(consumer, connection):
    heading('Phase 4: finish through the empty last page')
    while not consumer.finished:
        records = consumer.pending_records()
        if not records:
            print('empty page %r (no records), confirming it'
                  % consumer.checkpoint.request_token)
        for record in records:
            identifier = store_record(connection, record)
            consumer.ack(record)
            print('  persisted + acknowledged %s' % identifier)
        consumer.confirm_page()
        if not consumer.finished:
            consumer.next_page()
    print('harvest finished: %s' % consumer.finished)


def phase_page_changed_conflict():
    heading('Phase 5: restart against a changed page raises a conflict')
    conflict_dir = os.path.join(WORKDIR, 'conflict')
    checkpoint_path = os.path.join(conflict_dir, 'checkpoint.json')
    stub = OAIStub()
    with mock.patch('sickle.app.Sickle.harvest', stub.harvest):
        consumer = RecordConsumer(
            Sickle(ENDPOINT), CheckpointStore(checkpoint_path),
            metadataPrefix='oai_dc')
        first_page = consumer.pending_records()
        consumer.ack(first_page[0])
        print('acknowledged before the crash: %s'
              % first_page[0].header.identifier)
    # The repository now serves a different page 1 for the same request.
    changed_stub = OAIStub(overrides={None: 'page1_changed.xml'})
    with mock.patch('sickle.app.Sickle.harvest', changed_stub.harvest):
        try:
            RecordConsumer(Sickle(ENDPOINT),
                           CheckpointStore(checkpoint_path),
                           metadataPrefix='oai_dc')
        except CheckpointConflict as error:
            print('restart raised CheckpointConflict:')
            print('  %s' % error)
        else:  # pragma: no cover - defensive, should never happen
            raise AssertionError('expected CheckpointConflict')


def print_summary(connection):
    heading('Summary: what is in the database and the checkpoint')
    rows = connection.execute(
        'SELECT identifier, title, deleted FROM records ORDER BY identifier'
    ).fetchall()
    print('database rows (%d):' % len(rows))
    for identifier, title, deleted in rows:
        suffix = ' [deleted]' if deleted else ': %s' % title
        print('  %s%s' % (identifier, suffix))
    checkpoint = CheckpointStore(CHECKPOINT_PATH).load()
    print('final checkpoint state: %s' % checkpoint.state)
    print('acknowledged fingerprints: %d, page fingerprints: %d'
          % (len(checkpoint.acked_fingerprints),
             len(checkpoint.page_fingerprints)))


def main():
    if os.path.isdir(WORKDIR):
        for name in os.listdir(WORKDIR):
            path = os.path.join(WORKDIR, name)
            if os.path.isfile(path):
                os.unlink(path)
            else:
                for inner in os.listdir(path):
                    os.unlink(os.path.join(path, inner))
    os.makedirs(WORKDIR, exist_ok=True)
    connection = init_database()
    stub = OAIStub()
    with mock.patch('sickle.app.Sickle.harvest', stub.harvest):
        phase_first_run_interrupted(connection)
        consumer = phase_restart_and_redelivery(connection)
        phase_duplicate_and_unordered_acks(consumer, connection)
        phase_finish_through_empty_page(consumer, connection)
    phase_page_changed_conflict()
    print_summary(connection)
    connection.close()
    print('\nrequest log (resumption tokens actually sent): %s'
          % stub.requests)


if __name__ == '__main__':
    main()
