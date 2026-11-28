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
    ``no_park_undo``      a Butlers block with no attendees: the only step that runs without a card.
    ``owner_submit``      an owner-organised event with no other attendees (parked as a card).
    ``parked_per_event``  anything that reaches another person (attendees notified, a decline).

An event whose ownership is unproven, an all-day event, a recurring event whose occurrence cannot be
named and an event with no etag are never touched: each lands in ``skipped`` with its reason. A step
is bound to the etag the plan was compiled against (``etag``, outside the call's arguments), so a
later write can decline when the event moved. A destination never overlaps another busy interval,
including an earlier step's destination in the same plan.

Skips are decided before any slot counts as free: only an event that really gets a step vacates its
slot, found by iterating to a fixpoint. A step that lands on a slot another step vacates lists that
step in ``depends_on`` and comes after it; applying it only makes sense once the dependency was
applied. A partial overlap moves the whole event (a plan never splits one).
"""

from __future__ import annotations

import hashlib
import json
import uuid
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
    #: Indices of the steps whose vacated slot this step uses; it only runs if they were applied.
    depends_on: tuple[int, ...] = ()


@dataclass(frozen=True)
class Skipped:
    entry_id: str
    why: str


@dataclass(frozen=True)
class Plan:
    kind: PlanKind
    steps: list[PlanStep] = field(default_factory=list)
    skipped: list[Skipped] = field(default_factory=list)
    #: ``plan_unavailable`` or ``plan_too_large`` (no steps then), else None.
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
                "d": list(s.depends_on),
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


def _request_id(event: ResolutionEvent, plan_key: str, index: int) -> str:
    """A UUID bound to the event's state: same state replays one command, a changed etag is new."""
    seed = f"plan:{event.etag}:{plan_key}:{index}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, seed))


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
            # Explicit, not defaulted: a plan's decline shows on the owner's own calendar only.
            "send_updates": "none",
            "request_id": request_id,
        },
    }


def _static_skip(event: ResolutionEvent, kind: PlanKind, all_day: frozenset[str]) -> str | None:
    """Why an event is never touched, whatever the free slots are; None when it may be."""
    status = (event.status or "").lower()
    if event.entry_id in all_day:
        return "all_day_event"
    if status == "cancelled":
        return "cancelled"
    if event.event_class == "unproven":
        return "ownership_unproven"
    if not event.etag:
        return "no_etag"
    if event.event_class == "invited":
        if kind == "shift_window":
            return "only_the_organizer_can_move_it"
        if event.owner_response == "declined":
            return "already_declined"
        return None
    if event.owner_response == "declined":
        return "already_declined"
    if event.recurring and not event.instance_event_id:
        return "occurrence_unknown"
    return None


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
    working_hours: Sequence[tuple[datetime, datetime]] | None = None,
) -> Plan:
    """Compile one verb over ``window`` into steps and skipped events. Pure.

    ``events`` are the owner's events the caller found in the window; ``all_day_entry_ids`` names
    those that are all-day (or out of office), which are never moved. ``free_slots`` (clear) are
    windows free on every owner-busy calendar. ``busy`` (shift) holds every busy interval,
    including the events being shifted: a shifted event's old slot is free only for the steps that
    declare it as a dependency. ``stale_source`` also covers a partial mirror: no plan is compiled.
    ``working_hours`` (shift) are the owner-zone working-day windows a shifted event must land in.
    """
    start, end = window
    if end <= start:
        raise ValueError("window must end after it starts")
    if kind == "shift_window" and (delta is None or delta == timedelta(0)):
        raise ValueError("shift_window needs a non-zero delta")

    def refused(reason: str) -> Plan:
        return Plan(kind, reason=reason, plan_digest=_digest(kind, window, [], [], reason))

    if stale_source:
        return refused("plan_unavailable")
    if end - start > MAX_WINDOW:
        return refused("plan_too_large")

    plan_key = hashlib.sha256(
        f"{kind}\x00{_iso(start)}\x00{_iso(end)}\x00{delta}".encode()
    ).hexdigest()[:16]

    in_window = sorted(
        (e for e in events if _overlaps(e.start_at, e.end_at, start, end)),
        key=lambda e: (e.start_at, e.entry_id),
    )
    skipped: dict[str, str] = {}
    declines: list[ResolutionEvent] = []
    movers: list[ResolutionEvent] = []
    for event in in_window:
        why = _static_skip(event, kind, all_day_entry_ids)
        if why is not None:
            skipped[event.entry_id] = why
        elif event.event_class == "invited":
            declines.append(event)
        else:
            movers.append(event)

    # Fixpoint: only an event that really gets a step vacates its slot. An event that finds no
    # destination (or sits in a dependency cycle) stops vacating, and the rest are re-decided.
    vacating = {e.entry_id for e in movers}
    while True:
        free = list(free_slots)
        taken: list[tuple[datetime, datetime]] = []
        slots: dict[str, tuple[datetime, datetime]] = {}
        failed: dict[str, str] = {}
        for event in movers:
            if event.entry_id not in vacating:
                continue
            slot = _destination(
                kind, event, end, now, delta, free, busy, vacating, taken, working_hours
            )
            if isinstance(slot, str):
                failed[event.entry_id] = slot
            else:
                taken.append(slot)
                slots[event.entry_id] = slot
        if not failed:
            deps = _dependencies(movers, slots, busy)
            order, cyclic = _order(movers, deps)
            if not cyclic:
                break
            failed = {entry: "overlaps_another_moving_event" for entry in cyclic}
        vacating -= set(failed)
        skipped.update(failed)

    # Declines come first in window order; moves follow dependency order.
    ordered: list[tuple[ResolutionEvent, str]] = [(e, "decline") for e in declines]
    ordered += [(e, "move") for e in order]
    ordered.sort(key=lambda pair: 0 if pair[1] == "decline" else 1)
    position = {event.entry_id: i for i, (event, _) in enumerate(ordered)}
    steps: list[PlanStep] = []
    for index, (event, action) in enumerate(ordered):
        rid = _request_id(event, plan_key, index)
        if action == "decline":
            steps.append(
                PlanStep(
                    index,
                    event.entry_id,
                    "decline",
                    _decline_call(event, rid),
                    "parked_per_event",
                    event.etag or "",
                    "invited meeting in a cleared window",
                )
            )
            continue
        steps.append(
            PlanStep(
                index,
                event.entry_id,
                "move",
                _move_call(event, slots[event.entry_id], rid),
                _authority(event),
                event.etag or "",
                "moved out of a cleared window"
                if kind == "clear_window"
                else "shifted with the window",
                tuple(sorted(position[d] for d in deps[event.entry_id])),
            )
        )

    if len(steps) > MAX_STEPS:
        return refused("plan_too_large")
    skipped_out = [
        Skipped(e.entry_id, skipped[e.entry_id]) for e in in_window if e.entry_id in skipped
    ]
    return Plan(kind, steps, skipped_out, None, _digest(kind, window, steps, skipped_out, None))


