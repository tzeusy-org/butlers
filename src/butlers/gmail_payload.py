"""Gmail message-payload body extraction shared by the connector and the Gmail module.

The Gmail connector (ingestion) and the owner-facing Gmail module tools both turn a
Gmail API ``payload`` MIME tree into readable text. This is the single
implementation: plain text is preferred over HTML, HTML is reduced to visible text
with quoted ranges retained, signature parts are skipped, opaque S/MIME bodies get a
fixed placeholder, and nesting is bounded.

Pure functions only; nothing here performs I/O or touches credentials.
"""

from __future__ import annotations

import base64
import codecs
import logging
import re
from email.message import Message
from html.parser import HTMLParser
from typing import Any

logger = logging.getLogger(__name__)

# MIME types that carry cryptographic signatures rather than body content.
SIGNATURE_MIME_TYPES: frozenset[str] = frozenset(
    {
        "application/pkcs7-signature",  # S/MIME detached signature
        "application/pgp-signature",  # PGP/MIME detached signature
        "application/x-pkcs7-signature",  # Legacy S/MIME variant
    }
)

# Characters that can surround a Content-Type parameter value but are never part of
# a charset name (single quotes and unbalanced quotes from non-conformant senders).
CHARSET_VALUE_NOISE: str = " \t\r\n\"'"

MAX_DEPTH = 20


def header_name_is(header: Any, expected_lower_name: str) -> bool:
    """Return True when *header* is a Gmail header entry named *expected_lower_name*.

    Header entries arrive verbatim from the Gmail API; a malformed entry (non-dict,
    non-string name) is treated as non-matching. *expected_lower_name* is lowercase.
    """
    if not isinstance(header, dict):
        return False
    name = header.get("name")
    return isinstance(name, str) and name.lower() == expected_lower_name


def header_str_value(header: dict[str, Any], default: str = "") -> str:
    """Return a Gmail header's ``value`` when it is a string, else *default*."""
    value = header.get("value")
    return value if isinstance(value, str) else default


def charset_from_headers(headers: list[Any]) -> str:
    """Extract the declared charset from a MIME part's Content-Type header.

    Parsing is delegated to :mod:`email` (RFC 2045/2231 grammar); leftover quote
    noise is stripped and the result accepted only if :mod:`codecs` knows it.
    Falls back to ``utf-8``.
    """
    for header in headers:
        if not header_name_is(header, "content-type"):
            continue
        parsed = Message()
        parsed["Content-Type"] = header_str_value(header)
        declared = parsed.get_content_charset()
        if declared is None:
            continue
        charset_name = declared.strip(CHARSET_VALUE_NOISE)
        try:
            codecs.lookup(charset_name)
            return charset_name
        except LookupError:
            logger.debug("Unknown charset %r in Content-Type; falling back to utf-8", charset_name)
    return "utf-8"


def decode_part_bytes(raw_bytes: bytes, payload: dict[str, Any]) -> str:
    """Decode *raw_bytes* with the charset declared in *payload*'s Content-Type."""
    headers = payload.get("headers")
    charset = charset_from_headers(headers if isinstance(headers, list) else [])
    return raw_bytes.decode(charset, errors="replace")


def strip_html_with_quotes(raw_html: str) -> tuple[str, list[dict[str, Any]]]:
    """Reduce HTML to visible text, returning ``(text, quoted_spans)``.

    Quote tags/classes are structural provider facts; quoted characters travel through
    the same whitespace collapse as text. Hidden/script text is never included.
    """

    class _TextExtractor(HTMLParser):
        _SKIP_TAGS = frozenset({"style", "script", "head"})
        _VOID_TAGS = frozenset({"br", "hr", "img", "input", "meta", "link", "wbr"})

        def __init__(self) -> None:
            super().__init__(convert_charrefs=True)
            self.stack: list[tuple[str, bool]] = []
            self.parts: list[str] = []
            self.marked: list[bool] = []
            self.quote_seen = False

        def handle_starttag(self, tag: str, attrs: list) -> None:
            values = dict(attrs)
            classes = (values.get("class") or "").split()
            quoted = (
                tag == "blockquote"
                or (values.get("type") or "").lower() == "cite"
                or bool({"gmail_quote", "yahoo_quoted"}.intersection(classes))
            )
            if not any(t in self._SKIP_TAGS for t, _ in self.stack):
                self.quote_seen |= quoted
            if tag not in self._VOID_TAGS:
                self.stack.append((tag, quoted))

        def handle_startendtag(self, tag: str, attrs: list) -> None:
            self.handle_starttag(tag, attrs)
            self.handle_endtag(tag)

        def handle_endtag(self, tag: str) -> None:
            for index in range(len(self.stack) - 1, -1, -1):
                if self.stack[index][0] == tag:
                    del self.stack[index:]
                    break

        def handle_data(self, data: str) -> None:
            if not any(t in self._SKIP_TAGS for t, _ in self.stack):
                self.parts.append(data)
                self.marked.extend([any(q for _, q in self.stack)] * len(data))

        def result(self) -> tuple[str, list[dict[str, Any]]]:
            text, marked = "".join(self.parts), self.marked
            for pattern, replacement in ((r"[ \t]+", " "), (r"\n{3,}", "\n\n")):
                pieces, flags, offset = [], [], 0
                for match in re.finditer(pattern, text):
                    pieces.append(text[offset : match.start()])
                    flags.extend(marked[offset : match.start()])
                    pieces.append(replacement)
                    flags.extend([any(marked[match.start() : match.end()])] * len(replacement))
                    offset = match.end()
                pieces.append(text[offset:])
                flags.extend(marked[offset:])
                text, marked = "".join(pieces), flags
            lead = len(text) - len(text.lstrip())
            text = text.strip()
            marked = marked[lead : lead + len(text)]
            spans, start = [], None
            for index, flag in enumerate(marked + [False]):
                if flag and start is None:
                    start = index
                elif not flag and start is not None:
                    spans.append({"kind": "quoted", "start": start, "end": index})
                    start = None
            text = text or "(no body)"
            if self.quote_seen and not spans:
                spans = [{"kind": "quoted", "start": 0, "end": len(text)}]
            return text, spans

    extractor = _TextExtractor()
    extractor.feed(raw_html)
    extractor.close()
    return extractor.result()


