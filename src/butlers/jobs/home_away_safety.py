"""Home away safety: freezer, away door and sensing-blind conditions (bu-jv1moa.87).

One deterministic job, ``home_away_safety``, keeps three owner conditions
(openspec change specify-home-away-safety, home-deterministic-jobs requirements 002 to 007):

* ``home:freezer`` (fingerprint ``entity_id``): a declared freezer read above the owner's maximum
  on every run for the owner's dwell.  It resolves only on a known, fresh reading at or below the
  maximum minus the owner's hysteresis.
* ``home:away-door`` (fingerprint ``entity_id`` plus the absence's leave edge): an armed
  ``departure_watch`` item not secure, while the owner is confirmed away, for the item's
  ``away_alert_after_minutes`` counted from the later of its last change and the leave.
* ``home:sensing-blind`` (fingerprint the sensing group plus the leave edge): while the owner is
  away, a declared role (leak, smoke, CO, gas, freezer, doors) unreadable as a whole, or the HA
  lease lost, for the owner's ``blind_after_minutes``.

Each run also writes the canonical ``home.sensing`` slot for the one installation sensing group
as ``perception_sensing_writer`` (``BUTLERS_SENSING_WRITER_DSN``).

Hard rules:

* Every threshold is the owner's; nothing missing is filled in (``unconfigured``, silent).
* Unknown is never clear: an unhealthy lease, an unreadable, stale or wrong-unit reading, or an
  uncertain presence premise carries an open condition forward and opens nothing.  Only an
  item's own reading, the owner's confirmed return, a retracted leave or a removed declaration
  resolves anything, each with its reason.
* Dwell comes from consecutive runs (``home.away_safety_streaks``): a gap of more than two job
  periods restarts a streak, so a gap can delay an open but never fabricate one.
* Delivery is the routine ``_send_notify`` path (quiet hours apply).  ``notified_at`` and the
  latest-held-rung marker are set only when the send reports ``sent``.  Away-door and blind sends
  re-read the presence premise immediately before sending.
* No model, no Home Assistant service call, no prepared action.  Only owner labels and fixed
  words reach a message; HA's ``friendly_name`` is never read.
* Runs serialize on a transaction advisory lock; a concurrent run returns ``busy``.

Dark: ``HOME_AWAY_SAFETY_LIVE`` is off and no roster schedules the job.  Activation (P2 in the
specify-home-away-safety design) registers the sensing group, the sensing writer and owner
reader logins, the presence reducer schedule, this job's schedule and the flag, in one
owner-approved change.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
from collections import Counter
from collections.abc import Awaitable, Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID, uuid5
from zoneinfo import ZoneInfo

import asyncpg

from butlers.core.owner_conditions import (
    Observation,
    compute_fingerprint,
    reconcile_snapshot,
    resolve_condition,
)
from butlers.jobs.home_departure import Edge, PresenceRead

logger = logging.getLogger(__name__)

#: Master switch. Off: the job returns ``{"status": "dark"}`` without reading or writing.
HOME_AWAY_SAFETY_LIVE = False

FREEZER_SOURCE = "home:freezer"
AWAY_DOOR_SOURCE = "home:away-door"
BLIND_SOURCE = "home:sensing-blind"
SOURCES: tuple[str, ...] = (FREEZER_SOURCE, AWAY_DOOR_SOURCE, BLIND_SOURCE)

#: Two job periods: a streak whose last run is older than this restarts instead of bridging.
STREAK_GAP = timedelta(minutes=4)
#: The opt-in ladder, open-relative: 1 hour after opening, 4 hours later, then every 24 hours.
INITIAL_GRACE_SECONDS = 3600.0
ESCALATION_ADVANCE: dict[str, tuple[str, timedelta]] = {
    "L0": ("L1", timedelta(hours=4)),
    "L1": ("L2", timedelta(hours=24)),
    "L2": ("L3", timedelta(hours=24)),
    "L3": ("L3", timedelta(hours=24)),
}
#: An undelivered condition is retried by later runs after this long.
RETRY_AFTER = timedelta(seconds=120)
#: Delivery markers a concurrent confirmation must keep.
MARKERS: tuple[str, ...] = ("notified_at", "rung_due_at", "rung_notified_at")

SENSING_WRITER_DSN_ENV = "BUTLERS_SENSING_WRITER_DSN"
SENSING_SOURCE = "home.sensing"
SENSING_EVIDENCE_SOURCE = "home_away_safety"
_SENSING_NAMESPACE = UUID("7c0f3c55-3d1e-5a8f-9a57-2f4f8b1d6e21")
#: The one installation sensing group: the slot subject and its ``sensing_group`` reference.
#: The activation act registers exactly this id in the ``home.sensing`` profile; nothing in Home
#: can register or change it.
SENSING_GROUP = uuid5(_SENSING_NAMESPACE, "sensing_group:home")
SENSING_EVIDENCE = uuid5(_SENSING_NAMESPACE, "evidence:home")
#: How long one written slot stays fresh; the profile's ``max_age_seconds`` bounds it further.
SENSING_FRESHNESS = timedelta(minutes=10)

SAFETY_ROLES: tuple[str, ...] = ("leak", "smoke", "co", "gas")
#: Every role blindness is judged for, in message order, with its fixed message word.
ROLE_WORDS: dict[str, str] = {
    "leak": "leak",
    "smoke": "smoke",
    "co": "CO",
    "gas": "gas",
    "freezer": "freezer",
    "away_door": "doors",
}

_LOCK_KEY = "home:away-safety"
_FINGERPRINT_VERSION = 1
_UNREADABLE_STATES = frozenset({"", "unavailable", "unknown"})
_MARKDOWN_CHARS = re.compile(r"[*_`\[\]\x00-\x1f\x7f]")
_LABELS = {"version": 1, "label_instr": ["world"], "label_data": ["world"], "label_sens": []}

NotifyOutcome = Literal["sent", "suppressed", "failed"]
NotifyFn = Callable[[str], Awaitable[NotifyOutcome]]
ReadPresence = Callable[[datetime, Collection[UUID]], Awaitable[PresenceRead | None]]
SensingState = Literal["available", "degraded", "unavailable", "unmeasurable", "unconfigured"]
WriteOutcome = Literal["written", "unconfigured", "unavailable"]
WriteSensing = Callable[[SensingState, datetime], Awaitable[WriteOutcome]]
FreezerRun = Literal["warm", "below", "cold", "unknown"]
DoorState = Literal["secure", "open", "due", "unknown"]
PremiseStatus = Literal["away", "inside", "unknown"]
LeaveFate = Literal["current", "returned", "retracted", "unknown"]


# ---------------------------------------------------------------------------
# Declarations and readings
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Freezer:
    """One owner-declared freezer; ``max_c`` and ``hysteresis_c`` are always Celsius."""

    entity_id: str
    owner_label: str
    unit: str
    max_c: float
    hysteresis_c: float
    dwell_minutes: int
    max_reading_age_minutes: int


@dataclass(frozen=True)
class Door:
    """One ``departure_watch`` item; ``away_minutes`` is ``None`` when it is not armed."""

    entity_id: str
    owner_label: str
    secure_states: tuple[str, ...]
    away_minutes: int | None


@dataclass(frozen=True)
class Reading:
    """One ``ha_entity_snapshot`` row; only the fields the rules use, never ``friendly_name``."""

    state: str | None
    unit: str | None = None
    last_updated: datetime | None = None
    last_changed: datetime | None = None


@dataclass(frozen=True)
class Streak:
    start: datetime
    last_run_at: datetime


@dataclass(frozen=True)
class Premise:
    status: PremiseStatus
    leave: Edge | None = None
    edges: tuple[Edge, ...] = ()


# ---------------------------------------------------------------------------
# Pure rules
# ---------------------------------------------------------------------------


def readable(reading: Reading | None) -> bool:
    return reading is not None and (reading.state or "").strip().lower() not in _UNREADABLE_STATES


def sensing_state(
    *, lease_healthy: bool, entity_ids: Collection[str], readings: Mapping[str, Reading]
) -> SensingState:
    """The ``home.sensing`` value: lease first, then the readability of the declared entities."""
    if not entity_ids:
        return "unconfigured"
    if not lease_healthy:
        return "unmeasurable"
    unread = sum(1 for e in entity_ids if not readable(readings.get(e)))
    if unread == 0:
        return "available"
    return "unavailable" if unread == len(entity_ids) else "degraded"


def blind_roles(
    *,
    lease_healthy: bool,
    roles: Mapping[str, Collection[str]],
    readings: Mapping[str, Reading],
) -> frozenset[str]:
    """Declared roles that cannot be seen: all of them without the lease, else whole-role losses.

    A role with no declared entity is never blind, so no message claims coverage the owner did
    not set up.  A partial loss within a role is not blind.
    """
    declared = {role for role, ids in roles.items() if ids}
    if not lease_healthy:
        return frozenset(declared)
    return frozenset(
        role for role in declared if not any(readable(readings.get(e)) for e in roles[role])
    )


def advance_streak(prior: Streak | None, qualifies: bool, now: datetime) -> Streak | None:
    """Extend a streak by this run, end it, or restart it after a gap in runs."""
    if not qualifies:
        return None
    if prior is None or now - prior.last_run_at > STREAK_GAP:
        return Streak(now, now)
    return Streak(prior.start, now)


def _number(state: str | None) -> float | None:
    try:
        value = float((state or "").strip())
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def freezer_celsius(freezer: Freezer, reading: Reading | None, *, now: datetime) -> float | None:
    """The reading in °C, or ``None`` when it is not a known, fresh, declared-unit number."""
    if reading is None or reading.last_updated is None:
        return None
    if reading.unit != freezer.unit:
        return None
    if now - reading.last_updated > timedelta(minutes=freezer.max_reading_age_minutes):
        return None
    value = _number(reading.state)
    if value is None:
        return None
    return (value - 32.0) * 5.0 / 9.0 if freezer.unit == "°F" else value


def freezer_run(
    freezer: Freezer, reading: Reading | None, *, lease_healthy: bool, now: datetime
) -> tuple[FreezerRun, float | None]:
    """``warm`` qualifies the streak; ``cold`` (at or below max - hysteresis) resolves."""
    celsius = freezer_celsius(freezer, reading, now=now) if lease_healthy else None
    if celsius is None:
        return "unknown", None
    if celsius > freezer.max_c:
        return "warm", celsius
    if celsius <= freezer.max_c - freezer.hysteresis_c:
        return "cold", celsius
    return "below", celsius


def door_state(
    door: Door,
    reading: Reading | None,
    *,
    leave_at: datetime,
    lease_healthy: bool,
    now: datetime,
) -> tuple[DoorState, datetime | None]:
    """Not secure continuously since ``max(last_changed, leave)`` for the item's duration.

    Returns the state and the instant the open interval is counted from.  Judged from
    ``last_changed`` (a state change), never ``last_updated``; without it the item is unknown.
    """
    if not lease_healthy or not readable(reading) or door.away_minutes is None:
        return "unknown", None
    assert reading is not None
    if (reading.state or "").strip().lower() in door.secure_states:
        return "secure", None
    if reading.last_changed is None:
        return "unknown", None
    since = max(reading.last_changed, leave_at)
    due = now - since >= timedelta(minutes=door.away_minutes)
    return ("due" if due else "open"), since


def _instant(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


def away_premise(read: PresenceRead | None, now: datetime) -> Premise:
    """Away only when the fresh known slot says outside and agrees with a non-late latest leave."""
    if read is None:
        return Premise("unknown")
    edges = tuple(sorted(read.edges, key=lambda e: e.confirmed_at, reverse=True))
    latest = next((e for e in edges if not e.retracted), None)
    presence: Any = None
    slot = read.slot
    if slot is not None and slot.get("state") == "known":
        fresh_until = _instant(slot.get("fresh_until"))
        value = slot.get("value")
        if fresh_until is not None and fresh_until > now and isinstance(value, Mapping):
            presence = value.get("presence")
    if latest is None or latest.late or presence is None:
        return Premise("unknown", edges=edges)
    if presence == "outside" and latest.direction == "leave":
        return Premise("away", leave=latest, edges=edges)
    if presence == "inside" and latest.direction == "enter":
        return Premise("inside", edges=edges)
    return Premise("unknown", edges=edges)


def leave_fate(premise: Premise, leave_id: UUID, leave_at: datetime) -> LeaveFate:
    """What became of an open condition's absence.  Unknown premise: nothing is concluded."""
    if premise.status == "unknown":
        return "unknown"
    if premise.leave is not None and premise.leave.id == leave_id:
        return "current"
    if any(e.id == leave_id and e.retracted for e in premise.edges):
        return "retracted"
    returned = any(
        e.direction == "enter" and not e.retracted and not e.late and e.confirmed_at > leave_at
        for e in premise.edges
    )
    if returned or (premise.leave is not None and premise.leave.confirmed_at > leave_at):
        return "returned"
    return "unknown"


