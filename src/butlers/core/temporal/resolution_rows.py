"""Workspace rows to resolution events and owner busy intervals (bu-5ii4gj.11).

Pure functions over the shape one workspace row has (a calendar event instance joined to its event
and source). They live in core so both the dashboard read model and the calendar module's plan
tool read a row the same way; neither imports the other.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any

from butlers.core.temporal import conflict_resolution as cr
from butlers.core.temporal.calendar_provenance import counts_toward_owner_load
from butlers.core.temporal.calendar_source_roles import (
    DEFAULT_ROLE,
    asserts_owner_time,
    normalize_role,
)


def row_metadata(row: Mapping[str, Any]) -> dict[str, Any]:
    value = row.get("event_metadata")
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def _is_true(value: Any) -> bool:
    return value is True or (isinstance(value, str) and value.strip().lower() == "true")


def _classify(row: Mapping[str, Any], metadata: dict[str, Any]) -> cr.EventClass:
    """Butlers block, owner-organised (proven), invited, or unproven (offered nothing).

    The owner organises an event only when their own attendee row says so, or the stored organizer
    is their own address (the comparison ``is_quarantined_invitation`` makes). An event with no
    attendees is the owner's own only on an owner-role calendar. Anything else is unproven: it is
    never moved, and never answered.
    """
    attendees = [a for a in metadata.get("attendees") or [] if isinstance(a, Mapping)]
    mine = next((a for a in attendees if _is_true(a.get("self"))), None)
    if row.get("source_time_role") == "butler":
        return "butlers_block"
    if mine is None:
        solo = not attendees and normalize_role(row.get("source_time_role")) == "owner"
        return "owner_organized" if solo else "unproven"
    organizer = metadata.get("organizer")
    email = mine.get("email")
    own_address = (
        isinstance(organizer, str)
        and isinstance(email, str)
        and organizer.strip().casefold() == email.strip().casefold()
        and bool(organizer.strip())
    )
    return "owner_organized" if _is_true(mine.get("organizer")) or own_address else "invited"


def _instance_event_id(row: Mapping[str, Any]) -> str | None:
    """Google's id for one occurrence of a series: ``<series id>_<original start>``.

    All-day occurrences carry the date, timed ones the UTC instant. ``None`` when the series id
    or the occurrence start is unknown, so the compiler offers nothing rather than guess.
    """
    series = row.get("origin_ref")
    start = row.get("instance_starts_at")
    if not series or not isinstance(start, datetime):
        return None
    if row.get("all_day"):
        return f"{series}_{start:%Y%m%d}"
    return f"{series}_{start.astimezone(UTC):%Y%m%dT%H%M%SZ}"


def resolution_event(row: Mapping[str, Any], *, entry_id: str | None = None) -> cr.ResolutionEvent:
    """One side of an overlap from a workspace row: class, owner response, attendee count."""
    metadata = row_metadata(row)
    attendees = [a for a in metadata.get("attendees") or [] if isinstance(a, Mapping)]
    mine = next((a for a in attendees if _is_true(a.get("self"))), None)
    others = [a for a in attendees if not _is_true(a.get("self"))]
    response = None
    if mine is not None:
        raw = mine.get("responseStatus", mine.get("response_status"))
        response = raw if isinstance(raw, str) else None
    recurring = bool(row.get("recurrence_rule"))
    return cr.ResolutionEvent(
        entry_id=entry_id or str(row["instance_id"]),
        provider_event_id=str(row.get("origin_ref") or ""),
        calendar_id=row.get("calendar_id"),
        start_at=row["instance_starts_at"],
        end_at=row["instance_ends_at"],
        etag=row.get("event_etag"),
        event_class=_classify(row, metadata),
        has_other_attendees=bool(others),
        owner_response=response,
        recurring=recurring,
        # The instance's own start is the occurrence key; the series is never offered an edit.
        instance_start_at=row["instance_starts_at"] if recurring else None,
        instance_event_id=_instance_event_id(row) if recurring else None,
    )


def blocks_owner(row: Mapping[str, Any]) -> bool:
    """Whether a row could hold the owner: fail toward busy, never toward a free slot.

    Counted: the owner's own time and butler holds, owner-attended events on an unclassified
    calendar, and all-day opaque events (leave, out of office). Not counted: cancelled events, an
    event the owner declined or marked free, and another person's calendar.
    """
    status = str(row.get("instance_status") or row.get("event_status") or "confirmed")
    if status.lower() == "cancelled":
        return False
    metadata = row_metadata(row)
    if not counts_toward_owner_load(metadata):
        return False
    role = normalize_role(row.get("source_time_role"))
    return role == DEFAULT_ROLE or asserts_owner_time(role, metadata)


def busy_intervals(rows: Iterable[Mapping[str, Any]]) -> list[tuple[datetime, datetime]]:
    return [(r["instance_starts_at"], r["instance_ends_at"]) for r in rows if blocks_owner(r)]
