"""Today's declared working location, read from the calendar projection (bu-s11n0s.4 S3).

A Google ``workingLocation`` event's normalized location is persisted on ``calendar_events``
(core_341: ``working_location_type`` and ``working_location_label``). This module only reads it.
It is a declaration, not proof of where the owner is. It writes nothing, and nothing calls it
yet. Publishing it as a signal is deferred to a perception kind (``owner.work_location``) at the
M7/.21 switch; no writer is added to the retiring context bus.

Selection among active declarations: timed over all-day, then the latest start, then the
shortest, then the smallest event id. The answer is bounded to the owner-local day:
``until`` is the earlier of the event's end and the next local midnight in ``zone``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Any, Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from butlers.core.temporal.calendar_provenance import (
    is_explicit_butler_generated,
    is_owner_attending,
)
from butlers.core.temporal.calendar_source_roles import asserts_owner_time
from butlers.modules.calendar.google_mapping import WORKING_LOCATION_EVENT_TYPE

_ACTIVE_DECLARATIONS_SQL = f"""
SELECT ce.id, ce.status, ce.event_type, ce.starts_at, ce.ends_at, ce.all_day, ce.metadata,
       ce.working_location_type, ce.working_location_label, cs.time_role
FROM calendar_events ce
JOIN calendar_sources cs ON cs.id = ce.source_id
WHERE ce.status = 'confirmed'
  AND ce.event_type = '{WORKING_LOCATION_EVENT_TYPE}'
  AND ce.working_location_type IS NOT NULL
  AND ce.starts_at <= $1
  AND ce.ends_at > $1
-- quarantine-exempt: filtered in Python by is_owner_attending and asserts_owner_time
"""


class _Fetcher(Protocol):
    async def fetch(self, query: str, *args: Any) -> Iterable[Mapping[str, Any]]: ...


@dataclass(frozen=True)
class CurrentWorkingLocation:
    """The declared place of work today: its type, cleaned label and local-day bound."""

    type: str
    label: str
    until: datetime


def _owner_zone(now: datetime, zone: str) -> ZoneInfo:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    try:
        return ZoneInfo(zone)
    except (ZoneInfoNotFoundError, ValueError, TypeError) as exc:
        raise ValueError("unknown owner zone") from exc


def _next_local_midnight(now: datetime, tz: ZoneInfo) -> datetime:
    local_date = now.astimezone(tz).date()
    return datetime.combine(local_date + timedelta(days=1), time(0), tzinfo=tz)


def _is_eligible(row: Mapping[str, Any], now: datetime) -> bool:
    # The SQL filters these too; checking again keeps a canned row from bypassing them.
    return (
        row.get("status") == "confirmed"
        and row.get("event_type") == WORKING_LOCATION_EVENT_TYPE
        and isinstance(row.get("working_location_type"), str)
        and isinstance(row.get("working_location_label"), str)
        and bool(row["working_location_label"])
        and row["starts_at"] <= now < row["ends_at"]
        and not is_explicit_butler_generated(row.get("metadata"))
        and is_owner_attending(row.get("metadata"))
        # Another person's declared location is not where the owner is (REQ-module-calendar-035).
        and asserts_owner_time(row.get("time_role"), row.get("metadata"))
    )


def _preference(row: Mapping[str, Any]) -> tuple[bool, float, timedelta, str]:
    return (
        bool(row["all_day"]),
        -row["starts_at"].timestamp(),
        row["ends_at"] - row["starts_at"],
        str(row["id"]),
    )


def select_working_location(
    rows: Iterable[Mapping[str, Any]], *, now: datetime, zone: str
) -> CurrentWorkingLocation | None:
    """Pick today's declared working location from projection rows, or None."""
    tz = _owner_zone(now, zone)
    eligible = [row for row in rows if _is_eligible(row, now)]
    if not eligible:
        return None
    chosen = min(eligible, key=_preference)
    return CurrentWorkingLocation(
        type=chosen["working_location_type"],
        label=chosen["working_location_label"],
        until=min(chosen["ends_at"], _next_local_midnight(now, tz)),
    )


async def current_working_location(
    conn: _Fetcher, *, now: datetime, zone: str
) -> CurrentWorkingLocation | None:
    """Read today's declared working location from ``calendar_events`` (one SELECT, no write)."""
    _owner_zone(now, zone)
    rows = await conn.fetch(_ACTIVE_DECLARATIONS_SQL, now)
    return select_working_location(rows, now=now, zone=zone)
