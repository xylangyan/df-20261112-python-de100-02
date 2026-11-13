from io import StringIO

import pytest
import requests_mock
from lxml import etree
from pretend import stub
from requests_toolbelt.multipart.decoder import BodyPart, MultipartDecoder

from tests.utils import assert_nodes_equal
from zeep import Client
from zeep.transports import Transport
from zeep.wsdl.attachments import MessagePack
from zeep.wsdl.messages import xop


XOP_NS = "http://www.w3.org/2004/08/xop/include"


def make_body_part(content_id, content=b"BINARYDATA"):
    return BodyPart(
        b"\r\n".join(
            [
                b"Content-Type: application/octet-stream",
                b"Content-Transfer-Encoding: binary",
                b"Content-ID: " + content_id,
                b"",
                content,
            ]
        ),
        "utf-8",
    )


def make_two_reference_document():
    return etree.fromstring(
        """
        <soap:Envelope
            xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"
            xmlns:xop="http://www.w3.org/2004/08/xop/include"
            xmlns:test="http://tests.python-zeep.org/xsd-main">
            <soap:Body>
                <test:resultSimple>
                    <test:BinaryData>
                        <xop:Include href="cid:first"/>
                        <xop:Include href="cid:second"/>
                    </test:BinaryData>
                </test:resultSimple>
            </soap:Body>
        </soap:Envelope>
        """
    )


def include_nodes(document):
    return document.xpath("//xop:Include", namespaces={"xop": XOP_NS})


def test_rebuild_xml():
    data = "\r\n".join(
        line.strip()
        for line in """
        --MIME_boundary
        Content-Type: application/soap+xml; charset=UTF-8
        Content-Transfer-Encoding: 8bit
        Content-ID: <claim@insurance.com>

        <soap:Envelope
        xmlns:soap="http://www.w3.org/2003/05/soap-envelope"
        xmlns:xop='http://www.w3.org/2004/08/xop/include'
        xmlns:xop-mime='http://www.w3.org/2005/05/xmlmime'>
        <soap:Body>
        <submitClaim>
        <accountNumber>5XJ45-3B2</accountNumber>
        <eventType>accident</eventType>
        <image xop-mime:content-type='image/jpeg'><xop:Include href="cid:image@insurance.com"/></image>
        </submitClaim>
        </soap:Body>
        </soap:Envelope>

        --MIME_boundary
        Content-Type: image/jpeg
        Content-Transfer-Encoding: binary
        Content-ID: <image@insurance.com>

        ...binary JPG image...

        --MIME_boundary--
    """.splitlines()
    ).encode("utf-8")

    response = stub(
        status_code=200,
        content=data,
        encoding=None,
        headers={
            "Content-Type": 'multipart/related; boundary=MIME_boundary; type="application/soap+xml"; start="<claim@insurance.com>" 1'
        },
    )

    decoder = MultipartDecoder(
        response.content, response.headers["Content-Type"], "utf-8"
    )

    document = etree.fromstring(decoder.parts[0].content)
    message_pack = MessagePack(parts=decoder.parts[1:])
    xop.process_xop(document, message_pack)

    expected = """
        <soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope" xmlns:xop="http://www.w3.org/2004/08/xop/include" xmlns:xop-mime="http://www.w3.org/2005/05/xmlmime">
          <soap:Body>
            <submitClaim>
            <accountNumber>5XJ45-3B2</accountNumber>
            <eventType>accident</eventType>
            <image xop-mime:content-type="image/jpeg">Li4uYmluYXJ5IEpQRyBpbWFnZS4uLg==</image>
            </submitClaim>
          </soap:Body>
        </soap:Envelope>
    """
    assert_nodes_equal(etree.tostring(document), expected)


