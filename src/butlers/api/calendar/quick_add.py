"""Natural-language calendar quick-add parsing (parse-only, never writes).

This module is the LLM seam behind ``POST /api/calendar/workspace/parse-quick-add``.
It turns a free-text string such as ``"lunch with Sarah Fri 1pm at Tartine"`` into
a structured draft event for confirmation. It performs **no** provider or
projection write: the resulting draft is advisory only and is materialized into a
real event exclusively through the existing ``/user-events`` create path on
confirm.

Resolution mirrors the dashboard-briefing pattern (``api/briefing/prompts.py``):
the cheapest possible single-turn LLM call routes to ``Complexity.CHEAP`` and runs
through the catalog-backed :class:`DiscretionDispatcher`. When ``resolve_model``
returns ``None`` (no enabled model in any tier) the parse is reported as
unavailable rather than fabricating an event.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import re
from datetime import UTC, datetime
from typing import Any

from butlers.connectors.discretion_dispatcher import DiscretionDispatcher
from butlers.core.model_routing import Complexity, resolve_model

logger = logging.getLogger(__name__)

# Synthetic butler name used for model resolution when the caller does not name
# a butler. An unknown name simply means "no per-butler override" — the global
# catalog applies.
QUICK_ADD_RUNTIME_BUTLER_NAME = "__calendar_quick_add__"

# Reason strings surfaced in the degraded response envelope.
_REASON_NO_MODEL = (
    "No cheap-tier model is configured, so the quick-add text could not be parsed. "
    "Enter the event details manually."
)
_REASON_UNPARSEABLE = (
    "The text could not be interpreted as a single calendar event. "
    "Try rephrasing, or enter the event details manually."
)

_SYSTEM_PROMPT = """\
You convert a single natural-language phrase into one draft calendar event. You \
do not create events; you only extract fields for a human to confirm.

Output ONLY a single JSON object (no prose, no markdown fences) with these keys:
- "title": short event title (string). Omit attendee plumbing words; keep it human.
- "start_at": ISO-8601 datetime with timezone offset, or null if no start is implied.
- "end_at": ISO-8601 datetime with timezone offset, or null. If only a start is \
given, assume a one-hour duration.
- "all_day": true only when the phrase implies a full-day event with no clock time, \
else false.
- "location": the place (string) or null.
- "description": any remaining detail such as named people (string) or null.

Resolve relative dates and times ("Fri 1pm", "tomorrow", "next week") against the \
provided reference time and timezone. If the phrase does not describe a \
schedulable event, output exactly {"title": null}.
"""

_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


@dataclasses.dataclass(frozen=True)
class QuickAddParseOutcome:
    """Result of a quick-add parse.

    ``parse_available`` is ``False`` for both the no-model degraded path and the
    unparseable-output path; ``draft`` is then ``None`` and ``reason`` explains
    why. No write ever occurs regardless of outcome.
    """

    parse_available: bool
    draft: dict[str, Any] | None
    reason: str | None


def _reference_now(now_iso: str | None) -> str:
    """Return a human-readable reference timestamp for relative-date resolution.

    Falls back to wall-clock UTC when no override is supplied. A malformed
    override is ignored (best-effort) rather than failing the parse.
    """
    if now_iso:
        try:
            return datetime.fromisoformat(now_iso).isoformat()
        except ValueError:
            logger.debug("quick-add: ignoring malformed now override %r", now_iso)
    return datetime.now(UTC).isoformat()


def _build_user_message(text: str, *, now_iso: str | None, timezone: str | None) -> str:
    """Render the user turn with the phrase and resolution context."""
    reference = _reference_now(now_iso)
    tz = timezone or "UTC"
    return (
        f"Reference time (now): {reference}\n"
        f"Timezone: {tz}\n\n"
        f"Phrase:\n{text}\n\n"
        "Return the draft event JSON object."
    )


def _coerce_draft(raw: str) -> dict[str, Any] | None:
    """Parse the model output into a normalized draft dict.

    Returns ``None`` when the output is not valid JSON, is not an object, or
    carries no usable ``title`` (the model's "not an event" sentinel). Extra
    keys the model invents are dropped; only the known draft fields survive.
    """
    match = _JSON_OBJECT_RE.search(raw or "")
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(parsed, dict):
        return None

    title = parsed.get("title")
    if not isinstance(title, str) or not title.strip():
        # The model's {"title": null} sentinel, or a malformed object.
        return None

    def _opt_str(key: str) -> str | None:
        value = parsed.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None

    return {
        "title": title.strip(),
        "start_at": _opt_str("start_at"),
        "end_at": _opt_str("end_at"),
        # Only explicit truthy values count. ``bool()`` would coerce a stray
        # string like "false" (which the LLM can emit) to True; match the
        # JSON/string forms the model is told to produce instead.
        "all_day": parsed.get("all_day") in (True, "true", "True", 1, "1"),
        "location": _opt_str("location"),
        "description": _opt_str("description"),
    }


async def parse_quick_add(
    pool: Any,
    *,
    text: str,
    butler_name: str | None = None,
    timezone: str | None = None,
    now_iso: str | None = None,
) -> QuickAddParseOutcome:
    """Parse ``text`` into a draft event via the cheap-tier LLM. Never writes.

    Resolution contract:
    1. ``resolve_model(pool, butler_name, Complexity.CHEAP)`` — when it returns
       ``None`` (no enabled model in any tier), return a degraded outcome with
       ``parse_available=False`` and no draft. No LLM call is made.
    2. Otherwise run a single-turn parse through :class:`DiscretionDispatcher`
       at the cheap tier and coerce the JSON output into a draft.
    3. Output that cannot be interpreted as a single event draft yields a
       degraded outcome (``parse_available=False``, no draft).
    """
    effective_butler = butler_name or QUICK_ADD_RUNTIME_BUTLER_NAME

    # Degraded path: no cheap-tier (or fallthrough) model is configured.
    if await resolve_model(pool, effective_butler, Complexity.CHEAP) is None:
        logger.info("quick-add: no cheap-tier model configured; returning degraded parse")
        return QuickAddParseOutcome(parse_available=False, draft=None, reason=_REASON_NO_MODEL)

    dispatcher = DiscretionDispatcher(
        pool,
        butler_name=effective_butler,
        complexity_tier=Complexity.CHEAP,
    )
    try:
        raw = (
            await dispatcher.call(
                _build_user_message(text, now_iso=now_iso, timezone=timezone),
                system_prompt=_SYSTEM_PROMPT,
            )
        ).strip()
    except Exception as exc:
        logger.warning("quick-add: LLM parse failed: %s", exc)
        return QuickAddParseOutcome(parse_available=False, draft=None, reason=_REASON_UNPARSEABLE)

    draft = _coerce_draft(raw)
    if draft is None:
        logger.info("quick-add: model output not a usable event draft")
        return QuickAddParseOutcome(parse_available=False, draft=None, reason=_REASON_UNPARSEABLE)
    return QuickAddParseOutcome(parse_available=True, draft=draft, reason=None)
