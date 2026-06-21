"""Session history endpoints — paginated, filterable session log.

Provides two routers:

- ``router`` — cross-butler endpoints:

  - ``GET /api/sessions``
  - ``GET /api/sessions/{session_id}``

- ``butler_sessions_router`` — butler-scoped endpoints:

  - ``GET /api/butlers/{name}/sessions``
  - ``GET /api/butlers/{name}/sessions/{session_id}``
  - ``GET /api/butlers/{name}/analytics/latency-stats``

Cross-butler reads (list + detail fan-outs) go through the versioned read-model
boundary in ``butlers.api.read_models.sessions_v1`` rather than constructing
ad-hoc SQL inline.  Butler-scoped single-pool queries also route through the
same module's ``query_session_detail_single``.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from butlers.api.db import DatabaseManager
from butlers.api.models import (
    ApiResponse,
    KeysetMeta,
    KeysetResponse,
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
    SessionAggregate,
    SessionAggregateButler,
    SessionDetail,
    SessionKindBreakdown,
    SessionKindItem,
)
from butlers.api.read_models.sessions_v1 import (
    SUMMARY_COLUMNS,
    SessionDetailRow,
    SessionSummaryRow,
    decode_session_cursor,
    query_session_aggregate_fan_out,
    query_session_detail_fan_out,
    query_session_detail_single,
    query_session_summaries_keyset_fan_out,
    row_to_summary,
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


def _build_where(
    *,
    trigger_source: str | None = None,
    success: bool | None = None,
    running: bool = False,
    from_date: datetime | None = None,
    to_date: datetime | None = None,
    request_id: str | None = None,
    start_idx: int = 1,
) -> tuple[str, list[object], int]:
    """Build a dynamic WHERE clause from the common session filter params.

    ``running`` is the ``status=running`` surface: it adds a ``success IS NULL``
    predicate (no bound arg).  It is mutually exclusive with ``success`` in
    practice — the resolver returns ``success=None`` for ``status=running``.

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

    if running:
        conditions.append("success IS NULL")

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


def _resolve_success_filter(
    status: str | None,
    success: bool | None,
) -> bool | None:
    """Resolve the effective ``success`` boolean filter from the two params.

    The frontend status dropdown sends ``?status=success|failed|running`` (and
    omits the param entirely for "all"). ``status`` is mapped to the ``success``
    boolean:

    - ``status=success`` -> ``success=True``
    - ``status=failed``  -> ``success=False``
    - ``status=running`` -> ``success=None`` (the ``success IS NULL`` predicate is
      applied separately via the ``running`` flag, so the legacy bool must not leak)
    - ``status`` absent / ``all`` -> fall through to the legacy ``success`` bool.

    ``status`` takes precedence over the legacy ``success`` bool param when both
    are present, so the two never conflict.
    """
    if status == "success":
        return True
    if status == "failed":
        return False
    if status == "running":
        return None
    # status is None or "all" -> preserve backward-compatible success filtering
    return success


def _dto_to_summary(dto: SessionSummaryRow) -> SessionSummary:
    """Convert a SessionSummaryRow DTO (sessions_v1) to a response model."""
    return SessionSummary(
        id=dto.id,
        butler=dto.butler,
        prompt=dto.prompt,
        trigger_source=dto.trigger_source,
        request_id=dto.request_id,
        success=dto.success,
        started_at=dto.started_at,
        completed_at=dto.completed_at,
        duration_ms=dto.duration_ms,
        model=dto.model,
        complexity=dto.complexity,
        input_tokens=dto.input_tokens,
        output_tokens=dto.output_tokens,
    )