def get_client_service():
    wsdl_main = StringIO(
        """
        <?xml version="1.0"?>
        <wsdl:definitions
          xmlns:wsdl="http://schemas.xmlsoap.org/wsdl/"
          xmlns:xsd="http://www.w3.org/2001/XMLSchema"
          xmlns:tns="http://tests.python-zeep.org/xsd-main"
          xmlns:sec="http://tests.python-zeep.org/wsdl-secondary"
          xmlns:soap="http://schemas.xmlsoap.org/wsdl/soap/"
          xmlns:wsdlsoap="http://schemas.xmlsoap.org/wsdl/soap/"
          targetNamespace="http://tests.python-zeep.org/xsd-main">
          <wsdl:types>
            <xsd:schema
                targetNamespace="http://tests.python-zeep.org/xsd-main"
                xmlns:tns="http://tests.python-zeep.org/xsd-main">
              <xsd:complexType name="responseTypeSimple">
                <xsd:sequence>
                  <xsd:element name="BinaryData" type="xsd:base64Binary"/>
                </xsd:sequence>
              </xsd:complexType>
              <xsd:complexType name="BinaryDataType">
                <xsd:simpleContent>
                  <xsd:extension base="xsd:base64Binary">
                    <xsd:anyAttribute namespace="##other" processContents="lax"/>
                  </xsd:extension>
                </xsd:simpleContent>
              </xsd:complexType>
              <xsd:complexType name="responseTypeComplex">
                <xsd:sequence>
                  <xsd:element name="BinaryData" type="tns:BinaryDataType"/>
                </xsd:sequence>
              </xsd:complexType>
              <xsd:element name="input" type="xsd:string"/>
              <xsd:element name="resultSimple" type="tns:responseTypeSimple"/>
              <xsd:element name="resultComplex" type="tns:responseTypeComplex"/>
            </xsd:schema>
          </wsdl:types>

          <wsdl:message name="dummyRequest">
            <wsdl:part name="response" element="tns:input"/>
          </wsdl:message>
          <wsdl:message name="dummyResponseSimple">
            <wsdl:part name="response" element="tns:resultSimple"/>
          </wsdl:message>
          <wsdl:message name="dummyResponseComplex">
            <wsdl:part name="response" element="tns:resultComplex"/>
          </wsdl:message>

          <wsdl:portType name="TestPortType">
            <wsdl:operation name="TestOperation1">
              <wsdl:input message="dummyRequest"/>
              <wsdl:output message="dummyResponseSimple"/>
            </wsdl:operation>
            <wsdl:operation name="TestOperation2">
              <wsdl:input message="dummyRequest"/>
              <wsdl:output message="dummyResponseComplex"/>
            </wsdl:operation>
          </wsdl:portType>

          <wsdl:binding name="TestBinding" type="tns:TestPortType">
            <soap:binding style="document" transport="http://schemas.xmlsoap.org/soap/http"/>
            <wsdl:operation name="TestOperation1">
              <soap:operation soapAction="urn:dummyRequest"/>
              <wsdl:input>
                <soap:body use="literal"/>
              </wsdl:input>
              <wsdl:output>
                <soap:body use="literal"/>
              </wsdl:output>
            </wsdl:operation>
            <wsdl:operation name="TestOperation2">
              <soap:operation soapAction="urn:dummyRequest"/>
              <wsdl:input>
                <soap:body use="literal"/>
              </wsdl:input>
              <wsdl:output>
                <soap:body use="literal"/>
              </wsdl:output>
            </wsdl:operation>
          </wsdl:binding>
          <wsdl:service name="TestService">
            <wsdl:documentation>Test service</wsdl:documentation>
            <wsdl:port name="TestPortType" binding="tns:TestBinding">
              <soap:address location="http://tests.python-zeep.org/test"/>
            </wsdl:port>
          </wsdl:service>
        </wsdl:definitions>
    """.strip()
    )

    client = Client(wsdl_main, transport=Transport())
    service = client.create_service(
        "{http://tests.python-zeep.org/xsd-main}TestBinding",
        "http://tests.python-zeep.org/test",
    )

    return service


