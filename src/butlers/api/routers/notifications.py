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

import json
import logging
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query

from butlers.api.briefing.cache import BriefingCache, get_cache, resolve_owner_id
from butlers.api.db import DatabaseManager
from butlers.api.models import ApiResponse, PaginationMeta
from butlers.api.models.notification import (
    AckFailedResult,
    NotificationActionResult,
    NotificationListResponse,
    NotificationStats,
    NotificationSummary,
)
from butlers.core.attention_ledger import record_attention_event
from butlers.credential_store import resolve_owner_entity_info

logger = logging.getLogger(__name__)
_missing_notifications_table_warnings: set[str] = set()

# Escalation only supports the two channels notify() itself dispatches
# directly (see SUPPORTED_CHANNELS in switchboard/tools/notification/deliver.py);
# whatsapp/other channels have no owner-credential resolver wired here.
_ESCALATE_ALTERNATE_CHANNEL = {"telegram": "email", "email": "telegram"}
_OWNER_INFO_TYPE_BY_CHANNEL = {"telegram": "telegram_chat_id", "email": "email"}

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
    JSONB string scalars encode one object. Decode that one layer so their
    provenance remains visible. Malformed strings and strings that decode to a
    non-object retain their exact outer value under ``_raw``. Actual non-string,
    non-object JSONB values remain ``None``.
    """
    if value is None:
        return None
    if isinstance(value, Mapping):
        return dict(value)
    if not isinstance(value, str):
        return None

    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return {"_raw": value}

    return dict(decoded) if isinstance(decoded, Mapping) else {"_raw": value}


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

    ``source_available=False`` when this all-zero payload reflects an
    unavailable or failed Switchboard source, not a truthful "no activity".
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
# the WHERE-clause filters and the SELECT effective_status CASE so special
# ``retried`` and ``terminal_failed`` filters match the computed lifecycle,
# not an impossible stored status value.
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

# A manually retried/escalated notification (POST .../retry or .../escalate)
# is flipped to status='read' and stamped with an explicit metadata marker
# pointing at the new attempt -- distinct from the session-based organic
# _RETRIED_EXISTS_SQL inference above, which never matches a dashboard-
# triggered attempt (no session_id). Checked first so an explicitly-actioned
# row reads as "retried"/"escalated" rather than the generic "read".
_EFFECTIVE_STATUS_CASE_SQL = (
    "CASE "
    "  WHEN status = 'read' AND metadata ? 'escalated_to' THEN 'escalated' "
    "  WHEN status = 'read' AND metadata ? 'retried_to' THEN 'retried' "
    f"  WHEN status = 'failed' AND {_RETRIED_EXISTS_SQL} THEN 'retried' "
    "  ELSE status "
    "END"
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
        # Matches _EFFECTIVE_STATUS_CASE_SQL's two "retried" branches: the
        # organic session-based inference, plus a manually-retried row
        # (status='read', stamped with the retried_to marker by POST
        # .../retry). Keeping the WHERE-clause filter and the SELECT CASE in
        # sync is required -- otherwise ?status=retried and the "Retried"
        # chip shown for the same row would silently disagree.
        conditions.append(
            f"((status = 'failed' AND {_RETRIED_EXISTS_SQL}) "
            "OR (status = 'read' AND metadata ? 'retried_to'))"
        )
    elif status == "escalated":
        conditions.append("status = 'read' AND metadata ? 'escalated_to'")
    elif status == "terminal_failed":
        conditions.append(f"status = 'failed' AND NOT ({_RETRIED_EXISTS_SQL})")
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
            f"{_EFFECTIVE_STATUS_CASE_SQL} AS effective_status "
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
    status: str | None = Query(
        None,
        description="Filter by status (sent/failed/pending/read/retried/escalated/terminal_failed)",
    ),
    since: datetime | None = Query(None, description="Only notifications created after this time"),
    until: datetime | None = Query(None, description="Only notifications created before this time"),
    db: DatabaseManager = Depends(_get_db_manager),
) -> NotificationListResponse:
    """Return paginated notification history from the Switchboard database.

    Supports filtering by butler, channel, status, and date range. The
    ``terminal_failed`` status excludes failed attempts that a later matching
    delivery successfully retried.
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
    status: str | None = Query(
        None,
        description="Filter by status (sent/failed/pending/read/retried/escalated/terminal_failed)",
    ),
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

        # by_butler is scoped to terminal failures only (unlike by_channel
        # above, which spans every status) so it remains a true breakdown of
        # the ``failed`` count shown by the notifications verdict opener.
        butler_rows = await pool.fetch(
            f"SELECT source_butler, count(*) AS cnt FROM notifications "
            f"WHERE status = 'failed'{window_and} "
            f"AND NOT ({_RETRIED_EXISTS_SQL}) GROUP BY source_butler",
            *window_args,
        )
    except Exception as exc:
        if _is_missing_notifications_table_error(exc):
            _log_missing_notifications_table_once(operation="notification_stats")
            return _empty_notification_stats()
        logger.warning(
            "Switchboard notification stats query failed; returning degraded empty stats",
            exc_info=True,
        )
        return _empty_notification_stats(source_available=False)

    by_channel = {row["channel"]: row["cnt"] for row in channel_rows}
    by_butler = {row["source_butler"]: row["cnt"] for row in butler_rows}

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


