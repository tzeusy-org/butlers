"""The owner clock and the owner-local time basis (RFC 0038 S1, bu-dhloi1.21).

Three parts, all pure and driven by an injected instant:

* ``reduce_clock``: the owner-clock policy. A phone fix in a new zone switches
  the clock with a notice and an undo. An unconfirmed booking or flight only
  asks, once per booking. An owner-confirmed booking switches. An owner-set
  zone holds against phone fixes until the owner clears it. Undo reverts an
  automatic switch and holds that phone zone off for ``UNDO_HOLD``, not forever.
  With no evidence the clock is the home zone, else UTC (``utc_fallback``).
  No zone is hardcoded.
* ``brief_due``: once-a-day idempotence on the owner's local date. The key is
  the local date at the tick; a watermark (the last key sent) stops a repeat
  after a westward jump, a DST repeated hour or an undo. A watermark past any
  real local date is ignored, so nothing suppresses the brief forever.
* ``clock_parity`` / ``owner_time_basis``: the shadow-parity receipt between
  the legacy basis (the saved ``settings.general`` zone, else the Singapore
  fallback the briefing uses) and the owner clock. While
  ``flags.OWNER_CLOCK_LIVE`` is off the basis stays legacy and every call still
  yields a receipt.

``clock_event`` builds the ``owner.clock`` record for the ``context.clock``
source. Dark: nothing schedules or writes it until the switch.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from butlers.perception import flags

SOURCE = "context.clock"
BASES = frozenset({"phone", "owner", "home", "utc_fallback", "owner_confirmed_booking"})
#: How long an undone phone zone is held off before a fresh fix may switch again.
UNDO_HOLD = timedelta(hours=24)
ASSERTION_SPAN = timedelta(hours=24)
#: The zone the legacy briefing falls back to when no owner zone is saved.
LEGACY_FALLBACK_ZONE = "Asia/Singapore"
_LABELS = {
    "version": 1,
    "label_instr": ["world"],
    "label_data": ["world"],
    "label_sens": ["location"],
}

Effect = Literal["switch", "revert", "ask", "none"]


def _zone(name: Any) -> ZoneInfo | None:
    if not isinstance(name, str) or not name:
        return None
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return None


@dataclass(frozen=True, slots=True)
class ClockValue:
    zone: str
    basis: str


# -- evidence ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PhoneFix:
    zone: str
    at: datetime


@dataclass(frozen=True, slots=True)
class Booking:
    """An unconfirmed booking or flight placing the owner in ``zone``."""

    zone: str
    at: datetime
    ref: str


@dataclass(frozen=True, slots=True)
class OwnerConfirm:
    """The owner confirmed a booking's zone."""

    zone: str
    at: datetime
    ref: str


@dataclass(frozen=True, slots=True)
class OwnerSet:
    zone: str
    at: datetime


@dataclass(frozen=True, slots=True)
class OwnerClear:
    at: datetime


@dataclass(frozen=True, slots=True)
class Undo:
    at: datetime


ClockEvidence = PhoneFix | Booking | OwnerConfirm | OwnerSet | OwnerClear | Undo


# -- reducer -----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ClockState:
    current: ClockValue
    home: ClockValue
    previous: ClockValue | None = None
    undone_zone: str | None = None
    undone_at: datetime | None = None
    asked: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def initial(cls, home_zone: str | None) -> ClockState:
        home = (
            ClockValue(home_zone, "home")
            if _zone(home_zone) is not None and home_zone is not None
            else ClockValue("UTC", "utc_fallback")
        )
        return cls(current=home, home=home)


@dataclass(frozen=True, slots=True)
class ClockStep:
    state: ClockState
    effect: Effect
    notice: bool = False
    ask_ref: str | None = None


def _held(state: ClockState, fix: PhoneFix) -> bool:
    return (
        state.undone_zone == fix.zone
        and state.undone_at is not None
        and fix.at <= state.undone_at + UNDO_HOLD
    )


def _switch(state: ClockState, value: ClockValue, *, automatic: bool) -> ClockStep:
    moved = replace(
        state,
        current=value,
        previous=state.current if automatic else None,
        undone_zone=None,
        undone_at=None,
    )
    return ClockStep(moved, "switch", notice=automatic)


def reduce_clock(state: ClockState, evidence: ClockEvidence) -> ClockStep:
    """Apply one piece of evidence. Invalid zones change nothing."""
    unchanged = ClockStep(state, "none")
    if isinstance(evidence, Undo):
        if state.previous is None or state.current.basis != "phone":
            return unchanged
        reverted = replace(
            state,
            current=state.previous,
            previous=None,
            undone_zone=state.current.zone,
            undone_at=evidence.at,
        )
        return ClockStep(reverted, "revert")
    if isinstance(evidence, OwnerClear):
        if state.current == state.home:
            return unchanged
        return _switch(state, state.home, automatic=False)
    if _zone(evidence.zone) is None:
        return unchanged
    if isinstance(evidence, PhoneFix):
        if (
            state.current.basis == "owner"
            or evidence.zone == state.current.zone
            or _held(state, evidence)
        ):
            return unchanged
        return _switch(state, ClockValue(evidence.zone, "phone"), automatic=True)
    if isinstance(evidence, Booking):
        if evidence.ref in state.asked:
            return unchanged
        asked = replace(state, asked=state.asked | {evidence.ref})
        return ClockStep(asked, "ask", ask_ref=evidence.ref)
    if isinstance(evidence, OwnerConfirm):
        return _switch(state, ClockValue(evidence.zone, "owner_confirmed_booking"), automatic=False)
    if isinstance(evidence, OwnerSet):
        return _switch(state, ClockValue(evidence.zone, "owner"), automatic=False)
    return unchanged


