"""Owner modes: the closed set, the recorded defaults and step-up on loosening.

RFC 0038 S3.1 and owner decision bu-u33sb7 (choices A, recorded 2026-10-20):

* Eight git mode IDs (``model.MODES``). Display aliases resolve to them; any
  other name refuses. "In flight" is a travel-derived posture, never a mode.
* Only the owner edits a mode's duration, what it holds and who may break
  through. A change that loosens (exit, shorter duration, fewer holds, more
  break-through senders) needs the .5 dashboard step-up; a change that only
  tightens does not. A mixed edit counts as loosening.
* Sick holds proactive items except medication, safety, bills due within 48 h
  and replies to people. Safety and medication are pinned: no mode may hold them.
* Only travel the owner declared or confirmed arms the absence plan; inferred
  travel never does.
* The brief follows the wake signal, never before 06:00; with no wake signal it
  goes at 09:30.

No automatic entry rule exists here (M7 owns those); an inferred entry refuses.
Dark: ``flags.MODE_CONTROLS_LIVE`` is off and no route or tool calls this. The
step-up kind ``mode_loosen`` is not yet in the trusted step-up catalog, so even a
live call is refused by the store until that catalog change lands.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Any, Literal
from uuid import UUID

from butlers.core.dashboard_stepup import StepUpBinding, consume_stepup, dashboard_stepup_proof
from butlers.perception import flags
from butlers.perception.model import MODES, canonical

SOURCE = "context.mode"
MODE_LOOSEN_KIND = "mode_loosen"
OWNER_CAUSES = frozenset({"owner_declared", "owner_confirmed"})
#: Obligation classes no mode may hold.
PINNED = frozenset({"safety", "medication"})
OBLIGATION_CLASSES = frozenset(
    {"medication", "safety", "bill_due_48h", "reply_to_person", "proactive"}
)
BILL_WINDOW = timedelta(hours=48)
WAKE_EARLIEST = time(6, 0)
WAKE_FALLBACK = time(9, 30)
_ALIASES = {
    **{mode: mode for mode in MODES},
    "sleep": "asleep",
    "house unattended": "house_unattended",
    "focused": "focus",
}
_IN_FLIGHT = frozenset({"in flight", "in_flight", "in-flight", "inflight"})
_LABELS = {
    "version": 1,
    "label_instr": ["world"],
    "label_data": ["world"],
    "label_sens": ["routine"],
}

Verdict = Literal["tighten", "loosen", "none"]


class ModeControlsDark(RuntimeError):
    """Mode controls are not live."""


class ModeStepUpRequired(PermissionError):
    """A loosening without an admitted dashboard step-up."""


def mode_id(name: str) -> str:
    """The wire ID for a mode name or display alias; anything else refuses."""
    key = name.lower() if isinstance(name, str) else ""
    if key in _IN_FLIGHT:
        raise ValueError("in flight is a travel posture, not a mode")
    if key not in _ALIASES:
        raise ValueError("unknown mode")
    return _ALIASES[key]


@dataclass(frozen=True, slots=True)
class Posture:
    modes: frozenset[str]
    in_flight: bool


def posture(modes: Iterable[str], *, in_flight: bool) -> Posture:
    """Active modes plus the in-flight flag, which only travel can carry."""
    active = frozenset(mode_id(m) for m in modes)
    return Posture(active, in_flight and "travel" in active)


# -- recorded defaults --------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ModeProfile:
    """A mode's owner-editable parameters. ``duration`` None is open-ended."""

    mode: str
    duration: timedelta | None
    holds: frozenset[str]
    break_through: frozenset[str]


DEFAULT_PROFILES: dict[str, ModeProfile] = {
    mode: ModeProfile(
        mode,
        None,
        frozenset({"proactive"}) if mode == "sick" else frozenset(),
        frozenset(),
    )
    for mode in sorted(MODES)
}


def classify_obligation(kind: str, *, due_at: datetime | None, now: datetime) -> str:
    """The obligation class a mode's holds are judged against."""
    if kind == "medication":
        return "medication"
    if kind == "safety":
        return "safety"
    if kind == "reply":
        return "reply_to_person"
    if kind == "bill" and due_at is not None and due_at - now <= BILL_WINDOW:
        return "bill_due_48h"
    return "proactive"


