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
- **home → at_home / in_space** (writer ``home``): fresh presence rows in
  ``ha_entity_snapshot`` belonging to the *owner* (per the
  ``home:presence:owner_entities`` state-store mapping) — a housemate's or
  guest's device never asserts or clears either signal. Freshness is judged on
  each row's HA-owned ``last_updated`` clock, not the connector's
  writer-stamped ``captured_at``. When ``ha_source_health`` shows HA itself is
  not confirmed reachable, or no owner presence entities are configured, the
  producer reports ``unmeasurable`` / ``unconfigured`` respectively and
  leaves both signals untouched rather than guessing (bu-8cdl1.11 slice 1).
  ``in_space`` additionally resolves which room/area the owner is currently in
  from those same owner-linked rows — from the entity's own state when it
  names a room/zone directly, else from Home Assistant area attributes — and
  degrades to the same untouched-self-heals-via-TTL behavior whenever the room
  cannot be freshly resolved (bu-8cdl1.11 slice 2).
- **travel → traveling** (writer ``travel``): a currently-underway trip in
  ``travel.trips`` (an active trip is the container for its legs). Cleared when
  no trip is underway.
- **travel → commuting** (writer ``travel``): OwnTracks-derived arrival lead
  time. While ``at_home`` is not currently asserted, fresh
  ``connectors.owntracks_points`` rows within the last
  :data:`_COMMUTING_FRESHNESS` window are compared against the owner-declared
  ``home`` entry in ``OWNTRACKS_PLACE_REFERENCES`` (the same env var
  :mod:`butlers.chronicler.adapters.owntracks_place_cluster` already parses).
  A closing distance to home over that window yields a genuine ETA: the signal
  is set with ``value`` naming the estimated arrival lead time and
  ``expires_at`` set to the estimated arrival instant itself, so the signal
  self-clears when the owner should have arrived. Already within the home
  radius clears ``commuting`` immediately (arrived); no fresh points, no
  configured home reference, or a distance that is not clearly closing leaves
  the signal untouched to self-heal via its own TTL rather than guessing
  (bu-8cdl1.11 slice 3).
- **health → sleeping** (writer ``health``): the owner-declared quiet-hours
  window in ``public.approvals_policy``. Setting ``sleeping`` here activates the
  already-shipped notify sleeping-gate. Expiry is the wake time (window end).

Explicit ``dnd`` / ``sick`` signals are user-initiated and set through the
``set_context`` / ``check_context`` MCP tools (general module), not a producer.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg

from butlers.chronicler.adapters.owntracks_place_cluster import (
    PlaceReference,
    haversine_meters,
    parse_place_references,
)
from butlers.context_bus import (
    ContextSignal,
    clear_context,
    is_user_in_context,
    set_context,
)
from butlers.core.approvals_policy import (
    get_approvals_policy_quiet_hours,
    policy_quiet_hours_deliver_at,
)
from butlers.core.state import state_get
from butlers.core.temporal.calendar_provenance import is_calendar_analysis_candidate
from butlers.jobs.home import HASourceUnmeasurableError, _extract_area, _require_ha_source_healthy

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
# in_space reuses the same bounded window (bu-8cdl1.11 slice 2) so a stopped
# producer self-heals a stale room the same way it self-heals stale presence.
_AT_HOME_REFRESH_TTL = timedelta(minutes=25)

# State values that never name a room -- generic presence/on-off vocabulary
# that a person./device_tracker. entity (or a binary_sensor) reports regardless
# of area. Any other state value is treated as a zone/room name reported
# directly by the entity (e.g. a Home Assistant zone-aware device tracker).
_NON_ROOM_STATE_VALUES = frozenset({"home", "not_home", "unknown", "unavailable", "on", "off", ""})


def _decode_ha_attributes(raw: Any) -> dict[str, Any]:
    """Decode a ``ha_entity_snapshot.attributes`` value into a dict.

    Mirrors the decode-if-string-else-empty-dict pattern already used by
    ``jobs/home.py`` readers of the same column (JSONB usually arrives
    pre-decoded via asyncpg's codec, but callers without it registered see a
    JSON string).
    """
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            return {}
    return raw if isinstance(raw, dict) else {}


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


