"""Calendar change plans: one owner verb compiled to a multi-event plan (bu-5ii4gj.11 P1).

Pure and deterministic: no model, no provider call, no randomness, no clock. Equal inputs give an
equal plan and an equal ``plan_digest``. The caller supplies the events (with provenance and the
owner's response), the busy intervals and, for ``clear_window``, the windows free on every
owner-busy calendar; this module only decides.

Verbs
    ``clear_window``  every movable event in the window moves to the earliest slot after it, and an
                      invited meeting is declined.
    ``shift_window``  every movable event in the window moves by one signed delta. Invited meetings
                      are skipped: only their organizer can move them.

Authority per step
    ``no_park_undo``      a Butlers block with no attendees.
    ``owner_submit``      an owner-organised event with no other attendees.
    ``parked_per_event``  anything that reaches another person (attendees notified, a decline).

An event whose ownership is unproven, an all-day event, a recurring event whose occurrence cannot be
named and an event with no etag are never touched: each lands in ``skipped`` with its reason. A step
is bound to the etag the plan was compiled against (``etag``, outside the call's arguments), so a
later write can decline when the event moved. A destination never overlaps another busy interval,
including an earlier step's destination in the same plan.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Literal

from butlers.core.temporal.conflict_resolution import ResolutionEvent, _grid_up

PlanKind = Literal["clear_window", "shift_window"]
StepAction = Literal["move", "decline"]
Authority = Literal["no_park_undo", "owner_submit", "parked_per_event"]

#: A plan is one decision set: at most this many steps (the decision-set item ceiling).
MAX_STEPS = 16
#: One working day, plus the hour a daylight-saving change adds.
MAX_WINDOW = timedelta(hours=25)


@dataclass(frozen=True)
class BusyInterval:
    """One interval the owner is busy for, keyed by the entry that holds it."""

    entry_id: str
    start_at: datetime
    end_at: datetime


@dataclass(frozen=True)
class PlanStep:
    index: int
    entry_id: str
    action: StepAction
    #: ``{"tool": str, "args": dict}``: exactly what applying the step runs.
    exact_call: dict[str, Any]
    authority: Authority
    #: The etag the plan was compiled against; the apply step declines if the event moved since.
    etag: str
    reason: str


@dataclass(frozen=True)
class Skipped:
    entry_id: str
    why: str


@dataclass(frozen=True)
class Plan:
    kind: PlanKind
    steps: list[PlanStep] = field(default_factory=list)
    skipped: list[Skipped] = field(default_factory=list)
    #: ``stale_source`` or ``plan_too_large`` (no steps then), else None.
    reason: str | None = None
    plan_digest: str = ""

    @property
    def plan_id(self) -> str:
        """Derived from the digest, so a retried call names the same plan."""
        return f"plan_{self.plan_digest[:24]}"


def _iso(moment: datetime) -> str:
    return moment.isoformat()


def _overlaps(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    return a_start < b_end and b_start < a_end


def _digest(
    kind: str,
    window: tuple[datetime, datetime],
    steps: Sequence[PlanStep],
    skipped: Sequence[Skipped],
    reason: str | None,
) -> str:
    body = {
        "kind": kind,
        "window": [_iso(window[0]), _iso(window[1])],
        "steps": [
            {
                "i": s.index,
                "e": s.entry_id,
                "a": s.action,
                "c": s.exact_call,
                "u": s.authority,
                "t": s.etag,
            }
            for s in steps
        ],
        "skipped": [[s.entry_id, s.why] for s in skipped],
        "reason": reason,
    }
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _authority(event: ResolutionEvent) -> Authority:
    if event.has_other_attendees:
        return "parked_per_event"
    return "no_park_undo" if event.event_class == "butlers_block" else "owner_submit"


def _take(
    free: list[tuple[datetime, datetime]], duration: timedelta, not_before: datetime
) -> tuple[datetime, datetime] | None:
    """The earliest slot for ``duration`` at or after ``not_before``, removed from ``free``."""
    for start, end in sorted(free):
        begin = _grid_up(max(start, not_before))
        if begin + duration <= end:
            slot = (begin, begin + duration)
            rest = [w for w in sorted(free) if w != (start, end)]
            for piece in ((start, slot[0]), (slot[1], end)):
                if piece[0] < piece[1]:
                    rest.append(piece)
            free[:] = rest
            return slot
    return None


def _move_call(event: ResolutionEvent, slot: tuple[datetime, datetime], request_id: str) -> dict:
    args: dict[str, Any] = {
        "event_id": event.instance_event_id or event.provider_event_id,
        "start_at": _iso(slot[0]),
        "end_at": _iso(slot[1]),
        "request_id": request_id,
    }
    if event.calendar_id:
        args["calendar_id"] = event.calendar_id
    return {"tool": "calendar_update_event", "args": args}


def _decline_call(event: ResolutionEvent, request_id: str) -> dict:
    return {
        "tool": "calendar_respond",
        "args": {
            "entry_id": event.entry_id,
            "response_status": "declined",
            # A plan's decline shows on the owner's own calendar only: no email reaches the
            # organizer.
            "send_updates": "none",
            "request_id": request_id,
        },
    }


def compile_plan(
    kind: PlanKind,
    window: tuple[datetime, datetime],
    events: Sequence[ResolutionEvent],
    *,
    now: datetime,
    free_slots: Sequence[tuple[datetime, datetime]] = (),
    busy: Sequence[BusyInterval] = (),
    delta: timedelta | None = None,
    all_day_entry_ids: frozenset[str] = frozenset(),
    stale_source: bool = False,
) -> Plan:
    """Compile one verb over ``window`` into steps and skipped events. Pure.

    ``events`` are the owner's events the caller found in the window; ``all_day_entry_ids`` names
    those that are all-day (or out of office), which are never moved. ``free_slots`` (clear) are
    windows free on every owner-busy calendar. ``busy`` (shift) holds every busy interval,
    including the events being shifted: their own current slots do not block their destinations.
    """
    start, end = window
    if end <= start:
        raise ValueError("window must end after it starts")
    if kind == "shift_window" and (delta is None or delta == timedelta(0)):
        raise ValueError("shift_window needs a non-zero delta")

    if stale_source:
        return Plan(
            kind, reason="stale_source", plan_digest=_digest(kind, window, [], [], "stale_source")
        )
    if end - start > MAX_WINDOW:
        return Plan(
            kind,
            reason="plan_too_large",
            plan_digest=_digest(kind, window, [], [], "plan_too_large"),
        )

    plan_key = hashlib.sha256(
        f"{kind}\x00{_iso(start)}\x00{_iso(end)}\x00{delta}".encode()
    ).hexdigest()[:16]

    in_window = sorted(
        (e for e in events if _overlaps(e.start_at, e.end_at, start, end)),
        key=lambda e: (e.start_at, e.entry_id),
    )
    moving = {
        e.entry_id for e in in_window if e.event_class in ("butlers_block", "owner_organized")
    }
    free = list(free_slots)
    taken: list[tuple[datetime, datetime]] = []
    steps: list[PlanStep] = []
    skipped: list[Skipped] = []

    def skip(event: ResolutionEvent, why: str) -> None:
        skipped.append(Skipped(event.entry_id, why))

    for event in in_window:
        if event.entry_id in all_day_entry_ids:
            skip(event, "all_day_event")
        elif event.event_class == "unproven":
            skip(event, "ownership_unproven")
        elif not event.etag:
            skip(event, "no_etag")
        elif event.event_class == "invited":
            if kind == "shift_window":
                skip(event, "only_the_organizer_can_move_it")
            elif event.owner_response == "declined":
                skip(event, "already_declined")
            else:
                index = len(steps)
                steps.append(
                    PlanStep(
                        index,
                        event.entry_id,
                        "decline",
                        _decline_call(event, f"plan:{plan_key}:{index}"),
                        "parked_per_event",
                        event.etag,
                        "invited meeting in a cleared window",
                    )
                )
        elif event.recurring and not event.instance_event_id:
            skip(event, "occurrence_unknown")
        else:
            slot = _destination(kind, event, end, now, delta, free, busy, moving, taken)
            if isinstance(slot, str):
                skip(event, slot)
                continue
            taken.append(slot)
            index = len(steps)
            steps.append(
                PlanStep(
                    index,
                    event.entry_id,
                    "move",
                    _move_call(event, slot, f"plan:{plan_key}:{index}"),
                    _authority(event),
                    event.etag,
                    "moved out of a cleared window"
                    if kind == "clear_window"
                    else "shifted with the window",
                )
            )

    if len(steps) > MAX_STEPS:
        return Plan(
            kind,
            reason="plan_too_large",
            plan_digest=_digest(kind, window, [], [], "plan_too_large"),
        )
    return Plan(kind, steps, skipped, None, _digest(kind, window, steps, skipped, None))


def _destination(
    kind: PlanKind,
    event: ResolutionEvent,
    window_end: datetime,
    now: datetime,
    delta: timedelta | None,
    free: list[tuple[datetime, datetime]],
    busy: Sequence[BusyInterval],
    moving: set[str],
    taken: Sequence[tuple[datetime, datetime]],
) -> tuple[datetime, datetime] | str:
    """Where one movable event goes, or the reason it stays."""
    if kind == "clear_window":
        slot = _take(free, event.duration, max(window_end, now))
        return slot if slot is not None else "no_free_slot"
    assert delta is not None
    begin, finish = event.start_at + delta, event.end_at + delta
    if begin < now:
        return "destination_in_the_past"
    for interval in busy:
        if interval.entry_id in moving:
            continue  # moves too: its old slot frees up, its new one is checked as a destination
        if _overlaps(begin, finish, interval.start_at, interval.end_at):
            return "destination_busy"
    for other in taken:
        if _overlaps(begin, finish, other[0], other[1]):
            return "destination_busy"
    return begin, finish