def held_by(profile: ModeProfile, obligation_class: str) -> bool:
    return obligation_class not in PINNED and obligation_class in profile.holds


@dataclass(frozen=True, slots=True)
class ModeEntry:
    mode: str
    since: datetime
    until: datetime | None
    cause: str


def arms_absence(entry: ModeEntry) -> bool:
    """Only owner-declared or owner-confirmed travel arms the absence plan."""
    return entry.mode == "travel" and entry.cause in OWNER_CAUSES


def wake_brief_time(wake: time | None) -> time:
    """Local brief time: the wake signal, never before 06:00; no signal, 09:30."""
    if wake is None:
        return WAKE_FALLBACK
    return min(max(wake, WAKE_EARLIEST), WAKE_FALLBACK)


# -- changes ------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Enter:
    mode: str
    until: datetime | None
    cause: str


@dataclass(frozen=True, slots=True)
class Exit:
    mode: str


@dataclass(frozen=True, slots=True)
class EditHolds:
    mode: str
    holds: frozenset[str]


@dataclass(frozen=True, slots=True)
class EditBreakThrough:
    mode: str
    senders: frozenset[str]


@dataclass(frozen=True, slots=True)
class EditDuration:
    mode: str
    duration: timedelta | None


ModeChange = Enter | Exit | EditHolds | EditBreakThrough | EditDuration


def _longer(a: timedelta | None, b: timedelta | None) -> bool:
    """Is ``a`` longer than ``b``? None is open-ended, the longest."""
    if a is None:
        return b is not None
    return b is not None and a > b


def classify_change(change: ModeChange, profiles: Mapping[str, ModeProfile]) -> Verdict:
    """Whether a change tightens, loosens or leaves the mode as it is."""
    if mode_id(change.mode) != change.mode:
        raise ValueError("wire mode ID required")
    current = profiles[change.mode]
    if isinstance(change, Enter):
        if change.cause not in OWNER_CAUSES:
            raise ValueError("only the owner enters a mode; no automatic entry rule")
        return "tighten"
    if isinstance(change, Exit):
        return "loosen"
    if isinstance(change, EditHolds):
        if not change.holds <= OBLIGATION_CLASSES or change.holds & PINNED:
            raise ValueError("safety and medication cannot be held")
        if change.holds == current.holds:
            return "none"
        return "loosen" if current.holds - change.holds else "tighten"
    if isinstance(change, EditBreakThrough):
        if change.senders == current.break_through:
            return "none"
        return "loosen" if change.senders - current.break_through else "tighten"
    if isinstance(change, EditDuration):
        if change.duration == current.duration:
            return "none"
        return "tighten" if _longer(change.duration, current.duration) else "loosen"
    raise ValueError("unknown mode change")


# -- step-up on loosening ------------------------------------------------------------


def _digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def _change_body(change: ModeChange) -> dict[str, Any]:
    body: dict[str, Any] = {"change": type(change).__name__, "mode": change.mode}
    if isinstance(change, EditHolds):
        body["holds"] = sorted(change.holds)
    elif isinstance(change, EditBreakThrough):
        body["senders"] = sorted(change.senders)
    elif isinstance(change, EditDuration):
        body["duration_s"] = None if change.duration is None else change.duration.total_seconds()
    return body


def _profile_body(profile: ModeProfile) -> dict[str, Any]:
    return {
        "mode": profile.mode,
        "duration_s": None if profile.duration is None else profile.duration.total_seconds(),
        "holds": sorted(profile.holds),
        "break_through": sorted(profile.break_through),
    }


def _target(change: ModeChange, version: int) -> str:
    return f"{change.mode}:{version}:{_digest(_change_body(change))[:16]}"


