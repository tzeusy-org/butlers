"""Deterministic producers for the situational context bus (RFC 0009).

For 3.5 months ``public.user_context`` held zero rows while three hardened
consumers read it: the notify dnd/sleeping suppression gate
(``core_tools/_notifications.py``), every spawned session's situational
preamble (``core/spawner_context.py``), and the attention-ledger context
reasons (``core/attention_ledger.py``). The read side was fully wired; nothing
ever wrote a signal. RFC 0009 named the *writers* in its permission matrix but
no producer was ever built.

This module lights the bus with **deterministic, zero-LLM** producers. Each
runs as a scheduled ``dispatch_mode="job"`` handler on the butler that RFC
0009 authorizes as the signal's writer, so a single writer owns each source
(single-writer discipline). Every producer is idempotent — it upserts the
current signal via :func:`butlers.context_bus.set_context` and clears it via
:func:`butlers.context_bus.clear_context` on the reverse transition — and every
signal carries a bounded TTL, so a crashed producer never leaves context
permanently pinned; the signal simply expires.

Producers and their sources
---------------------------
- **calendar → meeting / focused** (writer ``general``): the currently-active
  event in the general butler's ``calendar_events`` table. A focus-block title
  maps to ``focused``; everything else maps to ``meeting``. Expiry is the
  event's own end time.
- **home → at_home** (writer ``home``): fresh ``person.*`` / ``device_tracker.*``
  presence rows in ``ha_entity_snapshot``. A stale snapshot never asserts
  presence (freshness gate); once presence reads away, the signal is cleared.
- **travel → traveling** (writer ``travel``): a currently-underway trip in
  ``travel.trips`` (an active trip is the container for its legs). Cleared when
  no trip is underway.
- **health → sleeping** (writer ``health``): the owner-declared quiet-hours
  window in ``public.approvals_policy``. Setting ``sleeping`` here activates the
  already-shipped notify sleeping-gate. Expiry is the wake time (window end).

Explicit ``dnd`` / ``sick`` signals are user-initiated and set through the
``set_context`` / ``check_context`` MCP tools (general module), not a producer.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg

from butlers.context_bus import (
    ContextSignal,
    clear_context,
    set_context,
)
from butlers.core.approvals_policy import (
    get_approvals_policy_quiet_hours,
    policy_quiet_hours_deliver_at,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Calendar producer (writer: general) — meeting / focused
# ---------------------------------------------------------------------------

# Case-insensitive substrings that mark a calendar block as deep-focus rather
# than a meeting. Deterministic and explainable — no LLM classification.
_FOCUS_TITLE_MARKERS: tuple[str, ...] = (
    "focus",
    "deep work",
    "deep-work",
    "heads down",
    "heads-down",
    "no meetings",
    "do not disturb",
)


def classify_calendar_signal(title: str | None) -> ContextSignal:
    """Classify a currently-active calendar event as ``focused`` or ``meeting``.

    A title containing any :data:`_FOCUS_TITLE_MARKERS` substring (case
    insensitive) is a focus block; every other event is treated as a meeting.
    """
    lowered = (title or "").lower()
    if any(marker in lowered for marker in _FOCUS_TITLE_MARKERS):
        return ContextSignal.focused
    return ContextSignal.meeting


async def run_calendar_context_producer(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Publish ``meeting`` / ``focused`` from the general butler's live calendar.

    Reads the currently-active confirmed, non-all-day event from
    ``calendar_events`` (resolved via the general schema search_path) and sets
    the matching signal with the event's end time as expiry. When no event is
    active, both ``meeting`` and ``focused`` are cleared. Idempotent: safe to
    run on any cadence.
    """
    del job_args
    row = await pool.fetchrow(
        """
        SELECT title, ends_at
        FROM calendar_events
        WHERE status = 'confirmed'
          AND all_day = false
          AND starts_at <= now()
          AND ends_at > now()
        ORDER BY starts_at DESC
        LIMIT 1
        """
    )

    if row is None:
        # No live event — retract any stale meeting/focused assertion.
        await clear_context(pool, "general", ContextSignal.meeting.value)
        await clear_context(pool, "general", ContextSignal.focused.value)
        return {"signal": None, "cleared": ["meeting", "focused"]}

    signal = classify_calendar_signal(row["title"])
    other = ContextSignal.focused if signal is ContextSignal.meeting else ContextSignal.meeting

    await set_context(
        pool,
        butler_name="general",
        signal_type=signal.value,
        value=row["title"],
        expires_at=row["ends_at"],
        confidence=1.0,
        metadata={"source": "calendar", "title": row["title"]},
    )
    # Clear the sibling signal so a meeting→focus transition is immediate.
    await clear_context(pool, "general", other.value)
    return {"signal": signal.value, "value": row["title"], "cleared": [other.value]}


# ---------------------------------------------------------------------------
# Home-presence producer (writer: home) — at_home
# ---------------------------------------------------------------------------

# HA entity_id prefixes that carry owner presence.
_PRESENCE_ENTITY_PREFIXES: tuple[str, ...] = ("person.", "device_tracker.")
# A presence snapshot older than this is ignored — a dead HA feed must never
# assert (or hold) presence.
_PRESENCE_FRESHNESS = timedelta(minutes=30)
# Bounded refresh window: if the producer stops, at_home self-heals within
# roughly two run cadences rather than lingering the full 12h default TTL.
_AT_HOME_REFRESH_TTL = timedelta(minutes=25)


