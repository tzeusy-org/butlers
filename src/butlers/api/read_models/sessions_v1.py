"""Sessions read-model v1 — versioned read boundary for the sessions domain.

Centralises the SQL column projections and fan-out query functions so the
dashboard API's sessions router depends on this typed DTO contract rather than
ad-hoc SQL strings.  A breaking schema change (new required column, renamed
column, type change) should produce a new ``sessions_v2`` module rather than
silently altering this one.

Public surface
--------------
Column constants:
    SUMMARY_COLUMNS
    DETAIL_COLUMNS

Query functions (all async):
    query_session_summaries_fan_out(db, where, args, butler_names) -> FanOutSummaryResult
    query_session_detail_fan_out(db, session_id) -> FanOutDetailResult
    query_session_detail_single(pool, session_id) -> SingleDetailResult

Row-to-DTO converters:
    row_to_summary(row, butler) -> SessionSummaryRow
    row_to_detail(row, butler) -> SessionDetailRow
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg

from butlers.api.db import DatabaseManager

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Version marker
# ---------------------------------------------------------------------------

#: Stability contract — bump to ``sessions_v2`` for breaking changes.
READ_MODEL_VERSION = "sessions_v1"

# ---------------------------------------------------------------------------
# Column projections (v1 schema contract)
# ---------------------------------------------------------------------------

#: Columns returned for list / summary views.  Changing this list is a
#: breaking change — create ``sessions_v2`` instead of editing here.
SUMMARY_COLUMNS: str = (
    "id, prompt, trigger_source, request_id, success, started_at, completed_at, duration_ms, "
    "model, complexity, input_tokens, output_tokens"
)

#: Columns returned for single-session detail views.  Same versioning rule.
DETAIL_COLUMNS: str = (
    "id, prompt, trigger_source, result, tool_calls, duration_ms, trace_id, request_id, cost, "
    "started_at, completed_at, success, error, model, input_tokens, output_tokens, "
    "parent_session_id, complexity, resolution_source"
)

# ---------------------------------------------------------------------------
# Typed row DTOs
# ---------------------------------------------------------------------------


@dataclass
class SessionSummaryRow:
    """Typed DTO for a sessions list/summary row (v1 contract)."""

    id: UUID
    butler: str | None
    prompt: str | None
    trigger_source: str | None
    request_id: str | None
    success: bool | None
    started_at: datetime
    completed_at: datetime | None
    duration_ms: int | None
    model: str | None
    complexity: str | None
    input_tokens: int | None
    output_tokens: int | None


@dataclass
class SessionDetailRow:
    """Typed DTO for a full session detail row (v1 contract)."""

    id: UUID
    butler: str | None
    prompt: str | None
    trigger_source: str | None
    result: str | None
    tool_calls: list[Any]
    duration_ms: int | None
    trace_id: str | None
    request_id: str | None
    cost: dict[str, Any] | None
    started_at: datetime
    completed_at: datetime | None
    success: bool | None
    error: str | None
    model: str | None
    input_tokens: int | None
    output_tokens: int | None
    parent_session_id: UUID | None
    complexity: str | None
    resolution_source: str | None


# ---------------------------------------------------------------------------
# Fan-out result containers
# ---------------------------------------------------------------------------


@dataclass
class FanOutSummaryResult:
    """Result of a cross-butler session summary fan-out."""

    total: int
    rows: list[SessionSummaryRow] = field(default_factory=list)


@dataclass
class FanOutDetailResult:
    """Result of a cross-butler session detail fan-out (first match wins)."""

    row: SessionDetailRow | None = None
    butler: str | None = None


@dataclass
class SingleDetailResult:
    """Result of a single-butler session detail lookup."""

    row: SessionDetailRow | None = None

    @property
    def found(self) -> bool:
        return self.row is not None


# ---------------------------------------------------------------------------
# Row converters
# ---------------------------------------------------------------------------


def row_to_summary(row: asyncpg.Record, *, butler: str | None = None) -> SessionSummaryRow:
    """Convert an asyncpg Record to a :class:`SessionSummaryRow`.

    This is the single place that knows the column names from :data:`SUMMARY_COLUMNS`.
    """
    return SessionSummaryRow(
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


def row_to_detail(row: asyncpg.Record, *, butler: str | None = None) -> SessionDetailRow:
    """Convert an asyncpg Record to a :class:`SessionDetailRow`.

    This is the single place that knows the column names from :data:`DETAIL_COLUMNS`.
    Handles JSON coercion for ``tool_calls`` and ``cost`` which the asyncpg
    driver may return as either strings or parsed objects.
    """
    tool_calls = row["tool_calls"]
    if isinstance(tool_calls, str):
        tool_calls = json.loads(tool_calls)

    cost = row["cost"]
    if isinstance(cost, str):
        cost = json.loads(cost)

    return SessionDetailRow(
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


# ---------------------------------------------------------------------------
# Query functions
# ---------------------------------------------------------------------------


async def query_session_summaries_fan_out(
    db: DatabaseManager,
    where_clause: str,
    args: tuple[Any, ...],
    *,
    butler_names: list[str] | None = None,
) -> FanOutSummaryResult:
    """Fan out session summary queries across all (or a subset of) butlers.

    Parameters
    ----------
    db:
        The DatabaseManager that manages per-butler pools.
    where_clause:
        A SQL WHERE clause fragment (including the leading ``WHERE`` keyword,
        or an empty string for no filter).  Must use positional placeholders
        starting at ``$1`` matching the supplied *args*.
    args:
        Positional arguments for the WHERE clause parameters.
    butler_names:
        Subset of butler names to query.  Defaults to all registered butlers.

    Returns
    -------
    FanOutSummaryResult
        Aggregated total row count and unordered list of typed summary DTOs
        from all queried butlers.  Rows are *not* sorted — callers must sort
        as needed.
    """
    count_sql = f"SELECT count(*) FROM sessions{where_clause}"
    data_sql = f"SELECT {SUMMARY_COLUMNS} FROM sessions{where_clause} ORDER BY started_at DESC"

    count_results = await db.fan_out(count_sql, args, butler_names=butler_names)
    data_results = await db.fan_out(data_sql, args, butler_names=butler_names)

    total = sum(rows[0][0] if rows else 0 for rows in count_results.values())

    rows: list[SessionSummaryRow] = []
    for butler_name, db_rows in data_results.items():
        for db_row in db_rows:
            rows.append(row_to_summary(db_row, butler=butler_name))

    return FanOutSummaryResult(total=total, rows=rows)


async def query_session_detail_fan_out(
    db: DatabaseManager,
    session_id: UUID,
) -> FanOutDetailResult:
    """Fan out a session detail lookup across all registered butlers.

    Session IDs are globally unique UUIDs but live in per-butler schemas,
    so we query every butler and return the first match.

    Parameters
    ----------
    db:
        The DatabaseManager that manages per-butler pools.
    session_id:
        UUID of the session to fetch.

    Returns
    -------
    FanOutDetailResult
        The matched :class:`SessionDetailRow` and the owning butler name, or
        ``row=None`` if not found in any butler.
    """
    results = await db.fan_out(
        f"SELECT {DETAIL_COLUMNS} FROM sessions WHERE id = $1",
        (session_id,),
    )

    for butler_name, db_rows in results.items():
        if db_rows:
            return FanOutDetailResult(
                row=row_to_detail(db_rows[0], butler=butler_name),
                butler=butler_name,
            )

    return FanOutDetailResult()


async def query_session_detail_single(
    pool: asyncpg.Pool,
    session_id: UUID,
    *,
    butler: str | None = None,
) -> SingleDetailResult:
    """Fetch a single session detail from a specific butler pool.

    Parameters
    ----------
    pool:
        The asyncpg pool for a specific butler.
    session_id:
        UUID of the session to fetch.
    butler:
        Optional butler name to attach to the DTO (for cross-butler callers).

    Returns
    -------
    SingleDetailResult
        The matched :class:`SessionDetailRow`, or ``row=None`` if not found.
    """
    db_row = await pool.fetchrow(
        f"SELECT {DETAIL_COLUMNS} FROM sessions WHERE id = $1",
        session_id,
    )

    if db_row is None:
        return SingleDetailResult()

    return SingleDetailResult(row=row_to_detail(db_row, butler=butler))
