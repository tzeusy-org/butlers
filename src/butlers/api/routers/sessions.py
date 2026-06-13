"""Session history endpoints — paginated, filterable session log.

Provides two routers:

- ``router`` — cross-butler endpoints:

  - ``GET /api/sessions``
  - ``GET /api/sessions/{session_id}``

- ``butler_sessions_router`` — butler-scoped endpoints:

  - ``GET /api/butlers/{name}/sessions``
  - ``GET /api/butlers/{name}/sessions/{session_id}``
  - ``GET /api/butlers/{name}/analytics/latency-stats``
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from butlers.api.db import DatabaseManager
from butlers.api.models import (
    ApiResponse,
    PaginatedResponse,
    PaginationMeta,
    SessionSummary,
)
from butlers.api.models.session import (
    DailyActivity,
    DailyActivityBucket,
    HourlyActivity,
    HourlyActivityBucket,
    LatencyStats,
    ProcessLog,
    SessionDetail,
    SessionKindBreakdown,
    SessionKindItem,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/sessions", tags=["sessions"])
butler_sessions_router = APIRouter(prefix="/api/butlers", tags=["butlers", "sessions"])


def _get_db_manager() -> DatabaseManager:
    """Dependency stub — overridden at app startup or in tests."""
    raise RuntimeError("DatabaseManager not initialized")


# ---------------------------------------------------------------------------
# Shared SQL builder
# ---------------------------------------------------------------------------

_SUMMARY_COLUMNS = (
    "id, prompt, trigger_source, request_id, success, started_at, completed_at, duration_ms, "
    "model, complexity, input_tokens, output_tokens"
)

_DETAIL_COLUMNS = (
    "id, prompt, trigger_source, result, tool_calls, duration_ms, trace_id, request_id, cost, "
    "started_at, completed_at, success, error, model, input_tokens, output_tokens, "
    "parent_session_id, complexity, resolution_source"
)


def _build_where(
    *,
    trigger_source: str | None = None,
    success: bool | None = None,
    from_date: datetime | None = None,
    to_date: datetime | None = None,
    request_id: str | None = None,
    start_idx: int = 1,
) -> tuple[str, list[object], int]:
    """Build a dynamic WHERE clause from the common session filter params.

    Returns (where_clause, args, next_param_idx).
    """
    conditions: list[str] = []
    args: list[object] = []
    idx = start_idx

    if trigger_source is not None:
        conditions.append(f"trigger_source = ${idx}")
        args.append(trigger_source)
        idx += 1

    if success is not None:
        conditions.append(f"success = ${idx}")
        args.append(success)
        idx += 1

    if from_date is not None:
        conditions.append(f"started_at >= ${idx}")
        args.append(from_date)
        idx += 1

    if to_date is not None:
        conditions.append(f"started_at <= ${idx}")
        args.append(to_date)
        idx += 1

    if request_id is not None:
        conditions.append(f"request_id = ${idx}")
        args.append(request_id)
        idx += 1

    where_clause = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    return where_clause, args, idx


def _row_to_summary(row, *, butler: str | None = None) -> SessionSummary:
    """Convert an asyncpg Record to a SessionSummary."""
    return SessionSummary(
        id=row["id"],
        butler=butler,
        prompt=row["prompt"],
        trigger_source=row["trigger_source"],
        request_id=row["request_id"],
        success=row["success"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
        duration_ms=row["duration_ms"],
        model=row["model"],
        complexity=row["complexity"],
        input_tokens=row["input_tokens"],
        output_tokens=row["output_tokens"],
    )


def _row_to_detail(row, *, butler: str | None = None) -> SessionDetail:
    """Convert an asyncpg Record to a SessionDetail."""
    # tool_calls and cost may be JSON strings or dicts depending on driver
    tool_calls = row["tool_calls"]
    if isinstance(tool_calls, str):
        tool_calls = json.loads(tool_calls)

    cost = row["cost"]
    if isinstance(cost, str):
        cost = json.loads(cost)

    return SessionDetail(
        id=row["id"],
        butler=butler,
        prompt=row["prompt"],
        trigger_source=row["trigger_source"],
        result=row["result"],
        tool_calls=tool_calls if tool_calls else [],
        duration_ms=row["duration_ms"],
        trace_id=row["trace_id"],
        request_id=row["request_id"],
        cost=cost,
        started_at=row["started_at"],
        completed_at=row["completed_at"],
        success=row["success"],
        error=row["error"],
        model=row["model"],
        input_tokens=row["input_tokens"],
        output_tokens=row["output_tokens"],
        parent_session_id=row["parent_session_id"],
        complexity=row["complexity"],
        resolution_source=row["resolution_source"],
    )


async def _attach_session_extras(detail: SessionDetail, pool, session_id: UUID) -> SessionDetail:
    """Attach best-effort process log and correction count to a SessionDetail.

    Both lookups are best-effort: the backing tables may not exist yet in
    every butler schema, so failures are logged at debug and swallowed. The
    same enrichment is shared by the butler-scoped and cross-butler by-id
    detail endpoints so both return an identical ``SessionDetail`` shape.
    """
    # Attach process log if available (best-effort — table may not exist yet)
    try:
        plog_row = await pool.fetchrow(
            """
            SELECT pid, exit_code, command, stderr, runtime_type,
                   retry_attempted, retry_succeeded, result_source, attempt_count,
                   created_at, expires_at
            FROM session_process_logs
            WHERE session_id = $1 AND expires_at >= now()
            """,
            session_id,
        )
        if plog_row is not None:
            detail.process_log = ProcessLog(**dict(plog_row))
    except Exception:
        logger.debug("Could not fetch process log for session %s", session_id, exc_info=True)

    # Attach correction count (best-effort — corrections table may not exist yet)
    try:
        correction_count = await pool.fetchval(
            "SELECT count(*) FROM corrections WHERE target_session_id = $1",
            session_id,
        )
        detail.correction_count = int(correction_count or 0)
    except Exception:
        logger.debug("Could not fetch correction count for session %s", session_id, exc_info=True)

    return detail


# ---------------------------------------------------------------------------
# Cross-butler endpoint: GET /api/sessions
# ---------------------------------------------------------------------------


@router.get("", response_model=PaginatedResponse[SessionSummary])
async def list_sessions(
    offset: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(50, ge=1, le=1000, description="Max records to return"),
    butler: str | None = Query(None, description="Filter by butler name"),
    trigger_source: str | None = Query(None, description="Filter by trigger source"),
    success: bool | None = Query(None, description="Filter by success status"),
    from_date: datetime | None = Query(None, description="Sessions started after this time"),
    to_date: datetime | None = Query(None, description="Sessions started before this time"),
    request_id: str | None = Query(None, description="Filter by request_id"),
    db: DatabaseManager = Depends(_get_db_manager),
) -> PaginatedResponse[SessionSummary]:
    """Return paginated sessions aggregated across all butler databases.

    Uses ``DatabaseManager.fan_out()`` to query every registered butler DB
    concurrently, then merges, sorts, and paginates the combined results.
    When the ``butler`` query parameter is provided, only that butler's DB
    is queried.
    """
    where_clause, args, idx = _build_where(
        trigger_source=trigger_source,
        success=success,
        from_date=from_date,
        to_date=to_date,
        request_id=request_id,
    )

    # Fan-out query across all (or filtered) butler DBs
    count_sql = f"SELECT count(*) FROM sessions{where_clause}"
    data_sql = f"SELECT {_SUMMARY_COLUMNS} FROM sessions{where_clause} ORDER BY started_at DESC"

    target_butlers = [butler] if butler else None

    # Run count and data queries across butlers
    count_results = await db.fan_out(count_sql, tuple(args), butler_names=target_butlers)
    data_results = await db.fan_out(data_sql, tuple(args), butler_names=target_butlers)

    # Aggregate totals
    total = sum(rows[0][0] if rows else 0 for rows in count_results.values())

    # Merge all rows with butler name attached, sort by started_at DESC
    all_sessions: list[SessionSummary] = []
    for butler_name, rows in data_results.items():
        for row in rows:
            all_sessions.append(_row_to_summary(row, butler=butler_name))

    all_sessions.sort(key=lambda s: s.started_at, reverse=True)

    # Apply pagination on the merged result
    page = all_sessions[offset : offset + limit]

    return PaginatedResponse[SessionSummary](
        data=page,
        meta=PaginationMeta(total=total, offset=offset, limit=limit),
    )


# ---------------------------------------------------------------------------
# Cross-butler detail: GET /api/sessions/{session_id}
# ---------------------------------------------------------------------------


@router.get("/{session_id}", response_model=ApiResponse[SessionDetail])
async def get_session(
    session_id: UUID,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[SessionDetail]:
    """Return full detail for a single session, resolving it across butlers.

    Session ids are globally unique UUIDs but live in per-butler schemas, so
    this endpoint fans out the detail lookup across every registered butler DB
    via ``DatabaseManager.fan_out()`` and returns the first (and only) match.
    The response is the same ``SessionDetail`` shape produced by the
    butler-scoped ``GET /api/butlers/{name}/sessions/{session_id}`` path,
    including best-effort process log and correction count.
    """
    detail_results = await db.fan_out(
        f"SELECT {_DETAIL_COLUMNS} FROM sessions WHERE id = $1",
        (session_id,),
    )

    owning_butler: str | None = None
    row = None
    for butler_name, rows in detail_results.items():
        if rows:
            owning_butler = butler_name
            row = rows[0]
            break

    if row is None or owning_butler is None:
        raise HTTPException(status_code=404, detail="Session not found")

    detail = _row_to_detail(row, butler=owning_butler)
    await _attach_session_extras(detail, db.pool(owning_butler), session_id)

    return ApiResponse[SessionDetail](data=detail)


# ---------------------------------------------------------------------------
# Butler-scoped list: GET /api/butlers/{name}/sessions
# ---------------------------------------------------------------------------


@butler_sessions_router.get(
    "/{name}/sessions",
    response_model=PaginatedResponse[SessionSummary],
)
async def list_butler_sessions(
    name: str,
    offset: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(50, ge=1, le=1000, description="Max records to return"),
    trigger_source: str | None = Query(None, description="Filter by trigger source"),
    success: bool | None = Query(None, description="Filter by success status"),
    from_date: datetime | None = Query(None, description="Sessions started after this time"),
    to_date: datetime | None = Query(None, description="Sessions started before this time"),
    request_id: str | None = Query(None, description="Filter by request_id"),
    db: DatabaseManager = Depends(_get_db_manager),
) -> PaginatedResponse[SessionSummary]:
    """Return paginated sessions for a single butler.

    Queries the butler's database directly via ``DatabaseManager.pool()``.
    """
    try:
        pool = db.pool(name)
    except KeyError:
        raise HTTPException(
            status_code=503,
            detail=f"Butler '{name}' database is not available",
        )

    where_clause, args, idx = _build_where(
        trigger_source=trigger_source,
        success=success,
        from_date=from_date,
        to_date=to_date,
        request_id=request_id,
    )

    # Count query
    count_sql = f"SELECT count(*) FROM sessions{where_clause}"
    total = await pool.fetchval(count_sql, *args) or 0

    # Data query
    data_sql = (
        f"SELECT {_SUMMARY_COLUMNS} FROM sessions{where_clause} "
        f"ORDER BY started_at DESC "
        f"OFFSET ${idx} LIMIT ${idx + 1}"
    )
    args.extend([offset, limit])

    rows = await pool.fetch(data_sql, *args)

    sessions = [_row_to_summary(row, butler=name) for row in rows]

    return PaginatedResponse[SessionSummary](
        data=sessions,
        meta=PaginationMeta(total=total, offset=offset, limit=limit),
    )


# ---------------------------------------------------------------------------
# Butler-scoped detail: GET /api/butlers/{name}/sessions/{session_id}
# ---------------------------------------------------------------------------


@butler_sessions_router.get(
    "/{name}/sessions/{session_id}",
    response_model=ApiResponse[SessionDetail],
)
async def get_butler_session(
    name: str,
    session_id: UUID,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[SessionDetail]:
    """Return full detail for a single session from a butler's database."""
    try:
        pool = db.pool(name)
    except KeyError:
        raise HTTPException(
            status_code=503,
            detail=f"Butler '{name}' database is not available",
        )

    row = await pool.fetchrow(
        f"SELECT {_DETAIL_COLUMNS} FROM sessions WHERE id = $1",
        session_id,
    )

    if row is None:
        raise HTTPException(status_code=404, detail="Session not found")

    detail = _row_to_detail(row, butler=name)
    await _attach_session_extras(detail, pool, session_id)

    return ApiResponse[SessionDetail](data=detail)