def _dependencies(
    movers: Sequence[ResolutionEvent],
    slots: dict[str, tuple[datetime, datetime]],
    busy: Sequence[BusyInterval],
) -> dict[str, set[str]]:
    """For each moved event, the other moved events whose old slot its destination uses."""
    deps: dict[str, set[str]] = {entry: set() for entry in slots}
    for event in movers:
        slot = slots.get(event.entry_id)
        if slot is None:
            continue
        for other in movers:
            if other.entry_id == event.entry_id or other.entry_id not in slots:
                continue
            if _overlaps(slot[0], slot[1], other.start_at, other.end_at):
                deps[event.entry_id].add(other.entry_id)
    return deps


def _order(
    movers: Sequence[ResolutionEvent], deps: dict[str, set[str]]
) -> tuple[list[ResolutionEvent], list[str]]:
    """Dependencies first, ties in window order. Returns (ordered, members of a cycle)."""
    pending = [e for e in movers if e.entry_id in deps]
    done: set[str] = set()
    out: list[ResolutionEvent] = []
    while pending:
        ready = next((e for e in pending if deps[e.entry_id] <= done), None)
        if ready is None:
            return out, [e.entry_id for e in pending]
        pending.remove(ready)
        done.add(ready.entry_id)
        out.append(ready)
    return out, []


def _destination(
    kind: PlanKind,
    event: ResolutionEvent,
    window_end: datetime,
    now: datetime,
    delta: timedelta | None,
    free: list[tuple[datetime, datetime]],
    busy: Sequence[BusyInterval],
    vacating: set[str],
    taken: Sequence[tuple[datetime, datetime]],
    working_hours: Sequence[tuple[datetime, datetime]] | None = None,
) -> tuple[datetime, datetime] | str:
    """Where one movable event goes, or the reason it stays."""
    if kind == "clear_window":
        slot = _take(free, event.duration, max(window_end, now))
        return slot if slot is not None else "no_free_slot"
    assert delta is not None
    begin, finish = event.start_at + delta, event.end_at + delta
    if begin < now:
        return "destination_in_the_past"
    if working_hours is not None and not any(
        w0 <= begin and finish <= w1 for w0, w1 in working_hours
    ):
        return "destination_outside_working_hours"
    for interval in busy:
        if interval.entry_id == event.entry_id or interval.entry_id in vacating:
            continue  # itself, or an event that really moves: a declared dependency, checked below
        if _overlaps(begin, finish, interval.start_at, interval.end_at):
            return "destination_busy"
    for other in taken:
        if _overlaps(begin, finish, other[0], other[1]):
            return "overlaps_another_moving_event"
    return begin, finish
