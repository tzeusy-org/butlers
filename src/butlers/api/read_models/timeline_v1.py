"""Timeline read-model v1 — versioned read boundary for the cross-butler timeline.

Centralises the SQL projections and fan-out query functions for the unified
timeline endpoint, which merges sessions and notifications from all butler
schemas into a time-ordered stream.

A breaking schema change (new required column, renamed column, type change)
should produce a ``timeline_v2`` module rather than silently altering this one.

Public surface
--------------
Column constants:
    SESSION_COLUMNS
    NOTIFICATION_COLUMNS

Query functions (all async):
    query_timeline_sessions_fan_out(db, before, limit, butler_names)
        -> list[TimelineSessionRow]
    query_timeline_notifications_single(pool, before, limit, butler_names)
        -> list[TimelineNotificationRow]

Row DTOs:
    TimelineSessionRow
    TimelineNotificationRow
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

#: Stability contract — bump to ``timeline_v2`` for breaking changes.
READ_MODEL_VERSION = "timeline_v1"

# ---------------------------------------------------------------------------
# Column projections (v1 schema contract)
# ---------------------------------------------------------------------------

#: Session columns projected for timeline events.
SESSION_COLUMNS: str = "id, prompt, trigger_source, success, started_at, completed_at, duration_ms"

#: Notification columns projected for timeline events.
NOTIFICATION_COLUMNS: str = "id, source_butler, channel, recipient, message, status, created_at"

# ---------------------------------------------------------------------------
# Typed row DTOs
# ---------------------------------------------------------------------------


@dataclass
class TimelineSessionRow:
    """Typed DTO for a session row as used in the cross-butler timeline (v1)."""

    id: UUID
    prompt: str | None
    trigger_source: str | None
    success: bool | None
    started_at: datetime
    completed_at: datetime | None
    duration_ms: int | None
    #: Butler name attached after fan-out lookup.
    butler: str | None = None


@dataclass
class TimelineNotificationRow:
    """Typed DTO for a notification row as used in the cross-butler timeline (v1)."""

    id: UUID
    source_butler: str
    channel: str | None
    recipient: str | None
    message: str | None
    status: str | None
    created_at: datetime


# ---------------------------------------------------------------------------
# Row converters
# ---------------------------------------------------------------------------


def _row_to_session(row: asyncpg.Record, *, butler: str) -> TimelineSessionRow:
    """Convert a raw asyncpg row to a :class:`TimelineSessionRow`."""
    return TimelineSessionRow(
        id=row["id"],
        prompt=row["prompt"],
        trigger_source=row["trigger_source"],
        success=row["success"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
        duration_ms=row["duration_ms"],
        butler=butler,
    )


def _row_to_notification(row: asyncpg.Record) -> TimelineNotificationRow:
    """Convert a raw asyncpg row to a :class:`TimelineNotificationRow`."""
    return TimelineNotificationRow(
        id=row["id"],
        source_butler=row["source_butler"],
        channel=row["channel"],
        recipient=row["recipient"],
        message=row["message"],
        status=row["status"],
        created_at=row["created_at"],
    )


# ---------------------------------------------------------------------------
# Query functions
# ---------------------------------------------------------------------------


async def query_timeline_sessions_fan_out(
    db: DatabaseManager,
    *,
    before: datetime | None = None,
    limit: int,
    butler_names: list[str] | None = None,
) -> list[TimelineSessionRow]:
    """Fan out a timeline session query across all (or a subset of) butlers.

    Parameters
    ----------
    db:
        The DatabaseManager that manages per-butler pools.
    before:
        Cursor timestamp — only sessions with ``started_at < before`` are
        returned.  Pass ``None`` for no cursor filter (first page).
    limit:
        Maximum rows to fetch per butler (typically ``requested_limit + 1``
        to allow has_more detection before trimming).
    butler_names:
        Subset of butler names to query.  Defaults to all registered butlers.

    Returns
    -------
    list[TimelineSessionRow]
        Combined rows from all queried butlers, unordered.  Callers must sort
        and trim as needed.
    """
    conditions: list[str] = []
    args: list[Any] = []
    idx = 1

    if before is not None:
        conditions.append(f"started_at < ${idx}")
        args.append(before)
        idx += 1

    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    sql = f"SELECT {SESSION_COLUMNS} FROM sessions{where} ORDER BY started_at DESC LIMIT {limit}"

    results = await db.fan_out(sql, tuple(args), butler_names=butler_names)

    rows: list[TimelineSessionRow] = []
    for butler_name, db_rows in results.items():
        for db_row in db_rows:
            rows.append(_row_to_session(db_row, butler=butler_name))
    return rows


async def query_timeline_notifications_single(
    pool: asyncpg.Pool,
    *,
    before: datetime | None = None,
    limit: int,
    source_butlers: list[str] | None = None,
) -> list[TimelineNotificationRow]:
    """Query the notifications table from a single pool (switchboard DB).

    Notifications are stored in a single cross-butler table (in the switchboard
    schema), so a single-pool query is correct — not a fan-out.

    Parameters
    ----------
    pool:
        The asyncpg pool to query (typically the switchboard pool).
    before:
        Cursor timestamp — only notifications with ``created_at < before`` are
        returned.  Pass ``None`` for no cursor filter.
    limit:
        Maximum rows to fetch.
    source_butlers:
        If given, filter to notifications whose ``source_butler`` is in this list.

    Returns
    -------
    list[TimelineNotificationRow]
        Typed notification DTOs ordered by ``created_at DESC``.
    """
    conditions: list[str] = []
    args: list[Any] = []
    idx = 1

    if before is not None:
        conditions.append(f"created_at < ${idx}")
        args.append(before)
        idx += 1

    if source_butlers is not None:
        conditions.append(f"source_butler = ANY(${idx})")
        args.append(source_butlers)
        idx += 1

    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    sql = (
        f"SELECT {NOTIFICATION_COLUMNS} "
        f"FROM notifications{where} "
        f"ORDER BY created_at DESC "
        f"LIMIT {limit}"
    )

    db_rows = await pool.fetch(sql, *args)
    return [_row_to_notification(r) for r in db_rows]
