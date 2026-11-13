import contextvars
import os.path
import typing
from urllib.parse import urljoin, urlparse, urlunparse

from lxml import etree
from lxml.etree import Resolver, XMLParser, fromstring

from zeep.exceptions import (
    DTDForbidden,
    EntitiesForbidden,
    ExternalReferenceForbidden,
    XMLSyntaxError,
)
from zeep.settings import Settings

# Tracks the origin (requested URL and post-redirect resolved URL) of the
# document currently being parsed. Set by load_external() and by the lxml
# ImportResolver so that transports such as zeep.snapshot.SnapshotRecorder can
# attribute transitive imports to the document which referenced them.
_document_origin = contextvars.ContextVar("zeep_document_origin", default=None)
_document_resolved_url = contextvars.ContextVar(
    "zeep_document_resolved_url", default=None
)


def document_origin():
    """Return (requested_url, resolved_url) of the document currently being
    parsed, or (None, None) outside of a load_external() call.

    The resolved url is the actual source of the content (e.g. the final URL
    after redirects); it falls back to the requested url when the transport
    does not expose a resolved location.

    """
    return _document_origin.get(), _document_resolved_url.get()


class ImportResolver(Resolver):
    """Custom lxml resolve to use the transport object"""

    def __init__(self, transport, settings=None):
        self.transport = transport
        self.settings = settings or Settings()

    def resolve(self, url, pubid, context):
        if urlparse(url).scheme in ("http", "https"):
            if self.settings.forbid_external:
                raise ExternalReferenceForbidden(url)
            content, resolved_url = _load_document(self.transport, url)
            token_origin = _document_origin.set(url)
            token_resolved = _document_resolved_url.set(resolved_url)
            try:
                return self.resolve_string(content, context)
            finally:
                _document_origin.reset(token_origin)
                _document_resolved_url.reset(token_resolved)


def _load_document(transport, url):
    """Load a document and return (content, resolved_url).

    Uses transport.load_with_origin() when the transport supports it so that
    the post-redirect URL is known; otherwise falls back to transport.load().

    """
    load_with_origin = getattr(transport, "load_with_origin", None)
    if load_with_origin is not None:
        return load_with_origin(url)
    return transport.load(url), url


def parse_xml(content: str, transport, base_url=None, settings=None):
    """Parse an XML string and return the root Element.

    :param content: The XML string
    :type content: str
    :param transport: The transport instance to load imported documents
    :type transport: zeep.transports.Transport
    :param base_url: The base url of the document, used to make relative
      lookups absolute.
    :type base_url: str
    :param settings: A zeep.settings.Settings object containing parse settings.
    :type settings: zeep.settings.Settings
    :returns: The document root
    :rtype: lxml.etree._Element

    """
    settings = settings or Settings()
    recover = not settings.strict
    parser = XMLParser(
        remove_comments=True,
        resolve_entities=False,
        recover=recover,
        huge_tree=settings.xml_huge_tree,
    )
    parser.resolvers.add(ImportResolver(transport, settings))
    try:
        elementtree = fromstring(content, parser=parser, base_url=base_url)
        docinfo = elementtree.getroottree().docinfo
        if docinfo.doctype:
            if settings.forbid_dtd:
                raise DTDForbidden(
                    docinfo.doctype, docinfo.system_url, docinfo.public_id
                )
        if settings.forbid_entities:
            for dtd in docinfo.internalDTD, docinfo.externalDTD:
                if dtd is None:
                    continue
                for entity in dtd.iterentities():
                    raise EntitiesForbidden(entity.name, entity.content)

        return elementtree
    except etree.XMLSyntaxError as exc:
        raise XMLSyntaxError(
            "Invalid XML content received (%s)" % exc.msg, content=content
        )


