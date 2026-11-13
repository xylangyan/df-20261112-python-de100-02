"""Schema snapshots.

A snapshot captures the complete set of WSDL/XSD documents zeep had to load in
order to build a Client for a given entry-point URL, including every
transitively imported / included document. It can later be used to rebuild the
exact same Client offline, without touching the network.

Per document the snapshot records:

* the URL it was requested from, and the identifier it is stored under,
* the actual source it was served from (i.e. the final URL after redirects),
* a content digest,
* a reference to the document it was reached from (``referrer``) together with
  that document's digest, and
* the kind of reference (``wsdl:import`` / ``xsd:import`` / ``xsd:include``).

Documents are identified by the URL as zeep requests it. Two snapshots can
therefore contain different content for the same URL, and loading one snapshot
never consults the storage of another one.

Usage::

    from zeep.snapshot import client_from_snapshot, snapshot_client

    with snapshot_client("https://example.com/service.wsdl") as client:
        ...
    snapshot_client("https://example.com/service.wsdl", "/tmp/snap")

    client = client_from_snapshot("/tmp/snap")

"""

import hashlib
import json
import logging
import os
import typing
from collections import OrderedDict

from zeep.exceptions import (
    SnapshotChecksumMismatch,
    SnapshotDocumentMissing,
    SnapshotError,
)
from zeep.loader import document_origin

logger = logging.getLogger(__name__)

MANIFEST_VERSION = 1
MANIFEST_NAME = "manifest.json"
DOCUMENTS_NAME = "documents"

__all__ = [
    "Snapshot",
    "SnapshotRecorder",
    "SnapshotOfflineTransport",
    "client_from_snapshot",
    "snapshot_client",
]


def _digest(content: typing.Union[bytes, str]) -> str:
    if isinstance(content, str):
        content = content.encode("utf-8")
    return "sha256:" + hashlib.sha256(content).hexdigest()