def _dto_to_detail(dto: SessionDetailRow) -> SessionDetail:
    """Convert a SessionDetailRow DTO (sessions_v1) to a response model."""
    return SessionDetail(
        id=dto.id,
        butler=dto.butler,
        prompt=dto.prompt,
        trigger_source=dto.trigger_source,
        result=dto.result,
        tool_calls=dto.tool_calls,
        duration_ms=dto.duration_ms,
        trace_id=dto.trace_id,
        request_id=dto.request_id,
        cost=dto.cost,
        started_at=dto.started_at,
        completed_at=dto.completed_at,
        success=dto.success,
        error=dto.error,
        model=dto.model,
        input_tokens=dto.input_tokens,
        output_tokens=dto.output_tokens,
        parent_session_id=dto.parent_session_id,
        complexity=dto.complexity,
        resolution_source=dto.resolution_source,
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


# Query-budget: keyset (cursor) pagination — each butler fetches at most
# limit+1 rows after the cursor position, ordered (started_at DESC, id DESC),
# index-backed by ix_sessions_started_at (core_128).  NO count(*) is run (that
# is the perf win over the prior offset/total path).  Combined cost is
# O((limit+1) * butler_count) rows materialized, fanned out concurrently, then
# merge-sorted in memory and truncated to limit.
@router.get("", response_model=KeysetResponse[SessionSummary])
async def list_sessions(
    limit: int = Query(50, ge=1, le=1000, description="Max records to return"),
    cursor: str | None = Query(
        None,
        description=(
            "Opaque cursor from the previous page's ``next_cursor`` field. "
            "Omit to fetch the first page."
        ),
    ),
    butler: str | None = Query(None, description="Filter by butler name"),
    trigger_source: str | None = Query(None, description="Filter by trigger source"),
    status: Literal["all", "success", "failed", "running"] | None = Query(
        None,
        description=(
            "Filter by session outcome: 'success', 'failed', 'running' "
            "(success IS NULL), or 'all' (no filter)"
        ),
    ),
    success: bool | None = Query(
        None,
        description="Legacy success filter (bool). Superseded by 'status' when both are set.",
    ),
    from_date: datetime | None = Query(None, description="Sessions started after this time"),
    to_date: datetime | None = Query(None, description="Sessions started before this time"),
    request_id: str | None = Query(None, description="Filter by request_id"),
    db: DatabaseManager = Depends(_get_db_manager),
) -> KeysetResponse[SessionSummary]:
    """Return keyset-paginated sessions aggregated across all butler databases.

    Uses ``DatabaseManager.fan_out()`` to query every registered butler DB
    concurrently for ``limit + 1`` rows after the cursor position, then merges,
    sorts ``(started_at DESC, id DESC)``, and truncates to ``limit``.  When the
    ``butler`` query parameter is provided, only that butler's DB is queried.

    No total count is computed — pagination is forward-only via the opaque
    ``next_cursor``.  ``has_more`` is true when more rows exist beyond the page.

    The ``status`` param (``success`` | ``failed`` | ``running`` | ``all``) is
    the surface the frontend status dropdown uses; ``running`` maps onto
    ``success IS NULL`` and ``success``/``failed`` onto the ``success`` boolean
    filter (taking precedence over the legacy ``success`` bool param).
    """
    # Validate the cursor early so a malformed value is a clean 422.
    if cursor is not None:
        try:
            decode_session_cursor(cursor)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"Invalid cursor: {exc}") from exc

    # Keyset path indexes cursor params inside the read-model; the param index is unused here.
    where_clause, args, _ = _build_where(
        trigger_source=trigger_source,
        success=_resolve_success_filter(status, success),
        running=status == "running",
        from_date=from_date,
        to_date=to_date,
        request_id=request_id,
    )

    target_butlers = [butler] if butler else None

    # Fan out via the versioned sessions read-model boundary (sessions_v1)
    result = await query_session_summaries_keyset_fan_out(
        db,
        where_clause,
        tuple(args),
        limit=limit,
        cursor=cursor,
        butler_names=target_butlers,
    )

    return KeysetResponse[SessionSummary](
        data=[_dto_to_summary(dto) for dto in result.rows],
        meta=KeysetMeta(
            limit=limit,
            next_cursor=result.next_cursor,
            has_more=result.has_more,
        ),
    )


# ---------------------------------------------------------------------------
# Cross-butler aggregate: GET /api/sessions/aggregate
# ---------------------------------------------------------------------------