def load_external(
    url: typing.Union[typing.IO, str],
    transport,
    base_url=None,
    settings=None,
    *,
    _initial: bool = False,
):
    """Load an external XML document.

    :param url:
    :param transport:
    :param base_url:
    :param settings: A zeep.settings.Settings object containing parse settings.
    :type settings: zeep.settings.Settings
    :param _initial: Internal flag set by zeep when loading the user-supplied
      entry-point document; transitive imports leave it False so that
      ``settings.forbid_external`` can block them.

    """
    settings = settings or Settings()
    resolved_url = None
    if hasattr(url, "read"):
        content = url.read()
    else:
        if base_url:
            url = absolute_location(url, base_url)
        if not _initial and settings.forbid_external:
            if urlparse(str(url)).scheme in ("http", "https"):
                raise ExternalReferenceForbidden(url)
        content, resolved_url = _load_document(transport, url)
    token_origin = _document_origin.set(url if not hasattr(url, "read") else None)
    token_resolved = _document_resolved_url.set(resolved_url)
    try:
        return parse_xml(content, transport, base_url, settings=settings)
    finally:
        _document_origin.reset(token_origin)
        _document_resolved_url.reset(token_resolved)


async def load_external_async(
    url: typing.IO,
    transport,
    base_url=None,
    settings=None,
    *,
    _initial: bool = False,
):
    """Load an external XML document.

    :param url:
    :param transport:
    :param base_url:
    :param settings: A zeep.settings.Settings object containing parse settings.
    :type settings: zeep.settings.Settings
    :param _initial: Internal flag set by zeep when loading the user-supplied
      entry-point document; transitive imports leave it False so that
      ``settings.forbid_external`` can block them.

    """
    settings = settings or Settings()
    resolved_url = None
    if hasattr(url, "read"):
        content = url.read()
    else:
        if base_url:
            url = absolute_location(url, base_url)
        if not _initial and settings.forbid_external:
            if urlparse(str(url)).scheme in ("http", "https"):
                raise ExternalReferenceForbidden(url)
        load_with_origin = getattr(transport, "load_with_origin", None)
        if load_with_origin is not None:
            content, resolved_url = await load_with_origin(url)
        else:
            content = await transport.load(url)
            resolved_url = url
    token_origin = _document_origin.set(url if not hasattr(url, "read") else None)
    token_resolved = _document_resolved_url.set(resolved_url)
    try:
        return parse_xml(content, transport, base_url, settings=settings)
    finally:
        _document_origin.reset(token_origin)
        _document_resolved_url.reset(token_resolved)


def normalize_location(settings, url, base_url):
    """Return a 'normalized' url for the given url.

    This will make the url absolute and force it to https when that setting is
    enabled.

    """
    if base_url:
        url = absolute_location(url, base_url)

    if base_url and settings.force_https:
        base_url_parts = urlparse(base_url)
        url_parts = urlparse(url)
        if (
            base_url_parts.netloc == url_parts.netloc
            and base_url_parts.scheme != url_parts.scheme
        ):
            url = urlunparse(("https",) + url_parts[1:])
    return url


def absolute_location(location, base):
    """Make an url absolute (if it is optional) via the passed base url.

    :param location: The (relative) url
    :type location: str
    :param base: The base location
    :type base: str
    :returns: An absolute URL
    :rtype: str

    """
    if location == base:
        return location

    if urlparse(location).scheme in ("http", "https", "file"):
        return location

    if base and urlparse(base).scheme in ("http", "https", "file"):
        return urljoin(base, location)
    else:
        if os.path.isabs(location):
            return location
        if base:
            return os.path.realpath(os.path.join(os.path.dirname(base), location))
    return location


def is_relative_path(value):
    """Check if the given value is a relative path

    :param value: The value
    :type value: str
    :returns: Boolean indicating if the url is relative. If it is absolute then
      False is returned.
    :rtype: boolean

    """
    if urlparse(value).scheme in ("http", "https", "file"):
        return False
    return not os.path.isabs(value)
