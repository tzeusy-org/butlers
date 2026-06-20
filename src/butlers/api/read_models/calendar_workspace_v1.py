"""Calendar-workspace read-model v1 — versioned read boundary for workspace queries.

Centralises the SQL column projections and fan-out query functions for the
calendar workspace endpoints, which fan out queries across all butler-schema
``calendar_sources``, ``calendar_events``, ``calendar_event_instances``, and
``calendar_sync_cursors`` tables.

A breaking schema change (new required column, renamed column, type change)
should produce a new ``calendar_workspace_v2`` module rather than silently
altering this one.

Public surface
--------------
Column constants:
    SOURCE_COLUMNS
    WORKSPACE_COLUMNS

Row DTOs:
    CalendarSourceRow
    CalendarWorkspaceRow

Query functions (all async):
    query_calendar_sources(db, lane, butlers, sources) -> list[CalendarSourceRow]
    query_calendar_workspace(db, view, start, end, butlers, sources) -> list[CalendarWorkspaceRow]

Version marker:
    READ_MODEL_VERSION
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg

from butlers.api.db import DatabaseManager

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Version marker
# ---------------------------------------------------------------------------

#: Stability contract — bump to ``calendar_workspace_v2`` for breaking changes.
READ_MODEL_VERSION = "calendar_workspace_v1"

# ---------------------------------------------------------------------------
# Column projections (v1 schema contract)
# ---------------------------------------------------------------------------

#: Columns projected from ``calendar_sources`` joined with the latest
#: ``calendar_sync_cursors`` row for the source list and meta queries.
#: Changing this list is a breaking change — create ``calendar_workspace_v2``
#: instead.
SOURCE_COLUMNS: str = (
    "s.id AS source_id,"
    " s.source_key,"
    " s.source_kind,"
    " s.lane,"
    " s.provider,"
    " s.calendar_id,"
    " s.butler_name,"
    " s.display_name,"
    " s.writable,"
    " s.metadata AS source_metadata,"
    " c.cursor_name,"
    " c.last_synced_at,"
    " c.last_success_at,"
    " c.last_error_at,"
    " c.last_error,"
    " c.full_sync_required"
)

#: Columns projected for the workspace event-instance view: instances joined
#: to events, sources, and the latest sync cursor per source.
#: Changing this list is a breaking change — create ``calendar_workspace_v2``
#: instead.  Adding new NULLABLE columns from existing DB columns is safe
#: (additive, no existing consumer breaks).
WORKSPACE_COLUMNS: str = (
    "i.id AS instance_id,"
    " i.origin_instance_ref,"
    " i.timezone AS instance_timezone,"
    " i.starts_at AS instance_starts_at,"
    " i.ends_at AS instance_ends_at,"
    " i.status AS instance_status,"
    " i.metadata AS instance_metadata,"
    " e.id AS event_id,"
    " e.origin_ref,"
    " e.title,"
    " e.description,"
    " e.location,"
    " e.timezone AS event_timezone,"
    " e.all_day,"
    " e.status AS event_status,"
    " e.visibility,"
    " e.recurrence_rule,"
    " e.metadata AS event_metadata,"
    " e.source_butler,"
    " e.source_session_id,"
    " s.id AS source_id,"
    " s.source_key,"
    " s.source_kind,"
    " s.lane,"
    " s.provider,"
    " s.calendar_id,"
    " s.butler_name,"
    " s.display_name,"
    " s.writable,"
    " s.metadata AS source_metadata,"
    " c.cursor_name,"
    " c.last_synced_at,"
    " c.last_success_at,"
    " c.last_error_at,"
    " c.last_error,"
    " c.full_sync_required"
)

# ---------------------------------------------------------------------------
# Typed row DTOs
# ---------------------------------------------------------------------------


@dataclass
class CalendarSourceRow:
    """Typed DTO for a ``calendar_sources`` row with its latest sync cursor (v1)."""

    source_id: UUID
    source_key: str
    source_kind: str
    lane: str
    provider: str | None
    calendar_id: str | None
    butler_name: str | None
    display_name: str | None
    writable: bool
    source_metadata: Any  # raw asyncpg value (dict or None)
    cursor_name: str | None
    last_synced_at: datetime | None
    last_success_at: datetime | None
    last_error_at: datetime | None
    last_error: str | None
    full_sync_required: bool
    #: The butler schema this row was fetched from (set by the query function).
    db_butler: str = ""


@dataclass
class CalendarWorkspaceRow:
    """Typed DTO for a calendar event-instance row (v1).

    Merges columns from ``calendar_event_instances``, ``calendar_events``,
    ``calendar_sources``, and ``calendar_sync_cursors``.
    """

    instance_id: UUID
    origin_instance_ref: str | None
    instance_timezone: str | None
    instance_starts_at: datetime
    instance_ends_at: datetime
    instance_status: str | None
    instance_metadata: Any  # raw asyncpg value (dict or None)
    event_id: UUID
    origin_ref: str | None
    title: str | None
    description: str | None
    location: str | None
    event_timezone: str | None
    all_day: bool
    event_status: str | None
    visibility: str | None
    recurrence_rule: str | None
    event_metadata: Any  # raw asyncpg value (dict or None)
    # core_076 provenance columns on calendar_events
    source_butler: str | None
    source_session_id: str | None
    source_id: UUID
    source_key: str
    source_kind: str
    lane: str
    provider: str | None
    calendar_id: str | None
    butler_name: str | None
    display_name: str | None
    writable: bool
    source_metadata: Any  # raw asyncpg value (dict or None)
    cursor_name: str | None
    last_synced_at: datetime | None
    last_success_at: datetime | None
    last_error_at: datetime | None
    last_error: str | None
    full_sync_required: bool
    #: The butler schema this row was fetched from (set by the query function).
    db_butler: str = ""


# ---------------------------------------------------------------------------
# Row converters
# ---------------------------------------------------------------------------


def row_to_source(row: asyncpg.Record, *, db_butler: str) -> CalendarSourceRow:
    """Convert an asyncpg Record to a :class:`CalendarSourceRow`.

    This is the single place that knows the column names from
    :data:`SOURCE_COLUMNS`.  The ``db_butler`` kwarg carries the schema name
    from the fan-out; callers use it as a fallback for ``butler_name`` when
    the row's own ``butler_name`` is NULL.
    """
    return CalendarSourceRow(
        source_id=row["source_id"],
        source_key=row["source_key"],
        source_kind=row["source_kind"],
        lane=row["lane"],
        provider=row["provider"],
        calendar_id=row["calendar_id"],
        butler_name=row["butler_name"] or db_butler,
        display_name=row["display_name"],
        writable=bool(row["writable"] or False),
        source_metadata=row["source_metadata"],
        cursor_name=row["cursor_name"],
        last_synced_at=row["last_synced_at"],
        last_success_at=row["last_success_at"],
        last_error_at=row["last_error_at"],
        last_error=row["last_error"],
        full_sync_required=bool(row["full_sync_required"] or False),
        db_butler=db_butler,
    )


def row_to_workspace(row: asyncpg.Record, *, db_butler: str) -> CalendarWorkspaceRow:
    """Convert an asyncpg Record to a :class:`CalendarWorkspaceRow`.

    This is the single place that knows the column names from
    :data:`WORKSPACE_COLUMNS`.
    """
    return CalendarWorkspaceRow(
        instance_id=row["instance_id"],
        origin_instance_ref=row["origin_instance_ref"],
        instance_timezone=row["instance_timezone"],
        instance_starts_at=row["instance_starts_at"],
        instance_ends_at=row["instance_ends_at"],
        instance_status=row["instance_status"],
        instance_metadata=row["instance_metadata"],
        event_id=row["event_id"],
        origin_ref=row["origin_ref"],
        title=row["title"],
        description=row["description"],
        location=row["location"],
        event_timezone=row["event_timezone"],
        all_day=bool(row["all_day"] or False),
        event_status=row["event_status"],
        visibility=row["visibility"],
        recurrence_rule=row["recurrence_rule"],
        event_metadata=row["event_metadata"],
        source_butler=row.get("source_butler") or None,
        source_session_id=row.get("source_session_id") or None,
        source_id=row["source_id"],
        source_key=row["source_key"],
        source_kind=row["source_kind"],
        lane=row["lane"],
        provider=row["provider"],
        calendar_id=row["calendar_id"],
        butler_name=row["butler_name"],
        display_name=row["display_name"],
        writable=bool(row["writable"] or False),
        source_metadata=row["source_metadata"],
        cursor_name=row["cursor_name"],
        last_synced_at=row["last_synced_at"],
        last_success_at=row["last_success_at"],
        last_error_at=row["last_error_at"],
        last_error=row["last_error"],
        full_sync_required=bool(row["full_sync_required"] or False),
        db_butler=db_butler,
    )


# ---------------------------------------------------------------------------
# Query functions
# ---------------------------------------------------------------------------


async def query_calendar_sources(
    db: DatabaseManager,
    *,
    lane: str | None = None,
    butlers: list[str] | None = None,
    sources: list[str] | None = None,
) -> list[CalendarSourceRow]:
    """Fan-out query for ``calendar_sources`` across all calendar-enabled butlers.

    Supports optional filtering by lane, butler name(s), and source key(s).
    The query uses a ``LATERAL`` join to the latest ``calendar_sync_cursors``
    row per source.

    Parameters
    ----------
    db:
        The :class:`~butlers.api.db.DatabaseManager` instance.
    lane:
        If provided, only return sources matching this lane (e.g. ``'user'``
        or ``'butler'``).
    butlers:
        If provided, only query these butler schemas (and only return sources
        whose ``butler_name`` matches one of these values).
    sources:
        If provided, only return sources whose ``source_key`` is in this list.

    Returns
    -------
    list[CalendarSourceRow]
        Flat list of source rows from all queried butler schemas.
    """
    conditions: list[str] = []
    args: list[Any] = []
    idx = 1

    if lane is not None:
        conditions.append(f"s.lane = ${idx}")
        args.append(lane)
        idx += 1
    if butlers:
        conditions.append(f"COALESCE(s.butler_name, '') = ANY(${idx}::text[])")
        args.append(butlers)
        idx += 1
    if sources:
        conditions.append(f"s.source_key = ANY(${idx}::text[])")
        args.append(sources)
        idx += 1

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    sql = f"""
        SELECT {SOURCE_COLUMNS}
        FROM calendar_sources AS s
        LEFT JOIN LATERAL (
            SELECT cursor_name, last_synced_at, last_success_at, last_error_at, last_error,
                   full_sync_required, updated_at
            FROM calendar_sync_cursors
            WHERE source_id = s.id
            ORDER BY updated_at DESC
            LIMIT 1
        ) AS c ON TRUE
        {where}
        ORDER BY s.lane, s.source_kind, s.source_key
    """

    query_targets: list[str] | None
    if butlers:
        query_targets = sorted(set(butlers))
    else:
        query_targets = db.butlers_with_module("calendar")

    results = await db.fan_out(sql, tuple(args), butler_names=query_targets)
    rows: list[CalendarSourceRow] = []
    for butler_name, raw_rows in results.items():
        for row in raw_rows:
            dto = row_to_source(row, db_butler=butler_name)
            rows.append(dto)
    return rows


async def query_calendar_workspace_entry(
    db: DatabaseManager,
    *,
    entry_id: UUID,
) -> CalendarWorkspaceRow | None:
    """Fetch a single calendar event-instance by its instance ID.

    Fans out across all calendar-enabled butler schemas and returns the
    first matching row (instance IDs are UUIDs, so at most one exists).
    Returns ``None`` when no matching row is found.
    """
    sql = f"""
        SELECT {WORKSPACE_COLUMNS}
        FROM calendar_event_instances AS i
        JOIN calendar_events AS e ON e.id = i.event_id
        JOIN calendar_sources AS s ON s.id = i.source_id
        LEFT JOIN LATERAL (
            SELECT cursor_name, last_synced_at, last_success_at, last_error_at, last_error,
                   full_sync_required, updated_at
            FROM calendar_sync_cursors
            WHERE source_id = s.id
            ORDER BY updated_at DESC
            LIMIT 1
        ) AS c ON TRUE
        WHERE i.id = $1
        LIMIT 1
    """

    query_targets = db.butlers_with_module("calendar")
    results = await db.fan_out(sql, (entry_id,), butler_names=query_targets)
    for butler_name, raw_rows in results.items():
        for row in raw_rows:
            return row_to_workspace(row, db_butler=butler_name)
    return None


async def query_calendar_workspace(
    db: DatabaseManager,
    *,
    view: str,
    start: datetime,
    end: datetime,
    butlers: list[str] | None = None,
    sources: list[str] | None = None,
) -> list[CalendarWorkspaceRow]:
    """Fan-out query for calendar event instances in a time range.

    Joins ``calendar_event_instances``, ``calendar_events``,
    ``calendar_sources``, and ``calendar_sync_cursors`` across all
    calendar-enabled butler schemas.  Results are filtered by the ``lane``
    (``view`` param), time range overlap, and non-cancelled status.

    Parameters
    ----------
    db:
        The :class:`~butlers.api.db.DatabaseManager` instance.
    view:
        Lane filter (``'user'`` or ``'butler'``).
    start:
        Inclusive range start; events with ``ends_at > start`` are included.
    end:
        Exclusive range end; events with ``starts_at < end`` are included.
    butlers:
        If provided, restrict to these butler schemas.
    sources:
        If provided, restrict to sources with these ``source_key`` values.

    Returns
    -------
    list[CalendarWorkspaceRow]
        Flat list of event-instance rows from all queried butler schemas,
        ordered by ``instance_starts_at ASC, instance_id ASC``.
    """
    conditions: list[str] = [
        "s.lane = $1",
        "i.starts_at < $2",
        "i.ends_at > $3",
        "COALESCE(i.status, e.status) != 'cancelled'",
    ]
    args: list[Any] = [view, end, start]
    idx = 4

    if butlers:
        conditions.append(
            f"COALESCE(s.butler_name, e.metadata->>'butler_name', '') = ANY(${idx}::text[])"
        )
        args.append(butlers)
        idx += 1
    if sources:
        conditions.append(f"s.source_key = ANY(${idx}::text[])")
        args.append(sources)
        idx += 1

    where = " AND ".join(conditions)
    sql = f"""
        SELECT {WORKSPACE_COLUMNS}
        FROM calendar_event_instances AS i
        JOIN calendar_events AS e ON e.id = i.event_id
        JOIN calendar_sources AS s ON s.id = i.source_id
        LEFT JOIN LATERAL (
            SELECT cursor_name, last_synced_at, last_success_at, last_error_at, last_error,
                   full_sync_required, updated_at
            FROM calendar_sync_cursors
            WHERE source_id = s.id
            ORDER BY updated_at DESC
            LIMIT 1
        ) AS c ON TRUE
        WHERE {where}
        ORDER BY i.starts_at ASC, i.id ASC
    """

    query_targets: list[str] | None
    if butlers:
        query_targets = sorted(set(butlers))
    else:
        query_targets = db.butlers_with_module("calendar")

    results = await db.fan_out(sql, tuple(args), butler_names=query_targets)
    rows: list[CalendarWorkspaceRow] = []
    for butler_name, raw_rows in results.items():
        for row in raw_rows:
            dto = row_to_workspace(row, db_butler=butler_name)
            rows.append(dto)
    return rows
