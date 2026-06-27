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

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import asyncpg

from butlers.api.db import DatabaseManager
from butlers.core.temporal.conflicts import (
    ConflictCandidate,
    DetectedIssue,
    detect_conflict_issues,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Version marker
# ---------------------------------------------------------------------------

#: Stability contract — bump to ``calendar_workspace_v2`` for breaking changes.
READ_MODEL_VERSION = "calendar_workspace_v1"

# ---------------------------------------------------------------------------
# Cross-source dedup rules + keep-separate overrides (bu-tjo2m1)
# ---------------------------------------------------------------------------
#
# The workspace read-model collapses cross-source duplicate events via a
# two-pass dedup in ``routers/calendar_workspace.py``.  These settings let the
# user steer that collapse: the *match strategy* selects which passes run and how
# aggressively titles are normalised, the *noisy threshold* governs which
# clusters the review surface reports, and the *keep-separate overrides* pin
# specific clusters so the dedup never collapses them.
#
# Persistence lives in two ``public`` tables (migration ``core_144``) because the
# dedup operates on the cross-schema *merge* of the workspace read — the rules
# are workspace-global, not owned by any one butler schema.  Reads/writes go
# through one deterministically-chosen calendar-enabled butler pool (the same
# single-pool pattern used by :func:`query_calendar_overlays`).

#: Allowed dedup match strategies.  ``exact`` runs only the origin-ref identity
#: pass; ``balanced`` (default) adds the title/start collapse pass; ``aggressive``
#: additionally strips non-alphanumerics from titles before comparing.
DEDUP_STRATEGIES: tuple[str, ...] = ("exact", "balanced", "aggressive")
DEDUP_DEFAULT_STRATEGY = "balanced"
DEDUP_DEFAULT_NOISY_THRESHOLD = 2

_DEDUP_RULES_TABLE = "public.calendar_dedup_rules"
_DEDUP_OVERRIDES_TABLE = "public.calendar_dedup_overrides"

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

#: Columns projected from ``calendar_event_proposals`` for the proposals lane
#: (``view=proposals``).  Changing this list is a breaking change — create
#: ``calendar_workspace_v2`` instead.  Adding new NULLABLE columns is safe.
PROPOSAL_COLUMNS: str = (
    "p.id AS proposal_id,"
    " p.butler_name,"
    " p.title,"
    " p.start_at,"
    " p.end_at,"
    " p.description,"
    " p.location,"
    " p.timezone,"
    " p.source_event_id,"
    " p.source_snippet,"
    " p.confidence,"
    " p.entity_ids,"
    " p.status,"
    " p.accepted_event_id,"
    " p.created_at,"
    " p.updated_at"
)

#: ``RETURNING`` projection for ``calendar_event_proposals`` writes — the same
#: columns as :data:`PROPOSAL_COLUMNS` but without the ``p.`` table alias (an
#: UPDATE/INSERT ``RETURNING`` clause has no alias).  Kept derived so the read
#: and write projections can never drift.
PROPOSAL_RETURNING_COLUMNS: str = PROPOSAL_COLUMNS.replace("p.", "")

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
# Server-side facet expressions (v1)
# ---------------------------------------------------------------------------
#
# These SQL ``CASE`` expressions mirror the Python ``_source_type`` and
# ``_entry_status`` helpers in ``routers/calendar_workspace.py`` so the workspace
# read can filter on the *computed* entry kind and status **server-side** in the
# fan-out query (before ``LIMIT``), keeping keyset pagination correct.  They MUST
# stay in lock-step with those helpers; the router unit tests assert the same
# enums on both sides.

#: Computed entry source-type, mirroring ``_source_type``.  ``e.metadata`` /
#: ``s.source_kind`` map to one of the four real workspace source types.
SOURCE_TYPE_SQL: str = (
    "CASE"
    " WHEN lower(e.metadata->>'source_type') IN"
    " ('provider_event','scheduled_task','butler_reminder','manual_butler_event')"
    " THEN lower(e.metadata->>'source_type')"
    " WHEN lower(s.source_kind) = 'provider_event' THEN 'provider_event'"
    " WHEN lower(s.source_kind) = 'internal_scheduler' THEN 'scheduled_task'"
    " WHEN lower(s.source_kind) = 'internal_reminders' THEN 'butler_reminder'"
    " ELSE 'manual_butler_event'"
    " END"
)

#: Computed entry status, mirroring ``_entry_status``.  A disabled scheduled task
#: is ``paused``; otherwise the raw instance/event status maps to the workspace
#: status vocabulary (``cancelled``/``error``/``completed``/``active``).
STATUS_SQL: str = (
    "CASE"
    f" WHEN ({SOURCE_TYPE_SQL}) = 'scheduled_task'"
    " AND e.metadata->>'enabled' = 'false' THEN 'paused'"
    " WHEN lower(COALESCE(i.status, e.status)) IN ('cancelled','canceled') THEN 'cancelled'"
    " WHEN lower(COALESCE(i.status, e.status)) IN ('error','failed') THEN 'error'"
    " WHEN lower(COALESCE(i.status, e.status)) IN ('completed','done') THEN 'completed'"
    " ELSE 'active'"
    " END"
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


@dataclass
class CalendarSearchMatch:
    """A single full-text search hit: a workspace row plus its trigram rank.

    Wraps a :class:`CalendarWorkspaceRow` (the searchable event instance) with
    the ``search_rank`` computed by the query (trigram similarity, or ``0.0``
    when the schema degraded to an ``ILIKE`` fallback).  The wrapping keeps the
    v1 :data:`WORKSPACE_COLUMNS` contract untouched — the rank is carried
    alongside the row rather than added to the frozen DTO.
    """

    row: CalendarWorkspaceRow
    rank: float
    db_butler: str = ""


@dataclass
class CalendarSearchResults:
    """Result envelope for :func:`query_calendar_event_search`.

    Carries the ranked ``matches`` plus an honest ``available`` degraded signal
    so the API can follow the repo's fail-open + explicit-degraded convention:

    - ``available=True`` — the search ran across at least one targeted schema
      (or there were no calendar schemas to search at all); ``matches`` is the
      complete, ranked result set (possibly empty → genuinely no hits).
    - ``available=False`` — every targeted schema failed to respond (pool down,
      or both the trigram and ``ILIKE`` queries raised), so ``matches`` is empty
      because the search could not run, NOT because nothing matched. The UI must
      render "search unavailable" rather than a misleading "no results".
    """

    matches: list[CalendarSearchMatch]
    available: bool = True


@dataclass
class CalendarProposalRow:
    """Typed DTO for a ``calendar_event_proposals`` row (v1).

    Backs the proposals lane (``view=proposals``).  ``status`` is always
    ``'pending'`` for projected rows — accepted/dismissed proposals are
    filtered out at the query boundary.
    """

    proposal_id: UUID
    butler_name: str | None
    title: str | None
    start_at: datetime
    end_at: datetime
    description: str | None
    location: str | None
    timezone: str | None
    source_event_id: str | None
    source_snippet: str | None
    confidence: float | None
    entity_ids: Any  # raw asyncpg value (list[UUID] or None)
    status: str
    accepted_event_id: UUID | None
    created_at: datetime | None
    updated_at: datetime | None
    #: The butler schema this row was fetched from (set by the query function).
    db_butler: str = ""


@dataclass
class CalendarOverlayRow:
    """Typed DTO for a ``calendar.v_overlay_contributions`` row (v1).

    Backs the overlays lane (``view=overlays``).  Each row is one per-date
    contribution envelope: ``butler`` is the view's **hardcoded** source-schema
    literal (RFC 0010 Guardrail #2), ``key`` is the ``calendar/overlay/<date>``
    state key, and ``value`` is the raw envelope JSONB
    (``{butler, date, has_entries, entries:[{kind, label, priority, meta}]}``).
    """

    butler: str | None
    key: str | None
    value: Any  # raw asyncpg value (dict or None)


@dataclass
class CalendarPrepRow:
    """Typed DTO for a ``calendar.v_prep_contributions`` row (v1).

    Backs the meeting-prep rail (``GET /api/calendar/workspace/prep/{event_id}``).
    Each row is one per-event prep envelope: ``butler`` is the view's
    **hardcoded** source-schema literal (RFC 0010 Guardrail #2), ``key`` is the
    ``calendar/prep/<event_id>`` state key, and ``value`` is the raw envelope
    JSONB (``{butler, event_id, event_title, event_starts_at, has_context,
    attendees:[...]}``).
    """

    butler: str | None
    key: str | None
    value: Any  # raw asyncpg value (dict or None)


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


def row_to_proposal(row: asyncpg.Record, *, db_butler: str) -> CalendarProposalRow:
    """Convert an asyncpg Record to a :class:`CalendarProposalRow`.

    This is the single place that knows the column names from
    :data:`PROPOSAL_COLUMNS`.
    """
    return CalendarProposalRow(
        proposal_id=row["proposal_id"],
        butler_name=row["butler_name"] or db_butler,
        title=row["title"],
        start_at=row["start_at"],
        end_at=row["end_at"],
        description=row["description"],
        location=row["location"],
        timezone=row["timezone"],
        source_event_id=row["source_event_id"],
        source_snippet=row["source_snippet"],
        confidence=row["confidence"],
        entity_ids=row["entity_ids"],
        status=row["status"],
        accepted_event_id=row["accepted_event_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
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
    status: str | None = None,
    source_type: str | None = None,
    editable: bool | None = None,
    cursor: tuple[datetime, UUID] | None = None,
    limit: int | None = None,
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
    status:
        If provided, restrict to entries whose *computed* status (see
        :data:`STATUS_SQL`) equals this value, applied server-side.
    source_type:
        If provided, restrict to entries whose *computed* source type (see
        :data:`SOURCE_TYPE_SQL`) equals this value, applied server-side.
    editable:
        If provided, restrict to sources whose ``writable`` flag matches.
    cursor:
        Keyset position ``(starts_at, id)`` — only rows strictly after it (in
        ``(starts_at, id)`` order) are returned. Powers cursor pagination.
    limit:
        If provided, cap the number of rows returned per butler schema (the
        caller typically passes ``page_size + 1`` to detect ``has_more``).

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
    if status is not None:
        conditions.append(f"({STATUS_SQL}) = ${idx}")
        args.append(status)
        idx += 1
    if source_type is not None:
        conditions.append(f"({SOURCE_TYPE_SQL}) = ${idx}")
        args.append(source_type)
        idx += 1
    if editable is not None:
        conditions.append(f"COALESCE(s.writable, false) = ${idx}")
        args.append(editable)
        idx += 1
    if cursor is not None:
        conditions.append(f"(i.starts_at, i.id) > (${idx}, ${idx + 1})")
        args.append(cursor[0])
        args.append(cursor[1])
        idx += 2

    where = " AND ".join(conditions)
    limit_clause = ""
    if limit is not None:
        limit_clause = f"\n        LIMIT ${idx}"
        args.append(limit)
        idx += 1
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
        ORDER BY i.starts_at ASC, i.id ASC{limit_clause}
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


async def query_calendar_proposals(
    db: DatabaseManager,
    *,
    start: datetime,
    end: datetime,
    butlers: list[str] | None = None,
) -> list[CalendarProposalRow]:
    """Fan-out query for **pending** ``calendar_event_proposals`` in a time range.

    Backs the proposals lane (``GET /api/calendar/workspace?view=proposals``).
    Only rows with ``status='pending'`` whose ``start_at`` falls in the
    ``[start, end)`` range are returned; accepted and dismissed proposals are
    excluded at the query boundary.

    **Fail-open contract.** This read MUST NOT raise when the
    ``calendar_event_proposals`` table is absent (calendar module disabled or a
    schema that pre-dates the migration) or when the query otherwise fails.
    :meth:`DatabaseManager.fan_out` already isolates per-butler failures (a
    failing schema yields an empty result, logged), and the whole function is
    additionally wrapped so an unexpected failure degrades to an empty list
    rather than propagating an HTTP 500.

    Parameters
    ----------
    db:
        The :class:`~butlers.api.db.DatabaseManager` instance.
    start:
        Inclusive range start; proposals with ``start_at >= start`` are included.
    end:
        Exclusive range end; proposals with ``start_at < end`` are included.
    butlers:
        If provided, restrict to these butler schemas.

    Returns
    -------
    list[CalendarProposalRow]
        Flat list of pending proposal rows from all queried butler schemas,
        ordered by ``start_at ASC, id ASC``.  Empty on any failure (fail-open).
    """
    conditions: list[str] = [
        "p.status = 'pending'",
        "p.start_at >= $1",
        "p.start_at < $2",
    ]
    args: list[Any] = [start, end]
    idx = 3

    if butlers:
        conditions.append(f"COALESCE(p.butler_name, '') = ANY(${idx}::text[])")
        args.append(butlers)
        idx += 1

    where = " AND ".join(conditions)
    sql = f"""
        SELECT {PROPOSAL_COLUMNS}
        FROM calendar_event_proposals AS p
        WHERE {where}
        ORDER BY p.start_at ASC, p.id ASC
    """

    query_targets: list[str] | None
    if butlers:
        query_targets = sorted(set(butlers))
    else:
        query_targets = db.butlers_with_module("calendar")

    try:
        results = await db.fan_out(sql, tuple(args), butler_names=query_targets)
    except Exception:
        logger.warning("query_calendar_proposals fan-out failed; returning empty", exc_info=True)
        return []

    rows: list[CalendarProposalRow] = []
    for butler_name, raw_rows in results.items():
        for row in raw_rows:
            rows.append(row_to_proposal(row, db_butler=butler_name))
    return rows


@dataclass
class CalendarConflictScan:
    """Result envelope for :func:`query_calendar_conflicts`.

    ``issues`` carries the detected, proposal-joined issues; ``available`` is the
    honest degraded signal (``False`` only when the fan-out scan failed), letting
    the endpoint follow the repo's fail-open + explicit-degraded convention.
    """

    issues: list[DetectedIssue]
    available: bool = True


async def query_calendar_conflicts(
    db: DatabaseManager,
    *,
    start: datetime,
    end: datetime,
    butlers: list[str] | None = None,
    display_tz: ZoneInfo | None = None,
    back_to_back_gap_minutes: int = 15,
    overloaded_day_hours: float = 6.0,
) -> CalendarConflictScan:
    """Scan the user-lane events in ``[start, end)`` for scheduling issues.

    Reads the synced ``calendar_event_instances`` / ``calendar_events`` tables
    via the windowed fan-out (served by the GIST(tstzrange) index), runs the pure
    :func:`~butlers.core.temporal.conflicts.detect_conflict_issues` detector, and
    joins any ``pending`` ``calendar_event_proposals`` whose ``source_event_id``
    equals an overlap issue's canonical pair id.

    **Fail-open contract.** Any fan-out failure degrades to
    ``CalendarConflictScan(issues=[], available=False)`` rather than raising — the
    endpoint must never return HTTP 500.
    """
    try:
        rows = await query_calendar_workspace(
            db,
            view="user",
            start=start,
            end=end,
            butlers=butlers,
        )
    except Exception:
        logger.warning("query_calendar_conflicts workspace fan-out failed", exc_info=True)
        return CalendarConflictScan(issues=[], available=False)

    candidates = [
        ConflictCandidate(
            entry_id=str(row.instance_id),
            title=row.title or "Untitled",
            start_at=row.instance_starts_at,
            end_at=row.instance_ends_at,
            timezone=row.instance_timezone or row.event_timezone or "UTC",
            status=str(row.instance_status or row.event_status or "confirmed"),
            all_day=bool(row.all_day),
        )
        for row in rows
    ]

    issues = detect_conflict_issues(
        candidates,
        display_tz=display_tz,
        back_to_back_gap_minutes=back_to_back_gap_minutes,
        overloaded_day_hours=overloaded_day_hours,
    )

    # Join pending proposals by the canonical overlap-pair id. query_calendar_proposals
    # is itself fail-open (returns [] on failure), so a missing proposals table
    # leaves proposal_ids empty rather than degrading the whole scan.
    pending = await query_calendar_proposals(db, start=start, end=end, butlers=butlers)
    by_source: dict[str, list[str]] = {}
    for proposal in pending:
        if proposal.source_event_id:
            by_source.setdefault(proposal.source_event_id, []).append(str(proposal.proposal_id))
    if by_source:
        for issue in issues:
            if issue.pair_id is not None:
                issue.proposal_ids = by_source.get(str(issue.pair_id), [])

    return CalendarConflictScan(issues=issues, available=True)


async def query_calendar_proposal_by_id(
    db: DatabaseManager,
    *,
    proposal_id: UUID,
) -> CalendarProposalRow | None:
    """Fetch a single ``calendar_event_proposals`` row by id, any status.

    Backs the accept/dismiss endpoints, which need the proposal regardless of
    its lifecycle status (``pending``/``accepted``/``dismissed``) so they can
    apply idempotency and transition rules.  Fans out across all
    calendar-enabled butler schemas and returns the first matching row (proposal
    ids are UUIDs, so at most one exists).  Returns ``None`` when no row matches
    or on any query failure (fail-open: an absent table must not raise).
    """
    sql = f"""
        SELECT {PROPOSAL_COLUMNS}
        FROM calendar_event_proposals AS p
        WHERE p.id = $1
        LIMIT 1
    """
    query_targets = db.butlers_with_module("calendar")
    try:
        results = await db.fan_out(sql, (proposal_id,), butler_names=query_targets)
    except Exception:
        logger.warning(
            "query_calendar_proposal_by_id fan-out failed; returning None", exc_info=True
        )
        return None

    for butler_name, raw_rows in results.items():
        for row in raw_rows:
            return row_to_proposal(row, db_butler=butler_name)
    return None


async def update_calendar_proposal_status(
    db: DatabaseManager,
    *,
    schema: str,
    proposal_id: UUID,
    status: str,
    accepted_event_id: UUID | None = None,
    only_if_status: str | None = None,
) -> CalendarProposalRow | None:
    """Transition a proposal's lifecycle status, returning the updated row.

    Runs an ``UPDATE ... RETURNING`` on the single ``schema`` pool (proposals
    are per-butler-schema).  When ``only_if_status`` is supplied the update is
    guarded (``AND status = ...``) so a concurrent transition cannot be
    clobbered — the call returns ``None`` (no row updated) in that race.

    Parameters
    ----------
    schema:
        The butler schema holding the proposal (its ``db_butler``).
    proposal_id:
        The proposal's UUID.
    status:
        The target status (``'accepted'`` or ``'dismissed'``).
    accepted_event_id:
        The created butler-event id to record (accept only); ``None`` for
        dismiss.
    only_if_status:
        When set, the update only applies if the row is currently in this
        status (optimistic guard against concurrent transitions).

    Returns
    -------
    CalendarProposalRow | None
        The updated row, or ``None`` when no row matched the (guarded) filter.
    """
    conditions = ["id = $1"]
    args: list[Any] = [proposal_id, status, accepted_event_id]
    idx = 4
    if only_if_status is not None:
        conditions.append(f"status = ${idx}")
        args.append(only_if_status)
        idx += 1

    where = " AND ".join(conditions)
    sql = f"""
        UPDATE calendar_event_proposals
        SET status = $2,
            accepted_event_id = $3,
            updated_at = now()
        WHERE {where}
        RETURNING {PROPOSAL_RETURNING_COLUMNS}
    """

    results = await db.fan_out(sql, tuple(args), butler_names=[schema])
    for _butler_name, raw_rows in results.items():
        for row in raw_rows:
            return row_to_proposal(row, db_butler=schema)
    return None


async def query_calendar_overlays(
    db: DatabaseManager,
    *,
    butlers: list[str] | None = None,
) -> list[CalendarOverlayRow]:
    """Read precomputed overlay contributions from ``calendar.v_overlay_contributions``.

    Backs the overlays lane (``GET /api/calendar/workspace?view=overlays``). The
    view is a **single** cross-schema UNION object living in the ``calendar``
    schema (migration ``core_140``), so — unlike the per-schema proposals/event
    reads — it is queried through exactly **one** butler pool rather than fanned
    out across every calendar schema (fanning out would return the same view
    rows once per schema, duplicating every overlay).  The chosen reader is the
    first calendar-enabled butler (deterministic), whose pool can ``SELECT`` the
    fully-qualified view.

    **Fail-open contract.** This read MUST NOT raise. A missing view
    (pre-migration), a missing contributing specialist ``state`` table, or any
    query failure degrades to an empty list (logged at WARNING). The caller maps
    an empty list to ``has_domain_context=false`` and never an HTTP 500.

    Parameters
    ----------
    db:
        The :class:`~butlers.api.db.DatabaseManager` instance.
    butlers:
        Optional explicit reader-schema override (primarily for tests). When
        omitted, the calendar-enabled butlers are used.

    Returns
    -------
    list[CalendarOverlayRow]
        Flat list of overlay-contribution envelope rows, or ``[]`` on any
        failure (fail-open).
    """
    candidates = butlers or db.butlers_with_module("calendar") or db.butler_names
    if not candidates:
        return []
    # Read the shared view through a single deterministic pool — never fan out.
    target = sorted(candidates)[0]

    sql = "SELECT butler, key, value FROM calendar.v_overlay_contributions"
    try:
        results = await db.fan_out(sql, (), butler_names=[target])
    except Exception:
        logger.warning(
            "query_calendar_overlays read failed; returning empty (fail-open)",
            exc_info=True,
        )
        return []

    rows: list[CalendarOverlayRow] = []
    for _butler, raw_rows in results.items():
        for row in raw_rows:
            rows.append(
                CalendarOverlayRow(
                    butler=row["butler"],
                    key=row["key"],
                    value=row["value"],
                )
            )
    return rows


async def query_calendar_prep(
    db: DatabaseManager,
    *,
    event_id: UUID,
    butlers: list[str] | None = None,
) -> list[CalendarPrepRow]:
    """Read precomputed prep contributions for one event from ``calendar.v_prep_contributions``.

    Backs the meeting-prep rail (``GET /api/calendar/workspace/prep/{event_id}``).
    Like :func:`query_calendar_overlays`, the view is a **single** cross-schema
    UNION object in the ``calendar`` schema (migration ``core_142``), so it is
    queried through exactly **one** deterministic butler pool rather than fanned
    out across every calendar schema (fanning out would return the same view
    rows once per schema, duplicating every contribution).

    The query filters to the single ``calendar/prep/<event_id>`` key so the read
    is bounded to the requested event — it does NOT scan the whole view and does
    NOT touch ``relationship.*`` / ``health.*`` directly.

    **Fail-open contract.** This read MUST NOT raise. A missing view
    (pre-migration), a missing contributing specialist ``state`` table, or any
    query failure degrades to an empty list (logged at WARNING). The caller maps
    an empty list to a structured empty prep payload and never an HTTP 500.

    Returns
    -------
    list[CalendarPrepRow]
        Prep-contribution envelope rows for the event (one per contributing
        specialist), or ``[]`` on any failure (fail-open).
    """
    candidates = butlers or db.butlers_with_module("calendar") or db.butler_names
    if not candidates:
        return []
    # Read the shared view through a single deterministic pool — never fan out.
    target = sorted(candidates)[0]

    key = f"calendar/prep/{event_id}"
    sql = "SELECT butler, key, value FROM calendar.v_prep_contributions WHERE key = $1"
    try:
        results = await db.fan_out(sql, (key,), butler_names=[target])
    except Exception:
        logger.warning(
            "query_calendar_prep read failed; returning empty (fail-open)",
            exc_info=True,
        )
        return []

    rows: list[CalendarPrepRow] = []
    for _butler, raw_rows in results.items():
        for row in raw_rows:
            rows.append(
                CalendarPrepRow(
                    butler=row["butler"],
                    key=row["key"],
                    value=row["value"],
                )
            )
    return rows


def _build_search_sql(
    *,
    butlers: list[str] | None,
    sources: list[str] | None,
    with_similarity: bool,
) -> str:
    """Build the fan-out search SQL for one schema.

    ``$1`` is the (already-stripped) free-text query, ``$2`` is the lane.
    Optional ``butlers`` / ``sources`` filters and the trailing ``LIMIT`` bind
    to subsequent positional params; the param positions are **identical** for
    the trigram and ``ILIKE`` variants so the same args tuple drives both — the
    only difference is the rank expression and ordering, which depend on the
    ``pg_trgm`` ``similarity()`` function being available.

    Matching is always done with ``ILIKE '%q%'`` (index-accelerated by the GIN
    trigram index when present, a plain seq-scan otherwise), so the ``ILIKE``
    fallback does not require the extension at all.
    """
    # $1 = query, $2 = lane.  ILIKE substring match across all three columns.
    conditions: list[str] = [
        "s.lane = $2",
        (
            "(e.title ILIKE '%' || $1 || '%'"
            " OR e.description ILIKE '%' || $1 || '%'"
            " OR e.location ILIKE '%' || $1 || '%')"
        ),
        "COALESCE(i.status, e.status) != 'cancelled'",
    ]
    idx = 3
    if butlers:
        conditions.append(
            f"COALESCE(s.butler_name, e.metadata->>'butler_name', '') = ANY(${idx}::text[])"
        )
        idx += 1
    if sources:
        conditions.append(f"s.source_key = ANY(${idx}::text[])")
        idx += 1
    limit_idx = idx

    if with_similarity:
        rank_expr = (
            "GREATEST("
            "similarity(e.title, $1),"
            " similarity(COALESCE(e.description, ''), $1),"
            " similarity(COALESCE(e.location, ''), $1)"
            ")"
        )
        order_by = "search_rank DESC, i.starts_at ASC, i.id ASC"
    else:
        rank_expr = "0.0"
        order_by = "i.starts_at ASC, i.id ASC"

    where = " AND ".join(conditions)
    return f"""
        SELECT {WORKSPACE_COLUMNS}, {rank_expr} AS search_rank
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
        ORDER BY {order_by}
        LIMIT ${limit_idx}
    """


def _search_rank(row: asyncpg.Record) -> float:
    """Read the computed ``search_rank`` from a search result row (0.0 fallback)."""
    try:
        value = row["search_rank"]
    except (KeyError, IndexError, TypeError):
        return 0.0
    try:
        return float(value) if value is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


async def query_calendar_event_search(
    db: DatabaseManager,
    *,
    q: str,
    view: str,
    butlers: list[str] | None = None,
    sources: list[str] | None = None,
    limit: int = 50,
) -> CalendarSearchResults:
    """Fan-out full-text search over the ``calendar_events`` projection.

    Matches a free-text query ``q`` against event ``title``, ``description``,
    and ``location`` across all calendar-enabled butler schemas (or the
    ``butlers`` subset), honoring the ``view`` lane and optional ``sources``
    scoping — the same semantics as the workspace read.  Each hit carries the
    matching event instance's date(s) so callers can group by day and jump-to.

    Ranking & degradation
    ----------------------
    Results are ranked by trigram ``similarity()`` (highest first), then by
    ``(starts_at, id)``.  The search is **fail-open**: a per-schema trigram
    query failure (e.g. the ``pg_trgm`` extension or GIN index is absent in that
    schema) is caught and retried with an ``ILIKE``-only fallback; if even that
    fails the schema is skipped.  Schemas where the index is present still
    return their ranked matches.  A missing/blank ``q`` returns ``[]`` (the whole
    projection is never returned).

    Parameters
    ----------
    db:
        The :class:`~butlers.api.db.DatabaseManager` instance.
    q:
        Free-text query.  Leading/trailing whitespace is stripped; blank → ``[]``.
    view:
        Lane filter (``'user'`` or ``'butler'``).
    butlers:
        If provided, restrict to these butler schemas.
    sources:
        If provided, restrict to sources with these ``source_key`` values.
    limit:
        Maximum number of matches to return (applied per-schema in SQL and
        globally after the cross-schema merge + rank sort).

    Returns
    -------
    CalendarSearchResults
        Ranked matches (highest trigram relevance first), capped at ``limit``,
        plus an ``available`` flag that is ``False`` only when every targeted
        schema failed to respond (fail-open degraded signal).
    """
    query = (q or "").strip()
    if not query:
        return CalendarSearchResults(matches=[])

    query_targets: list[str] | None
    if butlers:
        query_targets = sorted(set(butlers))
    else:
        query_targets = db.butlers_with_module("calendar")
    if not query_targets:
        return CalendarSearchResults(matches=[])

    trgm_sql = _build_search_sql(butlers=butlers, sources=sources, with_similarity=True)
    ilike_sql = _build_search_sql(butlers=butlers, sources=sources, with_similarity=False)

    args: list[Any] = [query, view]
    if butlers:
        args.append(sorted(set(butlers)))
    if sources:
        args.append(sources)
    args.append(limit)
    bind = tuple(args)

    async def _search_one(name: str) -> tuple[str, list[CalendarSearchMatch], bool]:
        try:
            pool = db.pool(name)
        except Exception:
            logger.warning("calendar search: no pool for butler %s; skipping", name, exc_info=True)
            return (name, [], False)
        try:
            rows = await pool.fetch(trgm_sql, *bind)
        except Exception:
            # Fail-open: pg_trgm / the GIN index is unavailable in this schema.
            logger.warning(
                "calendar search: trigram query failed for %s; falling back to ILIKE",
                name,
                exc_info=True,
            )
            try:
                rows = await pool.fetch(ilike_sql, *bind)
            except Exception:
                logger.warning(
                    "calendar search: ILIKE fallback failed for %s; skipping", name, exc_info=True
                )
                return (name, [], False)
        matches = [
            CalendarSearchMatch(
                row=row_to_workspace(row, db_butler=name),
                rank=_search_rank(row),
                db_butler=name,
            )
            for row in rows
        ]
        return (name, matches, True)

    results = await asyncio.gather(*[_search_one(n) for n in query_targets])

    merged: list[CalendarSearchMatch] = []
    any_ok = False
    for _name, matches, ok in results:
        any_ok = any_ok or ok
        merged.extend(matches)

    merged.sort(key=lambda m: (-m.rank, m.row.instance_starts_at, str(m.row.instance_id)))
    # Degraded only when EVERY targeted schema failed to respond; a partial
    # failure still returns whatever matched with ``available=True``.
    return CalendarSearchResults(matches=merged[:limit], available=any_ok)


# ---------------------------------------------------------------------------
# Dedup rules + keep-separate override store (bu-tjo2m1)
# ---------------------------------------------------------------------------


@dataclass
class CalendarDedupRules:
    """The active cross-source dedup rules (workspace-global singleton)."""

    match_strategy: str = DEDUP_DEFAULT_STRATEGY
    noisy_threshold: int = DEDUP_DEFAULT_NOISY_THRESHOLD


def _dedup_pool(db: DatabaseManager) -> asyncpg.Pool | None:
    """Pick the deterministic butler pool used for the workspace-global dedup tables.

    The rules/overrides live in ``public`` and govern the cross-schema merge, so a
    single reader/writer pool is correct (fanning out would touch the same
    ``public`` rows once per schema).  The chosen pool is the first
    calendar-enabled butler (deterministic), falling back to any butler.  Returns
    ``None`` when no pool is available (fail-open at the callers).
    """
    candidates = db.butlers_with_module("calendar") or db.butler_names
    if not candidates:
        return None
    try:
        return db.pool(sorted(candidates)[0])
    except Exception:
        logger.warning("dedup store: no pool available; degrading", exc_info=True)
        return None


async def load_dedup_rules(db: DatabaseManager) -> CalendarDedupRules:
    """Load the active dedup rules, fail-open to defaults.

    A missing ``public.calendar_dedup_rules`` table (pre-migration), an empty
    table (never configured), or any query failure degrades to the defaults
    (``balanced`` strategy, threshold ``2``) rather than raising.
    """
    pool = _dedup_pool(db)
    if pool is None:
        return CalendarDedupRules()
    try:
        row = await pool.fetchrow(
            f"SELECT match_strategy, noisy_threshold FROM {_DEDUP_RULES_TABLE} WHERE id = TRUE"
        )
    except Exception:
        logger.warning("load_dedup_rules failed; returning defaults (fail-open)", exc_info=True)
        return CalendarDedupRules()
    if row is None:
        return CalendarDedupRules()
    strategy = row["match_strategy"]
    if strategy not in DEDUP_STRATEGIES:
        strategy = DEDUP_DEFAULT_STRATEGY
    return CalendarDedupRules(match_strategy=strategy, noisy_threshold=int(row["noisy_threshold"]))


async def update_dedup_rules(
    db: DatabaseManager,
    *,
    match_strategy: str | None = None,
    noisy_threshold: int | None = None,
) -> CalendarDedupRules:
    """Upsert the singleton dedup rules row, returning the persisted rules.

    Only provided (non-``None``) fields are written.  Validation of values is the
    caller's responsibility (the API layer rejects unknown strategies / bad
    thresholds with a 400 before reaching here).  Raises when no pool is
    available — this is an explicit user write, not a fail-open read.
    """
    pool = _dedup_pool(db)
    if pool is None:
        raise RuntimeError("no calendar-enabled butler pool available for dedup rules")

    fields: dict[str, Any] = {}
    if match_strategy is not None:
        fields["match_strategy"] = match_strategy
    if noisy_threshold is not None:
        fields["noisy_threshold"] = noisy_threshold
    if not fields:
        return await load_dedup_rules(db)

    cols = ["id"] + list(fields.keys())
    params: list[Any] = [True] + list(fields.values())
    placeholders = ", ".join(f"${i}" for i in range(1, len(params) + 1))
    set_clause = ", ".join(["updated_at = now()"] + [f"{col} = EXCLUDED.{col}" for col in fields])
    row = await pool.fetchrow(
        f"""
        INSERT INTO {_DEDUP_RULES_TABLE} ({", ".join(cols)})
        VALUES ({placeholders})
        ON CONFLICT (id) DO UPDATE SET {set_clause}
        RETURNING match_strategy, noisy_threshold
        """,
        *params,
    )
    return CalendarDedupRules(
        match_strategy=row["match_strategy"], noisy_threshold=int(row["noisy_threshold"])
    )


async def load_keep_separate_keys(db: DatabaseManager) -> set[str]:
    """Load the set of cluster keys the user pinned as keep-separate, fail-open.

    A missing ``public.calendar_dedup_overrides`` table or any query failure
    degrades to an empty set (no overrides) rather than raising.
    """
    pool = _dedup_pool(db)
    if pool is None:
        return set()
    try:
        rows = await pool.fetch(f"SELECT cluster_key FROM {_DEDUP_OVERRIDES_TABLE}")
    except Exception:
        logger.warning("load_keep_separate_keys failed; returning empty (fail-open)", exc_info=True)
        return set()
    return {row["cluster_key"] for row in rows}


async def set_keep_separate(
    db: DatabaseManager,
    *,
    cluster_key: str,
    keep_separate: bool,
    match_pass: str | None = None,
    label: str | None = None,
) -> bool:
    """Pin (or unpin) a cluster as keep-separate, returning the new state.

    When ``keep_separate`` is true an override row is upserted; when false the
    override row is removed (the cluster collapses again).  Raises when no pool is
    available — this is an explicit user write.
    """
    pool = _dedup_pool(db)
    if pool is None:
        raise RuntimeError("no calendar-enabled butler pool available for dedup overrides")

    if keep_separate:
        await pool.execute(
            f"""
            INSERT INTO {_DEDUP_OVERRIDES_TABLE} (cluster_key, match_pass, label)
            VALUES ($1, $2, $3)
            ON CONFLICT (cluster_key) DO UPDATE
                SET match_pass = EXCLUDED.match_pass, label = EXCLUDED.label
            """,
            cluster_key,
            match_pass or "",
            label,
        )
    else:
        await pool.execute(
            f"DELETE FROM {_DEDUP_OVERRIDES_TABLE} WHERE cluster_key = $1", cluster_key
        )
    return keep_separate