def mode_binding(
    change: ModeChange,
    profiles: Mapping[str, ModeProfile],
    *,
    owner_action_id: UUID,
    expected_version: int,
    namespace: str,
) -> StepUpBinding:
    """The exact loosening a ``mode_loosen`` step-up confirms.

    The request binds the owner action, the change and the expected version; the
    action also binds the mode's current parameters, so a confirmation cannot be
    spent after the mode changed under it.
    """
    if classify_change(change, profiles) != "loosen":
        raise ValueError("only a loosening takes a step-up")
    request = {
        "kind": MODE_LOOSEN_KIND,
        "namespace": namespace,
        "version": 1,
        "owner_action_id": str(owner_action_id),
        "expected_version": expected_version,
        **_change_body(change),
    }
    return StepUpBinding(
        MODE_LOOSEN_KIND,
        _target(change, expected_version + 1),
        _digest(request),
        _digest([request, _profile_body(profiles[change.mode])]),
        namespace,
    )


def _binds(binding: StepUpBinding, change: ModeChange) -> bool:
    if binding.kind != MODE_LOOSEN_KIND:
        return False
    mode, _, rest = binding.target.partition(":")
    return mode == change.mode and rest.endswith(":" + _digest(_change_body(change))[:16])


async def authorize_mode_change(
    connection: Any,
    change: ModeChange,
    profiles: Mapping[str, ModeProfile],
    *,
    binding: StepUpBinding | None,
) -> Verdict:
    """Admit a mode change: a tightening passes; a loosening spends one step-up.

    Called inside the transaction that writes the change; the writer records the
    step-up receipt (``consume_stepup(..., "finish", ...)``) with the write.
    """
    if not flags.MODE_CONTROLS_LIVE:
        raise ModeControlsDark("mode controls are dark")
    verdict = classify_change(change, profiles)
    if verdict != "loosen":
        return verdict
    if dashboard_stepup_proof.get() is None or binding is None or not _binds(binding, change):
        raise ModeStepUpRequired("loosening a mode needs dashboard step-up")
    admission = await consume_stepup(connection, "begin", binding)
    if admission.get("state") != "admitted":
        raise ModeStepUpRequired("dashboard step-up not admitted")
    return verdict


# -- the owner.mode record ----------------------------------------------------------


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def mode_event(
    modes: frozenset[str],
    *,
    subject: UUID,
    evidence_ref: UUID,
    generation: int,
    at: datetime,
    until: datetime,
    evidence_source: str = SOURCE,
) -> dict[str, Any]:
    """The ``owner.mode`` request body for the active mode set until ``until``."""
    ids = sorted(modes)
    if any(m not in MODES for m in ids):
        raise ValueError("only the eight mode IDs; in flight is posture")
    if until <= at:
        raise ValueError("a finite future effective end is required")
    return {
        "version": 1,
        "kind": "owner.mode",
        "subject": str(subject),
        "source": SOURCE,
        "event_key": f"mode:{','.join(ids)}:{_iso(at)}",
        "expected_generation": generation,
        "state": "known",
        "observed_at": _iso(at),
        "effective_span": {"start": _iso(at), "end": _iso(until)},
        "typed_value_or_ref": ids,
        "evidence_refs": [
            {"ref": str(evidence_ref), "source": evidence_source, "expires_at": _iso(until)}
        ],
        "labels": _LABELS,
        "at": _iso(at),
        "confirmed_at": _iso(at),
        "retracts_event_id": None,
    }


__all__ = [
    "BILL_WINDOW",
    "DEFAULT_PROFILES",
    "MODE_LOOSEN_KIND",
    "OBLIGATION_CLASSES",
    "OWNER_CAUSES",
    "PINNED",
    "SOURCE",
    "WAKE_EARLIEST",
    "WAKE_FALLBACK",
    "EditBreakThrough",
    "EditDuration",
    "EditHolds",
    "Enter",
    "Exit",
    "ModeChange",
    "ModeControlsDark",
    "ModeEntry",
    "ModeProfile",
    "ModeStepUpRequired",
    "Posture",
    "arms_absence",
    "authorize_mode_change",
    "classify_change",
    "classify_obligation",
    "held_by",
    "mode_binding",
    "mode_event",
    "mode_id",
    "posture",
    "wake_brief_time",
]