def resolve_owner_clock(state: str, value: Any, home_zone: str | None) -> ClockValue:
    """The clock from an ``owner.clock`` read: a valid known value, else home, else UTC."""
    if (
        state == "known"
        and isinstance(value, dict)
        and value.get("basis") in BASES
        and _zone(value.get("zone")) is not None
    ):
        return ClockValue(value["zone"], value["basis"])
    return ClockState.initial(home_zone).current


# -- once-a-day idempotence ----------------------------------------------------------


def brief_due(
    *, zone: ZoneInfo, now: datetime, at_local: time, watermark: date | None
) -> date | None:
    """The local-date key to send now, or ``None``.

    Due once the local wall time reaches ``at_local`` on a local date after the
    watermark. A DST gap that skips ``at_local`` is passed, so the brief still
    goes. No zone is more than one day ahead of UTC, so a watermark beyond the
    UTC date plus one is not a real local date and is ignored.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("an aware instant is required")
    local = now.astimezone(zone)
    if local.time().replace(tzinfo=None) < at_local:
        return None
    key = local.date()
    ceiling = now.astimezone(UTC).date() + timedelta(days=1)
    if watermark is not None and watermark <= ceiling and key <= watermark:
        return None
    return key


# -- shadow parity -------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ClockParity:
    """One comparison of the legacy owner-time basis with the owner clock."""

    at: datetime
    legacy_zone: str
    legacy_fallback: bool
    clock_zone: str
    clock_basis: str
    legacy_local_date: date
    clock_local_date: date
    same_zone: bool
    same_local_date: bool


def _legacy(legacy_zone: str | None) -> tuple[ZoneInfo, bool]:
    zone = _zone(legacy_zone)
    if zone is None:
        return ZoneInfo(LEGACY_FALLBACK_ZONE), True
    return zone, False


def clock_parity(legacy_zone: str | None, clock: ClockValue, at: datetime) -> ClockParity:
    legacy, fallback = _legacy(legacy_zone)
    current = _zone(clock.zone) or ZoneInfo("UTC")
    legacy_date = at.astimezone(legacy).date()
    clock_date = at.astimezone(current).date()
    return ClockParity(
        at=at,
        legacy_zone=legacy.key,
        legacy_fallback=fallback,
        clock_zone=current.key,
        clock_basis=clock.basis,
        legacy_local_date=legacy_date,
        clock_local_date=clock_date,
        same_zone=legacy.key == current.key,
        same_local_date=legacy_date == clock_date,
    )


def owner_time_basis(
    legacy_zone: str | None, clock: ClockValue, at: datetime
) -> tuple[ZoneInfo, ClockParity]:
    """The zone owner-local time uses now, and the parity receipt for it.

    Legacy while ``flags.OWNER_CLOCK_LIVE`` is off; the owner clock after.
    """
    receipt = clock_parity(legacy_zone, clock, at)
    zone = receipt.clock_zone if flags.OWNER_CLOCK_LIVE else receipt.legacy_zone
    return ZoneInfo(zone), receipt


# -- the owner.clock record ---------------------------------------------------------


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def clock_event(
    value: ClockValue,
    *,
    subject: UUID,
    evidence_ref: UUID,
    generation: int,
    at: datetime,
    observed_at: datetime,
    evidence_source: str = SOURCE,
) -> dict[str, Any]:
    """The ``owner.clock`` request body for one clock assertion."""
    return {
        "version": 1,
        "kind": "owner.clock",
        "subject": str(subject),
        "source": SOURCE,
        "event_key": f"clock:{value.basis}:{value.zone}:{_iso(at)}",
        "expected_generation": generation,
        "state": "known",
        "observed_at": _iso(observed_at),
        "effective_span": {"start": _iso(at), "end": _iso(at + ASSERTION_SPAN)},
        "typed_value_or_ref": {
            "zone": value.zone,
            "basis": value.basis,
            "evidence_ref": str(evidence_ref),
        },
        "evidence_refs": [
            {
                "ref": str(evidence_ref),
                "source": evidence_source,
                "expires_at": _iso(observed_at + ASSERTION_SPAN),
            }
        ],
        "labels": _LABELS,
        "at": _iso(at),
        "confirmed_at": _iso(at),
        "retracts_event_id": None,
    }


__all__ = [
    "BASES",
    "LEGACY_FALLBACK_ZONE",
    "SOURCE",
    "UNDO_HOLD",
    "Booking",
    "ClockParity",
    "ClockState",
    "ClockStep",
    "ClockValue",
    "OwnerClear",
    "OwnerConfirm",
    "OwnerSet",
    "PhoneFix",
    "Undo",
    "brief_due",
    "clock_event",
    "clock_parity",
    "owner_time_basis",
    "reduce_clock",
    "resolve_owner_clock",
]
