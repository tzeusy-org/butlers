"""Other people's calendar events as attributed, opaque context (REQ-module-calendar-038).

Bead bu-5ii4gj.1, slice S3.

A partner's or colleague's appointment is not the owner's time, but it is true and useful: the
owner's meeting card can say "also on <name>'s calendar" without counting it as load. This module
decides which events are household context and shapes them. It is pure (no I/O) and it is the
only place that builds a ``HouseholdEvent``.

Opacity is structural. ``HouseholdEvent`` has no title, location, description or attendee field,
so another person's text cannot reach a prompt, a context line or an API response through it.
An event the owner attends is owner time (``asserts_owner_time``) and is never context, so an
event is never both. Sources that are unclassified, subscriptions, holidays, birthdays or the
Butlers calendar produce nothing: unclassified counts as nothing.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, tzinfo
from typing import Any
from zoneinfo import ZoneInfo

from butlers.core.temporal.calendar_provenance import (
    counts_toward_owner_load,
    is_explicit_butler_generated,
    is_status_event,
)
from butlers.core.temporal.calendar_source_roles import asserts_owner_time, normalize_role

#: Roles whose events are other people's time, shown as context when the owner is not attending.
HOUSEHOLD_ROLES: frozenset[str] = frozenset({"household_member", "other_person", "team"})

#: Roles a ``role_subject_entity_id`` may accompany.
SUBJECT_ROLES = HOUSEHOLD_ROLES

_ROLE_NOUN = {
    "household_member": "a household member",
    "other_person": "another person",
    "team": "a team",
}


@dataclass(frozen=True, slots=True)
class HouseholdEvent:
    """One other-person event: when, whose, nothing else."""

    source_id: str
    role: str
    subject_entity_id: str | None
    subject_label: str | None
    starts_at: datetime
    ends_at: datetime
    all_day: bool = False

    def overlaps(self, start: datetime, end: datetime) -> bool:
        return self.starts_at < end and self.ends_at > start


def household_event(
    *,
    time_role: Any,
    metadata: Any,
    starts_at: datetime,
    ends_at: datetime,
    all_day: bool = False,
    source_id: Any = "",
    subject_entity_id: Any = None,
    subject_label: str | None = None,
    event_type: Any = None,
) -> HouseholdEvent | None:
    """The context event for a row, or ``None`` when the row is not household context.

    ``metadata`` is used to decide and is never copied: the owner's attendance and the event's
    transparency and authorship are read from it, and nothing else leaves.
    """
    role = normalize_role(time_role)
    if role not in HOUSEHOLD_ROLES:
        return None
    if asserts_owner_time(role, metadata):
        return None  # the owner is attending: owner time, not context
    if is_status_event(event_type):
        return None  # out of office and working location are statuses, not meetings
    if is_explicit_butler_generated(metadata) or not counts_toward_owner_load(metadata):
        return None  # a generated hold, or an event marked free
    return HouseholdEvent(
        source_id=str(source_id),
        role=role,
        subject_entity_id=str(subject_entity_id) if subject_entity_id is not None else None,
        subject_label=subject_label.strip() if subject_label and subject_label.strip() else None,
        starts_at=starts_at,
        ends_at=ends_at,
        all_day=bool(all_day),
    )


def household_events(
    rows: Iterable[Mapping[str, Any]],
    *,
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[HouseholdEvent]:
    """Context events for rows shaped with the keys ``household_event`` takes, in time order.

    A row is a mapping with ``time_role``, ``metadata``, ``starts_at``, ``ends_at``, ``all_day``,
    ``source_id``, ``subject_entity_id``, ``subject_label`` and optionally ``event_type``;
    readers alias their own columns to these. With a window, only events overlapping it are kept.
    """
    events: list[HouseholdEvent] = []
    for row in rows:
        event = household_event(
            time_role=row.get("time_role"),
            metadata=row.get("metadata"),
            starts_at=row["starts_at"],
            ends_at=row["ends_at"],
            all_day=row.get("all_day") is True,
            source_id=row.get("source_id", ""),
            subject_entity_id=row.get("subject_entity_id"),
            subject_label=row.get("subject_label"),
            event_type=row.get("event_type"),
        )
        if event is None:
            continue
        if start is not None and end is not None and not event.overlaps(start, end):
            continue
        events.append(event)
    return sorted(events, key=lambda e: (e.starts_at, e.ends_at, e.source_id))


def household_time(
    rows: Iterable[Mapping[str, Any]],
    *,
    start: datetime | None = None,
    end: datetime | None = None,
) -> dict[str | None, list[HouseholdEvent]]:
    """Context events grouped by ``subject_entity_id`` (``None`` for an unattributed source)."""
    grouped: dict[str | None, list[HouseholdEvent]] = defaultdict(list)
    for event in household_events(rows, start=start, end=end):
        grouped[event.subject_entity_id].append(event)
    return dict(grouped)


def context_line(event: HouseholdEvent, *, timezone: str | tzinfo = "UTC") -> str:
    """One attributed line, such as ``Also on Alex's calendar: 14:00-15:00``. Time only."""
    tz = ZoneInfo(timezone) if isinstance(timezone, str) else timezone
    owner = f"{event.subject_label}'s" if event.subject_label else f"{_ROLE_NOUN[event.role]}'s"
    if event.all_day:
        return f"Also on {owner} calendar: all day"
    start = event.starts_at.astimezone(tz).strftime("%H:%M")
    end = event.ends_at.astimezone(tz).strftime("%H:%M")
    return f"Also on {owner} calendar: {start}-{end}"