def test_xop():
    service = get_client_service()
    content_type = 'multipart/related; boundary="boundary"; type="application/xop+xml"; start="<soap:Envelope>"; start-info="application/soap+xml; charset=utf-8"'

    response1 = "\r\n".join(
        line.strip()
        for line in """
        Content-Type: application/xop+xml; charset=utf-8; type="application/soap+xml"
        Content-Transfer-Encoding: binary
        Content-ID: <soap:Envelope>

        <?xml version="1.0" encoding="UTF-8"?>
        <soap:Envelope
            xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"
            xmlns:xop="http://www.w3.org/2004/08/xop/include"
            xmlns:test="http://tests.python-zeep.org/xsd-main">
            <soap:Body>
                <test:resultSimple>
                    <test:BinaryData>
                        <xop:Include href="cid:id4"/>
                    </test:BinaryData>
                </test:resultSimple>
            </soap:Body>
        </soap:Envelope>
        --boundary
        Content-Type: application/binary
        Content-Transfer-Encoding: binary
        Content-ID: <id4>

        BINARYDATA
        --boundary--
    """.splitlines()
    )

    response2 = "\r\n".join(
        line.strip()
        for line in """
        Content-Type: application/xop+xml; charset=utf-8; type="application/soap+xml"
        Content-Transfer-Encoding: binary
        Content-ID: <soap:Envelope>

        <?xml version="1.0" encoding="UTF-8"?>
        <soap:Envelope
            xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"
            xmlns:xop="http://www.w3.org/2004/08/xop/include"
            xmlns:test="http://tests.python-zeep.org/xsd-main">
            <soap:Body>
                <test:resultComplex>
                    <test:BinaryData>
                        <xop:Include href="cid:id4"/>
                    </test:BinaryData>
                </test:resultComplex>
            </soap:Body>
        </soap:Envelope>
        --boundary
        Content-Type: application/binary
        Content-Transfer-Encoding: binary
        Content-ID: <id4>

        BINARYDATA

        --boundary--
    """.splitlines()
    )

    print(response1)
    with requests_mock.mock() as m:
        m.post(
            "http://tests.python-zeep.org/test",
            content=response2.encode("utf-8"),
            headers={"Content-Type": content_type},
        )
        result = service.TestOperation2("")
        assert result["_value_1"] == b"BINARYDATA"

        m.post(
            "http://tests.python-zeep.org/test",
            content=response1.encode("utf-8"),
            headers={"Content-Type": content_type},
        )
        result = service.TestOperation1("")
        assert result == b"BINARYDATA"


def test_xop_cid_encoded():
    service = get_client_service()
    content_type = 'multipart/related; boundary="boundary"; type="application/xop+xml"; start="<soap:Envelope>"; start-info="application/soap+xml; charset=utf-8"'

    response_encoded_cid = "\r\n".join(
        line.strip()
        for line in """
            Content-Type: application/xop+xml; charset=utf-8; type="application/soap+xml"
            Content-Transfer-Encoding: binary
            Content-ID: <soap:Envelope>

            <?xml version="1.0" encoding="UTF-8"?>
            <soap:Envelope
                xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"
                xmlns:xop="http://www.w3.org/2004/08/xop/include"
                xmlns:test="http://tests.python-zeep.org/xsd-main">
                <soap:Body>
                    <test:resultComplex>
                        <test:BinaryData>
                            <xop:Include href="cid:test_encoding%20cid%25%24%7D%40"/>
                        </test:BinaryData>
                    </test:resultComplex>
                </soap:Body>
            </soap:Envelope>
            --boundary
            Content-Type: application/binary
            Content-Transfer-Encoding: binary
            Content-ID: <test_encoding cid%$}@>

            BINARYDATA

            --boundary--
        """.splitlines()
    )

    with requests_mock.mock() as m:
        m.post(
            "http://tests.python-zeep.org/test",
            content=response_encoded_cid.encode("utf-8"),
            headers={"Content-Type": content_type},
        )
        result = service.TestOperation2("")
        assert result["_value_1"] == b"BINARYDATA"