# ---------------------------------------------------------------------------
# Butler-scoped analytics: GET /api/butlers/{name}/analytics/session-kinds
# ---------------------------------------------------------------------------

_SESSION_KINDS_SQL = """
SELECT trigger_source, COUNT(*) AS count
FROM sessions
WHERE started_at >= NOW() - ($1 * INTERVAL '1 day')
GROUP BY trigger_source
"""


@butler_sessions_router.get(
    "/{name}/analytics/session-kinds",
    response_model=ApiResponse[SessionKindBreakdown],
)
async def get_butler_session_kinds(
    name: str,
    window_days: int = Query(7, ge=0, description="Rolling window in days (default 7)"),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[SessionKindBreakdown]:
    """Return session counts grouped by trigger_source for a rolling window.

    Queries the butler's ``sessions`` table grouped by ``trigger_source``
    over the last ``window_days`` days.  Returns whatever trigger_source
    values exist — the spec does not prescribe a fixed set.

    When no sessions exist in the window, returns an empty ``kinds`` list.
    """
    try:
        pool = db.pool(name)
    except KeyError:
        raise HTTPException(
            status_code=503,
            detail=f"Butler '{name}' database is not available",
        )

    rows = await pool.fetch(_SESSION_KINDS_SQL, window_days)

    kinds = [SessionKindItem(kind=row["trigger_source"], count=int(row["count"])) for row in rows]

    return ApiResponse[SessionKindBreakdown](data=SessionKindBreakdown(kinds=kinds))


# ---------------------------------------------------------------------------
# Butler-scoped analytics: GET /api/butlers/{name}/analytics/daily-activity
# ---------------------------------------------------------------------------

_DAILY_ACTIVITY_SQL = """
SELECT DATE(started_at) AS d, COUNT(*) AS sessions_count
FROM sessions
WHERE started_at >= CURRENT_DATE - ($1 * INTERVAL '1 day')
GROUP BY d
ORDER BY d
"""

_VALID_WINDOW_DAYS = {7, 30}


@butler_sessions_router.get(
    "/{name}/analytics/daily-activity",
    response_model=ApiResponse[DailyActivity],
)
async def get_butler_daily_activity(
    name: str,
    window_days: int = Query(7, description="Rolling window in days; must be 7 or 30"),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[DailyActivity]:
    """Return daily session counts for a butler over a rolling 7- or 30-day window.

    Queries the butler's ``sessions`` table and groups rows by calendar date.
    Returns one ``DailyActivityBucket`` per day that had at least one session.
    Days with no sessions are omitted; an empty window yields ``buckets: []``.

    ``window_days`` must be exactly 7 or 30; other values are rejected with 422.
    """
    if window_days not in _VALID_WINDOW_DAYS:
        raise HTTPException(
            status_code=422,
            detail=f"window_days must be one of {sorted(_VALID_WINDOW_DAYS)}, got {window_days}",
        )

    try:
        pool = db.pool(name)
    except KeyError:
        raise HTTPException(
            status_code=503,
            detail=f"Butler '{name}' database is not available",
        )

    rows = await pool.fetch(_DAILY_ACTIVITY_SQL, window_days)

    buckets = [
        DailyActivityBucket(date=row["d"], sessions_count=row["sessions_count"]) for row in rows
    ]

    return ApiResponse[DailyActivity](data=DailyActivity(buckets=buckets))


# ---------------------------------------------------------------------------
# Butler-scoped analytics: GET /api/butlers/{name}/analytics/hourly-activity
# ---------------------------------------------------------------------------

_HOURLY_ACTIVITY_SQL = """
WITH hours AS (
  SELECT generate_series(
    DATE_TRUNC('hour', NOW()) - (($1 - 1) * INTERVAL '1 hour'),
    DATE_TRUNC('hour', NOW()),
    '1 hour'
  ) AS hour_start
)
SELECT
  h.hour_start,
  COUNT(s.id) AS sessions_count
FROM hours h
LEFT JOIN sessions s ON s.started_at >= h.hour_start
                    AND s.started_at < h.hour_start + INTERVAL '1 hour'
GROUP BY 1
ORDER BY 1 DESC
"""


@butler_sessions_router.get(
    "/{name}/analytics/hourly-activity",
    response_model=ApiResponse[HourlyActivity],
)
async def get_butler_hourly_activity(
    name: str,
    window_hours: int = Query(24, ge=1, le=24, description="Rolling window in hours (default 24)"),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[HourlyActivity]:
    """Return hourly session counts for a butler over a rolling window.

    Queries the butler's ``sessions`` table and returns a dense series of
    ``HourlyActivityBucket`` entries covering the last ``window_hours`` clock
    hours.  Every hour in the window is always present — zero-count hours are
    included via ``generate_series`` + LEFT JOIN.  ``hour_index=0`` is the
    current (most recent) hour; the SQL orders newest-first so the index equals
    the enumeration position directly.

    Returns 503 when the butler's DB pool is not registered.
    """
    try:
        pool = db.pool(name)
    except KeyError:
        raise HTTPException(
            status_code=503,
            detail=f"Butler '{name}' database is not available",
        )

    rows = await pool.fetch(_HOURLY_ACTIVITY_SQL, window_hours)

    buckets = [
        HourlyActivityBucket(
            hour_start=row["hour_start"],
            sessions_count=int(row["sessions_count"]),
            hour_index=idx,
        )
        for idx, row in enumerate(rows)
    ]

    return ApiResponse[HourlyActivity](data=HourlyActivity(buckets=buckets))


# ---------------------------------------------------------------------------
# Butler-scoped analytics: GET /api/butlers/{name}/analytics/latency-stats
# ---------------------------------------------------------------------------

_LATENCY_STATS_SQL = """
SELECT
    percentile_cont(0.5) WITHIN GROUP (ORDER BY duration_ms) AS p50_ms,
    percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_ms) AS p95_ms,
    AVG(duration_ms) AS mean_ms,
    COUNT(*) AS count,
    mode() WITHIN GROUP (ORDER BY model) AS model
FROM sessions
WHERE started_at >= NOW() - ($1 * INTERVAL '1 day')
  AND duration_ms IS NOT NULL
"""


@butler_sessions_router.get(
    "/{name}/analytics/latency-stats",
    response_model=ApiResponse[LatencyStats],
)
async def get_butler_latency_stats(
    name: str,
    window_days: int = Query(7, ge=1, le=365, description="Rolling window in days (default 7)"),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[LatencyStats]:
    """Return latency percentile statistics for a butler over a rolling window.

    Queries the butler's ``sessions`` table for rows with a recorded
    ``duration_ms`` within the last ``window_days`` days and returns p50, p95,
    mean, count, and the most-frequently-used model.

    When no matching sessions exist, returns ``count=0`` and ``None`` for all
    duration fields.
    """
    try:
        pool = db.pool(name)
    except KeyError:
        raise HTTPException(
            status_code=503,
            detail=f"Butler '{name}' database is not available",
        )

    row = await pool.fetchrow(_LATENCY_STATS_SQL, window_days)

    if row is None or row["count"] == 0:
        return ApiResponse[LatencyStats](data=LatencyStats())

    p50 = row["p50_ms"]
    p95 = row["p95_ms"]
    mean = row["mean_ms"]

    return ApiResponse[LatencyStats](
        data=LatencyStats(
            p50_ms=float(p50) if p50 is not None else None,
            p95_ms=float(p95) if p95 is not None else None,
            mean_ms=float(mean) if mean is not None else None,
            count=int(row["count"]),
            model=row["model"],
        )
    )