def context_lines(
    events: Iterable[HouseholdEvent],
    *,
    timezone: str | tzinfo = "UTC",
    limit: int = 5,
) -> list[str]:
    """Context lines for events in time order, capped so a busy household cannot flood a card."""
    return [context_line(e, timezone=timezone) for e in list(events)[:limit]]


def _overlap_sql(start: str, end: str) -> str:
    """Household-only occurrences overlapping ``[start, end)``: events and materialized instances.

    One selector for every surface (free slots, meeting card, context producer), so the effective
    occurrence is the same everywhere: the instance's times and its metadata laid over the series',
    all-day included, cancelled excluded.
    """
    return f"""
    SELECT cs.time_role AS time_role, ce.metadata AS metadata, ce.starts_at AS starts_at,
           ce.ends_at AS ends_at, ce.all_day AS all_day, cs.id AS source_id,
           cs.role_subject_entity_id AS subject_entity_id, ce.event_type AS event_type,
           (SELECT en.canonical_name FROM public.entities en
             WHERE en.id = cs.role_subject_entity_id) AS subject_label
    FROM calendar_events ce
    JOIN calendar_sources cs ON cs.id = ce.source_id
    WHERE ce.recurrence_rule IS NULL AND ce.status <> 'cancelled'
      AND cs.time_role IN ('household_member', 'other_person', 'team')
      AND ce.starts_at < {end} AND ce.ends_at > {start}
    UNION ALL
    SELECT cs.time_role, ce.metadata || ci.metadata, ci.starts_at, ci.ends_at, ce.all_day,
           cs.id, cs.role_subject_entity_id, ce.event_type,
           (SELECT en.canonical_name FROM public.entities en
             WHERE en.id = cs.role_subject_entity_id)
    FROM calendar_event_instances ci
    JOIN calendar_events ce ON ce.id = ci.event_id
    JOIN calendar_sources cs ON cs.id = ce.source_id
    WHERE ce.recurrence_rule IS NOT NULL AND ce.status <> 'cancelled' AND ci.status <> 'cancelled'
      AND cs.time_role IN ('household_member', 'other_person', 'team')
      AND ci.starts_at < {end} AND ci.ends_at > {start}
"""


#: Overlap with a window given as ``$1`` (start) and ``$2`` (end).
HOUSEHOLD_OVERLAP_SQL = _overlap_sql("$1", "$2")
#: Occurrences live right now, at the database clock (no parameters).
HOUSEHOLD_LIVE_SQL = _overlap_sql("now()", "now()")


def events_from_overlap_rows(rows: Iterable[Mapping[str, Any]]) -> list[HouseholdEvent]:
    """Context events for rows of :data:`HOUSEHOLD_OVERLAP_SQL` or :data:`HOUSEHOLD_LIVE_SQL`."""
    return household_events(
        {
            "time_role": row["time_role"],
            "metadata": row["metadata"],
            "starts_at": row["starts_at"],
            "ends_at": row["ends_at"],
            "all_day": row.get("all_day"),
            "source_id": row.get("source_id", ""),
            "subject_entity_id": row.get("subject_entity_id"),
            "subject_label": row.get("subject_label"),
            "event_type": row.get("event_type"),
        }
        for row in rows
    )
