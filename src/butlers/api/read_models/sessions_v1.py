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
    query_session_summaries_keyset_fan_out(db, where, args, *, limit, cursor, butler_names)
        -> FanOutKeysetResult
    query_session_aggregate_fan_out(db, where, args, *, butler_names) -> FanOutAggregateResult
    query_session_detail_fan_out(db, session_id) -> FanOutDetailResult
    query_session_detail_single(pool, session_id) -> SingleDetailResult

Cursor helpers:
    encode_session_cursor(started_at, row_id) -> str
    decode_session_cursor(cursor) -> (datetime, UUID)

Row-to-DTO converters:
    row_to_summary(row, butler) -> SessionSummaryRow
    row_to_detail(row, butler) -> SessionDetailRow
"""

from __future__ import annotations

import base64
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
class FanOutKeysetResult:
    """Result of a cross-butler keyset (cursor) session summary fan-out.

    ``rows`` is the merged page of at most ``limit`` summary DTOs, already
    sorted ``(started_at DESC, id DESC)``.  ``next_cursor`` encodes the keyset
    position of the last returned row when more rows exist, else ``None``.
    No ``total`` is computed — that is the keyset perf win.
    """

    rows: list[SessionSummaryRow] = field(default_factory=list)
    has_more: bool = False
    next_cursor: str | None = None


@dataclass
class ButlerCount:
    """A single butler's matching-session count (for aggregate ``by_butler``)."""

    butler: str
    count: int


@dataclass
class FanOutAggregateResult:
    """Combined cross-butler session aggregate (scalars summed across butlers).

    ``by_butler`` lists each butler's ``total`` (count > 0 only), sorted by
    count descending.  ``success_rate`` is intentionally NOT computed here —
    the router derives it (``success_count / (success_count + failed_count)``
    or ``None`` when the denominator is 0).
    """

    total: int = 0
    success_count: int = 0
    failed_count: int = 0
    running_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    by_butler: list[ButlerCount] = field(default_factory=list)


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


def encode_session_cursor(started_at: datetime, row_id: UUID | str) -> str:
    """Encode a keyset position into an opaque cursor string.

    The cursor encodes the ``(started_at, id)`` tuple of the last row returned.
    It is base64url-encoded JSON so it is safe to use as a query parameter.

    Parameters
    ----------
    started_at:
        Timestamp of the last row (``started_at`` column, tz-aware).
    row_id:
        Primary key (UUID) of the last row.
    """
    payload = {"t": started_at.isoformat(), "id": str(row_id)}
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()


