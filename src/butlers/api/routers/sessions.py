"""Session history endpoints — paginated, filterable session log.

Provides three routers:

- ``router`` — cross-butler endpoint at ``GET /api/sessions``
- ``butler_sessions_router`` — butler-scoped list at
  ``GET /api/butlers/{name}/sessions``
- ``butler_sessions_router`` — butler-scoped detail at
  ``GET /api/butlers/{name}/sessions/{session_id}``
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
from butlers.api.models.session import SessionDetail

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/sessions", tags=["sessions"])
butler_sessions_router = APIRouter(prefix="/api/butlers", tags=["butlers", "sessions"])


def _get_db_manager() -> DatabaseManager:
    """Dependency stub — overridden at app startup or in tests."""
    raise RuntimeError("DatabaseManager not initialized")


# ---------------------------------------------------------------------------
# Shared SQL builder
# ---------------------------------------------------------------------------

_SUMMARY_COLUMNS = "id, prompt, trigger_source, success, started_at, completed_at, duration_ms"

_DETAIL_COLUMNS = (
    "id, prompt, trigger_source, result, tool_calls, duration_ms, trace_id, cost, "
    "started_at, completed_at, success, error, model, input_tokens, output_tokens, "
    "parent_session_id"
)


def _build_where(
    *,
    trigger_source: str | None = None,
    success: bool | None = None,
    from_date: datetime | None = None,
    to_date: datetime | None = None,
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

    where_clause = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    return where_clause, args, idx


def _row_to_summary(row, *, butler: str | None = None) -> SessionSummary:
    """Convert an asyncpg Record to a SessionSummary."""
    return SessionSummary(
        id=row["id"],
        butler=butler,
        prompt=row["prompt"],
        trigger_source=row["trigger_source"],
        success=row["success"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
        duration_ms=row["duration_ms"],
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
        cost=cost,
        started_at=row["started_at"],
        completed_at=row["completed_at"],
        success=row["success"],
        error=row["error"],
        model=row["model"],
        input_tokens=row["input_tokens"],
        output_tokens=row["output_tokens"],
        parent_session_id=row["parent_session_id"],
    )


# ---------------------------------------------------------------------------
# Cross-butler endpoint: GET /api/sessions
# ---------------------------------------------------------------------------


@router.get("", response_model=PaginatedResponse[SessionSummary])
async def list_sessions(
    offset: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(50, ge=1, le=200, description="Max records to return"),
    butler: str | None = Query(None, description="Filter by butler name"),
    trigger_source: str | None = Query(None, description="Filter by trigger source"),
    success: bool | None = Query(None, description="Filter by success status"),
    from_date: datetime | None = Query(None, description="Sessions started after this time"),
    to_date: datetime | None = Query(None, description="Sessions started before this time"),
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
# Butler-scoped list: GET /api/butlers/{name}/sessions
# ---------------------------------------------------------------------------


@butler_sessions_router.get(
    "/{name}/sessions",
    response_model=PaginatedResponse[SessionSummary],
)
async def list_butler_sessions(
    name: str,
    offset: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(50, ge=1, le=200, description="Max records to return"),
    trigger_source: str | None = Query(None, description="Filter by trigger source"),
    success: bool | None = Query(None, description="Filter by success status"),
    from_date: datetime | None = Query(None, description="Sessions started after this time"),
    to_date: datetime | None = Query(None, description="Sessions started before this time"),
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

    return ApiResponse[SessionDetail](data=_row_to_detail(row, butler=name))
