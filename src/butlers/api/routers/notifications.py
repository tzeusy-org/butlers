"""Notification history endpoints — paginated, filterable notification log.

Queries the Switchboard butler's ``notifications`` table and returns results
in the standard ``PaginatedResponse`` envelope.

Provides two routers:

- ``router`` — cross-butler endpoint at ``/api/notifications``
- ``butler_notifications_router`` — butler-scoped at ``/api/butlers/{name}/notifications``

Mutation endpoints:
- PATCH /api/notifications/{notification_id}/read  — mark a notification as read
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping
from datetime import datetime

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query

from butlers.api.briefing.cache import BriefingCache, get_cache, resolve_owner_id
from butlers.api.db import DatabaseManager
from butlers.api.models import ApiResponse, PaginationMeta
from butlers.api.models.notification import (
    AckFailedResult,
    NotificationListResponse,
    NotificationStats,
    NotificationSummary,
)

logger = logging.getLogger(__name__)
_missing_notifications_table_warnings: set[str] = set()

router = APIRouter(prefix="/api/notifications", tags=["notifications"])
butler_notifications_router = APIRouter(prefix="/api/butlers", tags=["butlers", "notifications"])


def _get_db_manager() -> DatabaseManager:
    """Dependency stub — overridden at app startup or in tests."""
    raise RuntimeError("DatabaseManager not initialized")


def _get_switchboard_pool(db: DatabaseManager) -> asyncpg.Pool | None:
    """Return the switchboard pool, or ``None`` when it's unavailable."""
    try:
        return db.pool("switchboard")
    except KeyError:
        logger.warning(
            "Switchboard DB pool unavailable; returning empty notification payloads",
        )
        return None


def _normalize_notification_metadata(value: object | None) -> dict | None:
    """Normalize DB metadata to the API contract shape.

    ``NotificationSummary.metadata`` is an object-or-null field. Some legacy
    rows may contain non-object JSON values (for example strings, arrays, or
    scalars). Those values are normalized to ``None`` so list endpoints remain
    stable and never fail serialization.
    """
    if value is None:
        return None
    if isinstance(value, Mapping):
        return dict(value)
    return None


def _is_missing_notifications_table_error(exc: Exception) -> bool:
    """Return whether *exc* indicates an uninitialized ``notifications`` table."""
    if exc.__class__.__name__ == "UndefinedTableError":
        return True
    msg = str(exc).lower()
    return "relation" in msg and "notifications" in msg and "does not exist" in msg


def _log_missing_notifications_table_once(*, operation: str) -> None:
    """Log the missing-table degradation path once per endpoint operation."""
    if operation in _missing_notifications_table_warnings:
        logger.debug(
            "Notifications table still missing during %s; returning empty payload",
            operation,
        )
        return

    _missing_notifications_table_warnings.add(operation)
    logger.warning(
        "Switchboard notifications table is missing during %s; returning empty payload "
        "until switchboard migrations have run.",
        operation,
    )


def _empty_notification_page(
    *, offset: int, limit: int, source_available: bool = True
) -> NotificationListResponse:
    """Return the standard empty paginated notification envelope.

    ``source_available=False`` when this empty page reflects a genuinely
    unreachable Switchboard pool, not a truthful "no notifications match".
    """
    return NotificationListResponse(
        data=[],
        meta=PaginationMeta(total=0, offset=offset, limit=limit),
        source_available=source_available,
    )


def _empty_notification_stats(*, source_available: bool = True) -> ApiResponse[NotificationStats]:
    """Return the standard empty notification stats envelope.

    ``source_available=False`` when this all-zero payload reflects a
    genuinely unreachable Switchboard pool, not a truthful "no activity".
    """
    return ApiResponse[NotificationStats](
        data=NotificationStats(
            total=0,
            sent=0,
            failed=0,
            by_channel={},
            by_butler={},
            source_available=source_available,
        )
    )


# ---------------------------------------------------------------------------
# Shared query logic
# ---------------------------------------------------------------------------


