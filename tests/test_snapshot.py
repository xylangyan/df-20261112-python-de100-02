"""Schema snapshot tests.

These tests use an in-process HTTP stand-in (tests/snapshot_files/server.py)
which serves a set of WSDL/XSD documents that import/include each other,
including relative locations, a redirect, circular imports and multiple
schemas in the same target namespace.

"""

import json
import os

import pytest
from lxml import etree
from pretend import stub

from tests.snapshot_files.server import WsdlStandIn
from zeep.exceptions import SnapshotChecksumMismatch, SnapshotDocumentMissing
from zeep.snapshot import (
    Snapshot,
    SnapshotOfflineError,
    SnapshotOfflineTransport,
    SnapshotRecorder,
    client_from_snapshot,
    snapshot_client,
)

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "snapshot_files")


def _fixture(name):
    with open(os.path.join(FIXTURE_DIR, name), encoding="utf-8") as fh:
        return fh.read(), "text/xml"


def _routes(common_content=None):
    common = common_content or _fixture("common.xsd")[0]
    return {
        "/wsdl/main.wsdl": _fixture("main.wsdl"),
        "/wsdl/binding.wsdl": _fixture("binding.wsdl"),
        "/wsdl/types/common.xsd": (common, "text/xml"),
        "/wsdl/types/address-inc.xsd": _fixture("address-inc.xsd"),
        "/wsdl/types/address-detail.xsd": _fixture("address-detail.xsd"),
        "/wsdl/types/types-extra.xsd": _fixture("types-extra.xsd"),
        "/wsdl/types/cycle-a.xsd": _fixture("cycle-a.xsd"),
        "/wsdl/types/cycle-b.xsd": _fixture("cycle-b.xsd"),
        "/wsdl/redirect/redirected.xsd": ("redirect", "/target/redirected.xsd"),
        "/target/redirected.xsd": _fixture("redirected.xsd"),
    }


@pytest.fixture
def server():
    stand_in = WsdlStandIn(_routes())
    stand_in.start()
    yield stand_in
    stand_in.stop()


@pytest.fixture
def snapshot_path(tmp_path, server):
    path = str(tmp_path / "snapshot")
    snapshot_client(server.base_url + "/wsdl/main.wsdl", path)
    return path


@pytest.mark.requests
def test_snapshot_records_complete_dependency_set(server, snapshot_path):
    manifest = Snapshot(snapshot_path).manifest
    names = {entry["id"].rsplit("/", 1)[1] for entry in manifest["documents"]}
    assert names == {
        "main.wsdl",
        "binding.wsdl",
        "common.xsd",
        "address-inc.xsd",
        "address-detail.xsd",
        "types-extra.xsd",
        "cycle-a.xsd",
        "cycle-b.xsd",
        "redirected.xsd",
    }

    documents = {entry["id"].rsplit("/", 1)[1]: entry for entry in manifest["documents"]}

    # The entry point is recorded as the root.
    assert manifest["root"].endswith("/wsdl/main.wsdl")
    assert documents["main.wsdl"]["kind"] == "root"

    # Relative locations are resolved against the importing document.
    assert documents["binding.wsdl"]["referrer"].endswith("/wsdl/main.wsdl")
    assert documents["common.xsd"]["referrer"].endswith("/wsdl/main.wsdl")

    # Include dependency points at the document containing the statement.
    detail = documents["address-detail.xsd"]
    assert detail["kind"] == "xsd:include"
    assert detail["referrer"].endswith("/wsdl/types/address-inc.xsd")

    # Redirected document keeps both requested URL (id) and actual source.
    redirected = documents["redirected.xsd"]
    assert redirected["id"].endswith("/wsdl/redirect/redirected.xsd")
    assert redirected["location"].endswith("/target/redirected.xsd")
    assert redirected["id"] != redirected["location"]

    # Every document has a content digest and a stored content file.
    stored = os.listdir(os.path.join(snapshot_path, "documents"))
    for entry in manifest["documents"]:
        assert entry["digest"].startswith("sha256:")
        assert os.path.basename(entry["file"]) in stored
        assert entry["stored"] is True

    # Dependency graph is present per document.
    refs = {
        (e["id"].rsplit("/", 1)[1], r["referrer"].rsplit("/", 1)[1], r["kind"])
        for e in manifest["documents"]
        for r in e["references"]
    }
    assert ("binding.wsdl", "main.wsdl", "wsdl:import") in refs
    assert ("address-detail.xsd", "address-inc.xsd", "xsd:include") in refs
    assert ("cycle-b.xsd", "cycle-a.xsd", "xsd:import") in refs


@pytest.mark.requests
def test_snapshot_handles_circular_imports_and_same_namespace(server, snapshot_path):
    manifest = Snapshot(snapshot_path).manifest

    # Two independent schema documents share the types target namespace.
    types_docs = [
        entry
        for entry in manifest["documents"]
        if entry["id"].rsplit("/", 1)[1]
        in ("address-inc.xsd", "types-extra.xsd")
    ]
    assert len(types_docs) == 2
    assert all(entry["digest"] for entry in types_docs)

    # Circular import (cycle-a <-> cycle-b) terminates and stores both.
    names = {entry["id"].rsplit("/", 1)[1] for entry in manifest["documents"]}
    assert {"cycle-a.xsd", "cycle-b.xsd"} <= names

    # The rebuilt offline client resolves types across the full graph.
    client = client_from_snapshot(snapshot_path)
    cycle_a = client.get_type("{http://example.com/snapshot/cycle-a}CycleA")
    cycle_b = client.get_type("{http://example.com/snapshot/cycle-b}CycleB")
    assert cycle_a is not None
    assert cycle_b is not None


