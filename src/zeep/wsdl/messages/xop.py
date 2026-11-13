import base64
import re
from urllib.parse import unquote

XOP_NAMESPACE = "http://www.w3.org/2004/08/xop/include"
_INVALID_PERCENT_ESCAPE = re.compile(r"%(?![0-9A-Fa-f]{2})")


class XopError(ValueError):
    """Raised when an XOP message cannot be resolved as a whole.

    The document and the message pack are left untouched when this is
    raised, so the caller can retry after the missing attachment becomes
    available.
    """


def _resolve_content_id(href):
    """Return the MIME Content-ID (including angle brackets) for an href."""
    if not href:
        raise XopError("xop:Include is missing an href attribute")
    if not href.startswith("cid:"):
        raise XopError("Unsupported xop:Include href %r (expected a cid: URI)" % href)
    # URL can be encoded. RFC2392
    encoded = href[4:]
    if _INVALID_PERCENT_ESCAPE.search(encoded):
        raise XopError("Invalid percent-encoding in xop:Include href %r" % href)
    return "<%s>" % unquote(encoded)


def process_xop(document, message_pack):
    """Replace the xop:include elements in the document tree.

    All references are resolved and validated before the tree is modified,
    so an error never leaves the document (or the message pack) in a
    partially expanded state.

    :raises XopError: when an href is invalid, a referenced attachment is
        missing, or a Content-ID is matched by multiple attachments.

    """

    xop_nodes = document.xpath("//xop:Include", namespaces={"xop": XOP_NAMESPACE})

    # First pass: resolve every reference without touching the tree. A
    # single attachment may be referenced by multiple Include nodes.
    replacements = []
    for xop_node in xop_nodes:
        content_id = _resolve_content_id(xop_node.get("href"))
        try:
            attachment = message_pack.resolve_content_id(content_id)
        except KeyError as exc:
            raise XopError("No part found for: %r" % xop_node.get("href")) from exc
        except ValueError as exc:
            raise XopError(str(exc)) from exc
        replacements.append((xop_node, attachment))

    # Second pass: all references are valid, apply the replacements. The
    # attachment contents are read after validation so that an empty part
    # still counts as an existing attachment.
    for xop_node, attachment in replacements:
        xop_parent = xop_node.getparent()
        xop_parent.remove(xop_node)
        xop_parent.text = base64.b64encode(attachment.content)

    return bool(replacements)
