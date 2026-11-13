Schema snapshots
================
A WSDL frequently imports or includes other WSDL/XSD documents, and those
documents can import or include others again. To replay a request later with the
exact same schema, saving only the main WSDL is not enough.

A *schema snapshot* records the complete set of documents zeep loaded while
building a :class:`zeep.Client`, so the same client can be rebuilt offline
afterwards.

Creating a snapshot
-------------------
.. code-block:: python

    from zeep.snapshot import snapshot_client

    snapshot_client(
        "https://example.com/service?wsdl",
        "/var/lib/zeep/snapshots/service",
    )

``snapshot_client`` builds a normal client (with a :class:`zeep.snapshot.
SnapshotRecorder` transport) and writes the snapshot directory before
returning.

Using a snapshot offline
------------------------
.. code-block:: python

    from zeep.snapshot import client_from_snapshot

    client = client_from_snapshot("/var/lib/zeep/snapshots/service")
    envelope = client.create_message(client.service, "Operation", ...)

The WSDL and every transitive import is served from the snapshot; no network
access is performed while loading them. Actual SOAP calls raise
:class:`zeep.snapshot.SnapshotOfflineError` unless a call transport is passed
to ``client_from_snapshot(..., transport=...)``.

Missing documents and modified content
--------------------------------------
Offline loading never falls back to the network. It fails when:

* a document referenced by another document is absent from the snapshot
  (:class:`zeep.exceptions.SnapshotDocumentMissing`; the message names the
  document that referenced it),
* stored content no longer matches the digest recorded in the manifest
  (:class:`zeep.exceptions.SnapshotChecksumMismatch`).

Snapshot format
---------------
The directory contains ``manifest.json`` and a ``documents/`` sub-directory.
For every document the manifest records:

``id``
    The URL as zeep requested it; this is also the lookup key.
``url`` / ``location``
    The requested URL and the actual source it was served from (i.e. the final
    URL after redirects).
``digest``
    SHA-256 digest of the content.
``referrer`` / ``referrer_digest`` / ``kind``
    The document this one was reached from, the digest of that document and the
    kind of reference (``wsdl:import``, ``xsd:import`` or ``xsd:include``).
``references``
    All known reference edges pointing at this document.

Documents are identified by their requested URL, so two snapshots may contain
different content for the same URL. Loading a snapshot only reads its own
directory; it never uses the storage or cache of another snapshot.
