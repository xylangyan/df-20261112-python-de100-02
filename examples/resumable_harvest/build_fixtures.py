# coding: utf-8
"""Generate the OAI-PMH XML fixtures used by the local response stand-in.

The fixtures follow the same layout as the sample data shipped with sickle
(``sickle/tests/sample_data/ListRecords*.xml``): each page is an
``OAI-PMH`` document whose ``resumptionToken`` names the fixture of the next
page. The last page has no token; an additional empty page demonstrates the
"empty last page" situation.

Run from the repository root::

    .venv/bin/python -B examples/resumable_harvest/build_fixtures.py
"""

import os

from lxml import etree

OAI_NS = 'http://www.openarchives.org/OAI/2.0/'
OAI_DC_NS = 'http://www.openarchives.org/OAI/2.0/oai_dc/'
DC_NS = 'http://purl.org/dc/elements/1.1/'
XSI_NS = 'http://www.w3.org/2001/XMLSchema-instance'

NSMAP = {None: OAI_NS, 'xsi': XSI_NS, 'oai_dc': OAI_DC_NS, 'dc': DC_NS}

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
FIXTURE_DIR = os.path.join(THIS_DIR, 'fixtures')

ENDPOINT = 'http://localhost:8000/oai'
COMPLETE_LIST_SIZE = 7


def build_record(number, deleted=False):
    identifier = 'oai:demo:%04d' % number
    record = etree.Element('{%s}record' % OAI_NS, nsmap=NSMAP)
    header = etree.SubElement(record, '{%s}header' % OAI_NS)
    if deleted:
        header.set('status', 'deleted')
    etree.SubElement(header, '{%s}identifier' % OAI_NS).text = identifier
    etree.SubElement(header, '{%s}datestamp' % OAI_NS).text = \
        '2026-11-12T08:00:%02dZ' % number
    if not deleted:
        metadata = etree.SubElement(record, '{%s}metadata' % OAI_NS)
        dc = etree.SubElement(metadata, '{%s}dc' % OAI_DC_NS)
        etree.SubElement(dc, '{%s}title' % DC_NS).text = \
            'Demo publication %d' % number
        etree.SubElement(dc, '{%s}identifier' % DC_NS).text = \
            'https://example.org/demo/%d' % number
        etree.SubElement(dc, '{%s}creator' % DC_NS).text = 'Demo Author'
    return record


def build_page(records, next_token=None, cursor=0):
    root = etree.Element('{%s}OAI-PMH' % OAI_NS, nsmap=NSMAP)
    etree.SubElement(root, '{%s}responseDate' % OAI_NS).text = \
        '2026-11-12T09:00:00Z'
    request = etree.SubElement(root, '{%s}request' % OAI_NS,
                               metadataPrefix='oai_dc', verb='ListRecords')
    request.text = ENDPOINT
    list_records = etree.SubElement(root, '{%s}ListRecords' % OAI_NS)
    for record in records:
        list_records.append(record)
    if next_token is not None:
        token = etree.SubElement(
            list_records, '{%s}resumptionToken' % OAI_NS,
            completeListSize=str(COMPLETE_LIST_SIZE), cursor=str(cursor))
        token.text = next_token
    return etree.tostring(root, xml_declaration=True,
                          encoding='UTF-8', pretty_print=True)


def build_changed_page():
    """Page 1 with a different record in the middle.

    Used to demonstrate the conflict detection when the content of an
    already-downloaded page changes between runs.
    """
    first = build_record(1)
    replacement = build_record(99)
    third = build_record(3)
    return build_page([first, replacement, third],
                      next_token='demo-page-2', cursor=0)


def write_fixture(name, content):
    path = os.path.join(FIXTURE_DIR, name)
    with open(path, 'wb') as fp:
        fp.write(content)
    print('wrote', os.path.relpath(path))


def main():
    if not os.path.isdir(FIXTURE_DIR):
        os.makedirs(FIXTURE_DIR)
    write_fixture('page1.xml', build_page(
        [build_record(1), build_record(2, deleted=True), build_record(3)],
        next_token='demo-page-2', cursor=0))
    write_fixture('page2.xml', build_page(
        [build_record(4), build_record(5), build_record(6)],
        next_token='demo-page-3', cursor=3))
    write_fixture('page3.xml', build_page(
        [build_record(7)], next_token='demo-page-4', cursor=6))
    write_fixture('page4.xml', build_page([], next_token=None, cursor=7))
    write_fixture('page1_changed.xml', build_changed_page())


if __name__ == '__main__':
    main()
