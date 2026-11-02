"""Canonical public arguments shared by calendar approval parking and replay."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any, overload

# Required names and defaults mirror the eight public mutation signatures.
_CALLS: dict[str, tuple[tuple[str, ...], dict[str, Any]]] = {
    "calendar_update_event": (
        ("event_id",),
        {
            "title": None,
            "start_at": None,
            "end_at": None,
            "all_day": None,
            "timezone": None,
            "description": None,
            "body": None,
            "location": None,
            "attendees": None,
            "recurrence_rule": None,
            "recurrence_scope": "series",
            "instance_start_at": None,
            "color_id": None,
            "calendar_id": None,
            "conflict_policy": None,
            "entity_ids": None,
            "clear_entity_ids": False,
            "request_id": None,
        },
    ),
    "calendar_delete_event": (
        ("event_id",),
        {
            "calendar_id": None,
            "recurrence_scope": "series",
            "instance_start_at": None,
            "send_updates": None,
            "request_id": None,
        },
    ),
    "calendar_update_event_instance": (
        ("event_id", "instance_start_at"),
        {
            "title": None,
            "start_at": None,
            "end_at": None,
            "timezone": None,
            "description": None,
            "body": None,
            "location": None,
            "recurrence_scope": "this",
            "calendar_id": None,
            "entity_ids": None,
            "request_id": None,
        },
    ),
    "calendar_delete_event_instance": (
        ("event_id", "instance_start_at"),
        {
            "recurrence_scope": "this",
            "send_updates": None,
            "calendar_id": None,
            "request_id": None,
        },
    ),
    "calendar_create_butler_event": (
        ("butler_name", "title", "start_at"),
        {
            "end_at": None,
            "timezone": None,
            "description": None,
            "location": None,
            "recurrence_rule": None,
            "cron": None,
            "until_at": None,
            "action": "Run butler event",
            "action_args": None,
            "source_hint": None,
            "request_id": None,
        },
    ),
    "calendar_update_butler_event": (
        ("event_id",),
        {
            "title": None,
            "start_at": None,
            "end_at": None,
            "timezone": None,
            "recurrence_rule": None,
            "cron": None,
            "until_at": None,
            "enabled": None,
            "source_hint": None,
            "body": None,
            "entity_ids": None,
            "request_id": None,
        },
    ),
    "calendar_delete_butler_event": (
        ("event_id",),
        {
            "scope": "series",
            "instance_start_at": None,
            "source_hint": None,
            "request_id": None,
        },
    ),
    "calendar_toggle_butler_event": (
        ("event_id", "enabled"),
        {
            "source_hint": None,
            "request_id": None,
        },
    ),
}

CALENDAR_APPROVAL_TOOLS = frozenset(_CALLS)

# Tools whose effect lands on a provider calendar. Their approved call must name
# that calendar concretely: ``calendar_id=None`` means "resolve at execution",
# which would let a later default-calendar change redirect an approved effect.
_CALENDAR_ID_TOOLS = frozenset(name for name, (_, d) in _CALLS.items() if "calendar_id" in d)

_DATETIMES = frozenset({"start_at", "end_at", "instance_start_at", "until_at"})


@overload
def calendar_approval_datetime(value: datetime | str) -> datetime: ...


@overload
def calendar_approval_datetime(value: None) -> None: ...


def calendar_approval_datetime(value: datetime | str | None) -> datetime | None:
    """Restore stored ISO boundaries before datetime-aware mutation logic."""
    if value is None or isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    raise ValueError("Invalid calendar approval datetime")


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    raise TypeError("Unsupported calendar approval argument")


def calendar_approval_args(tool_name: str, **arguments: Any) -> dict[str, Any]:
    """Bind the complete public call, defaults and ISO datetimes included.

    Unknown keys fail closed. No argument in this envelope conveys authority;
    only the existing executor context can approve its exact canonical digest.
    """
    required, defaults = _CALLS[tool_name]
    if arguments.keys() - (defaults.keys() | set(required)):
        raise ValueError("Unknown calendar approval argument")
    if set(required) - arguments.keys():
        raise ValueError("Missing calendar approval argument")
    complete = {**defaults, **arguments}
    for name in _DATETIMES & complete.keys():
        value = calendar_approval_datetime(complete[name])
        complete[name] = value.isoformat() if value is not None else None
    return json.loads(json.dumps(complete, default=_json_value, allow_nan=False))


def bind_calendar_approval_calendar(
    approval_args: dict[str, Any], resolved_calendar_id: str
) -> dict[str, Any]:
    """Pin the calendar resolved at park time into a parked call's canonical args.

    An explicit caller ``calendar_id`` is already bound and is kept as given;
    ``None`` becomes the concrete calendar the gate resolved, so the approved
    digest covers the calendar the owner saw and replay cannot follow a moved
    default.
    """
    if approval_args.get("calendar_id") is not None:
        return approval_args
    return {**approval_args, "calendar_id": resolved_calendar_id}


def retire_legacy_calendar_approval_args(
    tool_name: str, stored_args: dict[str, Any]
) -> dict[str, Any]:
    """Canonicalize a locked, digest-verified stored calendar call for dispatch.

    Rows parked before bu-svqpzo.1 carry the old producer's literal
    ``_approval_bypass: True`` marker, and update-instance rows carry a
    ``clear_entity_ids: False`` the tool never accepted. Only those exact
    no-authority leftovers are dropped; any other value or unknown key raises
    so the executor refuses before effect. Deletable once no pending calendar
    row carries the marker.

    Rows parked before bu-eio2my may also leave ``calendar_id`` unbound. Those
    are refused rather than resolved at execution time: the owner never saw
    the calendar such a replay would touch, so they must re-request.
    """
    public_args = dict(stored_args)
    if "_approval_bypass" in public_args and public_args.pop("_approval_bypass") is not True:
        raise ValueError("invalid legacy calendar approval marker")
    if (
        tool_name == "calendar_update_event_instance"
        and "clear_entity_ids" in public_args
        and public_args.pop("clear_entity_ids") is not False
    ):
        raise ValueError("invalid legacy calendar approval argument")
    canonical = calendar_approval_args(tool_name, **public_args)
    if tool_name in _CALENDAR_ID_TOOLS and canonical["calendar_id"] is None:
        raise ValueError("approved calendar call does not bind a calendar_id; re-request it")
    return canonical