def resolve_owner_room(
    rows: list[dict[str, Any]] | list[asyncpg.Record],
    *,
    owner_entity_ids: frozenset[str],
    now: datetime,
    freshness: timedelta = _PRESENCE_FRESHNESS,
) -> str | None:
    """Resolve the freshest room/area reported by an owner-linked entity.

    Returns the resolved room name, or ``None`` when no fresh owner-linked
    entity exposes one. ``None`` is deliberately ambiguous between "owner is
    home but no room data is available" and "no fresh owner data at all" --
    the caller combines this with :func:`resolve_owner_presence` to decide
    whether that means "leave the existing in_space signal to self-heal via
    its TTL" or "owner confirmed away, clear it", the same three-way handling
    ``resolve_owner_presence`` already applies to ``at_home``.

    A room is read from the entity's own ``state`` when that state is not one
    of :data:`_NON_ROOM_STATE_VALUES` (a Home Assistant zone-aware device
    tracker reports the zone/room name directly as its state); otherwise it
    falls back to :func:`butlers.jobs.home._extract_area` against the
    entity's attributes/entity_id/friendly_name -- the same area-resolution
    Home butler jobs already use for room-tagged sensors, reused here rather
    than reimplemented.

    Ties across multiple fresh owner-linked entities resolve to the
    most-recently-updated one. Each row must expose ``entity_id``, ``state``,
    ``last_updated`` and ``attributes``.
    """
    cutoff = now - freshness
    best: tuple[datetime, str] | None = None
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

        state = (row["state"] or "").strip()
        room = state if state.lower() not in _NON_ROOM_STATE_VALUES else None
        if room is None:
            attributes = _decode_ha_attributes(row["attributes"])
            room = _extract_area(
                attributes,
                entity_id=entity_id,
                friendly_name=attributes.get("friendly_name"),
            )
        if room is None:
            continue
        if best is None or last_updated > best[0]:
            best = (last_updated, room)
    return best[1] if best else None