# ---------------------------------------------------------------------------
# POST /api/notifications/{notification_id}/retry
# POST /api/notifications/{notification_id}/escalate
# ---------------------------------------------------------------------------


async def _fetch_notification_row(
    pool: asyncpg.Pool, notification_id: uuid.UUID
) -> asyncpg.Record | None:
    try:
        return await pool.fetchrow(
            """
            SELECT id, source_butler, channel, recipient, message, metadata,
                   status, error, session_id, trace_id, created_at
            FROM notifications
            WHERE id = $1
            """,
            notification_id,
        )
    except Exception as exc:
        if _is_missing_notifications_table_error(exc):
            raise HTTPException(
                status_code=503,
                detail="Notifications table is not yet initialised",
            ) from exc
        raise


def _extract_stored_envelope(row: Mapping[str, Any]) -> dict[str, Any]:
    """Reconstruct a notify.v1 envelope for a stored notification row.

    Prefers the full envelope persisted at delivery time
    (``metadata.notify_request``, written by ``deliver()``'s notify_request
    path). Falls back to a minimal synthetic envelope built from the row's
    own columns for older rows or the legacy channel/recipient/message
    dispatch path, which never persisted an envelope -- so every failed row
    is retryable, not just the ones that happen to carry one.
    """
    metadata = row.get("metadata")
    if isinstance(metadata, Mapping):
        stored = metadata.get("notify_request")
        if isinstance(stored, Mapping):
            return dict(stored)
    return {
        "schema_version": "notify.v1",
        "origin_butler": row["source_butler"],
        "delivery": {
            "intent": "send",
            "channel": row["channel"],
            "recipient": row["recipient"],
            "message": row["message"],
        },
    }


async def _redeliver(
    pool: asyncpg.Pool,
    *,
    envelope: dict[str, Any],
    source_butler: str,
    extra_metadata: dict[str, Any],
) -> dict[str, Any]:
    """Re-invoke delivery in-process against the Switchboard pool.

    Mirrors the dashboard-API's approval-push precedent
    (``ingestion_connectors.py::_build_dashboard_approval_push_runtime``):
    the dashboard-API process is not a butler daemon, so it imports
    ``deliver()`` directly and calls it in-process rather than proxying
    through a ``switchboard_client`` MCP connection that does not exist here.
    """
    from butlers.tools.switchboard.notification.deliver import deliver as _switchboard_deliver

    return await _switchboard_deliver(
        pool,
        source_butler=source_butler,
        notify_request=envelope,
        metadata=extra_metadata,
    )


async def _claim_failed_notification(
    pool: asyncpg.Pool, notification_id: uuid.UUID
) -> asyncpg.Record | None:
    """Atomically claim a ``failed`` row for a manual retry/escalate action.

    Concurrency guard for ``retry_notification``/``escalate_notification``:
    a plain ``fetchrow`` status check followed by a real send is a classic
    check-then-act race (two concurrent/replayed requests both see
    ``status='failed'``, both redeliver, the user gets the message twice).
    This conditional ``UPDATE ... WHERE status = 'failed'`` is a single
    atomic statement -- Postgres serializes concurrent updates to the same
    row, so only the first caller's ``UPDATE`` can match and return a row;
    every other concurrent or replayed caller affects zero rows and gets
    ``None`` back, which the endpoint turns into an honest 409 instead of a
    second real delivery.

    The claim flips ``status`` straight to ``'read'`` -- the terminal state
    :func:`_finalize_manual_action` would reach anyway -- rather than a
    dedicated in-flight status, because ``chk_notifications_status`` only
    permits ``sent``/``failed``/``read`` and reusing ``'read'`` avoids a
    schema migration for this fix. Call this only immediately before the
    real delivery attempt (after any read-only 404/422 validation), never
    before checks that can legitimately reject without sending anything --
    claiming on a path that never delivers would strand a retryable failure
    as unactionable.

    Crash-window semantics: if the process dies (or :func:`_finalize_manual_action`
    raises) between this claim and the follow-up metadata merge, the row is
    left at ``status='read'`` with no ``retried_to``/``escalated_to`` marker.
    It then renders as a plain "read" notification instead of
    "retried"/"escalated" -- a cosmetic gap -- but it can never be re-claimed
    or double-delivered, because it is already off ``'failed'``. This is a
    deliberate safety-over-completeness tradeoff: an orphaned marker is
    recoverable by inspection; a duplicate real send is not.
    """
    return await pool.fetchrow(
        """
        UPDATE notifications
        SET status = 'read'
        WHERE id = $1 AND status = 'failed'
        RETURNING id, source_butler, channel, recipient, message, metadata,
                  status, error, session_id, trace_id, created_at
        """,
        notification_id,
    )