@pytest.mark.requests
def test_offline_client_builds_requests_and_parses_responses(snapshot_path):
    # The server is stopped before the offline client is used; any network
    # access would raise.
    client = client_from_snapshot(snapshot_path)

    assert list(client.wsdl.services) == ["SnapshotService"]

    envelope = client.create_message(
        client.service,
        "Echo",
        message="hello",
        address={"street": "Main Street"},
    )
    body = envelope.find("{http://schemas.xmlsoap.org/soap/envelope/}Body")
    assert body is not None
    request = body[0]
    assert etree.QName(request).localname == "EchoRequest"
    assert request.findtext(
        "{http://example.com/snapshot/types}message"
    ) == "hello"
    assert request.find(
        "{http://example.com/snapshot/types}address/{http://example.com/snapshot/types}street"
    ).text == "Main Street"

    response = """
        <soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"
                          xmlns:ns="http://example.com/snapshot/types"
                          xmlns:cm="http://example.com/snapshot/common">
          <soapenv:Body>
            <ns:EchoResponse>
              <ns:result>echo: hello</ns:result>
              <ns:common>
                <cm:code>42</cm:code>
              </ns:common>
            </ns:EchoResponse>
          </soapenv:Body>
        </soapenv:Envelope>
    """.strip()

    operation = client.service._binding.get("Echo")
    result = client.service._binding.process_reply(
        client,
        operation,
        stub(status_code=200, content=response, headers={}, encoding="utf-8"),
    )
    assert result["result"] == "echo: hello"
    assert result["common"]["code"] == "42"


@pytest.mark.requests
def test_offline_client_rejects_service_calls(snapshot_path):
    client = client_from_snapshot(snapshot_path)
    with pytest.raises(SnapshotOfflineError):
        client.service.Echo(message="hi")


@pytest.mark.requests
def test_missing_dependency_names_its_referrer(server, snapshot_path):
    snapshot = Snapshot(snapshot_path)
    detail_url = next(
        entry["id"]
        for entry in snapshot.manifest["documents"]
        if entry["id"].endswith("/wsdl/types/address-detail.xsd")
    )
    os.remove(os.path.join(snapshot_path, snapshot.documents[detail_url]["file"]))

    with pytest.raises(SnapshotDocumentMissing) as exc_info:
        client_from_snapshot(snapshot_path)

    error = exc_info.value
    assert error.url.endswith("/wsdl/types/address-detail.xsd")
    assert error.referrer.endswith("/wsdl/types/address-inc.xsd")
    assert "address-inc.xsd" in str(error)
    assert "network" in str(error)


@pytest.mark.requests
def test_checksum_mismatch_fails(server, snapshot_path):
    snapshot = Snapshot(snapshot_path)
    common_url = next(
        entry["id"]
        for entry in snapshot.manifest["documents"]
        if entry["id"].endswith("/wsdl/types/common.xsd")
    )
    entry = snapshot.documents[common_url]
    with open(os.path.join(snapshot_path, entry["file"]), "wb") as fh:
        fh.write(b"<tampered/>")

    with pytest.raises(SnapshotChecksumMismatch) as exc_info:
        client_from_snapshot(snapshot_path)
    assert exc_info.value.url == common_url
    assert exc_info.value.expected.startswith("sha256:")


@pytest.mark.requests
def test_snapshots_with_same_url_are_isolated(server, tmp_path):
    first_path = str(tmp_path / "first")
    second_path = str(tmp_path / "second")

    snapshot_client(server.base_url + "/wsdl/main.wsdl", first_path)

    common_v2 = _fixture("common.xsd")[0] + "\n<!-- changed content -->\n"
    v2_server = WsdlStandIn(_routes(common_content=common_v2))
    with v2_server:
        snapshot_client(v2_server.base_url + "/wsdl/main.wsdl", second_path)

    first = Snapshot(first_path)
    second = Snapshot(second_path)

    first_common = next(
        entry for entry in first.manifest["documents"]
        if entry["id"].endswith("/wsdl/types/common.xsd")
    )
    second_common = next(
        entry for entry in second.manifest["documents"]
        if entry["id"].endswith("/wsdl/types/common.xsd")
    )
    assert first_common["digest"] != second_common["digest"]

    # Each snapshot serves its own content; they don't share storage.
    assert first.get(first_common["id"]) != second.get(second_common["id"])

    # Loading the first snapshot never consults the second one's files.
    client = client_from_snapshot(first_path)
    assert client.get_type("{http://example.com/snapshot/common}CommonType")


@pytest.mark.requests
def test_offline_transport_never_falls_back_to_network(server, snapshot_path, monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("network access attempted in offline mode")

    monkeypatch.setattr("socket.socket", fail)

    # client_from_snapshot() must succeed with all socket usage disabled.
    client = client_from_snapshot(snapshot_path)
    assert client.wsdl.location == Snapshot(snapshot_path).root

    transport = SnapshotOfflineTransport(snapshot_path)
    with pytest.raises(SnapshotDocumentMissing):
        transport.load("http://unknown.invalid/missing.wsdl")


@pytest.mark.requests
def test_recorder_delegates_transport_operations(server, snapshot_path):
    from zeep.transports import Transport

    recorder = SnapshotRecorder(Transport())
    # Non-snapshot attributes/methods are delegated to the wrapped transport.
    assert recorder.load_timeout == 300
    assert hasattr(recorder, "post")


@pytest.mark.requests
def test_snapshot_manifest_is_json_serializable(server, snapshot_path):
    with open(os.path.join(snapshot_path, "manifest.json"), encoding="utf-8") as fh:
        manifest = json.load(fh)
    assert manifest["version"] == 1
    assert manifest["root"]
    assert manifest["documents"]