async def run_home_presence_context_producer(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Publish ``at_home`` and ``in_space`` from the owner's HA presence entities.

    Sets ``at_home`` when a fresh owner-linked entity reads ``home``; clears it
    when fresh owner-linked presence reads away; leaves the signal untouched
    when there is no fresh owner-linked data (avoids flapping on a stale feed
    — the existing signal expires on its own TTL). Non-owner entities
    (housemates, guests) never assert or clear this signal.

    ``in_space`` (bu-8cdl1.11 slice 2) resolves which room/area the owner is
    currently in from those same owner-linked rows (see
    :func:`resolve_owner_room`): set alongside ``at_home`` when a room is
    resolved, cleared when the owner is confirmed away, and otherwise left
    untouched — the same self-heals-via-TTL treatment ``at_home`` gets on a
    stale or ambiguous feed, extended to room granularity rather than
    reinvented.

    Reports ``unmeasurable`` instead of guessing when the HA source itself is
    not confirmed healthy (``ha_source_health`` — bu-8cdl1.12 slice 1's guard,
    reused rather than reimplemented), and ``unconfigured`` when no owner
    presence entities are on file — neither case touches either signal, so a
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
        "SELECT entity_id, state, attributes, last_updated "
        "FROM ha_entity_snapshot WHERE entity_id = ANY($1)",
        list(owner_entity_ids),
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
        room = resolve_owner_room(rows, owner_entity_ids=owner_entity_ids, now=now)
        if room is not None:
            await set_context(
                pool,
                butler_name="home",
                signal_type=ContextSignal.in_space.value,
                value=room,
                expires_at=now + _AT_HOME_REFRESH_TTL,
                confidence=1.0,
                metadata={"source": "ha_presence"},
            )
        return {"signal": "at_home", "presence": "home", "room": room}
    if presence is False:
        await clear_context(pool, "home", ContextSignal.at_home.value)
        await clear_context(pool, "home", ContextSignal.in_space.value)
        return {"signal": None, "presence": "away", "cleared": ["at_home", "in_space"]}
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
# Commuting/ETA producer (writer: travel) — commuting
# ---------------------------------------------------------------------------

# A durable OwnTracks point older than this is never used to derive commuting
# state -- a device that stopped reporting must never hold a stale ETA.
_COMMUTING_FRESHNESS = timedelta(minutes=20)

# The home-to-owner distance must shrink by at least this much across the
# fresh window before the trend counts as "closing" -- filters ordinary GPS
# jitter and a stationary-but-imprecise fix from reading as commuting.
_COMMUTING_MIN_CLOSING_METERS = 100.0

# The OWNTRACKS_PLACE_REFERENCES entry that names the owner's home. Matched
# case-insensitively against each reference's label.
_HOME_PLACE_LABEL = "home"


@dataclass(frozen=True)
class CommutingEta:
    """Outcome of :func:`resolve_commuting_eta`.

    ``arrived`` means the freshest point already sits inside the home
    reference's radius -- the caller clears ``commuting`` rather than setting
    an ETA. Otherwise ``distance_meters``, ``eta_seconds`` and ``eta_at`` carry
    the derived arrival lead time.
    """

    arrived: bool
    distance_meters: float
    eta_seconds: float | None = None
    eta_at: datetime | None = None


def resolve_commuting_eta(
    points: list[dict[str, Any]] | list[asyncpg.Record],
    *,
    home: PlaceReference,
    now: datetime,
    freshness: timedelta = _COMMUTING_FRESHNESS,
    min_closing_meters: float = _COMMUTING_MIN_CLOSING_METERS,
) -> CommutingEta | None:
    """Derive arrival lead time from OwnTracks points, or ``None`` when ambiguous.

    Considers only points with ``ts`` inside ``[now - freshness, now]``. With
    no fresh points at all, returns ``None`` (unmeasurable -- leave any prior
    signal untouched). With at least one fresh point, a freshest-point distance
    inside ``home.radius_m`` returns ``arrived=True`` regardless of how many
    points are available -- being inside the geofence is decisive on its own.

    Otherwise, a genuine ETA requires at least two fresh points so a closing
    speed can be derived: the distance-to-home at the oldest fresh point minus
    the distance-to-home at the freshest fresh point is the closing distance
    over the elapsed interval between them. A single fresh point, a
    non-positive elapsed interval, or a closing distance below
    ``min_closing_meters`` (stationary, moving away, or GPS jitter) all return
    ``None`` -- the caller leaves the existing signal to self-heal via its own
    TTL rather than asserting a guess.

    Each point must expose ``ts``, ``lat`` and ``lon``.
    """
    cutoff = now - freshness
    fresh: list[tuple[datetime, float, float]] = []
    for point in points:
        ts = point["ts"]
        if ts is None:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        if ts < cutoff or ts > now:
            continue
        fresh.append((ts, point["lat"], point["lon"]))
    if not fresh:
        return None

    fresh.sort(key=lambda item: item[0])
    latest_ts, latest_lat, latest_lon = fresh[-1]
    distance_latest = haversine_meters(latest_lat, latest_lon, home.lat, home.lon)

    if distance_latest <= home.radius_m:
        return CommutingEta(arrived=True, distance_meters=distance_latest)

    if len(fresh) < 2:
        return None

    earliest_ts, earliest_lat, earliest_lon = fresh[0]
    elapsed_seconds = (latest_ts - earliest_ts).total_seconds()
    if elapsed_seconds <= 0:
        return None

    distance_earliest = haversine_meters(earliest_lat, earliest_lon, home.lat, home.lon)
    closing_meters = distance_earliest - distance_latest
    if closing_meters < min_closing_meters:
        return None

    speed_mps = closing_meters / elapsed_seconds
    eta_seconds = distance_latest / speed_mps
    return CommutingEta(
        arrived=False,
        distance_meters=distance_latest,
        eta_seconds=eta_seconds,
        eta_at=now + timedelta(seconds=eta_seconds),
    )


def _load_home_place_reference() -> PlaceReference | None:
    """Resolve the owner's ``home`` entry from ``OWNTRACKS_PLACE_REFERENCES``.

    Reused, not reimplemented: the same env var and parser
    :mod:`butlers.chronicler.adapters.owntracks_place_cluster` already uses to
    label place clusters. Re-read on every call (cheap, no I/O) so an operator
    correction takes effect on the next scheduled tick without a restart --
    mirrors ``run_project_owntracks_place_cluster``'s degrade-gracefully
    handling of the same env var: a malformed value logs a warning and is
    treated as unconfigured rather than wedging this job on every run.
    """
    raw = os.environ.get("OWNTRACKS_PLACE_REFERENCES", "")
    try:
        references = parse_place_references(raw)
    except ValueError:
        logger.warning(
            "context_producer_commuting_eta: malformed OWNTRACKS_PLACE_REFERENCES; "
            "treating as unconfigured",
            exc_info=True,
        )
        return None
    for reference in references:
        if reference.label.strip().lower() == _HOME_PLACE_LABEL:
            return reference
    return None


async def run_commuting_eta_context_producer(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Publish ``commuting`` with a genuine arrival ETA from OwnTracks GPS data.

    Skips entirely while ``at_home`` is currently asserted (by any butler) --
    an already-home owner cannot be arriving home, so any lingering
    ``commuting`` assertion is cleared immediately rather than left to expire.
    Otherwise resolves the owner's ``home`` reference point from
    ``OWNTRACKS_PLACE_REFERENCES`` and the freshest ``connectors.owntracks_points``
    rows (see :func:`resolve_commuting_eta`): a closing distance over the fresh
    window sets ``commuting`` with ``expires_at`` pinned to the estimated
    arrival instant itself, so the signal naturally clears once the owner
    should have arrived; already inside the home radius clears ``commuting``
    immediately (arrived); anything ambiguous (no fresh points, no configured
    home reference, or a distance that is not clearly closing) leaves any
    prior signal untouched to self-heal via its own TTL.
    """
    del job_args
    now = datetime.now(UTC)

    if await is_user_in_context(pool, ContextSignal.at_home.value):
        await clear_context(pool, "travel", ContextSignal.commuting.value)
        return {"signal": None, "reason": "at_home", "cleared": ["commuting"]}

    home = _load_home_place_reference()
    if home is None:
        return {"signal": None, "reason": "unconfigured"}

    rows = await pool.fetch(
        "SELECT ts, lat, lon FROM connectors.owntracks_points WHERE ts >= $1 ORDER BY ts ASC",
        now - _COMMUTING_FRESHNESS,
    )
    result = resolve_commuting_eta(rows, home=home, now=now)

    if result is None:
        return {"signal": None, "reason": "unmeasurable"}

    if result.arrived:
        await clear_context(pool, "travel", ContextSignal.commuting.value)
        return {"signal": None, "reason": "arrived", "cleared": ["commuting"]}

    assert result.eta_seconds is not None and result.eta_at is not None
    eta_minutes = round(result.eta_seconds / 60)
    await set_context(
        pool,
        butler_name="travel",
        signal_type=ContextSignal.commuting.value,
        value=f"home in ~{eta_minutes} min",
        expires_at=result.eta_at,
        confidence=0.6,
        metadata={
            "source": "owntracks",
            "distance_meters": round(result.distance_meters),
            "eta_minutes": round(result.eta_seconds / 60, 1),
        },
    )
    return {
        "signal": "commuting",
        "eta_minutes": eta_minutes,
        "distance_meters": round(result.distance_meters),
    }


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
    "CommutingEta",
    "classify_calendar_signal",
    "resolve_commuting_eta",
    "resolve_owner_presence",
    "run_calendar_context_producer",
    "run_commuting_eta_context_producer",
    "run_home_presence_context_producer",
    "run_sleep_window_context_producer",
    "run_travel_context_producer",
]
