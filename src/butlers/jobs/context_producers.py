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
  presence rows in ``ha_entity_snapshot`` belonging to the *owner* (per the
  ``home:presence:owner_entities`` state-store mapping) — a housemate's or
  guest's device never asserts or clears this signal. Freshness is judged on
  each row's HA-owned ``last_updated`` clock, not the connector's
  writer-stamped ``captured_at``. When ``ha_source_health`` shows HA itself is
  not confirmed reachable, or no owner presence entities are configured, the
  producer reports ``unmeasurable`` / ``unconfigured`` respectively and
  leaves the signal untouched rather than guessing (bu-8cdl1.11 slice 1).
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
from butlers.core.state import state_get
from butlers.core.temporal.calendar_provenance import is_calendar_analysis_candidate
from butlers.jobs.home import HASourceUnmeasurableError, _require_ha_source_healthy

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

    Reads the latest eligible active confirmed human event from
    ``calendar_events`` (resolved via the general schema search_path) and sets
    the matching signal with the event's end time as expiry. Explicitly
    butler-generated and legacy all-day-shaped rows remain projected but cannot
    assert context. When no eligible event is active, both ``meeting`` and
    ``focused`` are cleared. Idempotent: safe to run on any cadence.
    """
    del job_args
    rows = await pool.fetch(
        """
        SELECT title, starts_at, ends_at, timezone, all_day, metadata
        FROM calendar_events
        WHERE status = 'confirmed'
          AND all_day = false
          AND starts_at <= now()
          AND ends_at > now()
        ORDER BY starts_at DESC
        """
    )
    row = next(
        (
            candidate
            for candidate in rows
            if is_calendar_analysis_candidate(
                metadata=candidate["metadata"],
                all_day=candidate["all_day"],
                starts_at=candidate["starts_at"],
                ends_at=candidate["ends_at"],
                timezone=candidate["timezone"],
            )
        ),
        None,
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

# State-store key holding the owner's HA presence entity ids (a JSON list of
# ``person.*`` / ``device_tracker.*`` entity ids). Absent, malformed, or empty
# means "unconfigured" — at_home must never fall back to treating every
# person/device_tracker entity (housemates, guests) as the owner.
_OWNER_PRESENCE_ENTITIES_KEY = "home:presence:owner_entities"
# A presence entity whose HA-owned last_updated clock is older than this is
# ignored — a device tracker that stopped reporting must never assert (or
# hold) presence.
_PRESENCE_FRESHNESS = timedelta(minutes=30)
# Bounded refresh window: if the producer stops, at_home self-heals within
# roughly two run cadences rather than lingering the full 12h default TTL.
_AT_HOME_REFRESH_TTL = timedelta(minutes=25)


async def _load_owner_presence_entity_ids(pool: asyncpg.Pool) -> frozenset[str] | None:
    """Load the owner's HA presence entity ids from the state store.

    Returns ``None`` when unconfigured — no key, a non-list value, or an empty
    list — so the caller can report an explicit ``unconfigured`` presence
    state instead of silently treating any fresh person/device_tracker entity
    as the owner (bu-8cdl1.11 slice 1: the fleet-wide-any-entity defect this
    producer fixes).
    """
    raw = await state_get(pool, _OWNER_PRESENCE_ENTITIES_KEY)
    if not isinstance(raw, list):
        return None
    entity_ids = frozenset(item for item in raw if isinstance(item, str) and item)
    if not entity_ids:
        return None
    return entity_ids


def resolve_owner_presence(
    rows: list[dict[str, Any]] | list[asyncpg.Record],
    *,
    owner_entity_ids: frozenset[str],
    now: datetime,
    freshness: timedelta = _PRESENCE_FRESHNESS,
) -> bool | None:
    """Decide owner presence from HA snapshot rows, scoped to the owner's entities.

    Returns ``True`` if any *fresh*, owner-linked entity reads ``home``,
    ``False`` if fresh owner-linked entities exist but none read ``home``, and
    ``None`` when there is no fresh owner-linked data at all (unknown —
    neither assert nor clear).

    Only rows whose ``entity_id`` is a member of *owner_entity_ids* are
    considered — a housemate's or guest's device must never assert or clear
    ``at_home``. Freshness is judged against each row's HA-owned
    ``last_updated`` clock rather than the connector's writer-stamped
    ``captured_at``: the poll cycle re-stamps ``captured_at`` every run
    regardless of whether the entity itself changed, so a ``captured_at``-based
    cutoff cannot detect a genuinely stale presence feed.

    Each row must expose ``entity_id``, ``state`` and ``last_updated``.
    """
    cutoff = now - freshness
    saw_fresh = False
    for row in rows:
        entity_id = row["entity_id"] or ""
        if entity_id not in owner_entity_ids:
            continue
        last_updated = row["last_updated"]
        if last_updated is None:
            continue
        if last_updated.tzinfo is None:
            last_updated = last_updated.replace(tzinfo=UTC)
        if last_updated < cutoff:
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
    """Publish ``at_home`` from the owner's fresh Home Assistant presence entities.

    Sets ``at_home`` when a fresh owner-linked ``person.*``/``device_tracker.*``
    entity reads ``home``; clears it when fresh owner-linked presence reads
    away; leaves the signal untouched when there is no fresh owner-linked
    data (avoids flapping on a stale feed — the existing signal expires on its
    own TTL). Non-owner entities (housemates, guests) never assert or clear
    this signal.

    Reports ``unmeasurable`` instead of guessing when the HA source itself is
    not confirmed healthy (``ha_source_health`` — bu-8cdl1.12 slice 1's guard,
    reused rather than reimplemented), and ``unconfigured`` when no owner
    presence entities are on file — neither case touches the signal, so a
    prior assertion self-heals via its bounded TTL instead of a producer
    guessing in either direction.
    """
    del job_args
    now = datetime.now(UTC)

    try:
        await _require_ha_source_healthy(pool)
    except HASourceUnmeasurableError:
        return {"signal": None, "presence": "unmeasurable"}

    owner_entity_ids = await _load_owner_presence_entity_ids(pool)
    if owner_entity_ids is None:
        return {"signal": None, "presence": "unconfigured"}

    rows = await pool.fetch(
        """
        SELECT entity_id, state, last_updated
        FROM ha_entity_snapshot
        WHERE entity_id LIKE 'person.%' OR entity_id LIKE 'device_tracker.%'
        """
    )
    presence = resolve_owner_presence(rows, owner_entity_ids=owner_entity_ids, now=now)

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

    bu-317s5 (domain-event bus slice 2): also best-effort publishes the
    ``travel.trip_active`` domain event exactly once per trip's activation --
    this same query already detects "a trip is underway right now," so rather
    than a second deterministic job re-deriving the same condition, this
    reuses the detection and fans the transition out via
    ``publish_domain_event_once`` (memoized on the trip id, so the 15-minute
    poll cadence re-observing the same active trip does not re-publish or
    re-wake subscribers on every tick). Health is seeded (core_189) as a
    standing subscriber to front-load medication prep. A bus hiccup here must
    never break the context-bus signal write this producer is otherwise
    responsible for.
    """
    del job_args
    row = await pool.fetchrow(
        """
        SELECT id, name, destination, start_date, end_date, status
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
    await _publish_trip_active_event(pool, row)
    return {"signal": "traveling", "value": row["destination"]}


async def _publish_trip_active_event(pool: asyncpg.Pool, row: asyncpg.Record) -> None:
    """Best-effort, at-most-once-per-trip publish of ``travel.trip_active``.

    Isolated from :func:`run_travel_context_producer` so a domain-event-bus
    failure (fan-out hiccup, Switchboard unavailable) can never fail the
    context-bus signal write that already succeeded above -- mirrors
    ``roster/travel/modules/tools.py::record_booking``'s best-effort publish
    of ``travel.trip_booked``.
    """
    from butlers.core.tool_call_capture import get_current_switchboard_client
    from butlers.core_tools._domain_events import publish_domain_event_once

    trip_id = str(row["id"])
    try:
        await publish_domain_event_once(
            pool,
            get_current_switchboard_client(),
            event_type="travel.trip_active",
            source_butler="travel",
            dedup_namespace="travel.trip_active",
            dedup_key=trip_id,
            payload={
                "trip_id": trip_id,
                "name": row["name"],
                "destination": row["destination"],
                "start_date": row["start_date"].isoformat(),
                "end_date": row["end_date"].isoformat(),
                "status": row["status"],
            },
        )
    except Exception:
        logger.warning(
            "context_producer_travel: failed to publish travel.trip_active for trip_id=%s",
            trip_id,
            exc_info=True,
        )


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
    "resolve_owner_presence",
    "run_calendar_context_producer",
    "run_home_presence_context_producer",
    "run_sleep_window_context_producer",
    "run_travel_context_producer",
]