# Query-budget: one filter-aware aggregate scan per butler (count + three
# FILTERed counts + two coalesced token sums), fanned out concurrently and
# summed in memory.  No row materialization, no pagination.  Index-backed time
# range via ix_sessions_started_at (core_128).  Powers the window-true KPI
# strip; recomputed on filter change only (not on paging).
@router.get("/aggregate", response_model=ApiResponse[SessionAggregate])
async def get_session_aggregate(
    butler: str | None = Query(None, description="Filter by butler name"),
    trigger_source: str | None = Query(None, description="Filter by trigger source"),
    status: Literal["all", "success", "failed", "running"] | None = Query(
        None,
        description=(
            "Filter by session outcome: 'success', 'failed', 'running' "
            "(success IS NULL), or 'all' (no filter)"
        ),
    ),
    success: bool | None = Query(
        None,
        description="Legacy success filter (bool). Superseded by 'status' when both are set.",
    ),
    from_date: datetime | None = Query(None, description="Sessions started after this time"),
    to_date: datetime | None = Query(None, description="Sessions started before this time"),
    request_id: str | None = Query(None, description="Filter by request_id"),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[SessionAggregate]:
    """Return a filter-aware, window-true session rollup across all butlers.

    Shares the exact filter params and semantics of ``GET /api/sessions`` (minus
    pagination), so the KPI strip reflects the same matching set the list shows.
    Counts every matching session across the queried butlers — not just the
    fetched page — and never derives a window number from page rows.

    ``success_rate`` is ``success_count / (success_count + failed_count)`` or
    ``null`` when no completed sessions exist.  Cost is intentionally omitted.
    """
    # Aggregate path binds no extra params beyond the WHERE clause; param index is unused here.
    where_clause, args, _ = _build_where(
        trigger_source=trigger_source,
        success=_resolve_success_filter(status, success),
        running=status == "running",
        from_date=from_date,
        to_date=to_date,
        request_id=request_id,
    )

    target_butlers = [butler] if butler else None

    result = await query_session_aggregate_fan_out(
        db, where_clause, tuple(args), butler_names=target_butlers
    )

    rated = result.success_count + result.failed_count
    success_rate = (result.success_count / rated) if rated > 0 else None

    return ApiResponse[SessionAggregate](
        data=SessionAggregate(
            total=result.total,
            success_count=result.success_count,
            failed_count=result.failed_count,
            running_count=result.running_count,
            success_rate=success_rate,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            by_butler=[
                SessionAggregateButler(butler=b.butler, count=b.count) for b in result.by_butler
            ],
        )
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
    # Fan out via the versioned sessions read-model boundary (sessions_v1)
    fan_out_result = await query_session_detail_fan_out(db, session_id)

    if fan_out_result.row is None or fan_out_result.butler is None:
        raise HTTPException(status_code=404, detail="Session not found")

    detail = _dto_to_detail(fan_out_result.row)
    await _attach_session_extras(detail, db.pool(fan_out_result.butler), session_id)

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
    status: Literal["all", "success", "failed"] | None = Query(
        None,
        description="Filter by session outcome: 'success', 'failed', or 'all' (no filter)",
    ),
    success: bool | None = Query(
        None,
        description="Legacy success filter (bool). Superseded by 'status' when both are set.",
    ),
    from_date: datetime | None = Query(None, description="Sessions started after this time"),
    to_date: datetime | None = Query(None, description="Sessions started before this time"),
    request_id: str | None = Query(None, description="Filter by request_id"),
    db: DatabaseManager = Depends(_get_db_manager),
) -> PaginatedResponse[SessionSummary]:
    """Return paginated sessions for a single butler.

    Queries the butler's database directly via ``DatabaseManager.pool()``.

    The ``status`` param (``success`` | ``failed`` | ``all``) maps onto the
    ``success`` boolean filter and takes precedence over the legacy ``success``
    bool param.
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
        success=_resolve_success_filter(status, success),
        from_date=from_date,
        to_date=to_date,
        request_id=request_id,
    )

    # Count query
    count_sql = f"SELECT count(*) FROM sessions{where_clause}"
    total = await pool.fetchval(count_sql, *args) or 0

    # Data query — columns from the versioned sessions read-model (sessions_v1)
    data_sql = (
        f"SELECT {SUMMARY_COLUMNS} FROM sessions{where_clause} "
        f"ORDER BY started_at DESC "
        f"OFFSET ${idx} LIMIT ${idx + 1}"
    )
    args.extend([offset, limit])

    rows = await pool.fetch(data_sql, *args)

    sessions = [_dto_to_summary(row_to_summary(row, butler=name)) for row in rows]

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

    # Route through the versioned sessions read-model boundary (sessions_v1)
    single_result = await query_session_detail_single(pool, session_id, butler=name)

    if single_result.row is None:
        raise HTTPException(status_code=404, detail="Session not found")

    detail = _dto_to_detail(single_result.row)
    await _attach_session_extras(detail, pool, session_id)

    return ApiResponse[SessionDetail](data=detail)


# ---------------------------------------------------------------------------
# Butler-scoped analytics: GET /api/butlers/{name}/analytics/session-kinds
# ---------------------------------------------------------------------------

# Query-budget: O(rows_in_window) per butler.  Index ix_sessions_started_at
# (core_128) provides a range-scan entry point; the GROUP BY aggregate runs
# over the filtered set only.  Default window=7 days; caller-supplied max is
# unbounded (ge=0) — a very large window_days on a prolific butler can pull
# many rows.  Acceptable at current session volumes; monitor if p95 > 200 ms.
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

# Query-budget: window_days is validated to {7, 30} before this query runs
# (see _VALID_WINDOW_DAYS guard below).  With ix_sessions_started_at (core_128)
# the range filter is index-backed; result set is at most 30 rows (one per day).
# Budget: O(sessions_in_window) scan → O(30) GROUP BY buckets.  Bounded and safe.
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

# Query-budget: percentile_cont and mode() are O(N log N) aggregates over the
# filtered window.  With ix_sessions_started_at (core_128) the range predicate
# is index-backed.  window_days is capped at 365 (le=365 in the Query param).
# Worst case: 365 days × high-frequency butler → potentially 10k-100k rows in
# the aggregate.  At that scale expect 50-200 ms per call; this is acceptable
# for a dashboard detail view that fires once per page load, not on every render.
# If p95 exceeds 500 ms on a production butler, consider caching the result for
# 60 s or restricting the max window to 90 days.
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