def test_xop_retry_after_missing_attachment():
    """First reference resolves, second is missing: the failure must not
    leave a half-expanded tree behind, and retrying on the same document
    with the completed message pack must give the full result."""
    service = get_client_service()
    operation = service._binding.get("TestOperation1")

    document = make_two_reference_document()
    snapshot = etree.tostring(document)
    message_pack = MessagePack(parts=[make_body_part(b"<first>", b"FIRST")])

    with pytest.raises(xop.XopError, match="No part found for: 'cid:second'"):
        xop.process_xop(document, message_pack)

    # The failed attempt left no half-expanded state behind.
    assert etree.tostring(document) == snapshot
    assert len(include_nodes(document)) == 2
    assert len(message_pack.attachments) == 1
    assert message_pack.attachments[0].content_id == "<first>"
    assert message_pack.attachments[0].content == b"FIRST"
    # The original MessagePack interface is untouched.
    assert message_pack.get_by_content_id("<first>").content == b"FIRST"
    assert message_pack.get_by_content_id("<second>") is None

    # Retry on the same document once the missing attachment is available.
    completed_pack = MessagePack(
        parts=[
            make_body_part(b"<first>", b"FIRST"),
            make_body_part(b"<second>", b"SECOND"),
        ]
    )
    assert xop.process_xop(document, completed_pack)

    expected = """
        <soap:Envelope
            xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"
            xmlns:xop="http://www.w3.org/2004/08/xop/include"
            xmlns:test="http://tests.python-zeep.org/xsd-main">
            <soap:Body>
                <test:resultSimple>
                    <test:BinaryData>U0VDT05E</test:BinaryData>
                </test:resultSimple>
            </soap:Body>
        </soap:Envelope>
    """
    assert_nodes_equal(etree.tostring(document), expected)
    assert len(include_nodes(document)) == 0
    assert len(completed_pack.attachments) == 2
    assert operation.process_reply(document) == b"SECOND"


def test_xop_missing_reference_keeps_message_untouched():
    document = make_two_reference_document()
    snapshot = etree.tostring(document)
    message_pack = MessagePack(parts=[make_body_part(b"<first>", b"FIRST")])

    with pytest.raises(xop.XopError, match="No part found for: 'cid:second'"):
        xop.process_xop(document, message_pack)

    assert etree.tostring(document) == snapshot
    assert len(include_nodes(document)) == 2


def test_xop_ambiguous_content_id_keeps_message_untouched():
    document = make_two_reference_document()
    snapshot = etree.tostring(document)
    message_pack = MessagePack(
        parts=[
            make_body_part(b"<first>", b"ONE"),
            make_body_part(b"<first>", b"TWO"),
            make_body_part(b"<second>", b"SECOND"),
        ]
    )

    with pytest.raises(xop.XopError, match="Ambiguous Content-ID <first>"):
        xop.process_xop(document, message_pack)

    assert etree.tostring(document) == snapshot
    assert len(include_nodes(document)) == 2
    assert len(message_pack.attachments) == 3


def test_xop_invalid_href_keeps_message_untouched():
    message_pack = MessagePack(parts=[make_body_part(b"<first>", b"FIRST")])
    invalid_hrefs = (
        "http://example.com/part",
        "cid:bad%2",
        "cid:bad%zz",
    )

    for href in invalid_hrefs:
        document = make_two_reference_document()
        include_nodes(document)[0].set("href", href)
        snapshot = etree.tostring(document)

        with pytest.raises(xop.XopError):
            xop.process_xop(document, message_pack)

        assert etree.tostring(document) == snapshot
        assert len(include_nodes(document)) == 2

    document = make_two_reference_document()
    del include_nodes(document)[0].attrib["href"]
    snapshot = etree.tostring(document)
    with pytest.raises(xop.XopError):
        xop.process_xop(document, message_pack)
    assert etree.tostring(document) == snapshot
    assert len(include_nodes(document)) == 2


def test_xop_shared_attachment_and_empty_content():
    """One attachment may be referenced multiple times and an empty part
    still counts as an existing attachment."""
    service = get_client_service()
    operation = service._binding.get("TestOperation1")

    document = etree.fromstring(
        """
        <soap:Envelope
            xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"
            xmlns:xop="http://www.w3.org/2004/08/xop/include"
            xmlns:test="http://tests.python-zeep.org/xsd-main">
            <soap:Body>
                <test:resultSimple>
                    <test:BinaryData>
                        <xop:Include href="cid:shared"/>
                        <xop:Include href="cid:shared"/>
                    </test:BinaryData>
                </test:resultSimple>
            </soap:Body>
        </soap:Envelope>
        """
    )
    message_pack = MessagePack(parts=[make_body_part(b"<shared>", b"")])

    assert xop.process_xop(document, message_pack)
    assert len(include_nodes(document)) == 0
    assert operation.process_reply(document) == b""