def _clean(text: str, *, limit: int, fallback: str) -> str:
    return _MARKDOWN_CHARS.sub("", text).strip()[:limit] or fallback


def _minutes(span: timedelta) -> int:
    return max(0, int(span.total_seconds() // 60))


def freezer_message(
    freezer: Freezer, run: FreezerRun, celsius: float | None, since: datetime, now: datetime
) -> str:
    label = _clean(freezer.owner_label, limit=80, fallback="Freezer")
    if run == "warm" and celsius is not None:
        return (
            f"Freezer {label}: {celsius:.1f} °C, above {freezer.max_c:.1f} °C "
            f"for {_minutes(now - since)} min"
        )
    if run == "below" and celsius is not None:
        floor = freezer.max_c - freezer.hysteresis_c
        return f"Freezer {label}: {celsius:.1f} °C, not yet back to {floor:.1f} °C"
    return f"Freezer {label}: sensor not reporting"


def door_message(door: Door, state: str | None, since: datetime, now: datetime) -> str:
    label = _clean(door.owner_label, limit=80, fallback="Watched item")
    shown = _clean((state or "").strip().lower(), limit=32, fallback="not secure")
    return f"{label} still {shown} {_minutes(now - since)} min into your absence"


def door_unreadable_message(door: Door, leave_at: datetime, now: datetime) -> str:
    label = _clean(door.owner_label, limit=80, fallback="Watched item")
    return f"{label} not reporting {_minutes(now - leave_at)} min into your absence"


def blind_message(roles: Collection[str], since: datetime, tz: ZoneInfo) -> str:
    words = ", ".join(ROLE_WORDS[r] for r in ROLE_WORDS if r in roles)
    clock = since.astimezone(tz).strftime("%H:%M")
    return f"Home cannot see {words} since {clock}; those alerts cannot reach you"


def freezer_fingerprint(entity_id: str) -> str:
    return compute_fingerprint(FREEZER_SOURCE, _FINGERPRINT_VERSION, {"entity_id": entity_id})


def door_fingerprint(entity_id: str, leave_id: UUID) -> str:
    return compute_fingerprint(
        AWAY_DOOR_SOURCE,
        _FINGERPRINT_VERSION,
        {"entity_id": entity_id, "leave_edge_id": str(leave_id)},
    )


def blind_fingerprint(leave_id: UUID) -> str:
    return compute_fingerprint(
        BLIND_SOURCE,
        _FINGERPRINT_VERSION,
        {"sensing_group": str(SENSING_GROUP), "leave_edge_id": str(leave_id)},
    )


def late_detection(
    detected_since: datetime, dwell: timedelta, now: datetime
) -> dict[str, int] | None:
    """The lag past the response deadline (dwell plus two job periods), or ``None`` on time."""
    lag = now - detected_since - dwell
    return {"lag_seconds": int(lag.total_seconds())} if lag > STREAK_GAP else None


# ---------------------------------------------------------------------------
# The home.sensing writer
# ---------------------------------------------------------------------------


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def sensing_request(state: SensingState, now: datetime, generation: int) -> Any:
    """One ``perception_write`` request for the installation sensing group."""
    from butlers.perception.model import EventRequest

    now = now.astimezone(UTC)
    known = state in ("available", "degraded", "unavailable")
    digest = hashlib.sha256(
        json.dumps([state, _iso(now), generation], separators=(",", ":")).encode()
    ).hexdigest()[:40]
    return EventRequest(
        {
            "version": 1,
            "kind": "home.sensing",
            "subject": str(SENSING_GROUP),
            "source": SENSING_SOURCE,
            "event_key": f"home-sensing:{generation}:{digest}",
            "expected_generation": generation,
            "state": "known" if known else state,
            "observed_at": _iso(now),
            "effective_span": {"start": _iso(now), "end": _iso(now + SENSING_FRESHNESS)},
            "typed_value_or_ref": state if known else None,
            "evidence_refs": [
                {
                    "ref": str(SENSING_EVIDENCE),
                    "source": SENSING_EVIDENCE_SOURCE,
                    "expires_at": _iso(now + SENSING_FRESHNESS),
                }
            ],
            "labels": _LABELS,
            "at": _iso(now),
            "confirmed_at": _iso(now),
            "retracts_event_id": None,
        }
    )


async def write_sensing(state: SensingState, now: datetime) -> WriteOutcome:
    """Write the slot as ``perception_sensing_writer``.

    ``unconfigured`` without the writer login or when the profile refuses the group (not
    registered); ``unavailable`` on any other failure.  A stale generation is retried once.
    """
    from butlers.db import register_jsonb_codec
    from butlers.perception import PerceptionStore

    dsn = (os.environ.get(SENSING_WRITER_DSN_ENV) or "").strip()
    if not dsn:
        return "unconfigured"
    try:
        connection = await asyncpg.connect(dsn, command_timeout=15)
    except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError):
        logger.warning("home away safety: sensing writer connect failed")
        return "unavailable"
    try:
        await register_jsonb_codec(connection)
        store = PerceptionStore(connection)
        generation = 0
        for _ in range(2):
            outcome = await store.write(sensing_request(state, now, generation))
            status = outcome.get("status") if isinstance(outcome, dict) else None
            if status in ("committed", "historical"):
                return "written"
            current = outcome.get("generation") if isinstance(outcome, dict) else None
            if status != "conflict" or type(current) is not int:
                return "unavailable"
            generation = current
        return "unavailable"
    except asyncpg.InsufficientPrivilegeError:
        return "unconfigured"
    except asyncpg.PostgresError as exc:
        logger.warning(
            "home away safety: sensing write failed sqlstate=%s", getattr(exc, "sqlstate", None)
        )
        return "unavailable"
    except (asyncpg.InterfaceError, OSError):
        # A dropped writer connection is an outcome, never an abort of the whole run.
        logger.warning("home away safety: sensing writer connection lost")
        return "unavailable"
    finally:
        try:
            await connection.close()
        except (asyncpg.InterfaceError, OSError):
            logger.warning("home away safety: sensing writer close failed")


# ---------------------------------------------------------------------------
# Ledger plan and delivery
# ---------------------------------------------------------------------------


@dataclass
class _Plan:
    """One source's check-in: what to observe, resolve and possibly send this run."""

    observations: list[Observation] = field(default_factory=list)
    resolutions: dict[str, str] = field(default_factory=dict)
    messages: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: The leave edge per fingerprint, for the pre-send premise re-read (away-door, blind).
    leaves: dict[str, tuple[UUID, datetime]] = field(default_factory=dict)

    def observe(self, fingerprint: str, message: str, metadata: dict[str, Any]) -> None:
        self.observations.append(
            Observation(fingerprint=fingerprint, summary=message, metadata=metadata)
        )
        self.messages[fingerprint] = message
        self.metadata[fingerprint] = metadata

    def carry(self, fingerprint: str, message: str | None = None) -> None:
        """Carried forward, never resolved; ``message`` makes a due rung audible."""
        self.observations.append(Observation(fingerprint=fingerprint, confirm_only=True))
        if message is not None:
            self.messages[fingerprint] = message


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return dict(value) if isinstance(value, Mapping) else {}


def _due_first_send(prior: Mapping[str, Any] | None, transition: str, now: datetime) -> bool:
    if transition in ("opened", "reopened"):
        return True
    if prior is None or _as_dict(prior.get("metadata")).get("notified_at"):
        return False
    first = _instant(prior.get("first_detected_at"))
    return first is not None and now - first >= RETRY_AFTER


def _held_rung(metadata: Mapping[str, Any]) -> bool:
    due = _instant(metadata.get("rung_due_at"))
    sent = _instant(metadata.get("rung_notified_at"))
    return due is not None and (sent is None or sent < due)


async def _apply(
    pool: asyncpg.Pool,
    source: str,
    plan: _Plan,
    actives: Mapping[str, Mapping[str, Any]],
    *,
    now: datetime,
    notify: NotifyFn,
    recheck: Callable[[UUID, datetime], Awaitable[LeaveFate]] | None = None,
) -> Counter[str]:
    counts: Counter[str] = Counter()
    transitions = await reconcile_snapshot(
        pool,
        source=source,
        observations=plan.observations,
        snapshot_complete=False,
        initial_grace_seconds=INITIAL_GRACE_SECONDS,
        escalation_advance=ESCALATION_ADVANCE,
        preserve_metadata_keys=MARKERS,
    )
    for fingerprint, reason in plan.resolutions.items():
        done = await resolve_condition(
            pool,
            source=source,
            fingerprint=fingerprint,
            resolution_metadata={"resolution_reason": reason},
        )
        counts["resolved"] += int(done is not None)

    for transition in transitions:
        fingerprint = transition.fingerprint
        message = plan.messages.get(fingerprint)
        prior = actives.get(fingerprint)
        prior_meta = _as_dict(prior.get("metadata")) if prior else {}
        rung_due = (
            _instant(prior.get("next_reescalate_at"))
            if prior is not None and transition.transition == "escalation_due"
            else None
        )
        if message is None:
            # Carried without a message (premise or leave unknown): the ledger has already
            # advanced the rung, so record it as held for the next known-premise run.
            if rung_due is not None:
                counts["held"] += 1
                await _write_markers(
                    pool,
                    source,
                    fingerprint,
                    None,
                    {**_without_identity(prior_meta), "rung_due_at": rung_due.isoformat()},
                )
            continue
        counts["opened"] += int(transition.transition in ("opened", "reopened"))
        first = _due_first_send(prior, transition.transition, now)
        held = _held_rung(prior_meta)
        if not (first or rung_due or held):
            continue

        markers: dict[str, Any] = {}
        if rung_due is not None:
            markers["rung_due_at"] = rung_due.isoformat()
        fate: LeaveFate = "current"
        if recheck is not None and fingerprint in plan.leaves:
            fate = await recheck(*plan.leaves[fingerprint])
        if fate in ("returned", "retracted"):
            reason = "owner_returned" if fate == "returned" else "premise_retracted"
            await resolve_condition(
                pool,
                source=source,
                fingerprint=fingerprint,
                resolution_metadata={"resolution_reason": reason},
            )
            counts["resolved"] += 1
            continue
        if fate == "unknown":
            counts["held"] += 1
        else:
            try:
                outcome: NotifyOutcome = await notify(message)
            except Exception:
                logger.warning("home away safety: notify raised for %s", source, exc_info=True)
                outcome = "failed"
            counts[outcome] += 1
            if outcome == "sent":
                markers["notified_at"] = prior_meta.get("notified_at") or now.isoformat()
                if rung_due is not None or held:
                    markers["rung_notified_at"] = now.isoformat()
        if not markers:
            continue
        base = plan.metadata.get(fingerprint) or _without_identity(prior_meta)
        await _write_markers(pool, source, fingerprint, message, {**base, **markers})
    return counts


def _without_identity(metadata: Mapping[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in metadata.items() if k != "identity_payload"}


async def _write_markers(
    pool: asyncpg.Pool,
    source: str,
    fingerprint: str,
    summary: str | None,
    metadata: dict[str, Any],
) -> None:
    """Confirm-only write of delivery markers; ``None`` keeps the stored summary."""
    await reconcile_snapshot(
        pool,
        source=source,
        observations=[
            Observation(
                fingerprint=fingerprint,
                summary=summary,
                metadata=metadata,
                confirm_only=True,
            )
        ],
        snapshot_complete=False,
        initial_grace_seconds=INITIAL_GRACE_SECONDS,
        escalation_advance=ESCALATION_ADVANCE,
        preserve_metadata_keys=MARKERS,
    )


# ---------------------------------------------------------------------------
# The three producers
# ---------------------------------------------------------------------------


@dataclass
class _Run:
    """What one run read, shared by the producers."""

    now: datetime
    lease_healthy: bool
    readings: dict[str, Reading]
    freezers: list[Freezer]
    doors: list[Door]
    safety: dict[str, list[str]]
    blind_after: int | None
    streaks: dict[tuple[str, str], Streak]
    actives: dict[str, dict[str, dict[str, Any]]]
    premise: Premise
    new_streaks: dict[tuple[str, str], Streak] = field(default_factory=dict)


def _plan_freezers(run: _Run) -> _Plan:
    plan = _Plan()
    declared = {f.entity_id for f in run.freezers}
    active = run.actives[FREEZER_SOURCE]
    for freezer in run.freezers:
        fingerprint = freezer_fingerprint(freezer.entity_id)
        reading = run.readings.get(freezer.entity_id)
        verdict, celsius = freezer_run(
            freezer, reading, lease_healthy=run.lease_healthy, now=run.now
        )
        streak = advance_streak(
            run.streaks.get(("freezer", freezer.entity_id)), verdict == "warm", run.now
        )
        if streak is not None:
            run.new_streaks[("freezer", freezer.entity_id)] = streak
        prior = active.get(fingerprint)
        dwell = timedelta(minutes=freezer.dwell_minutes)
        if prior is None:
            if streak is None or run.now - streak.start < dwell:
                continue
            metadata: dict[str, Any] = {
                "entity_id": freezer.entity_id,
                "owner_label": _clean(freezer.owner_label, limit=80, fallback="Freezer"),
                "detected_since": streak.start.isoformat(),
            }
            late = late_detection(streak.start, dwell, run.now)
            if late is not None:
                metadata["late_detection"] = late
            plan.observe(
                fingerprint,
                freezer_message(freezer, verdict, celsius, streak.start, run.now),
                metadata,
            )
        elif verdict == "cold":
            plan.resolutions[fingerprint] = "temperature_recovered"
        else:
            since = _instant(_as_dict(prior.get("metadata")).get("detected_since")) or run.now
            plan.carry(fingerprint, freezer_message(freezer, verdict, celsius, since, run.now))
    if run.lease_healthy:
        for fingerprint, prior in active.items():
            if _as_dict(prior.get("metadata")).get("entity_id") not in declared:
                plan.resolutions[fingerprint] = "watch_removed"
    return plan


def _plan_doors(run: _Run) -> _Plan:
    plan = _Plan()
    doors = {d.entity_id: d for d in run.doors}
    active = run.actives[AWAY_DOOR_SOURCE]
    handled: set[str] = set()
    for fingerprint, prior in active.items():
        meta = _as_dict(prior.get("metadata"))
        entity_id = str(meta.get("entity_id"))
        leave_at = _instant(meta.get("leave_confirmed_at"))
        try:
            leave_id = UUID(str(meta.get("leave_edge_id")))
        except ValueError:
            leave_id = None
        door = doors.get(entity_id)
        if leave_id is None or leave_at is None:
            plan.carry(fingerprint)
            continue
        if run.premise.leave is not None and leave_id == run.premise.leave.id:
            handled.add(entity_id)
        state, since = (
            door_state(
                door,
                run.readings.get(entity_id),
                leave_at=leave_at,
                lease_healthy=run.lease_healthy,
                now=run.now,
            )
            if door is not None
            else ("unknown", None)
        )
        if state == "secure":
            plan.resolutions[fingerprint] = "secured"
            continue
        fate = leave_fate(run.premise, leave_id, leave_at)
        if fate in ("returned", "retracted"):
            plan.resolutions[fingerprint] = (
                "owner_returned" if fate == "returned" else "premise_retracted"
            )
            continue
        if run.lease_healthy and (door is None or door.away_minutes is None or state == "open"):
            # Removed, disarmed, or an edited duration the item no longer meets.
            plan.resolutions[fingerprint] = "watch_removed"
            continue
        if fate == "unknown":
            plan.carry(fingerprint)
            continue
        plan.leaves[fingerprint] = (leave_id, leave_at)
        assert door is not None
        if state == "due" and since is not None:
            reading = run.readings.get(entity_id)
            plan.carry(
                fingerprint, door_message(door, reading.state if reading else None, since, run.now)
            )
        else:
            plan.carry(fingerprint, door_unreadable_message(door, leave_at, run.now))

    leave = run.premise.leave
    if run.premise.status != "away" or leave is None:
        return plan
    for door in run.doors:
        if door.away_minutes is None or door.entity_id in handled:
            continue
        reading = run.readings.get(door.entity_id)
        state, since = door_state(
            door,
            reading,
            leave_at=leave.confirmed_at,
            lease_healthy=run.lease_healthy,
            now=run.now,
        )
        if state != "due" or since is None:
            continue
        fingerprint = door_fingerprint(door.entity_id, leave.id)
        metadata: dict[str, Any] = {
            "entity_id": door.entity_id,
            "owner_label": _clean(door.owner_label, limit=80, fallback="Watched item"),
            "leave_edge_id": str(leave.id),
            "leave_confirmed_at": leave.confirmed_at.isoformat(),
            "detected_since": since.isoformat(),
        }
        late = late_detection(since, timedelta(minutes=door.away_minutes), run.now)
        if late is not None:
            metadata["late_detection"] = late
        plan.observe(
            fingerprint,
            door_message(door, reading.state if reading else None, since, run.now),
            metadata,
        )
        plan.leaves[fingerprint] = (leave.id, leave.confirmed_at)
    return plan


def _roles(run: _Run) -> dict[str, list[str]]:
    roles = {role: list(run.safety.get(role, ())) for role in SAFETY_ROLES}
    roles["freezer"] = [f.entity_id for f in run.freezers]
    roles["away_door"] = [d.entity_id for d in run.doors if d.away_minutes is not None]
    return roles


def _plan_blind(run: _Run, *, configured: bool, tz: ZoneInfo) -> _Plan:
    plan = _Plan()
    roles = _roles(run)
    blind = blind_roles(lease_healthy=run.lease_healthy, roles=roles, readings=run.readings)
    for role in ROLE_WORDS:
        streak = advance_streak(run.streaks.get(("blind", role)), role in blind, run.now)
        if streak is not None:
            run.new_streaks[("blind", role)] = streak
    active = run.actives[BLIND_SOURCE]
    if not configured or run.blind_after is None:
        for fingerprint in active:
            plan.resolutions[fingerprint] = "watch_removed"
        return plan
    dwell = timedelta(minutes=run.blind_after)
    past = {
        role: run.new_streaks[("blind", role)].start
        for role in blind
        if run.now - run.new_streaks[("blind", role)].start >= dwell
    }
    named = past or {role: run.new_streaks[("blind", role)].start for role in blind}
    since = min(named.values(), default=run.now)

    for fingerprint, prior in active.items():
        meta = _as_dict(prior.get("metadata"))
        leave_at = _instant(meta.get("leave_confirmed_at"))
        try:
            leave_id = UUID(str(meta.get("leave_edge_id")))
        except ValueError:
            leave_id = None
        if leave_id is None or leave_at is None:
            plan.carry(fingerprint)
            continue
        fate = leave_fate(run.premise, leave_id, leave_at)
        if fate in ("returned", "retracted"):
            plan.resolutions[fingerprint] = (
                "owner_returned" if fate == "returned" else "premise_retracted"
            )
        elif not blind:
            plan.resolutions[fingerprint] = "sight_restored"
        elif fate == "unknown":
            plan.carry(fingerprint)
        else:
            metadata = {**meta, "roles": sorted(named)}
            metadata.pop("identity_payload", None)
            for key in MARKERS:
                metadata.pop(key, None)
            plan.observe(fingerprint, blind_message(named, since, tz), metadata)
            plan.leaves[fingerprint] = (leave_id, leave_at)

    leave = run.premise.leave
    if run.premise.status == "away" and leave is not None and past:
        fingerprint = blind_fingerprint(leave.id)
        if fingerprint not in active:
            started = min(past.values())
            metadata = {
                "sensing_group": str(SENSING_GROUP),
                "roles": sorted(past),
                "leave_edge_id": str(leave.id),
                "leave_confirmed_at": leave.confirmed_at.isoformat(),
                "detected_since": started.isoformat(),
            }
            late = late_detection(started, dwell, run.now)
            if late is not None:
                metadata["late_detection"] = late
            plan.observe(fingerprint, blind_message(past, started, tz), metadata)
            plan.leaves[fingerprint] = (leave.id, leave.confirmed_at)
    return plan


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------


async def _load(pool: asyncpg.Pool) -> dict[str, Any]:
    freezers = [
        Freezer(
            r["entity_id"],
            r["owner_label"],
            r["unit"],
            float(r["max_c"]),
            float(r["hysteresis_c"]),
            int(r["dwell_minutes"]),
            int(r["max_reading_age_minutes"]),
        )
        for r in await pool.fetch(
            "SELECT entity_id, owner_label, unit, max_c, hysteresis_c, dwell_minutes, "
            "max_reading_age_minutes FROM temperature_watch ORDER BY entity_id"
        )
    ]
    doors = [
        Door(
            r["entity_id"],
            r["owner_label"],
            tuple(s.lower() for s in r["secure_states"]),
            r["away_alert_after_minutes"],
        )
        for r in await pool.fetch(
            "SELECT entity_id, owner_label, secure_states, away_alert_after_minutes "
            "FROM departure_watch ORDER BY entity_id"
        )
    ]
    safety: dict[str, list[str]] = {}
    for r in await pool.fetch("SELECT entity_id, role FROM safety_watch ORDER BY entity_id"):
        safety.setdefault(r["role"], []).append(r["entity_id"])
    blind_after = await pool.fetchval("SELECT blind_after_minutes FROM away_safety_settings")
    streaks = {
        (r["kind"], r["streak_key"]): Streak(r["streak_start"], r["last_run_at"])
        for r in await pool.fetch(
            "SELECT kind, streak_key, streak_start, last_run_at FROM away_safety_streaks"
        )
    }
    actives: dict[str, dict[str, dict[str, Any]]] = {source: {} for source in SOURCES}
    for r in await pool.fetch(
        "SELECT source, fingerprint, metadata, first_detected_at, next_reescalate_at "
        "FROM public.owner_conditions WHERE source = ANY($1::text[]) "
        "AND state IN ('open', 'aging')",
        list(SOURCES),
    ):
        actives[r["source"]][r["fingerprint"]] = dict(r)
    return {
        "freezers": freezers,
        "doors": doors,
        "safety": safety,
        "blind_after": blind_after,
        "streaks": streaks,
        "actives": actives,
    }


async def _read_snapshot(pool: asyncpg.Pool, entity_ids: Sequence[str]) -> dict[str, Reading]:
    rows = await pool.fetch(
        "SELECT entity_id, state, attributes ->> 'unit_of_measurement' AS unit, "
        "last_updated, last_changed FROM ha_entity_snapshot WHERE entity_id = ANY($1::text[])",
        list(entity_ids),
    )
    return {
        r["entity_id"]: Reading(
            r["state"], r["unit"], _instant(r["last_updated"]), _instant(r["last_changed"])
        )
        for r in rows
    }


async def _save_streaks(pool: asyncpg.Pool, streaks: Mapping[tuple[str, str], Streak]) -> None:
    keys = [f"{kind}\x1f{key}" for kind, key in streaks]
    await pool.execute(
        "DELETE FROM away_safety_streaks WHERE NOT (kind || chr(31) || streak_key = ANY($1))",
        keys,
    )
    if streaks:
        await pool.executemany(
            "INSERT INTO away_safety_streaks (kind, streak_key, streak_start, last_run_at) "
            "VALUES ($1, $2, $3, $4) ON CONFLICT (kind, streak_key) DO UPDATE SET "
            "streak_start = EXCLUDED.streak_start, last_run_at = EXCLUDED.last_run_at",
            [(kind, key, s.start, s.last_run_at) for (kind, key), s in streaks.items()],
        )


async def _owner_subject(pool: asyncpg.Pool) -> UUID | None:
    rows = await pool.fetch("SELECT id FROM public.entities WHERE 'owner' = ANY(roles)")
    return rows[0]["id"] if len(rows) == 1 else None


def _default_reader(pool: asyncpg.Pool) -> ReadPresence:
    async def read(now: datetime, wanted: Collection[UUID]) -> PresenceRead | None:
        from butlers.jobs.home_departure import read_presence_state

        subject = await _owner_subject(pool)
        if subject is None:
            return None
        return await read_presence_state(now, subject=subject, wanted=wanted)

    return read


def _wanted_leaves(actives: Mapping[str, Mapping[str, Mapping[str, Any]]]) -> set[UUID]:
    wanted: set[UUID] = set()
    for source in (AWAY_DOOR_SOURCE, BLIND_SOURCE):
        for prior in actives[source].values():
            try:
                wanted.add(UUID(str(_as_dict(prior.get("metadata")).get("leave_edge_id"))))
            except ValueError:
                continue
    return wanted


async def _read_premise(read: ReadPresence, now: datetime, wanted: Collection[UUID]) -> Premise:
    try:
        return away_premise(await read(now, wanted), now)
    except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError, ValueError, KeyError):
        logger.warning("home away safety: presence read failed; premise unknown")
        return Premise("unknown")


async def run_away_safety(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None = None,
    *,
    read_presence: ReadPresence | None = None,
    sensing_writer: WriteSensing | None = None,
    notify_fn: NotifyFn | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """One run of all three producers, serialized by a transaction advisory lock."""
    del job_args
    if not HOME_AWAY_SAFETY_LIVE:
        return {"status": "dark"}
    pinned = now
    async with pool.acquire() as lock_connection, lock_connection.transaction():
        if not await lock_connection.fetchval(
            "SELECT pg_try_advisory_xact_lock(hashtext($1))", _LOCK_KEY
        ):
            return {"status": "busy"}
        return await _run_locked(
            pool,
            read_presence=read_presence or _default_reader(pool),
            sensing_writer=sensing_writer or write_sensing,
            notify=notify_fn or _default_notify(pool),
            now=pinned or datetime.now(UTC),
            # The pre-send re-read uses the live clock; a caller-pinned ``now`` pins it too.
            clock=lambda: pinned or datetime.now(UTC),
        )


def _default_notify(pool: asyncpg.Pool) -> NotifyFn:
    async def notify(message: str) -> NotifyOutcome:
        from butlers.jobs.home import _send_notify

        return await _send_notify(pool, message)

    return notify


async def _run_locked(
    pool: asyncpg.Pool,
    *,
    read_presence: ReadPresence,
    sensing_writer: WriteSensing,
    notify: NotifyFn,
    now: datetime,
    clock: Callable[[], datetime],
) -> dict[str, Any]:
    from butlers.core.general_settings import resolve_general_timezone
    from butlers.jobs.home import HASourceUnmeasurableError, _require_ha_source_healthy

    loaded = await _load(pool)
    try:
        await _require_ha_source_healthy(pool)
        lease_healthy = True
    except HASourceUnmeasurableError:
        lease_healthy = False
    run = _Run(
        now=now,
        lease_healthy=lease_healthy,
        readings={},
        premise=Premise("unknown"),
        **loaded,
    )
    roles = _roles(run)
    declared = sorted({e for ids in roles.values() for e in ids})
    if lease_healthy and declared:
        run.readings = await _read_snapshot(pool, declared)
    sensing = sensing_state(lease_healthy=lease_healthy, entity_ids=declared, readings=run.readings)
    written = await sensing_writer(sensing, now)
    run.premise = await _read_premise(read_presence, now, _wanted_leaves(run.actives))

    async def recheck(leave_id: UUID, leave_at: datetime) -> LeaveFate:
        fresh = await _read_premise(read_presence, clock(), [leave_id])
        return leave_fate(fresh, leave_id, leave_at)

    try:
        tz = ZoneInfo(await resolve_general_timezone(pool))
    except (KeyError, ValueError):
        tz = ZoneInfo("UTC")
    plans = {
        FREEZER_SOURCE: _plan_freezers(run),
        AWAY_DOOR_SOURCE: _plan_doors(run),
        BLIND_SOURCE: _plan_blind(run, configured=written != "unconfigured", tz=tz),
    }
    await _save_streaks(pool, run.new_streaks)

    result: dict[str, Any] = {
        "status": "ok" if lease_healthy else "source_unmeasurable",
        "sensing": "unconfigured" if written == "unconfigured" else sensing,
        "sensing_write": written,
        "premise": run.premise.status,
    }
    statuses = {
        FREEZER_SOURCE: "ok" if run.freezers else "unconfigured",
        AWAY_DOOR_SOURCE: (
            "unconfigured"
            if not roles["away_door"]
            else ("presence_unknown" if run.premise.status == "unknown" else "ok")
        ),
        BLIND_SOURCE: (
            "unconfigured"
            if written == "unconfigured" or run.blind_after is None
            else ("presence_unknown" if run.premise.status == "unknown" else "ok")
        ),
    }
    for source, plan in plans.items():
        counts = await _apply(
            pool,
            source,
            plan,
            run.actives[source],
            now=now,
            notify=notify,
            recheck=None if source == FREEZER_SOURCE else recheck,
        )
        result[source] = {"status": statuses[source], **counts}
    return result
