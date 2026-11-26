"""Resolution options for an overlap: pure, deterministic, no model (bu-5ii4gj.11 slice R1).

``options_for`` turns one overlap (two events with their provenance, the owner's response to each
and the slots free on every owner-busy calendar) into one to five typed options. Each option names
its exact tool call and an authority class, so the dashboard can render it as a verb and the resolve
route can run exactly that call and nothing else.

Nothing here reads a database, calls a provider or draws a random number. Same input, same options
and same ``issue_digest``: the resolve route recomputes the options server side and refuses a stale
digest, so the digest must be a pure function of what the owner was shown.

Authority classes
-----------------
``no_park_undo``      a Butlers-owned block with no attendees: moved at once, with an undo.
``owner_submit``      the owner's own solo event: the owner's click is the submission.
``parked_per_event``  anything that reaches another person (an attendee-bearing event the owner
                      organised, or a response to an invitation): parked for approval, per event,
                      never in a batch.

Slots sit on a 15-minute grid (``SLOT_GRID``), so two reads a few seconds apart give the same
options and the same digest. A recurring event is only ever acted on per occurrence
(``recurrence_scope='this'``); a series edit is never an option; a recurring event whose
occurrence is unknown gets no move at all. A stale
calendar source offers nothing, ``keep_both`` included: ``reason='stale_source'``. A movable
event with no free slot sets ``reason='no_free_slot'`` while the other options stay.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, tzinfo
from typing import Any, Literal

OptionKind = Literal["move_block", "move_own", "decline", "tentative", "ask_organizer", "keep_both"]
Authority = Literal["no_park_undo", "owner_submit", "parked_per_event"]
EventClass = Literal["butlers_block", "owner_organized", "invited"]

#: The most options one issue shows; ``keep_both`` always holds the last place.
MAX_OPTIONS = 5
#: How many free slots ``ask_organizer`` offers the organizer (typed datetime slots only).
ASK_ORGANIZER_SLOTS = 2
#: Proposed slots start on this grid, never at an odd second.
SLOT_GRID = timedelta(minutes=15)
#: Dark until the resolve route ships (R2): the read model attaches options only when true.
RESOLUTION_OPTIONS_LIVE = False
#: The working day a moved event may land in, in the owner's display zone.
DAY_START_HOUR = 8
DAY_END_HOUR = 19
#: The tool that records an acknowledgement. It has no provider effect.
ACKNOWLEDGE_TOOL = "calendar_conflict_acknowledge"


@dataclass(frozen=True)
class ResolutionEvent:
    """One side of an overlap, as the compiler needs it. Carries no title and no description."""

    entry_id: str
    provider_event_id: str
    calendar_id: str | None
    start_at: datetime
    end_at: datetime
    etag: str | None
    event_class: EventClass
    #: True when anyone other than the owner is on the event.
    has_other_attendees: bool = False
    #: The owner's own response (``accepted``, ``tentative``, ``needsAction``) for an invitation.
    owner_response: str | None = None
    #: True when the event recurs. Without ``instance_start_at`` no move is offered: the compiler
    #: never edits a series by omission.
    recurring: bool = False
    #: Set when the event recurs: the occurrence's original start. Options then act on it alone.
    instance_start_at: datetime | None = None

    @property
    def duration(self) -> timedelta:
        return self.end_at - self.start_at


@dataclass(frozen=True)
class Option:
    id: str
    kind: OptionKind
    authority: Authority
    #: ``{"tool": str, "args": dict}``: exactly what the resolve route runs.
    exact_call: dict[str, Any]
    #: Which fields explain this option (entry ids and the chosen slot), never any event text.
    rationale_fields: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Resolution:
    options: list[Option]
    #: Why options are missing: ``stale_source`` (all) or ``no_free_slot`` (a move), else None.
    reason: str | None = None


def _iso(moment: datetime) -> str:
    return moment.isoformat()


def _request_id(pair_id: str, option_id: str) -> str:
    return f"resolve:{pair_id}:{option_id}"


def _grid_up(moment: datetime) -> datetime:
    """``moment`` rounded up to the next ``SLOT_GRID`` boundary (itself when already on one)."""
    step = SLOT_GRID.total_seconds()
    seconds = moment.timestamp()
    return moment + timedelta(seconds=(-seconds) % step) if (seconds % step) else moment


def earliest_slot(
    free_slots: Sequence[tuple[datetime, datetime]], duration: timedelta, *, not_before: datetime
) -> tuple[datetime, datetime] | None:
    """The earliest ``duration``-long slot in a free window, at or after ``not_before``."""
    for start, end in sorted(free_slots):
        begin = _grid_up(max(start, not_before))
        if begin + duration <= end:
            return begin, begin + duration
    return None


def _slots(
    free_slots: Sequence[tuple[datetime, datetime]],
    duration: timedelta,
    *,
    count: int,
    not_before: datetime,
) -> list[tuple[datetime, datetime]]:
    """Up to ``count`` distinct fitting slots, earliest first, each in a different free window."""
    found: list[tuple[datetime, datetime]] = []
    for start, end in sorted(free_slots):
        begin = _grid_up(max(start, not_before))
        if begin + duration <= end:
            found.append((begin, begin + duration))
        if len(found) == count:
            break
    return found


def _move_call(event: ResolutionEvent, slot: tuple[datetime, datetime], request_id: str) -> dict:
    args: dict[str, Any] = {
        "event_id": event.provider_event_id,
        "start_at": _iso(slot[0]),
        "end_at": _iso(slot[1]),
        "request_id": request_id,
    }
    if event.calendar_id:
        args["calendar_id"] = event.calendar_id
    if event.instance_start_at is not None:
        args["recurrence_scope"] = "this"
        args["instance_start_at"] = _iso(event.instance_start_at)
    return {"tool": "calendar_update_event", "args": args}


def options_for(
    pair_id: str,
    first: ResolutionEvent,
    second: ResolutionEvent,
    free_slots: Sequence[tuple[datetime, datetime]],
    *,
    not_before: datetime,
    stale_source: bool = False,
    templates_active: bool = False,
) -> Resolution:
    """The resolution options for one overlap. Pure: see the module docstring.

    ``free_slots`` are windows free on EVERY owner-busy calendar, so a move never trades one
    overlap for another. ``not_before`` is "now": nothing is ever proposed in the past.
    """
    if stale_source:
        return Resolution([], reason="stale_source")
    moves: list[Option] = []
    declines: list[Option] = []
    tentatives: list[Option] = []
    asks: list[Option] = []
    reason: str | None = None
    for event in sorted((first, second), key=lambda e: e.entry_id):
        if event.event_class in ("butlers_block", "owner_organized"):
            if event.recurring and event.instance_start_at is None:
                continue  # an occurrence we cannot name is never edited, and never the series
            slot = earliest_slot(free_slots, event.duration, not_before=not_before)
            if slot is None:
                reason = "no_free_slot"
                continue
            kind: OptionKind = "move_block" if event.event_class == "butlers_block" else "move_own"
            option_id = f"{kind}:{event.entry_id}"
            if event.event_class == "butlers_block" and not event.has_other_attendees:
                authority: Authority = "no_park_undo"
            elif event.has_other_attendees:
                authority = "parked_per_event"  # shifting it notifies the attendees
            else:
                authority = "owner_submit"
            moves.append(
                Option(
                    option_id,
                    kind,
                    authority,
                    _move_call(event, slot, _request_id(pair_id, option_id)),
                    {
                        "entry_id": event.entry_id,
                        "slot_start": _iso(slot[0]),
                        "slot_end": _iso(slot[1]),
                    },
                )
            )
        else:
            for status, bucket in (("declined", declines), ("tentative", tentatives)):
                if status == "tentative" and event.owner_response == "tentative":
                    continue
                kind = "decline" if status == "declined" else "tentative"
                option_id = f"{kind}:{event.entry_id}"
                bucket.append(
                    Option(
                        option_id,
                        kind,
                        "parked_per_event",
                        {
                            "tool": "calendar_respond",
                            "args": {
                                "entry_id": event.entry_id,
                                "response_status": status,
                                # A radar response is shown on the owner's own calendar only:
                                # no email reaches the organizer.
                                "send_updates": "none",
                                "request_id": _request_id(pair_id, option_id),
                            },
                        },
                        {"entry_id": event.entry_id},
                    )
                )
            slots = _slots(
                free_slots, event.duration, count=ASK_ORGANIZER_SLOTS, not_before=not_before
            )
            if templates_active and slots:
                option_id = f"ask_organizer:{event.entry_id}"
                asks.append(
                    Option(
                        option_id,
                        "ask_organizer",
                        "parked_per_event",
                        {
                            "tool": "approvals_standing_reply",
                            "args": {
                                "entry_id": event.entry_id,
                                "slots": [_iso(s[0]) for s in slots],
                                "request_id": _request_id(pair_id, option_id),
                            },
                        },
                        {"entry_id": event.entry_id, "slots": [_iso(s[0]) for s in slots]},
                    )
                )
    keep = Option(
        "keep_both",
        "keep_both",
        "owner_submit",
        {
            "tool": ACKNOWLEDGE_TOOL,
            "args": {
                "pair_id": pair_id,
                "etags": {e.entry_id: e.etag for e in (first, second)},
            },
        },
        {"entry_ids": sorted((first.entry_id, second.entry_id))},
    )
    # Moves first, then one of each response kind in turn, so a cap never drops a whole kind.
    responses = [
        bucket[i] for i in range(2) for bucket in (declines, tentatives, asks) if i < len(bucket)
    ]
    ordered = [*moves, *responses]
    return Resolution([*ordered[: MAX_OPTIONS - 1], keep], reason=reason)


def issue_digest(events: Sequence[ResolutionEvent], options: Sequence[Option]) -> str:
    """A digest over both events' provider etags and the option set, for the resolve route."""
    payload = {
        "etags": sorted((e.entry_id, e.etag, _iso(e.start_at), _iso(e.end_at)) for e in events),
        "options": [(o.id, o.kind, o.authority, o.exact_call) for o in options],
    }
    blob = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()


