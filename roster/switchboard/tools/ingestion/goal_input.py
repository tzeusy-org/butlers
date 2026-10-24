"""Immutable accepted input. Data is structural, never delimited by user text."""

from __future__ import annotations

import json
from dataclasses import dataclass

from butlers.core.goal_admission import EvidenceRefused, digest
from butlers.tools.switchboard.contracts import IngestPayloadV1


@dataclass(frozen=True)
class InputSegment:
    text: str
    data: bool
    kinds: tuple[str, ...] = ()
    attribution: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class GoalInput:
    source_ref: str
    source_digest: str
    segments: tuple[InputSegment, ...]
    reply_locator: str | None
    marked: bool

    def serialize(self) -> str:
        """JSON escapes delimiter-like text; attribution never becomes an instruction."""
        return json.dumps(
            [
                {
                    "type": "data" if s.data else "owner_text",
                    "text": s.text,
                    "kinds": s.kinds,
                    "attribution_data": s.attribution,
                }
                for s in self.segments
            ],
            ensure_ascii=True,
        )


def prepare_goal_input(source_ref, source_digest, payload, reply_locator, *, owner):
    """Use the canonical validator's whole-text normalization, including attachments."""
    try:
        parsed = IngestPayloadV1.model_validate(payload)
        text, spans = parsed.normalized_text, parsed.untrusted_spans or ()
        boundaries = sorted({0, len(text)} | {p for s in spans for p in (s.start, s.end)})
        segments = []
        # Adjacent boundaries make N-1 intervals; the final boundary has no successor.
        for lo, hi in zip(boundaries, boundaries[1:], strict=False):
            covering = [s for s in spans if s.start <= lo and s.end >= hi]
            segments.append(
                InputSegment(
                    text[lo:hi],
                    not owner or bool(covering),
                    tuple(sorted({s.kind for s in covering})),
                    tuple(
                        (k, v)
                        for s in covering
                        for k, v in (
                            ("origin_identity", s.origin_identity),
                            ("origin_display_name", s.origin_display_name),
                        )
                        if v is not None
                    ),
                )
            )
        if not text:
            segments.append(
                InputSegment(
                    "",
                    True,
                    tuple(sorted({s.kind for s in spans})),
                    tuple(
                        (k, v)
                        for s in spans
                        for k, v in (
                            ("origin_identity", s.origin_identity),
                            ("origin_display_name", s.origin_display_name),
                        )
                        if v is not None
                    ),
                )
            )
        return GoalInput(source_ref, source_digest, tuple(segments), reply_locator, bool(spans))
    except Exception:
        raise EvidenceRefused("accepted_input_unavailable") from None


def scoped_reply_ref(
    *, transport, endpoint, conversation, recipient_entity, recipient_channel, locator
):
    """Server-side outbound writers bind this value on the immutable effect.

    The incoming provider locator is never an asserted effect/goal identifier.
    Every scope component must agree before admission can even find a goal.
    """
    if (
        not all(
            isinstance(x, str) and x
            for x in (transport, endpoint, conversation, recipient_channel, locator)
        )
        or recipient_entity is None
    ):
        raise EvidenceRefused("reply_scope_unavailable")
    return "goal-reply-v1:" + digest(
        [transport, endpoint, conversation, str(recipient_entity), recipient_channel, locator]
    )