async def _finalize_manual_action(
    pool: asyncpg.Pool,
    *,
    notification_id: uuid.UUID,
    marker_key: str,
    new_notification_id: str | None,
    extra_fields: dict[str, Any],
) -> None:
    """Flip the original row to ``read`` and stamp a forward-link marker.

    A human has now acted on this failure (retried or escalated it), so it
    no longer belongs in the ``failed``/``terminal_failed`` attention view --
    ``effective_status`` picks the marker up (see
    ``_EFFECTIVE_STATUS_CASE_SQL``) to render "retried"/"escalated" rather
    than the generic "read". Written even when the new attempt itself
    raised before producing an id (``new_notification_id=None``): the
    original must not sit forever in "failed" after a human has already
    acted on it.
    """
    marker = {marker_key: new_notification_id, **extra_fields}
    await pool.execute(
        """
        UPDATE notifications
        SET status = 'read', metadata = COALESCE(metadata, '{}'::jsonb) || $2::jsonb
        WHERE id = $1
        """,
        notification_id,
        json.dumps(marker),
    )


@router.post(
    "/{notification_id}/retry",
    response_model=ApiResponse[NotificationActionResult],
)
async def retry_notification(
    notification_id: uuid.UUID,
    db: DatabaseManager = Depends(_get_db_manager),
    cache: BriefingCache = Depends(get_cache),
) -> ApiResponse[NotificationActionResult]:
    """Manually re-attempt delivery of a failed notification, right now.

    Distinct from ``notify()``'s own automatic transport-failure retry
    (which only fires for a narrow "unexpected exception after resolution"
    class and enqueues onto the deferred-notifications/scheduler-flush
    path): this is a human-triggered, synchronous re-send for the ordinary
    case -- a plain delivery error/timeout that ``notify()`` correctly
    records as a terminal ``failed`` attention-ledger outcome and does not
    retry on its own (core-notify spec, "Attention Ledger Recording at the
    notify() Boundary"). Deliberately bypasses ``notify()``'s quiet-hours /
    policy gate, the same way the dashboard's approval-push runtime does --
    an explicit human retry carries the same "act now" intent as a
    high-priority notification.

    Returns HTTP 404 if the notification does not exist, 409 if it is not
    currently ``failed`` (including a concurrent/replayed retry that lost the
    atomic claim below -- see :func:`_claim_failed_notification`), 503 if the
    Switchboard pool is unavailable.
    """
    pool = _get_switchboard_pool(db)
    if pool is None:
        raise HTTPException(status_code=503, detail="Switchboard database is not available")

    row = await _fetch_notification_row(pool, notification_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Notification not found: {notification_id}")
    if row["status"] != "failed":
        raise HTTPException(
            status_code=409,
            detail=(
                f"Only failed notifications can be retried; current status is '{row['status']}'."
            ),
        )

    envelope = _extract_stored_envelope(row)

    claimed = await _claim_failed_notification(pool, notification_id)
    if claimed is None:
        # Lost a concurrent claim race (double-click, two tabs, a client
        # resending after a perceived timeout) -- another request already
        # flipped this row off 'failed' between our read above and this
        # claim. Report the same 409 a fresh read would give instead of
        # redelivering a second time.
        raise HTTPException(
            status_code=409,
            detail=(
                "Only failed notifications can be retried; this notification was already actioned."
            ),
        )

    result = await _redeliver(
        pool,
        envelope=envelope,
        source_butler=row["source_butler"],
        extra_metadata={"retried_from": str(notification_id)},
    )

    new_id = result.get("notification_id")
    status = result.get("status", "failed")
    await _finalize_manual_action(
        pool,
        notification_id=notification_id,
        marker_key="retried_to",
        new_notification_id=new_id,
        extra_fields={"retried_at": datetime.now(UTC).isoformat()},
    )
    await record_attention_event(
        pool,
        origin_butler=row["source_butler"],
        source="notify",
        outcome="delivered" if status == "sent" else "failed",
        channel=row["channel"],
        reason=(
            "manual_retry"
            if status == "sent"
            else f"manual_retry_failed:{result.get('error_class', 'delivery_error')}"
        ),
        notification_ref=new_id,
        metadata={"retried_from": str(notification_id)},
    )

    owner_id = await resolve_owner_id(pool)
    if owner_id is not None:
        cache.invalidate(owner_id)
    else:
        cache.invalidate_all()

    return ApiResponse(
        data=NotificationActionResult(
            original_notification_id=notification_id,
            new_notification_id=uuid.UUID(new_id) if new_id else None,
            channel=row["channel"],
            status=status,
            error=result.get("error"),
        )
    )


@router.post(
    "/{notification_id}/escalate",
    response_model=ApiResponse[NotificationActionResult],
)
async def escalate_notification(
    notification_id: uuid.UUID,
    db: DatabaseManager = Depends(_get_db_manager),
    cache: BriefingCache = Depends(get_cache),
) -> ApiResponse[NotificationActionResult]:
    """Re-attempt a failed notification on the owner's alternate channel.

    Escalation exists for the case a plain retry cannot fix: the *channel
    itself* is the problem (a Telegram outage, a stale chat id) rather than
    a one-off transient error. Swaps telegram<->email and resolves the
    owner's contact for that channel via ``resolve_owner_entity_info``, the
    same owner-credential lookup the dashboard's connector actions already
    use for owner-directed delivery (``ingestion_connectors.py``). This
    endpoint only escalates owner-directed notifications -- a third-party
    recipient has no configured alternate to fall back to.

    Returns HTTP 404 if not found, 409 if not ``failed`` (including a
    concurrent/replayed escalate that lost the atomic claim below -- see
    :func:`_claim_failed_notification`), 422 if the channel has no supported
    alternate or the owner has no contact configured for it, 503 if the
    Switchboard pool is unavailable.
    """
    pool = _get_switchboard_pool(db)
    if pool is None:
        raise HTTPException(status_code=503, detail="Switchboard database is not available")

    row = await _fetch_notification_row(pool, notification_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Notification not found: {notification_id}")
    if row["status"] != "failed":
        raise HTTPException(
            status_code=409,
            detail=(
                f"Only failed notifications can be escalated; current status is '{row['status']}'."
            ),
        )

    alt_channel = _ESCALATE_ALTERNATE_CHANNEL.get(row["channel"])
    if alt_channel is None:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Escalation is only supported for telegram/email notifications, "
                f"not '{row['channel']}'."
            ),
        )

    info_type = _OWNER_INFO_TYPE_BY_CHANNEL[alt_channel]
    alt_recipient = await resolve_owner_entity_info(pool, info_type)
    if not alt_recipient:
        raise HTTPException(
            status_code=422,
            detail=f"No owner {alt_channel} contact is configured; cannot escalate.",
        )

    envelope: dict[str, Any] = {
        "schema_version": "notify.v1",
        "origin_butler": row["source_butler"],
        "delivery": {
            "intent": "send",
            "channel": alt_channel,
            "recipient": alt_recipient,
            "message": row["message"],
        },
    }
    if alt_channel == "email":
        envelope["delivery"]["subject"] = f"Escalated notification from {row['source_butler']}"

    claimed = await _claim_failed_notification(pool, notification_id)
    if claimed is None:
        # Lost a concurrent claim race -- see the matching comment in
        # retry_notification. Checked here, after the 422 validation above,
        # so a request that would fail validation never claims the row.
        raise HTTPException(
            status_code=409,
            detail=(
                "Only failed notifications can be escalated; "
                "this notification was already actioned."
            ),
        )

    result = await _redeliver(
        pool,
        envelope=envelope,
        source_butler=row["source_butler"],
        extra_metadata={
            "escalated_from": str(notification_id),
            "escalated_from_channel": row["channel"],
        },
    )

    new_id = result.get("notification_id")
    status = result.get("status", "failed")
    await _finalize_manual_action(
        pool,
        notification_id=notification_id,
        marker_key="escalated_to",
        new_notification_id=new_id,
        extra_fields={
            "escalated_at": datetime.now(UTC).isoformat(),
            "escalated_channel": alt_channel,
        },
    )
    await record_attention_event(
        pool,
        origin_butler=row["source_butler"],
        source="notify",
        outcome="delivered" if status == "sent" else "failed",
        channel=alt_channel,
        reason=(
            "manual_escalate"
            if status == "sent"
            else f"manual_escalate_failed:{result.get('error_class', 'delivery_error')}"
        ),
        notification_ref=new_id,
        metadata={"escalated_from": str(notification_id)},
    )

    owner_id = await resolve_owner_id(pool)
    if owner_id is not None:
        cache.invalidate(owner_id)
    else:
        cache.invalidate_all()

    return ApiResponse(
        data=NotificationActionResult(
            original_notification_id=notification_id,
            new_notification_id=uuid.UUID(new_id) if new_id else None,
            channel=alt_channel,
            status=status,
            error=result.get("error"),
        )
    )