def decode_session_cursor(cursor: str) -> tuple[datetime, UUID]:
    """Decode an opaque session cursor back to ``(started_at, id)``.

    Parameters
    ----------
    cursor:
        Opaque cursor string as returned by :func:`encode_session_cursor`.

    Returns
    -------
    tuple[datetime, UUID]
        The ``(started_at, id)`` keyset position.

    Raises
    ------
    ValueError
        If the cursor is malformed or cannot be decoded.
    """
    try:
        raw = base64.urlsafe_b64decode(cursor.encode())
        payload = json.loads(raw)
        started_at = datetime.fromisoformat(payload["t"])
        row_id = UUID(str(payload["id"]))
    except (KeyError, ValueError, TypeError, AttributeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid session cursor: {exc}") from exc
    return started_at, row_id


async def query_session_summaries_keyset_fan_out(
    db: DatabaseManager,
    where_clause: str,
    args: tuple[Any, ...],
    *,
    limit: int,
    cursor: str | None = None,
    butler_names: list[str] | None = None,
) -> FanOutKeysetResult:
    """Fan out a keyset (cursor) session summary query across butlers.

    Each per-butler query fetches ``limit + 1`` rows ordered
    ``(started_at DESC, id DESC)`` after the cursor position, so no
    ``count(*)`` is ever run.  Rows from all butlers are merged, re-sorted on
    the same key, and truncated to ``limit``.

    Cross-shard correctness: the globally (``limit + 1``)-th row is guaranteed
    to be within some single butler's ``limit + 1`` fetch, so fetching
    ``limit + 1`` per butler and merging yields an exact page boundary.

    Parameters
    ----------
    db:
        The DatabaseManager that manages per-butler pools.
    where_clause:
        A SQL WHERE clause fragment (including the leading ``WHERE`` keyword,
        or an empty string for no filter).  Must use positional placeholders
        ``$1..$N`` matching the supplied *args*.
    args:
        Positional arguments for the WHERE clause parameters.
    limit:
        Maximum number of rows to return in the merged page.
    cursor:
        Opaque cursor from a prior page's ``next_cursor``.  ``None`` fetches
        the first page.  Malformed cursors raise ``ValueError``.
    butler_names:
        Subset of butler names to query.  Defaults to all registered butlers.

    Returns
    -------
    FanOutKeysetResult
        The merged, sorted, truncated page plus ``has_more`` / ``next_cursor``.
    """
    keyset_clause = where_clause
    keyset_args: list[Any] = list(args)
    if cursor is not None:
        started_at, row_id = decode_session_cursor(cursor)
        idx = len(keyset_args) + 1
        predicate = f"(started_at, id) < (${idx}, ${idx + 1})"
        keyset_clause += (" AND " if keyset_clause else " WHERE ") + predicate
        keyset_args.extend([started_at, row_id])

    data_sql = (
        f"SELECT {SUMMARY_COLUMNS} FROM sessions{keyset_clause} "
        f"ORDER BY started_at DESC, id DESC LIMIT {limit + 1}"
    )

    data_results = await db.fan_out(data_sql, tuple(keyset_args), butler_names=butler_names)

    merged: list[SessionSummaryRow] = []
    for butler_name, db_rows in data_results.items():
        for db_row in db_rows:
            merged.append(row_to_summary(db_row, butler=butler_name))

    merged.sort(key=lambda s: (s.started_at, s.id), reverse=True)

    has_more = len(merged) > limit
    page = merged[:limit]

    next_cursor = (
        encode_session_cursor(page[-1].started_at, page[-1].id) if has_more and page else None
    )

    return FanOutKeysetResult(rows=page, has_more=has_more, next_cursor=next_cursor)


# Query-budget: one aggregate scan per butler over the filtered window — no row
# materialization, no count(*) of a paged set.  Each butler runs a single pass
# emitting six scalars (count + three FILTERed counts + two coalesced sums);
# with ix_sessions_started_at (core_128) the time-range predicate is
# index-backed.  Combined cost is O(rows_in_window) per butler, fanned out
# concurrently.  Acceptable at current session volumes for a per-page KPI strip.
_AGGREGATE_SQL_TEMPLATE = (
    "SELECT "
    "count(*) AS total, "
    "count(*) FILTER (WHERE success IS TRUE) AS success_count, "
    "count(*) FILTER (WHERE success IS FALSE) AS failed_count, "
    "count(*) FILTER (WHERE success IS NULL) AS running_count, "
    "coalesce(sum(input_tokens), 0) AS input_tokens, "
    "coalesce(sum(output_tokens), 0) AS output_tokens "
    "FROM sessions{where_clause}"
)


async def query_session_aggregate_fan_out(
    db: DatabaseManager,
    where_clause: str,
    args: tuple[Any, ...],
    *,
    butler_names: list[str] | None = None,
) -> FanOutAggregateResult:
    """Fan out a filter-aware session aggregate across butlers.

    Runs the per-butler aggregate (see :data:`_AGGREGATE_SQL_TEMPLATE`) on every
    queried butler, then sums the scalar fields into a single combined result.
    ``by_butler`` carries each butler's ``total`` (count > 0 only), sorted by
    count descending, powering the "top butler" surface.

    Parameters
    ----------
    db:
        The DatabaseManager that manages per-butler pools.
    where_clause:
        A SQL WHERE clause fragment (including the leading ``WHERE`` keyword,
        or an empty string).  Must use ``$1..$N`` matching *args*.
    args:
        Positional arguments for the WHERE clause parameters.
    butler_names:
        Subset of butler names to query.  Defaults to all registered butlers.

    Returns
    -------
    FanOutAggregateResult
        Combined scalar totals and per-butler counts.
    """
    sql = _AGGREGATE_SQL_TEMPLATE.format(where_clause=where_clause)
    results = await db.fan_out(sql, args, butler_names=butler_names)

    combined = FanOutAggregateResult()
    by_butler: list[ButlerCount] = []
    for butler_name, db_rows in results.items():
        if not db_rows:
            continue
        row = db_rows[0]
        total = int(row["total"] or 0)
        combined.total += total
        combined.success_count += int(row["success_count"] or 0)
        combined.failed_count += int(row["failed_count"] or 0)
        combined.running_count += int(row["running_count"] or 0)
        combined.input_tokens += int(row["input_tokens"] or 0)
        combined.output_tokens += int(row["output_tokens"] or 0)
        if total > 0:
            by_butler.append(ButlerCount(butler=butler_name, count=total))

    by_butler.sort(key=lambda b: b.count, reverse=True)
    combined.by_butler = by_butler
    return combined


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
