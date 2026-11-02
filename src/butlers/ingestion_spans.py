"""Untrusted span marking and channel kind for ``ingest.v1`` envelopes (bu-xmueeq).

RFC 0038 D5 starts an owner goal exposed when its message carries text from someone else: a
forwarded message, a quoted earlier message, or embedded third-party content. Connectors mark
those parts of ``payload.normalized_text`` as ``payload.untrusted_spans``
(REQ-connector-base-spec-004); admission (not built yet) reads them. Each connector also registers
whether its channel carries messages from people or is a device or telemetry feed
(REQ-connector-base-spec-003). The kind is a per-connector fact on the Switchboard's connector
registry, sent once per heartbeat as ``capabilities.channel_kind``; an envelope cannot set it,
because it selects the feed path instead of owner-channel origin. An unregistered or undeclared
channel is a message channel.

Offsets are Python ``str`` indices into ``normalized_text`` *after* the contract strips its
surrounding whitespace, ``[start, end)``. A connector that knows a message carries such content
but cannot locate it marks the whole text (fail closed). The marking is representation only:
nothing here changes routing, and the forwarding owner stays the sender.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Literal

SpanKind = Literal["forwarded", "quoted", "embedded"]
ChannelKind = Literal["message", "feed"]

SPAN_KINDS: frozenset[str] = frozenset({"forwarded", "quoted", "embedded"})
CHANNEL_KIND_MESSAGE: ChannelKind = "message"
CHANNEL_KIND_FEED: ChannelKind = "feed"
CHANNEL_KINDS: frozenset[str] = frozenset({CHANNEL_KIND_MESSAGE, CHANNEL_KIND_FEED})
#: Heartbeat ``capabilities`` key under which a connector registers its channel kind.
CHANNEL_KIND_CAPABILITY = "channel_kind"


def make_span(
    kind: SpanKind,
    start: int,
    end: int,
    *,
    origin_identity: str | None = None,
    origin_display_name: str | None = None,
) -> dict[str, Any]:
    """One span record; origin fields are included only when the provider supplied them."""
    span: dict[str, Any] = {"kind": kind, "start": start, "end": end}
    if origin_identity:
        span["origin_identity"] = origin_identity
    if origin_display_name:
        span["origin_display_name"] = origin_display_name
    return span


def whole_text_span(kind: SpanKind, text: str, **origin: str | None) -> dict[str, Any]:
    """Mark all of ``text`` (as the contract will strip it) as one span of ``kind``.

    An empty text yields the zero-length span ``[0, 0)``, which stands for the attachments that
    carry the content of a captionless media message.
    """
    return make_span(kind, 0, len(text.strip()), **origin)


def span_in_text(
    kind: SpanKind, text: str, start: int, end: int, **origin: str | None
) -> dict[str, Any]:
    """Map ``[start, end)`` in the unstripped ``text`` onto the stripped text the contract keeps."""
    stripped = text.strip()
    lead = len(text) - len(text.lstrip())
    lo = min(max(start - lead, 0), len(stripped))
    hi = min(max(end - lead, 0), len(stripped))
    if hi <= lo:
        return whole_text_span(kind, text, **origin)
    return make_span(kind, lo, hi, **origin)


def forwarded_line_spans(
    normalized_text: str,
    header_lines: list[str],
    text_parts: list[str],
    marked: list[tuple[int, int, int, dict[str, str | None]]],
) -> list[dict[str, Any]]:
    """``forwarded`` spans for messages inside a batch transcript.

    The transcript is ``"\\n".join(header_lines + text_parts + footer)``; each ``marked`` entry is
    ``(index into text_parts, start, end, origin)`` with offsets inside that line.
    """
    line_starts: list[int] = []
    offset = sum(len(line) + 1 for line in header_lines)
    for line in text_parts:
        line_starts.append(offset)
        offset += len(line) + 1
    return [
        span_in_text(
            "forwarded", normalized_text, line_starts[i] + lo, line_starts[i] + hi, **origin
        )
        for i, lo, hi, origin in marked
    ]


def normalize_spans(text: str, spans: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Fit spans to the stripped ``text``; a span that does not fit becomes a whole-text span.

    A malformed or out-of-range span is a connector bug, and dropping it would admit the
    third-party text unexposed, so it fails closed to the whole text instead. A span with an
    unknown kind is kept as is, so the contract rejects it by name.
    """
    stripped = text.strip()
    out: list[dict[str, Any]] = []
    for raw in spans:
        span = dict(raw)
        kind = span.get("kind")
        start, end = span.get("start"), span.get("end")
        if kind not in SPAN_KINDS:
            out.append(span)
            continue
        in_range = (
            isinstance(start, int)
            and isinstance(end, int)
            and 0 <= start <= end <= len(stripped)
            and (start < end or not stripped)
        )
        if not in_range:
            span["start"], span["end"] = 0, len(stripped)
        out.append(span)
    return out


def collapse_spans(text: str, spans: Any) -> Any:
    """One whole-text span per kind, keeping the first origin seen for that kind.

    Used when ``text`` was rewritten after the spans were computed (bearer scrubbing), so the
    old offsets can no longer be trusted but the fact that the message carries such content can.
    Malformed containers, entries and unknown kinds stay present for canonical rejection.
    A rewrite must never turn a present exposure fact into an unmarked message.
    """
    if not isinstance(spans, (list, tuple)):
        return spans
    by_kind: dict[str, dict[str, Any]] = {}
    out: list[Any] = []
    for span in spans:
        if (
            not isinstance(span, Mapping)
            or not isinstance(span.get("kind"), str)
            or span["kind"] not in SPAN_KINDS
        ):
            out.append(span)
            continue
        kind = span.get("kind")
        if kind in SPAN_KINDS and kind not in by_kind:
            by_kind[kind] = whole_text_span(
                kind,
                text,
                origin_identity=span.get("origin_identity"),
                origin_display_name=span.get("origin_display_name"),
            )
            out.append(by_kind[kind])
    return out


def effective_channel_kind(capabilities: Mapping[str, Any] | None) -> ChannelKind:
    """The channel kind registered in a connector's ``capabilities``.

    Only an explicit ``feed`` is a feed; anything else (absent, unknown, malformed) is a message
    channel, whose goals take origin from owner-channel authorization.
    """
    declared = (capabilities or {}).get(CHANNEL_KIND_CAPABILITY)
    return CHANNEL_KIND_FEED if declared == CHANNEL_KIND_FEED else CHANNEL_KIND_MESSAGE
