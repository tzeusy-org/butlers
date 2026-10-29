"""Deterministic Relationship reminder jobs (bu-idc5mx.4).

``upcoming-dates-check`` (daily) and ``relationship-maintenance`` (weekly) are job-mode
schedules. Each reads through an existing tool, renders a fixed template and sends it to the
owner through ``butlers.jobs.owner_notify.send_owner_telegram``. No session is spawned and no
model is called: RFC 0038 D18 keeps model-composed owner text inside a goal.

A suppressed send (quiet hours, context bus) is dropped and recorded, not deferred. The daily
dates job re-evaluates the next morning; a suppressed weekly maintenance run is not resent.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime
from typing import Any

import asyncpg

from butlers.jobs.owner_notify import send_owner_telegram
from butlers.tools.relationship.dates import upcoming_dates
from butlers.tools.relationship.dunbar import (
    _BONUS_PENDING_GIFT,
    _BONUS_UPCOMING_DATE,
    compute_tier_ranking,
    compute_urgency,
    contacts_overdue_evaluation,
)

logger = logging.getLogger(__name__)

_ORIGIN = "relationship"
_DATES_DAYS_AHEAD = 7
_MAX_SUGGESTIONS = 3

ALL_CLEAR_MESSAGE = "All your key relationships are up to date — no overdue check-ins this week."

NotifyFn = Callable[[str], Awaitable[str]]


def _owner_notify(pool: asyncpg.Pool, log_context: str) -> NotifyFn:
    async def notify(message: str) -> str:
        return await send_owner_telegram(
            pool, origin_butler=_ORIGIN, message=message, log_context=log_context
        )

    return notify


# ---------------------------------------------------------------------------
# upcoming-dates-check
# ---------------------------------------------------------------------------


def _relative_day(days: int) -> str:
    if days == 0:
        return "today"
    if days == 1:
        return "tomorrow"
    return f"in {days} days"


def render_upcoming_dates(rows: list[dict[str, Any]], *, today: date) -> str:
    """Render the dates reminder: one line per date, soonest first."""
    entries = sorted(
        ((date.fromisoformat(r["upcoming_date"]), r["contact_name"], r["label"]) for r in rows),
        key=lambda e: (e[0], e[1]),
    )
    lines = ["Upcoming dates this week:"]
    for when, name, label in entries:
        stamp = f"{when:%b} {when.day}"
        lines.append(f"- {name}: {label}, {stamp} ({_relative_day((when - today).days)})")
    return "\n".join(lines)


async def run_upcoming_dates_check(
    pool: asyncpg.Pool,
    *,
    dates_fn: Callable[..., Awaitable[list[dict[str, Any]]]] | None = None,
    notify_fn: NotifyFn | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Remind the owner of active people's dates in the next 7 days.

    A raised read propagates, so a failed posture or DB read sends neither a reminder nor an
    all-clear. A successful empty read sends nothing.
    """
    read = dates_fn or upcoming_dates
    rows = await read(pool, days_ahead=_DATES_DAYS_AHEAD)
    if not rows:
        return {"dates": 0, "notify": None}
    today = (now or datetime.now(UTC)).date()
    notify = notify_fn or _owner_notify(pool, "relationship.upcoming_dates_check")
    outcome = await notify(render_upcoming_dates(rows, today=today))
    return {"dates": len(rows), "notify": outcome}


# ---------------------------------------------------------------------------
# relationship-maintenance
# ---------------------------------------------------------------------------


def _context_hints(context_bonus: float) -> list[str]:
    """Name the date and gift bonuses compute_urgency folded into ``context_bonus``."""
    hints = []
    remaining = context_bonus
    if remaining >= _BONUS_UPCOMING_DATE:
        hints.append("a date is coming up")
        remaining -= _BONUS_UPCOMING_DATE
    if remaining >= _BONUS_PENDING_GIFT:
        hints.append("gift pending")
    return hints


def render_maintenance(suggestions: list[tuple[dict[str, Any], dict[str, Any]]]) -> str:
    """Render the weekly reconnect list from (overdue contact, urgency entry) pairs."""
    lines = ["Reconnect this week:"]
    for n, (contact, ranked) in enumerate(suggestions, start=1):
        days = contact.get("days_since_last_interaction")
        cadence = contact["effective_cadence"]
        if days is None:
            line = f"{n}. {contact['name']}: no conversation on record (every {cadence} days)"
        else:
            line = (
                f"{n}. {contact['name']}: {round(days)} days since you last talked "
                f"(every {cadence} days)"
            )
        hints = _context_hints(float(ranked.get("context_bonus") or 0.0))
        lines.append("; ".join([line, *hints]))
    return "\n".join(lines)


async def run_relationship_maintenance(
    pool: asyncpg.Pool,
    *,
    evaluate_fn: Callable[..., Awaitable[dict[str, Any]]] | None = None,
    rank_fn: Callable[..., Awaitable[list[dict[str, Any]]]] | None = None,
    urgency_fn: Callable[..., Awaitable[list[dict[str, Any]]]] | None = None,
    notify_fn: NotifyFn | None = None,
) -> dict[str, Any]:
    """Suggest up to 3 overdue reconnections, ranked by unified urgency.

    Eligibility is ``contacts_overdue_evaluation``'s overdue, measurable set. When any cadence
    is unmeasurable nothing is sent, not even the all-clear. Only eligible contacts enter
    ``compute_urgency``; an eligible contact missing from the tier ranking is counted, never
    back-filled.
    """
    evaluate = evaluate_fn or contacts_overdue_evaluation
    rank = rank_fn or compute_tier_ranking
    urgency = urgency_fn or compute_urgency

    evaluation = await evaluate(pool)
    if not evaluation["cadence_available"]:
        return {
            "status": "unmeasurable",
            "unmeasurable_count": evaluation["unmeasurable_count"],
            "notify": None,
        }

    notify = notify_fn or _owner_notify(pool, "relationship.relationship_maintenance")
    overdue = {c["id"]: c for c in evaluation["contacts"]}
    if not overdue:
        outcome = await notify(ALL_CLEAR_MESSAGE)
        return {"status": "all_clear", "suggested": 0, "notify": outcome}

    eligible_ranking = [e for e in await rank(pool) if e["contact_id"] in overdue]
    unranked = len(overdue) - len(eligible_ranking)
    if unranked:
        logger.warning(
            "relationship_maintenance: %d overdue contact(s) missing from the tier ranking",
            unranked,
        )
    ranked = (
        await urgency(pool, tier_ranking=eligible_ranking, top_n=_MAX_SUGGESTIONS)
        if eligible_ranking
        else []
    )
    suggestions = [(overdue[r["contact_id"]], r) for r in ranked if r["contact_id"] in overdue]
    if not suggestions:
        return {
            "status": "unranked",
            "suggested": 0,
            "overdue": len(overdue),
            "unranked": unranked,
            "notify": None,
        }
    outcome = await notify(render_maintenance(suggestions))
    return {
        "status": "suggested",
        "suggested": len(suggestions),
        "overdue": len(overdue),
        "unranked": unranked,
        "notify": outcome,
    }
