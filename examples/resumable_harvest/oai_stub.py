# coding: utf-8
"""Local OAI-PMH response stand-in used by the demo and the tests.

It serves OAI-PMH pages built from the XML fixtures in ``fixtures/`` and
keeps a request log so callers can inspect which resumption tokens were
actually requested. No network access is involved: the stand-in is plugged
into :meth:`sickle.app.Sickle.harvest` via ``mock.patch``.
"""

import os

from sickle.response import OAIResponse

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
FIXTURE_DIR = os.path.join(THIS_DIR, 'fixtures')

OAI_NS = 'http://www.openarchives.org/OAI/2.0/'

#: Identifiers delivered by the default fixture pages, in page order.
PAGE_1_IDS = ['oai:demo:0001', 'oai:demo:0002', 'oai:demo:0003']
PAGE_2_IDS = ['oai:demo:0004', 'oai:demo:0005', 'oai:demo:0006']
PAGE_3_IDS = ['oai:demo:0007']
PAGE_4_IDS = []  # empty last page

DEFAULT_PAGES = {
    None: 'page1.xml',
    'demo-page-2': 'page2.xml',
    'demo-page-3': 'page3.xml',
    'demo-page-4': 'page4.xml',
}


class StubResponse(object):
    """Mimics the parts of a ``requests`` response sickle relies on."""

    def __init__(self, text):
        self.text = text
        self.content = text.encode('utf-8')


class OAIStub(object):
    """Serves fixture pages for ``ListRecords`` requests.

    :param pages: Mapping of resumption token (``None`` for the first
                  request) to a fixture file name in ``fixtures/``.
    :param overrides: Mapping of resumption token to replacement fixture
                      file names, applied on top of ``pages``. Used to
                      simulate a repository whose page content changed
                      between two runs.
    """

    def __init__(self, pages=None, overrides=None):
        self.pages = dict(pages or DEFAULT_PAGES)
        if overrides:
            self.pages.update(overrides)
        self.requests = []

    def harvest(self, *args, **kwargs):
        verb = kwargs.get('verb')
        if verb != 'ListRecords':
            raise ValueError('The stub only implements ListRecords, '
                             'got %r' % verb)
        token = kwargs.get('resumptionToken')
        self.requests.append(token)
        filename = self.pages.get(token)
        if filename is None:
            raise ValueError('Unknown resumptionToken %r' % token)
        with open(os.path.join(FIXTURE_DIR, filename), 'r') as fp:
            return OAIResponse(StubResponse(fp.read()), kwargs)