def resolve_presence(
    rows: list[dict[str, Any]] | list[asyncpg.Record],
    *,
    now: datetime,
    freshness: timedelta = _PRESENCE_FRESHNESS,
) -> bool | None:
    """Decide owner presence from HA snapshot rows.

    Returns ``True`` if any *fresh* presence entity reads ``home``, ``False`` if
    fresh presence entities exist but none read ``home``, and ``None`` when
    there is no fresh presence data at all (unknown — neither assert nor clear).

    Each row must expose ``entity_id``, ``state`` and ``captured_at``.
    """
    cutoff = now - freshness
    saw_fresh = False
    for row in rows:
        entity_id = row["entity_id"] or ""
        if not entity_id.startswith(_PRESENCE_ENTITY_PREFIXES):
            continue
        captured_at = row["captured_at"]
        if captured_at is None:
            continue
        if captured_at.tzinfo is None:
            captured_at = captured_at.replace(tzinfo=UTC)
        if captured_at < cutoff:
            continue
        saw_fresh = True
        if (row["state"] or "").strip().lower() == "home":
            return True
    if saw_fresh:
        return False
    return None


async def run_home_presence_context_producer(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Publish ``at_home`` from fresh Home Assistant presence entities.

    Sets ``at_home`` when a fresh ``person.*``/``device_tracker.*`` snapshot
    reads ``home``; clears it when fresh presence reads away; leaves the signal
    untouched when there is no fresh presence data (avoids flapping on a stale
    feed — the existing signal expires on its own TTL).
    """
    del job_args
    now = datetime.now(UTC)
    rows = await pool.fetch(
        """
        SELECT entity_id, state, captured_at
        FROM ha_entity_snapshot
        WHERE entity_id LIKE 'person.%' OR entity_id LIKE 'device_tracker.%'
        """
    )
    presence = resolve_presence(rows, now=now)

    if presence is True:
        await set_context(
            pool,
            butler_name="home",
            signal_type=ContextSignal.at_home.value,
            expires_at=now + _AT_HOME_REFRESH_TTL,
            confidence=1.0,
            metadata={"source": "ha_presence"},
        )
        return {"signal": "at_home", "presence": "home"}
    if presence is False:
        await clear_context(pool, "home", ContextSignal.at_home.value)
        return {"signal": None, "presence": "away", "cleared": ["at_home"]}
    return {"signal": None, "presence": "unknown"}


# ---------------------------------------------------------------------------
# Travel producer (writer: travel) — traveling
# ---------------------------------------------------------------------------


async def run_travel_context_producer(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Publish ``traveling`` from a currently-underway trip.

    A trip is underway when its status is ``active`` or when today falls inside
    a ``planned``/``active`` trip's ``[start_date, end_date]`` window (the trip
    is the container for its legs). Sets ``traveling`` with the destination as
    value; clears it when no trip is underway. Uses the default ``traveling``
    TTL as a crash backstop; the clear path handles the normal trip end.
    """
    del job_args
    row = await pool.fetchrow(
        """
        SELECT destination, end_date, status
        FROM travel.trips
        WHERE status = 'active'
           OR (status IN ('planned', 'active')
               AND start_date <= current_date
               AND end_date >= current_date)
        ORDER BY (status = 'active') DESC, start_date ASC
        LIMIT 1
        """
    )

    if row is None:
        await clear_context(pool, "travel", ContextSignal.traveling.value)
        return {"signal": None, "cleared": ["traveling"]}

    await set_context(
        pool,
        butler_name="travel",
        signal_type=ContextSignal.traveling.value,
        value=row["destination"],
        confidence=1.0,
        metadata={"source": "travel_trip", "status": row["status"]},
    )
    return {"signal": "traveling", "value": row["destination"]}


# ---------------------------------------------------------------------------
# Sleep-window producer (writer: health) — sleeping
# ---------------------------------------------------------------------------


async def run_sleep_window_context_producer(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Publish ``sleeping`` from the owner-declared quiet-hours window.

    Reads ``public.approvals_policy`` (the same owner-declared window the notify
    gate consults directly) and, when the current time in the policy timezone
    falls inside the quiet window, asserts ``sleeping`` with the wake time as
    expiry — activating the already-shipped notify sleeping-gate. Clears
    ``sleeping`` outside the window.
    """
    del job_args
    try:
        policy = await get_approvals_policy_quiet_hours(pool)
    except Exception:
        logger.warning(
            "sleep producer: approvals_policy unavailable; clearing sleep signal",
            exc_info=True,
        )
        policy = None

    now = datetime.now(UTC)
    expires_at = policy_quiet_hours_deliver_at(policy, now=now)
    if expires_at is None:
        await clear_context(pool, "health", ContextSignal.sleeping.value)
        return {"signal": None, "reason": "not_quiet", "cleared": ["sleeping"]}

    assert policy is not None
    await set_context(
        pool,
        butler_name="health",
        signal_type=ContextSignal.sleeping.value,
        expires_at=expires_at,
        confidence=1.0,
        metadata={"source": "quiet_hours", "timezone": policy["timezone"]},
    )
    return {"signal": "sleeping", "expires_at": expires_at.isoformat()}


__all__ = [
    "classify_calendar_signal",
    "resolve_presence",
    "run_calendar_context_producer",
    "run_home_presence_context_producer",
    "run_sleep_window_context_producer",
    "run_travel_context_producer",
]
