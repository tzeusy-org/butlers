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
    query_session_trigger_breakdown_fan_out(db, where, args, *, butler_names)
        -> FanOutTriggerBreakdownResult
    query_session_detail_fan_out(db, session_id) -> FanOutDetailResult

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
from butlers.core.spawner import SESSION_CANCELLED_ERROR

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Version marker
# ---------------------------------------------------------------------------

#: Stability contract — bump to ``sessions_v2`` for breaking changes.
READ_MODEL_VERSION = "sessions_v1"

# ---------------------------------------------------------------------------
# Column projections (v1 schema contract)
# ---------------------------------------------------------------------------

#: Derived, privacy-preserving list discriminator. The owner-cancellation
#: marker is canonicalized in ``Spawner.cancel_session()``; summary reads
#: expose only this boolean and never the raw ``sessions.error`` text.
_CANCELLED_BY_OWNER_SQL = (
    "COALESCE((success IS FALSE AND error = "
    f"'{SESSION_CANCELLED_ERROR.replace("'", "''")}'), FALSE) AS cancelled_by_owner"
)

#: Columns returned for list / summary views. The additive cancellation
#: discriminator is computed in SQL so generic error text cannot reach the
#: summary DTO or list response.
SUMMARY_COLUMNS: str = (
    "id, prompt, trigger_source, request_id, success, started_at, completed_at, duration_ms, "
    "model, complexity, input_tokens, output_tokens, "
    f"{_CANCELLED_BY_OWNER_SQL}"
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
    cancelled_by_owner: bool


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
    #: Butler pools whose per-butler query raised during the fan-out. Non-empty
    #: means the merged page UNDERCOUNTS — the caller must surface these as a
    #: degraded source (``meta.sources_degraded``) rather than presenting the
    #: partial page as the whole truth. ``sessions`` is a core table present in
    #: every schema, so any fan-out failure here is a genuine pool/connection
    #: fault (never a legitimate table-absence), so classify-before-flagging
    #: resolves to "always a degraded source" for this domain.
    degraded_sources: list[str] = field(default_factory=list)


@dataclass
class ButlerCount:
    """A single butler's matching-session count (for aggregate ``by_butler``)."""

    butler: str
    count: int


@dataclass
class TriggerSourceCount:
    """A single trigger_source's matching-session count (opt-in breakdown)."""

    trigger_source: str
    count: int


@dataclass
class FanOutTriggerBreakdownResult:
    """Merged trigger buckets and failures from the optional grouped fan-out.

    ``degraded_sources`` is deliberately separate from the scalar aggregate's
    degraded sources: the optional trigger breakdown can fail after a complete
    scalar aggregate, making attribution partial without invalidating counts.
    """

    breakdown: list[TriggerSourceCount] = field(default_factory=list)
    degraded_sources: list[str] = field(default_factory=list)


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
    #: Butler pools whose aggregate scan raised during the fan-out. Non-empty
    #: means the summed scalars UNDERCOUNT — surface as ``meta.sources_degraded``
    #: so a downed pool never renders as a truthful zero (e.g. "No sessions
    #: failed"). See ``FanOutKeysetResult.degraded_sources`` for the
    #: classify-before-flagging rationale (sessions is a core table).
    degraded_sources: list[str] = field(default_factory=list)


@dataclass
class FanOutDetailResult:
    """Result of a cross-butler session detail fan-out (first match wins).

    ``degraded_sources`` names any butler pool whose lookup raised. It lets the
    router split a genuine 404 (``row is None`` AND every queried pool answered
    — the id is unknown across all reachable schemas) from a 503 (``row is
    None`` but one or more pools were unreachable — the session may live in a
    pool we could not reach) instead of collapsing a pool outage into a
    misleading "Session not found".
    """

    row: SessionDetailRow | None = None
    butler: str | None = None
    degraded_sources: list[str] = field(default_factory=list)


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
        # The SQL projection is non-null, but fail closed at the read-model
        # boundary if a legacy/mock row still reaches us with NULL.
        cancelled_by_owner=row["cancelled_by_owner"] is True,
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

    data_results, failed = await db.fan_out_with_status(
        data_sql, tuple(keyset_args), butler_names=butler_names
    )

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

    return FanOutKeysetResult(
        rows=page,
        has_more=has_more,
        next_cursor=next_cursor,
        degraded_sources=failed,
    )


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
    results, failed = await db.fan_out_with_status(sql, args, butler_names=butler_names)

    combined = FanOutAggregateResult(degraded_sources=failed)
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


# Query-budget: one GROUP BY trigger_source scan per butler over the filtered
# window, fanned out concurrently and merged (summed per trigger_source) in
# memory.  Deliberately a SEPARATE query/function from
# query_session_aggregate_fan_out above rather than folded into it: the common
# KPI-strip path calls the scalar aggregate on every filter change, and this
# extra GROUP BY scan should only run when a caller opts in (the sessions
# verdict opener's failure-clustering clause -- bu-y0v0c, JARVIS pursuit move
# 9 slice 3), not on every KPI recompute.
_TRIGGER_BREAKDOWN_SQL_TEMPLATE = (
    "SELECT trigger_source, count(*) AS count FROM sessions{where_clause} GROUP BY trigger_source"
)


async def query_session_trigger_breakdown_fan_out(
    db: DatabaseManager,
    where_clause: str,
    args: tuple[Any, ...],
    *,
    butler_names: list[str] | None = None,
) -> FanOutTriggerBreakdownResult:
    """Fan out a filter-aware ``GROUP BY trigger_source`` breakdown across butlers.

    Runs the per-butler grouped count on every queried butler, then merges by
    summing counts for the same ``trigger_source`` across butlers (a given
    trigger_source, e.g. ``"schedule"``, is not butler-scoped).  Result is
    sorted by count descending.

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
    FanOutTriggerBreakdownResult
        Combined per-trigger_source counts, sorted count descending, plus the
        pool failures from this optional fan-out only.
    """
    sql = _TRIGGER_BREAKDOWN_SQL_TEMPLATE.format(where_clause=where_clause)
    results, failed = await db.fan_out_with_status(sql, args, butler_names=butler_names)

    counts: dict[str, int] = {}
    for _butler_name, db_rows in results.items():
        for row in db_rows:
            trigger_source = row["trigger_source"]
            counts[trigger_source] = counts.get(trigger_source, 0) + int(row["count"] or 0)

    breakdown = [TriggerSourceCount(trigger_source=k, count=v) for k, v in counts.items()]
    breakdown.sort(key=lambda t: t.count, reverse=True)
    return FanOutTriggerBreakdownResult(breakdown=breakdown, degraded_sources=failed)


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
        ``row=None`` if not found in any butler. ``degraded_sources`` names any
        pool that was unreachable, so a not-found result over a partial fan-out
        can be reported as a 503 rather than a false 404.
    """
    results, failed = await db.fan_out_with_status(
        f"SELECT {DETAIL_COLUMNS} FROM sessions WHERE id = $1",
        (session_id,),
    )

    for butler_name, db_rows in results.items():
        if db_rows:
            return FanOutDetailResult(
                row=row_to_detail(db_rows[0], butler=butler_name),
                butler=butler_name,
                degraded_sources=failed,
            )

    return FanOutDetailResult(degraded_sources=failed)
