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
    query_timeline_sessions_fan_out(db, before, before_id, limit, butler_names, only_errors)
        -> tuple[list[TimelineSessionRow], list[str]]  (rows, degraded butler names)
    query_timeline_notifications_single(pool, before, before_id, limit, butler_names)
        -> list[TimelineNotificationRow]

Row DTOs:
    TimelineSessionRow
    TimelineNotificationRow

Cursor helpers:
    encode_cursor(timestamp, event_id) -> opaque composite ``(timestamp, id)`` cursor
    decode_cursor(cursor) -> (timestamp, event_id | None)
        Also accepts a bare ISO-8601 timestamp (pre-fix cursor format) for
        backward compatibility, in which case there is no id tiebreak.
"""

from __future__ import annotations

import base64
import json
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
# Cursor helpers — composite (timestamp, id) keyset cursor
#
# The pre-fix cursor was a bare ISO-8601 timestamp echoed back as ``before``.
# Comparing only on timestamp means events sharing the exact boundary
# timestamp (e.g. heartbeat ticks that fire on the same cron second across
# many butlers) are silently skipped once they straddle a page boundary —
# whichever of them weren't included in page N are gone forever, because page
# N+1's ``started_at < before`` / ``created_at < before`` predicate excludes
# every row at that timestamp, not just the ones already returned. Encoding
# the id of the last row alongside its timestamp lets the next page use a
# strict ``(ts, id) < (before_ts, before_id)`` tuple comparison instead,
# matching the keyset order documented for GET /api/ingestion/events.
# ---------------------------------------------------------------------------


def encode_cursor(timestamp: datetime, event_id: UUID | str) -> str:
    """Encode a composite ``(timestamp, id)`` keyset position into an opaque cursor.

    Mirrors :func:`butlers.core.ingestion_events.encode_cursor`.
    """
    payload = {"ts": timestamp.isoformat(), "id": str(event_id)}
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()


def decode_cursor(cursor: str) -> tuple[datetime, UUID | None]:
    """Decode an opaque timeline cursor back to ``(timestamp, event_id)``.

    Accepts the composite cursor produced by :func:`encode_cursor`. Also
    accepts a bare ISO-8601 timestamp for backward compatibility with the
    pre-fix cursor format (in which case ``event_id`` is ``None`` and callers
    fall back to the old timestamp-only predicate — same-timestamp events at
    that specific page boundary may still be skipped, exactly as before this
    fix, until the caller re-requests with a fresh composite cursor).

    Raises:
        ValueError: If the cursor is neither a valid composite cursor nor a
            valid bare ISO-8601 timestamp.
    """
    try:
        raw = base64.urlsafe_b64decode(cursor.encode())
        payload = json.loads(raw)
        return datetime.fromisoformat(payload["ts"]), UUID(payload["id"])
    except Exception:  # noqa: BLE001 - fall through to legacy bare-timestamp parsing
        pass

    try:
        return datetime.fromisoformat(cursor), None
    except ValueError as exc:
        raise ValueError(f"Invalid cursor: {exc}") from exc


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
    before_id: UUID | None = None,
    limit: int,
    butler_names: list[str] | None = None,
    only_errors: bool | None = None,
) -> tuple[list[TimelineSessionRow], list[str]]:
    """Fan out a timeline session query across all (or a subset of) butlers.

    Parameters
    ----------
    db:
        The DatabaseManager that manages per-butler pools.
    before:
        Cursor timestamp — only sessions strictly before this position are
        returned.  Pass ``None`` for no cursor filter (first page).
    before_id:
        Id half of the composite ``(started_at, id)`` keyset position from
        the previous page's last row.  When given alongside ``before``, the
        predicate is ``(started_at, id) < (before, before_id)`` instead of
        the timestamp-only ``started_at < before`` — this is what stops
        same-timestamp rows (e.g. simultaneous heartbeat ticks) from being
        skipped across a page boundary.  Ignored when ``before`` is ``None``.
    limit:
        Maximum rows to fetch per butler (typically ``requested_limit + 1``
        to allow has_more detection before trimming).
    butler_names:
        Subset of butler names to query.  Defaults to all registered butlers.
    only_errors:
        Pushes the ``event_type`` filter into SQL instead of filtering the
        derived type after the fact (which under-reports and breaks
        pagination — a page of errors used to be computed from the newest
        ``limit`` sessions post-filtered, so older errors past that window
        were unreachable and ``has_more`` was wrong).
        ``True`` → only failed sessions (``success = false``, the "error"
        event type). ``False`` → only non-failed sessions (``success`` true
        or null, the "session" event type). ``None`` → no filter (both types).

    Returns
    -------
    tuple[list[TimelineSessionRow], list[str]]
        ``(rows, degraded_butlers)``. ``rows`` are combined from all queried
        butlers, unordered — callers must sort and trim as needed.
        ``degraded_butlers`` lists the names of any butler whose query failed
        (so the caller can surface a per-source degraded flag rather than
        silently returning a partial, indistinguishable-from-empty result).
    """
    conditions: list[str] = []
    args: list[Any] = []
    idx = 1

    if before is not None:
        if before_id is not None:
            conditions.append(f"(started_at, id) < (${idx}, ${idx + 1})")
            args.extend([before, before_id])
            idx += 2
        else:
            conditions.append(f"started_at < ${idx}")
            args.append(before)
            idx += 1

    if only_errors is True:
        conditions.append("success = false")
    elif only_errors is False:
        conditions.append("success IS DISTINCT FROM false")

    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    sql = (
        f"SELECT {SESSION_COLUMNS} FROM sessions{where} "
        f"ORDER BY started_at DESC, id DESC LIMIT {limit}"
    )

    results, degraded_butlers = await db.fan_out_with_status(
        sql, tuple(args), butler_names=butler_names
    )

    rows: list[TimelineSessionRow] = []
    for butler_name, db_rows in results.items():
        for db_row in db_rows:
            rows.append(_row_to_session(db_row, butler=butler_name))
    return rows, degraded_butlers


async def query_timeline_notifications_single(
    pool: asyncpg.Pool,
    *,
    before: datetime | None = None,
    before_id: UUID | None = None,
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
        Cursor timestamp — only notifications strictly before this position
        are returned.  Pass ``None`` for no cursor filter.
    before_id:
        Id half of the composite ``(created_at, id)`` keyset position from the
        previous page's last row — see :func:`query_timeline_sessions_fan_out`
        for why this tiebreak matters.  Ignored when ``before`` is ``None``.
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
        if before_id is not None:
            conditions.append(f"(created_at, id) < (${idx}, ${idx + 1})")
            args.extend([before, before_id])
            idx += 2
        else:
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
        f"ORDER BY created_at DESC, id DESC "
        f"LIMIT {limit}"
    )

    db_rows = await pool.fetch(sql, *args)
    return [_row_to_notification(r) for r in db_rows]
