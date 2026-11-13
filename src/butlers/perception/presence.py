"""Deterministic owner presence reducer (RFC 0038 S3.2, bu-jv1moa.17).

Pure: no I/O, no clock, no logging. The caller passes typed, coordinate-free
observations and the previous :class:`PresenceState`; the reducer returns the next
state plus the :class:`Decision` records the adapter must publish through
``PerceptionStore.write`` as ``owner.place`` (source ``home.dots``).

Rules (named constants, deliberately not runtime configuration):

* A side change (inside to outside, or the reverse) opens a candidate on its first
  qualifying observation. It is confirmed once ``CONFIRM_AFTER`` has passed with no
  fresh observation of the confirmed side, or once ``CONFIRM_FIXES`` distinct
  qualifying observations agree, whichever comes first.
* A fresh home SSID match on an otherwise outside fix counts as an inside observation,
  so it vetoes a leave.
* An unknown prior state is never turned into an edge: establishing a side from
  unknown writes an explicit ``unknown`` first (see :attr:`Decision.reset_first`),
  so the database cannot classify the first assertion as a leave or enter.
* Stale input drops the reducer back to unknown.
* An enter confirmed within ``RETRACT_WINDOW`` of the leave it follows carries that
  leave's event id so the writer retracts it in the same compare-and-swap.

Nothing here holds coordinates, SSIDs or zone ids; observations carry only a side,
an opaque place reference, and an opaque per-observation key.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from butlers.perception.model import event_id

Side = Literal["inside", "outside"]

SOURCE = "home.dots"
CONFIRM_AFTER = timedelta(minutes=3)
CONFIRM_FIXES = 2
RETRACT_WINDOW = timedelta(minutes=10)
#: Observations older than this are stale: they neither open nor hold a state.
OBSERVATION_FRESH = timedelta(minutes=30)
#: A confirmed state is republished at least this often so its slot stays fresh.
HEARTBEAT = timedelta(minutes=5)
#: GPS qualification (OwnTracks feed): accuracy ceiling and distance margin floor, metres.
MAX_ACCURACY_M = 100
MIN_MARGIN_M = 50

_MAX_KEYS = 64


def gps_outside(distance_m: float, accuracy_m: float, radius_m: float) -> bool:
    """Qualifying outside fix: accuracy <=100 m and distance > radius + max(accuracy, 50 m)."""
    return accuracy_m <= MAX_ACCURACY_M and distance_m > radius_m + max(accuracy_m, MIN_MARGIN_M)


def observation_key(*parts: str) -> str:
    """Opaque stable key for one source observation (never the raw identifiers)."""
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:24]


@dataclass(frozen=True)
class Observation:
    """One typed, coordinate-free, already-qualified owner observation."""

    observed_at: datetime
    side: Side
    place_ref: UUID
    evidence: str  # registered evidence source label, e.g. "home_assistant"
    key: str
    ssid_match: bool = False

    @property
    def effective_side(self) -> Side:
        return "inside" if self.ssid_match else self.side


@dataclass(frozen=True)
class Candidate:
    side: Side
    first_at: datetime
    keys: tuple[str, ...]
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class PresenceState:
    """What the reducer carries between runs. Empty means unknown."""

    side: Side | None = None
    place_ref: UUID | None = None
    since: datetime | None = None
    last_agree: datetime | None = None
    last_written: datetime | None = None
    candidate: Candidate | None = None
    cursor: tuple[datetime, str] | None = None
    leave_event: UUID | None = None
    leave_at: datetime | None = None
    seen: tuple[str, ...] = field(default=())


@dataclass(frozen=True)
class Decision:
    """One write the adapter must publish."""

    action: Literal["leave", "enter", "establish", "heartbeat"]
    side: Side
    place_ref: UUID
    at: datetime
    observed_at: datetime
    confirmed_at: datetime
    evidence: tuple[str, ...]
    reset_first: bool = False
    retracts: UUID | None = None

    @property
    def event_key(self) -> str:
        stamp = self.at.isoformat()
        if self.action == "heartbeat":
            return f"heartbeat:{self.place_ref}:{self.side}:{stamp}"
        return f"{self.action}:{self.place_ref}:{self.side}:{stamp}"

    @property
    def event_id(self) -> UUID:
        return event_id(SOURCE, self.event_key)

    @property
    def reset_event_key(self) -> str:
        return f"reset:{self.place_ref}:{self.at.isoformat()}"


def _bounded(seen: tuple[str, ...], key: str) -> tuple[str, ...]:
    return (*seen, key)[-_MAX_KEYS:]


def _after_cursor(obs: Observation, cursor: tuple[datetime, str] | None) -> bool:
    return cursor is None or (obs.observed_at, obs.key) > cursor


def reduce(
    state: PresenceState,
    observations: list[Observation],
    now: datetime,
    *,
    place_ref: UUID,
) -> tuple[PresenceState, list[Decision]]:
    """Fold ``observations`` into ``state`` as of ``now`` and return the decisions.

    ``place_ref`` is the one place this run reduces; a different place in the carried
    state resets it to unknown (a retargeted home never fabricates an edge).
    """
    if state.place_ref is not None and state.place_ref != place_ref:
        state = PresenceState()
    fresh = [
        o
        for o in observations
        if o.place_ref == place_ref
        and o.observed_at <= now
        and now - o.observed_at <= OBSERVATION_FRESH
    ]
    ordered = sorted(fresh, key=lambda o: (o.observed_at, o.key))
    decisions: list[Decision] = []

    for obs in ordered:
        if not _after_cursor(obs, state.cursor) or obs.key in state.seen:
            continue
        state = replace(
            state, cursor=(obs.observed_at, obs.key), seen=_bounded(state.seen, obs.key)
        )
        side = obs.effective_side
        if state.side == side:
            state = replace(state, last_agree=obs.observed_at, candidate=None)
            continue
        cand = state.candidate
        if cand is None or cand.side != side:
            cand = Candidate(side, obs.observed_at, (obs.key,), (obs.evidence,))
        elif obs.key not in cand.keys:
            cand = replace(
                cand,
                keys=(*cand.keys, obs.key),
                evidence=tuple(sorted({*cand.evidence, obs.evidence})),
            )
        state = replace(state, candidate=cand)

    cand = state.candidate
    if cand is not None:
        if _ready(cand, now):
            state, decision = _confirm(state, cand, now, place_ref)
            decisions.append(decision)

    side = state.side
    if side is not None:
        agreeing = [o for o in ordered if o.effective_side == side]
        if agreeing:
            newest = agreeing[-1]
            if state.last_agree is None or newest.observed_at > state.last_agree:
                state = replace(state, last_agree=newest.observed_at)
        if state.last_agree is None or now - state.last_agree > OBSERVATION_FRESH:
            return PresenceState(cursor=state.cursor, seen=state.seen), decisions
        if (
            not decisions
            and agreeing
            and state.candidate is None
            and state.last_written is not None
            and now - state.last_written >= HEARTBEAT
        ):
            newest = agreeing[-1]
            decisions.append(
                Decision(
                    "heartbeat",
                    side,
                    place_ref,
                    now,
                    newest.observed_at,
                    now,
                    (newest.evidence,),
                )
            )
            state = replace(state, last_written=now)
    return state, decisions


def _ready(cand: Candidate, now: datetime) -> bool:
    """Confirmable: enough distinct fixes, or the quiet period elapsed with no agreeing fix.

    An agreeing observation clears the candidate during the fold, so a surviving candidate
    has had none since it opened.
    """
    return len(cand.keys) >= CONFIRM_FIXES or now - cand.first_at >= CONFIRM_AFTER


def _confirm(
    state: PresenceState,
    cand: Candidate,
    now: datetime,
    place_ref: UUID,
) -> tuple[PresenceState, Decision]:
    prior = state.side
    latest = state.cursor[0] if state.cursor is not None else cand.first_at
    observed = max(cand.first_at, min(latest, now))
    retracts: UUID | None = None
    if prior == "outside" and cand.side == "inside" and state.leave_event is not None:
        assert state.leave_at is not None
        if timedelta(0) < cand.first_at - state.leave_at <= RETRACT_WINDOW:
            retracts = state.leave_event
    if prior is None:
        action = "establish"
    else:
        action = "leave" if cand.side == "outside" else "enter"
    decision = Decision(
        action,  # type: ignore[arg-type]
        cand.side,
        place_ref,
        cand.first_at,
        observed,
        now,
        cand.evidence,
        reset_first=prior is None,
        retracts=retracts,
    )
    leave_event = state.leave_event
    leave_at = state.leave_at
    if action == "leave":
        leave_event, leave_at = decision.event_id, cand.first_at
    elif action == "enter":
        leave_event, leave_at = None, None
    state = replace(
        state,
        side=cand.side,
        place_ref=place_ref,
        since=cand.first_at,
        last_agree=observed,
        last_written=now,
        candidate=None,
        leave_event=leave_event,
        leave_at=leave_at,
    )
    return state, decision