def strip_html(raw_html: str) -> str:
    """Visible text of *raw_html* without span bookkeeping."""
    return strip_html_with_quotes(raw_html)[0]


def extract_body_with_quotes(
    payload: dict[str, Any], depth: int = 0
) -> tuple[str, list[dict[str, Any]]]:
    """Recursively extract body text from a Gmail message payload.

    Preference: text/plain > text/html (stripped) > ``"(no body)"``. Multipart/signed
    skips detached-signature parts; opaque S/MIME yields a fixed placeholder.
    """
    if depth > MAX_DEPTH:
        logger.warning("Maximum recursion depth reached in email parsing")
        return "(body too deeply nested)", []

    mime_type = payload.get("mimeType", "")

    if mime_type in ("application/pkcs7-mime", "application/x-pkcs7-mime"):
        logger.warning("S/MIME encrypted email (application/pkcs7-mime): body cannot be extracted")
        return "(S/MIME encrypted body — cannot extract)", []

    if mime_type == "text/plain":
        body = payload.get("body")
        body_data = body.get("data") if isinstance(body, dict) else ""
        if isinstance(body_data, str) and body_data:
            raw_bytes = base64.urlsafe_b64decode(body_data)
            return decode_part_bytes(raw_bytes, payload), []

    if mime_type == "text/html":
        body = payload.get("body")
        body_data = body.get("data") if isinstance(body, dict) else ""
        if isinstance(body_data, str) and body_data:
            raw_bytes = base64.urlsafe_b64decode(body_data)
            return strip_html_with_quotes(decode_part_bytes(raw_bytes, payload))

    parts = payload.get("parts")
    if isinstance(parts, list):
        plain_candidate: tuple[str, list[dict[str, Any]]] | None = None
        html_candidate: tuple[str, list[dict[str, Any]]] | None = None
        for part in parts:
            if not isinstance(part, dict):
                continue
            child_mime = part.get("mimeType", "")
            if child_mime in SIGNATURE_MIME_TYPES:
                continue
            body = extract_body_with_quotes(part, depth + 1)
            if body[0] in ("(no body)", "(body too deeply nested)"):
                continue
            if child_mime == "text/plain" and plain_candidate is None:
                plain_candidate = body
            elif child_mime == "text/html" and html_candidate is None:
                html_candidate = body
            elif plain_candidate is None and html_candidate is None:
                plain_candidate = body
        if plain_candidate is not None:
            return plain_candidate
        if html_candidate is not None:
            return html_candidate

    return "(no body)", []


def extract_body(payload: dict[str, Any]) -> str:
    """Body text of a Gmail payload (no quote spans)."""
    return extract_body_with_quotes(payload)[0]


def attachment_metadata(payload: dict[str, Any], depth: int = 0) -> list[dict[str, Any]]:
    """Attachment *metadata only* (filename, mime type, size) from a payload tree.

    Never returns attachment bytes or ids; a part counts as an attachment when it
    carries a filename and an ``attachmentId``.
    """
    if depth > MAX_DEPTH or not isinstance(payload, dict):
        return []
    found: list[dict[str, Any]] = []
    filename = payload.get("filename")
    body = payload.get("body")
    if (
        isinstance(filename, str)
        and filename
        and isinstance(body, dict)
        and body.get("attachmentId")
    ):
        size = body.get("size")
        found.append(
            {
                "filename": filename,
                "mime_type": payload.get("mimeType", ""),
                "size_bytes": size if isinstance(size, int) else None,
            }
        )
    parts = payload.get("parts")
    if isinstance(parts, list):
        for part in parts:
            found.extend(attachment_metadata(part, depth + 1))
    return found