# "retried" is not a stored status -- it's a failed notification that a later
# sent notification (same session/channel/message) superseded. Shared between
# the WHERE-clause filter and the SELECT effective_status CASE so a
# "?status=retried" filter always matches what effective_status reports as
# retried (bu-qvnce.2 -- the filter used to compare status = 'retried', which
# can never match since the column never stores that value).
_RETRIED_EXISTS_SQL = (
    "session_id IS NOT NULL AND EXISTS ("
    "SELECT 1 FROM notifications n2 "
    "WHERE n2.session_id = notifications.session_id "
    "AND n2.channel = notifications.channel "
    "AND n2.message = notifications.message "
    "AND n2.status = 'sent' "
    "AND n2.created_at > notifications.created_at"
    ")"
)


async def _query_notifications(
    pool: asyncpg.Pool,
    *,
    offset: int,
    limit: int,
    butler: str | None = None,
    channel: str | None = None,
    status: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> NotificationListResponse:
    """Build and execute the paginated notification query.

    Shared by both the cross-butler and butler-scoped endpoints.
    """
    # Build dynamic WHERE clause
    conditions: list[str] = []
    args: list[object] = []
    idx = 1

    if butler is not None:
        conditions.append(f"source_butler = ${idx}")
        args.append(butler)
        idx += 1

    if channel is not None:
        conditions.append(f"channel = ${idx}")
        args.append(channel)
        idx += 1

    if status == "retried":
        conditions.append(f"status = 'failed' AND {_RETRIED_EXISTS_SQL}")
    elif status is not None:
        conditions.append(f"status = ${idx}")
        args.append(status)
        idx += 1

    if since is not None:
        conditions.append(f"created_at >= ${idx}")
        args.append(since)
        idx += 1

    if until is not None:
        conditions.append(f"created_at <= ${idx}")
        args.append(until)
        idx += 1

    where_clause = (" WHERE " + " AND ".join(conditions)) if conditions else ""

    try:
        # Count query
        count_sql = f"SELECT count(*) FROM notifications{where_clause}"
        count_row = await pool.fetchval(count_sql, *args)
        total = count_row or 0

        # Data query — compute effective_status: a failed notification is "retried"
        # when a later sent notification exists in the same session/channel/message.
        data_sql = (
            f"SELECT id, source_butler, channel, recipient, message, metadata, "
            f"status, error, session_id, trace_id, created_at, "
            f"CASE "
            f"  WHEN status = 'failed' AND {_RETRIED_EXISTS_SQL} THEN 'retried' "
            f"  ELSE status "
            f"END AS effective_status "
            f"FROM notifications{where_clause} "
            f"ORDER BY created_at DESC "
            f"OFFSET ${idx} LIMIT ${idx + 1}"
        )
        args.extend([offset, limit])

        rows = await pool.fetch(data_sql, *args)
    except Exception as exc:
        if _is_missing_notifications_table_error(exc):
            raise
        logger.warning(
            "Switchboard notifications query failed; returning a degraded empty page",
            exc_info=True,
        )
        return _empty_notification_page(offset=offset, limit=limit, source_available=False)

    notifications = [
        NotificationSummary(
            id=row["id"],
            source_butler=row["source_butler"],
            channel=row["channel"],
            recipient=row["recipient"],
            message=row["message"],
            metadata=_normalize_notification_metadata(row["metadata"]),
            status=row["status"],
            effective_status=row["effective_status"],
            error=row["error"],
            session_id=row["session_id"],
            trace_id=row["trace_id"],
            created_at=row["created_at"],
        )
        for row in rows
    ]

    return NotificationListResponse(
        data=notifications,
        meta=PaginationMeta(total=total, offset=offset, limit=limit),
    )


# ---------------------------------------------------------------------------
# Cross-butler endpoint: GET /api/notifications/
# ---------------------------------------------------------------------------


@router.get("", response_model=NotificationListResponse)
async def list_notifications(
    offset: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(50, ge=1, le=200, description="Max records to return"),
    butler: str | None = Query(None, description="Filter by source butler name"),
    channel: str | None = Query(None, description="Filter by delivery channel"),
    status: str | None = Query(None, description="Filter by status (sent/failed/pending)"),
    since: datetime | None = Query(None, description="Only notifications created after this time"),
    until: datetime | None = Query(None, description="Only notifications created before this time"),
    db: DatabaseManager = Depends(_get_db_manager),
) -> NotificationListResponse:
    """Return paginated notification history from the Switchboard database.

    Supports filtering by butler, channel, status, and date range.
    Results are ordered by ``created_at DESC`` (newest first).
    """
    pool = _get_switchboard_pool(db)
    if pool is None:
        return _empty_notification_page(offset=offset, limit=limit, source_available=False)
    try:
        return await _query_notifications(
            pool,
            offset=offset,
            limit=limit,
            butler=butler,
            channel=channel,
            status=status,
            since=since,
            until=until,
        )
    except Exception as exc:
        if not _is_missing_notifications_table_error(exc):
            raise
        _log_missing_notifications_table_once(operation="list_notifications")
        return _empty_notification_page(offset=offset, limit=limit)


# ---------------------------------------------------------------------------
# Butler-scoped endpoint: GET /api/butlers/{name}/notifications
# ---------------------------------------------------------------------------


@butler_notifications_router.get(
    "/{name}/notifications",
    response_model=NotificationListResponse,
)
async def list_butler_notifications(
    name: str,
    offset: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(50, ge=1, le=200, description="Max records to return"),
    channel: str | None = Query(None, description="Filter by delivery channel"),
    status: str | None = Query(None, description="Filter by status (sent/failed/pending)"),
    since: datetime | None = Query(None, description="Only notifications created after this time"),
    until: datetime | None = Query(None, description="Only notifications created before this time"),
    db: DatabaseManager = Depends(_get_db_manager),
) -> NotificationListResponse:
    """Return paginated notifications for a specific butler.

    Identical to ``GET /api/notifications`` but with ``source_butler``
    pre-filtered to the butler identified by *name* in the URL path.
    """
    pool = _get_switchboard_pool(db)
    if pool is None:
        return _empty_notification_page(offset=offset, limit=limit, source_available=False)
    try:
        return await _query_notifications(
            pool,
            offset=offset,
            limit=limit,
            butler=name,
            channel=channel,
            status=status,
            since=since,
            until=until,
        )
    except Exception as exc:
        if not _is_missing_notifications_table_error(exc):
            raise
        _log_missing_notifications_table_once(operation="list_butler_notifications")
        return _empty_notification_page(offset=offset, limit=limit)


def _window_predicate(
    since: datetime | None, until: datetime | None, column: str
) -> tuple[str, list[object]]:
    """Build a ``created_at`` window predicate fragment (no leading AND/WHERE).

    Returns ``("", [])`` when neither bound is set. *column* lets callers
    qualify the column for aliased queries (e.g. ``"n.created_at"``).
    """
    conditions: list[str] = []
    args: list[object] = []
    idx = 1
    if since is not None:
        conditions.append(f"{column} >= ${idx}")
        args.append(since)
        idx += 1
    if until is not None:
        conditions.append(f"{column} <= ${idx}")
        args.append(until)
        idx += 1
    return " AND ".join(conditions), args


@router.get("/stats", response_model=ApiResponse[NotificationStats])
async def notification_stats(
    since: datetime | None = Query(
        None,
        description=(
            "Only count notifications created at/after this timestamp -- window "
            "scoping for verdict-style summaries (e.g. 'in the last 24h')."
        ),
    ),
    until: datetime | None = Query(
        None, description="Only count notifications created at/before this timestamp"
    ),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[NotificationStats]:
    """Return aggregated notification statistics.

    Queries the Switchboard database for total counts, sent/failed breakdowns,
    and per-channel / per-butler distributions. ``since``/``until`` are
    optional -- omitted, this is the same all-time rollup as before; when set,
    every count below is scoped to that ``created_at`` window (bu-y0v0c,
    JARVIS pursuit move 9 slice 3 -- powers the notifications verdict opener's
    windowed by_butler clause).
    """
    pool = _get_switchboard_pool(db)
    if pool is None:
        return _empty_notification_stats(source_available=False)

    # Query-budget: this handler fires 4 separate COUNT/GROUP-BY queries against
    # the notifications table on every /stats request.
    #
    # Q1 (total): O(N) sequential scan.  Acceptable — count(*) on a heap table.
    # Q2 (sent): O(N) with idx_notifications_status (sw_001) partial filter.
    # Q3 (failed / terminal-failure self-join): most expensive.  The outer scan
    #    filters to status='failed' via idx_notifications_status; the EXISTS
    #    subquery uses ix_notifications_session_id (sw_016) to locate sibling
    #    rows by session_id.  Budget: O(F * log N) where F = failed-row count.
    # Q4/Q5 (by_channel, by_butler): O(N) GROUP BY — backed by composite indexes
    #    idx_notifications_channel_created and idx_notifications_source_butler_created.
    #
    # At current notification volumes (< 100k rows) the full endpoint stays
    # under 50 ms.  If total rows exceed ~1 M, consider adding a materialized
    # summary table refreshed on insert, or caching this response for 60 s.
    #
    # since/until add one bound `created_at` predicate to each query above
    # (qualified as `n.created_at` in Q3, which aliases the table `n`); the
    # window's own bound values are re-used verbatim (unaliased vs. aliased
    # predicate text differs, args do not).
    window_clause, window_args = _window_predicate(since, until, "created_at")
    window_where = f" WHERE {window_clause}" if window_clause else ""
    window_and = f" AND {window_clause}" if window_clause else ""
    n_clause, n_args = _window_predicate(since, until, "n.created_at")
    n_and = f" AND {n_clause}" if n_clause else ""

    try:
        total = (
            await pool.fetchval(f"SELECT count(*) FROM notifications{window_where}", *window_args)
            or 0
        )
        sent = (
            await pool.fetchval(
                f"SELECT count(*) FROM notifications WHERE status = 'sent'{window_and}",
                *window_args,
            )
            or 0
        )
        # Only count terminal failures — exclude failed attempts that were
        # successfully retried in the same session with the same message.
        failed = (
            await pool.fetchval(
                f"""
                SELECT count(*) FROM notifications n
                WHERE n.status = 'failed'{n_and}
                AND NOT (
                    n.session_id IS NOT NULL
                    AND EXISTS (
                        SELECT 1 FROM notifications n2
                        WHERE n2.session_id = n.session_id
                        AND n2.channel = n.channel
                        AND n2.message = n.message
                        AND n2.status = 'sent'
                        AND n2.created_at > n.created_at
                    )
                )
                """,
                *n_args,
            )
            or 0
        )

        channel_rows = await pool.fetch(
            f"SELECT channel, count(*) AS cnt FROM notifications{window_where} GROUP BY channel",
            *window_args,
        )
        by_channel = {row["channel"]: row["cnt"] for row in channel_rows}

        # by_butler is scoped to FAILED notifications only (unlike by_channel
        # above, which spans every status) -- it powers the notifications
        # verdict opener's "M from <butler>" clause, which is only meaningful
        # as a breakdown of the failures already being reported (bu-y0v0c).
        butler_rows = await pool.fetch(
            f"SELECT source_butler, count(*) AS cnt FROM notifications "
            f"WHERE status = 'failed'{window_and} GROUP BY source_butler",
            *window_args,
        )
        by_butler = {row["source_butler"]: row["cnt"] for row in butler_rows}
    except Exception as exc:
        if not _is_missing_notifications_table_error(exc):
            raise
        _log_missing_notifications_table_once(operation="notification_stats")
        return _empty_notification_stats()

    return ApiResponse[NotificationStats](
        data=NotificationStats(
            total=total,
            sent=sent,
            failed=failed,
            by_channel=by_channel,
            by_butler=by_butler,
        ),
    )


# ---------------------------------------------------------------------------
# PATCH /api/notifications/{notification_id}/read
# ---------------------------------------------------------------------------


@router.patch(
    "/{notification_id}/read",
    response_model=ApiResponse[NotificationSummary],
)
async def mark_notification_read(
    notification_id: uuid.UUID,
    db: DatabaseManager = Depends(_get_db_manager),
    cache: BriefingCache = Depends(get_cache),
) -> ApiResponse[NotificationSummary]:
    """Mark a notification as read.

    Updates ``status = 'read'`` on the given notification row in the
    Switchboard ``notifications`` table and invalidates the briefing cache
    so the next GET /api/dashboard/briefing reflects the updated attention
    list without waiting for TTL expiry.

    Returns HTTP 404 when the notification is not found.
    Returns HTTP 503 when the Switchboard pool is unavailable.
    """
    pool = _get_switchboard_pool(db)
    if pool is None:
        raise HTTPException(status_code=503, detail="Switchboard database is not available")

    try:
        row = await pool.fetchrow(
            """
            UPDATE notifications
            SET status = 'read'
            WHERE id = $1
            RETURNING id, source_butler, channel, recipient, message, metadata,
                      status, error, session_id, trace_id, created_at
            """,
            notification_id,
        )
    except Exception as exc:
        if _is_missing_notifications_table_error(exc):
            raise HTTPException(
                status_code=503,
                detail="Notifications table is not yet initialised",
            )
        raise

    if row is None:
        raise HTTPException(
            status_code=404,
            detail=f"Notification not found: {notification_id}",
        )

    # Invalidate the briefing cache so the next briefing request reflects the
    # updated attention list (category a from bu-qzjpm).
    owner_id = await resolve_owner_id(pool)
    if owner_id is not None:
        cache.invalidate(owner_id)
    else:
        cache.invalidate_all()

    return ApiResponse(
        data=NotificationSummary(
            id=row["id"],
            source_butler=row["source_butler"],
            channel=row["channel"],
            recipient=row["recipient"],
            message=row["message"],
            metadata=_normalize_notification_metadata(row["metadata"]),
            status=row["status"],
            effective_status=row["status"],
            error=row["error"],
            session_id=row["session_id"],
            trace_id=row["trace_id"],
            created_at=row["created_at"],
        )
    )


# ---------------------------------------------------------------------------
# POST /api/notifications/ack-failed
# ---------------------------------------------------------------------------


@router.post(
    "/ack-failed",
    response_model=ApiResponse[AckFailedResult],
)
async def ack_failed_notifications(
    db: DatabaseManager = Depends(_get_db_manager),
    cache: BriefingCache = Depends(get_cache),
) -> ApiResponse[AckFailedResult]:
    """Acknowledge all failed notifications in bulk.

    Flips every notification with ``status = 'failed'`` to ``status = 'read'``
    and invalidates the briefing cache so the overview "Delivery pressure needs
    review" badge clears immediately without waiting for TTL expiry.

    Returns the count of notifications that were acknowledged.  Returns zero
    when the Switchboard pool is unavailable or the notifications table does not
    yet exist.
    """
    pool = _get_switchboard_pool(db)
    if pool is None:
        return ApiResponse(data=AckFailedResult(acknowledged=0))

    try:
        result = await pool.execute(
            "UPDATE notifications SET status = 'read' WHERE status = 'failed'"
        )
    except Exception as exc:
        if _is_missing_notifications_table_error(exc):
            _log_missing_notifications_table_once(operation="ack_failed_notifications")
            return ApiResponse(data=AckFailedResult(acknowledged=0))
        raise

    # asyncpg returns the command tag string, e.g. "UPDATE 12".
    acknowledged = 0
    if isinstance(result, str) and result.startswith("UPDATE "):
        try:
            acknowledged = int(result.split()[-1])
        except (ValueError, IndexError):
            pass

    # Invalidate briefing cache so the attention badge reflects the new counts.
    owner_id = await resolve_owner_id(pool)
    if owner_id is not None:
        cache.invalidate(owner_id)
    else:
        cache.invalidate_all()

    return ApiResponse(data=AckFailedResult(acknowledged=acknowledged))