class SnapshotRecorder:
    """Transport wrapper that records every document loaded while a Client is
    built.

    All transport calls are delegated to the wrapped transport (``session``);
    the recorder additionally keeps track of the requested URL, the resolved
    source URL, the content and the referring document for every document.
    """

    def __init__(self, session):
        self.session = session
        self.root_location = None
        self._documents = OrderedDict()  # type: typing.OrderedDict[str, dict]
        self._content = {}  # type: typing.Dict[str, bytes]

    def __getattr__(self, name):
        if name in ("session", "_documents", "_content", "root_location"):
            raise AttributeError(name)
        return getattr(self.session, name)

    def record_root(self, url):
        """Mark ``url`` as the entry-point document of the snapshot."""
        self.root_location = url
        if url not in self._documents:
            self._add(url, None, url, None, None, kind="root")

    # -- hooks called from zeep internals --------------------------------

    def load_with_origin(self, url):
        content, resolved_url = self.session.load_with_origin(url)
        referrer, _resolved_referrer = document_origin()
        self._add(url, content, resolved_url, referrer, _resolved_referrer)
        return content, resolved_url

    def record_dependency(self, url, referrer, kind):
        """Record that ``referrer`` references ``url``.

        The content itself is recorded when (and if) it is actually fetched;
        this only registers the relationship, which is also used to produce a
        helpful error when a document is missing from a snapshot.

        """
        if url not in self._documents:
            self._add(url, None, url, referrer, None, kind=kind, edge_only=True)
        else:
            entry = self._documents[url]
            if referrer is not None and referrer not in [
                edge["referrer"] for edge in entry["references"]
            ]:
                entry["references"].append({"referrer": referrer, "kind": kind})
            if entry.get("referrer") is None and referrer is not None:
                entry["referrer"] = referrer
                entry["kind"] = kind

    # -- internals -------------------------------------------------------

    def _add(
        self,
        url,
        content,
        resolved_url,
        referrer,
        referrer_source,
        kind=None,
        edge_only=False,
    ):
        entry = self._documents.get(url)
        if entry is None:
            entry = self._documents[url] = {
                "id": url,
                "url": url,
                "location": resolved_url or url,
                "digest": None,
                "referrer": None,
                "referrer_digest": None,
                "kind": kind or "document",
                "references": [],
            }
        elif resolved_url and not edge_only:
            entry["location"] = resolved_url

        if kind and (entry["kind"] == "document" or edge_only):
            entry["kind"] = kind

        if content is not None:
            entry["digest"] = _digest(content)
            self._content[url] = content
        if referrer is not None and entry["referrer"] is None:
            entry["referrer"] = referrer
            entry["referrer_digest"] = None
            if kind:
                entry["kind"] = kind
        if referrer is not None and not any(
            edge["referrer"] == referrer and edge["kind"] == kind
            for edge in entry["references"]
        ):
            entry["references"].append({"referrer": referrer, "kind": kind})
        return entry

    # -- serialization ---------------------------------------------------

    def manifest(self):
        """Return the snapshot manifest as a dict."""
        loaded = set(self._content)
        documents = []
        for entry in self._documents.values():
            referrer = entry["referrer"]
            referrer_digest = None
            if referrer is not None:
                referrer_entry = self._documents.get(referrer)
                if referrer_entry is not None:
                    referrer_digest = referrer_entry["digest"]
            documents.append(
                {
                    "id": entry["id"],
                    "url": entry["url"],
                    "location": entry["location"],
                    "digest": entry["digest"],
                    "referrer": referrer,
                    "referrer_digest": referrer_digest,
                    "kind": entry["kind"],
                    "references": list(entry["references"]),
                    "stored": entry["id"] in loaded,
                }
            )
        return {
            "version": MANIFEST_VERSION,
            "root": self.root_location,
            "documents": documents,
        }

    def write(self, path):
        """Write the snapshot to a directory.

        The directory contains a ``manifest.json`` and a ``documents/``
        sub-directory with one file per recorded document.

        """
        os.makedirs(path, exist_ok=True)
        documents_dir = os.path.join(path, DOCUMENTS_NAME)
        os.makedirs(documents_dir, exist_ok=True)

        manifest = self.manifest()
        for entry in manifest["documents"]:
            content = self._content.get(entry["id"])
            if content is None:
                # A document which was only referenced but never fetched
                # (e.g. an import with an unreachable/optional location).
                continue
            filename = _filename(entry["id"])
            with open(os.path.join(documents_dir, filename), "wb") as fh:
                fh.write(content)
            entry["file"] = os.path.join(DOCUMENTS_NAME, filename)

        with open(os.path.join(path, MANIFEST_NAME), "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, indent=2, sort_keys=True)
        return manifest


def _filename(document_id: str) -> str:
    return hashlib.sha256(document_id.encode("utf-8")).hexdigest() + ".xml"


class Snapshot:
    """Read access to a snapshot stored in a directory."""

    def __init__(self, path):
        self.path = path
        self._manifest = None
        self._cache = {}

    @property
    def manifest(self):
        if self._manifest is None:
            with open(
                os.path.join(self.path, MANIFEST_NAME), encoding="utf-8"
            ) as fh:
                manifest = json.load(fh)
            if manifest.get("version") != MANIFEST_VERSION:
                raise ValueError(
                    "Unsupported snapshot version %r" % (manifest.get("version"),)
                )
            self._manifest = manifest
        return self._manifest

    @property
    def root(self):
        return self.manifest["root"]

    @property
    def documents(self):
        return {entry["id"]: entry for entry in self.manifest["documents"]}

    def get(self, document_id):
        """Return the recorded content for the given document id.

        Raises :class:`zeep.exceptions.SnapshotDocumentMissing` when the
        document (or its stored content) is not part of the snapshot, and
        :class:`zeep.exceptions.SnapshotChecksumMismatch` when the content no
        longer matches the recorded digest.

        """
        entry = self.documents.get(document_id)
        if entry is None:
            raise self.missing(document_id)
        if document_id in self._cache:
            return self._cache[document_id]

        try:
            with open(os.path.join(self.path, entry["file"]), "rb") as fh:
                content = fh.read()
        except (KeyError, OSError):
            raise self.missing(document_id) from None

        if _digest(content) != entry["digest"]:
            raise SnapshotChecksumMismatch(
                document_id, entry["digest"], _digest(content)
            )
        self._cache[document_id] = content
        return content

    def missing(self, document_id):
        """Return a SnapshotDocumentMissing error for the given document.

        When the document was referenced by another document in the snapshot,
        that referrer is included in the error.

        """
        entry = self.documents.get(document_id)
        referrer = entry.get("referrer") if entry else None
        if referrer is None and entry:
            references = entry.get("references") or []
            if references:
                referrer = references[0]["referrer"]
        if referrer is None:
            for candidate in self.documents.values():
                edges = candidate.get("references") or []
                if any(edge["referrer"] == document_id for edge in edges):
                    referrer = candidate["id"]
                    break
        return SnapshotDocumentMissing(document_id, referrer)


class SnapshotOfflineTransport:
    """Transport that serves documents from a snapshot only.

    It never falls back to the network for schema/WSDL documents: a missing
    document or content that no longer matches the recorded digest results in
    a :class:`zeep.exceptions.SnapshotError`.

    An optional ``call_transport`` can be given to handle actual SOAP calls
    (POST/GET); without it, calls raise :class:`SnapshotOfflineError`.

    """

    binding_classes = None

    def __init__(self, snapshot, call_transport=None):
        if isinstance(snapshot, Snapshot):
            self.snapshot = snapshot
        else:
            self.snapshot = Snapshot(snapshot)
        self.call_transport = call_transport

    def load_with_origin(self, url):
        entry = self.snapshot.documents.get(url)
        if entry is None:
            raise self.snapshot.missing(url)
        content = self.snapshot.get(url)
        return content, entry.get("location") or url

    def load(self, url):
        return self.load_with_origin(url)[0]

    def post(self, address, message, headers):
        if self.call_transport is None:
            raise SnapshotOfflineError(address)
        return self.call_transport.post(address, message, headers)

    def post_xml(self, address, envelope, headers):
        if self.call_transport is None:
            raise SnapshotOfflineError(address)
        return self.call_transport.post_xml(address, envelope, headers)

    def get(self, address, params, headers):
        if self.call_transport is None:
            raise SnapshotOfflineError(address)
        return self.call_transport.get(address, params, headers)


class SnapshotOfflineError(SnapshotError):
    """Raised when an operation (POST/GET) is attempted while running in
    snapshot offline mode.

    Snapshot transports only serve schema/WSDL documents; actual SOAP calls
    must use a different transport.

    """

    def __init__(self, address):
        super().__init__(
            "Cannot perform a service call to %r from an offline snapshot "
            "transport; only WSDL/XSD documents are available." % (address,)
        )
        self.address = address


def snapshot_client(wsdl, output_path, transport=None, settings=None):
    """Build a Client while recording a complete schema snapshot.

    :param wsdl: Entry-point WSDL URL.
    :param output_path: Directory the snapshot is written to.
    :param transport: Optional base transport used to perform the actual
      network requests. Defaults to a new :class:`zeep.Transport`.
    :param settings: Optional zeep settings.
    :returns: The constructed Client (whose transport is the
      :class:`SnapshotRecorder`).

    """
    from zeep.client import Client
    from zeep.transports import Transport

    if hasattr(wsdl, "location"):
        wsdl = wsdl.location

    session = transport or Transport()
    recorder = SnapshotRecorder(session)
    client = Client(wsdl, transport=recorder, settings=settings)
    recorder.write(output_path)
    return client


def client_from_snapshot(path, settings=None, transport=None):
    """Rebuild a Client from a previously written snapshot.

    No network access is performed while parsing the WSDL and its
    imports/includes.

    :param path: Snapshot directory.
    :param settings: Optional zeep settings.
    :param transport: Optional transport used for actual service calls. It is
      not used for WSDL/XSD loading (the snapshot is).
    :returns: A Client built from the snapshot.

    """
    from zeep.client import Client

    snapshot = Snapshot(path)
    offline_transport = SnapshotOfflineTransport(snapshot, call_transport=transport)
    return Client(
        snapshot.root,
        transport=offline_transport,
        settings=settings,
    )


