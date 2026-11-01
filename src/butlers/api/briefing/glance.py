"""Deterministic glance payload for ambient projection surfaces.

compose_glance(state, ...) -> dict
    One expiring "one thing" payload, classified from the SAME composed attention
    model the dashboard briefing uses (``classify``), with no LLM call.

Spec: openspec/changes/add-ambient-projection-grants (REQ-ambient-projections-003).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from butlers.api.briefing.classify import StateClass, classify

logger = logging.getLogger(__name__)

# Every state source _fetch_dashboard_state reads, in its degraded_sources vocabulary.
GLANCE_SOURCES = ("audit", "board", "approvals", "notifications", "qa", "projections")
LINE_MAX = 48

_SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}

# Household surfaces never carry free text from an attention item: an item's
# description can name a person, a health fact or an amount. Only its kind is shown.
_HOUSEHOLD_LABELS = {
    "approval": "An approval is waiting",
    "notification": "A notification failed",
    "qa": "System check needs a look",
    "runtime": "A butler needs a look",
    "scheduled_task_failure": "A scheduled task failed",
    "audit_error_group": "A butler reported an error",
    "projection": "A display stopped updating",
}
_HOUSEHOLD_FALLBACK_LABEL = "Something needs a look"


def _plural(n: int, one: str, many: str) -> str:
    return one if n == 1 else many


def glance_line(state_class: StateClass, state: dict) -> str:
    """Return a one-line summary of at most ``LINE_MAX`` characters."""
    items = state.get("attention_items", [])
    high = sum(1 for item in items if item.get("severity") == "high")
    if state_class == "urgent":
        line = f"{high} {_plural(high, 'thing needs', 'things need')} you now"
    elif state_class in {"busy", "mild"}:
        line = f"{len(items)} {_plural(len(items), 'item', 'items')} waiting"
    elif state_class == "degraded-quiet":
        down = sum(
            1 for b in state.get("butler_statuses", []) if b.get("status") in ("degraded", "error")
        )
        line = f"Quiet, but {down} {_plural(down, 'butler is', 'butlers are')} degraded"
    elif state_class == "degraded":
        line = "Some sources unreachable; may be incomplete"
    else:
        line = "Everything is in hand"
    return line[:LINE_MAX]


def _top_item(items: list[dict], audience_class: str) -> dict | None:
    if not items:
        return None
    ranked = sorted(
        enumerate(items),
        key=lambda pair: (_SEVERITY_RANK.get(str(pair[1].get("severity")), 3), pair[0]),
    )
    item = ranked[0][1]
    kind = str(item.get("type") or item.get("source") or "other")
    if audience_class == "household":
        return {
            "kind": kind if kind in _HOUSEHOLD_LABELS else "other",
            "label": _HOUSEHOLD_LABELS.get(kind, _HOUSEHOLD_FALLBACK_LABEL),
            "door_url": None,
        }
    label = str(item.get("description") or kind)
    return {"kind": kind, "label": label[:120], "door_url": item.get("link")}


def compose_glance(
    state: dict, *, audience_class: str, as_of: datetime, refresh: timedelta
) -> dict:
    """Compose the glance payload; never raises for a malformed classification.

    ``valid_until`` is ``as_of + 2 * refresh``: a surface that stops refreshing
    shows a visibly expired picture instead of a stale one wearing current authority.
    """
    try:
        state_class = classify(state)
    except Exception as exc:
        # Mirrors the briefing: a classifier failure is itself a swallowed failure.
        logger.error("Glance classification failed, defaulting to degraded: %s", exc)
        state_class = "degraded"
    failed = [source for source in GLANCE_SOURCES if source in state.get("degraded_sources", [])]
    items = state.get("attention_items", [])
    return {
        "state_class": state_class,
        "line": glance_line(state_class, state),
        "needs_you": len(items),
        "top_item": _top_item(items, audience_class),
        "as_of": as_of.isoformat(),
        "valid_until": (as_of + 2 * refresh).isoformat(),
        "coverage": {
            "ok": [source for source in GLANCE_SOURCES if source not in failed],
            "failed": failed,
            "stale": [],
        },
        "audience_class": audience_class,
    }
