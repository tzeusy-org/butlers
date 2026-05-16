"""Pinned local-runtime prompt for the dashboard briefing elaboration.

The prompt encodes the dashboard voice rules from
about/heart-and-soul/design-language.md:
    - Past tense for events, present tense for state.
    - No future tense.
    - No first person (I, we, us, our).
    - Avoid "your" when "the" works.
    - No hedging adverbs (currently, presently, just, simply, basically).
    - No exclamation marks.
    - No em-dashes.

Design reference: openspec/changes/dashboard-overview-briefing/design.md D2.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from butlers.connectors.discretion_dispatcher import DiscretionDispatcher
from butlers.core.model_routing import Complexity

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Runtime configuration
# ---------------------------------------------------------------------------

BRIEFING_RUNTIME_BUTLER_NAME = "__dashboard_briefing__"
_UNHEALTHY_STATUSES = {"degraded", "down", "error", "stale", "quarantined"}

# ---------------------------------------------------------------------------
# Pinned system prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You write a one-to-three sentence elaboration paragraph for a personal multi-agent \
dashboard. The paragraph names what is true about the system right now, drawing only \
from the state JSON provided in the user message.

Voice rules (all mandatory):
- Past tense for completed events. Present tense for current state. No future tense. \
Do not write "will be" or "is going to."
- No first person. Write "the system," "the butler," "the queue." Never "I," "we," \
"us," or "our."
- Avoid "your" when "the" works. Write "the calendar" not "your calendar."
- No hedging adverbs: do not write "currently," "presently," "just," "simply," \
or "basically."
- No exclamation marks.
- No em-dashes.
- Maximum 50 words. Three sentences at most.
- Write only the paragraph. No preamble, no sign-off, no markdown formatting.
"""


def _severity_rank(item: dict) -> int:
    rank = {"high": 0, "medium": 1, "low": 2}
    severity = str(item.get("severity", "low")).lower()
    return rank.get(severity, 3)


def _attention_summary(items: list[dict]) -> dict[str, int]:
    return {
        "total": len(items),
        "high": sum(1 for item in items if item.get("severity") == "high"),
        "medium": sum(1 for item in items if item.get("severity") == "medium"),
        "low": sum(1 for item in items if item.get("severity") == "low"),
    }


def _compact_attention_item(item: dict) -> dict:
    keys = (
        "severity",
        "type",
        "butler",
        "description",
        "last_seen_at",
        "link",
        "source",
        "occurrences",
        "error_message",
    )
    return {key: item.get(key) for key in keys if item.get(key) is not None}


def _compact_butler_status(item: dict) -> dict:
    keys = (
        "name",
        "status",
        "type",
        "eligibility_state",
        "last_seen_at",
        "quarantine_reason",
    )
    return {key: item.get(key) for key in keys if item.get(key) is not None}


def _compact_notification(item: dict) -> dict:
    keys = (
        "source_butler",
        "channel",
        "message",
        "severity",
        "status",
        "error",
        "created_at",
    )
    return {key: item.get(key) for key in keys if item.get(key) is not None}


def _build_user_message(state: dict, state_class: str) -> str:
    """Render the user turn from dashboard state and the computed class."""
    attention_items = state.get("attention_items", [])
    butler_statuses = state.get("butler_statuses", [])
    notification_items = state.get("notification_items", [])

    top_attention_items = [
        _compact_attention_item(item) for item in sorted(attention_items, key=_severity_rank)[:3]
    ]
    unhealthy_butlers = [
        _compact_butler_status(item)
        for item in butler_statuses
        if str(item.get("status", "")).lower() in _UNHEALTHY_STATUSES
    ][:5]

    state_summary = {
        "state_class": state_class,
        "generated_for_local_time": state.get("now"),
        "overview_totals": state.get("overview_totals", {}),
        "attention_summary": _attention_summary(attention_items),
        "top_attention_items": top_attention_items,
        "butler_health": {
            "total": len(butler_statuses),
            "unhealthy_total": len(unhealthy_butlers),
            "unhealthy": unhealthy_butlers,
        },
        "recent_notifications": [_compact_notification(item) for item in notification_items[:3]],
    }
    return (
        f"Dashboard state:\n{json.dumps(state_summary, default=str, indent=2)}\n\n"
        f"Write the elaboration paragraph for state_class={state_class!r}."
    )


async def elaborate_llm(pool: Any, state: dict, state_class: str) -> str | None:
    """Call the catalog-backed local runtime and return the paragraph or None.

    Returns:
        The model response string if the local runtime call succeeded and the
        response is non-empty. None on any failure so the caller can use the
        deterministic fallback path.
    """
    dispatcher = DiscretionDispatcher(
        pool,
        butler_name=BRIEFING_RUNTIME_BUTLER_NAME,
        complexity_tier=Complexity.CHEAP,
    )
    try:
        text = (
            await dispatcher.call(
                _build_user_message(state, state_class),
                system_prompt=_SYSTEM_PROMPT,
            )
        ).strip()
        if not text:
            logger.info("LLM elaboration returned empty text")
            return None
        return text
    except Exception as exc:
        logger.warning("Local runtime elaboration failed: %s", exc)
        return None
