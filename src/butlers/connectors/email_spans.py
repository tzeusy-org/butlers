"""Untrusted span marking for email bodies (bu-xmueeq, REQ-connector-base-spec-004).

Works on the connector's final ``normalized_text`` (``Subject: …`` plus the HTML-escaped body),
so ``>`` quote markers appear as ``&gt;``; both spellings are recognised.

- A forward marker line (Gmail ``---------- Forwarded message ---------``, Apple
  ``Begin forwarded message:``) starts a ``forwarded`` span that runs to the end of the text.
- An Outlook-style ``-----Original Message-----`` header starts a ``quoted`` span to the end.
- Each run of ``>``-prefixed lines is a ``quoted`` span, together with an ``On … wrote:``
  attribution line directly above it.
- A ``Fwd:`` subject with no forward marker in the body, and a message that carries an attached
  message (``message/rfc822``), mark the whole text (boundaries unknown: fail closed).
"""

from __future__ import annotations

import html
import re
from typing import Any

from butlers.ingestion_spans import make_span, span_in_text, whole_text_span

_FORWARD_MARKER = re.compile(
    r"^\s*(?:-{2,}\s*Forwarded message\s*-{2,}|Begin forwarded message:)\s*$", re.IGNORECASE
)
_ORIGINAL_MESSAGE = re.compile(r"^\s*-{2,}\s*Original Message\s*-{2,}\s*$", re.IGNORECASE)
_ATTRIBUTION = re.compile(r"^\s*On\b.+\bwrote:\s*$")
_QUOTE_LINE = re.compile(r"^\s*(?:&gt;|>)")
_FORWARD_SUBJECT = re.compile(r"^\s*(?:fwd?|fw)\s*:", re.IGNORECASE)


def _lines_with_offsets(text: str) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    offset = 0
    for line in text.split("\n"):
        out.append((offset, line))
        offset += len(line) + 1
    return out


def email_untrusted_spans(
    normalized_text: str,
    *,
    subject: str,
    has_attached_message: bool,
    body_text: str = "",
    body_quote_spans: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Spans over the stripped ``normalized_text`` for forwarded, quoted and embedded parts."""
    text = normalized_text.strip()
    if has_attached_message:
        return [whole_text_span("embedded", text)]
    lines = _lines_with_offsets(text)
    spans: list[dict[str, Any]] = []
    prefix = len(f"Subject: {html.escape(subject)}\n\n")
    for span in body_quote_spans or []:
        start, end = span["start"], span["end"]
        spans.append(
            span_in_text(
                "quoted",
                normalized_text,
                prefix + len(html.escape(body_text[:start])),
                prefix + len(html.escape(body_text[:end])),
            )
        )
    index = 0
    while index < len(lines):
        offset, line = lines[index]
        if _FORWARD_MARKER.match(line):
            spans.append(make_span("forwarded", offset, len(text)))
            break
        if _ORIGINAL_MESSAGE.match(line):
            spans.append(make_span("quoted", offset, len(text)))
            break
        if _QUOTE_LINE.match(line):
            start = offset
            if index > 0 and _ATTRIBUTION.match(lines[index - 1][1]):
                start = lines[index - 1][0]
            while index + 1 < len(lines) and _QUOTE_LINE.match(lines[index + 1][1]):
                index += 1
            end_offset, end_line = lines[index]
            spans.append(make_span("quoted", start, end_offset + len(end_line)))
        index += 1
    if _FORWARD_SUBJECT.match(subject) and not any(s["kind"] == "forwarded" for s in spans):
        return [whole_text_span("forwarded", text)]
    return spans