def free_windows(
    busy: Iterable[tuple[datetime, datetime]],
    lo: datetime,
    hi: datetime,
    zone: tzinfo,
    *,
    day_start_hour: int = DAY_START_HOUR,
    day_end_hour: int = DAY_END_HOUR,
) -> list[tuple[datetime, datetime]]:
    """Working-day windows inside ``[lo, hi)`` that no busy interval touches, earliest first.

    ``busy`` must hold every owner-busy interval across every calendar read, so a slot taken from
    here never trades one overlap for another. Only the scanned window is searched: a slot outside
    it was never checked, so it is never offered.
    """
    merged: list[list[datetime]] = []
    for start, end in sorted(busy):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    windows: list[tuple[datetime, datetime]] = []
    day = lo.astimezone(zone).date()
    last_day = hi.astimezone(zone).date()
    while day <= last_day:
        w_start = max(datetime.combine(day, time(day_start_hour), tzinfo=zone), lo)
        w_end = min(datetime.combine(day, time(day_end_hour), tzinfo=zone), hi)
        cursor = w_start
        for b_start, b_end in merged:
            if b_end <= cursor or b_start >= w_end:
                continue
            if b_start > cursor:
                windows.append((cursor, b_start))
            cursor = max(cursor, b_end)
        if cursor < w_end:
            windows.append((cursor, w_end))
        day += timedelta(days=1)
    return [(a, b) for a, b in windows if b > a]
