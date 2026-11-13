"""The calendar replan exception (RFC 0038 D13, owner decision bu-qlx055 q12 = B).

A goal's ``calendar_update_event`` call is a communication whose party set the
broker cannot determine, so it parks for approval. One narrow kind skips the
park: a pure move of an owner-only block Butlers created. Both halves are
checked where they can be trusted:

* The broker checks the *call*: only time fields change (``move_only``). It then
  forwards the call conditionally, naming ``REPLAN_MOVE`` in the assertion.
* The calendar domain checks the *event*, at execution, against the provider's
  copy rather than the model's arguments (``movable``): the event carries the
  ``butler_generated`` marker the server wrote when Butlers created it, has no
  attendees and no recurrence. The write is bound to the etag it checked, so an
  attendee added in between fails the write instead of slipping through.

Anything else (an owner-created or unmarked event, attendees, a recurring
series or all-day event, an unreadable or missing event, a new slot that
overlaps another event) is declined with ``REPLAN_DECLINED``
before any write, and the broker parks the call as it always has. Events made
before Butlers wrote the marker carry none, so they count as owner-created.

A committed move returns an undo receipt: the pre-image and the exact inverse
call, which is itself a move of the same marked event.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .models import CalendarEvent

#: The conditional the broker names in a forwarded assertion.
REPLAN_MOVE = "replan_move"
#: The ``status`` of a declined conditional move: nothing was written.
REPLAN_DECLINED = "replan_declined"
#: The one tool the exception covers.
REPLAN_TOOL = "calendar_update_event"

#: Arguments a move may carry. ``request_id`` is deliberately absent: a declined
#: move must never leave a replayable receipt under a key the parked call reuses.
MOVE_ARGS = frozenset({"event_id", "start_at", "end_at", "timezone", "calendar_id"})
#: At least one of these must change.
_TIME_ARGS = ("start_at", "end_at")


def move_only(args: Mapping[str, Any]) -> bool:
    """True when ``args`` move one event in time and change nothing else."""
    if not set(args) <= MOVE_ARGS:
        return False
    event_id = args.get("event_id")
    if not isinstance(event_id, str) or not event_id.strip():
        return False
    return any(args.get(name) is not None for name in _TIME_ARGS)


def movable(event: CalendarEvent | None) -> str | None:
    """``None`` when the provider's event may move without a park, else why not."""
    if event is None:
        return "unreadable"
    if event.butler_generated is not True:
        return "not_butler_created"
    if event.attendees:
        return "has_attendees"
    if event.recurrence_rule:
        return "recurring"
    if event.all_day:
        return "all_day"
    return None


def declined(reason: str, *, event_id: str, calendar_id: str | None) -> dict[str, Any]:
    """The declined result: names why, and that nothing was written."""
    return {
        "status": REPLAN_DECLINED,
        "reason": reason,
        "event_id": event_id,
        "calendar_id": calendar_id,
    }


def undo_receipt(pre_state: Mapping[str, Any]) -> dict[str, Any]:
    """The inverse of a committed move: the exact call that puts the block back."""
    arguments = {
        "event_id": pre_state["event_id"],
        "calendar_id": pre_state["calendar_id"],
        "start_at": pre_state["start_at"],
        "end_at": pre_state["end_at"],
    }
    if pre_state.get("timezone"):
        arguments["timezone"] = pre_state["timezone"]
    return {"tool": REPLAN_TOOL, "arguments": arguments}


__all__ = [
    "MOVE_ARGS",
    "REPLAN_DECLINED",
    "REPLAN_MOVE",
    "REPLAN_TOOL",
    "declined",
    "movable",
    "move_only",
    "undo_receipt",
]
