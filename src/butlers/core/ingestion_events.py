"""Query functions for shared.ingestion_events — the canonical ingestion event registry.

Each ingestion event is a first-class record of an accepted ingest envelope.
The UUID7 primary key (``id``) matches the ``request_id`` returned to connectors
and propagated to all downstream butler sessions.

Functions
---------
ingestion_event_get             — fetch a single event by id
ingestion_events_list           — paginated list, newest first, optional channel/status filter
ingestion_events_count          — total row count matching the same filters as ingestion_events_list
ingestion_event_sessions        — fan-out across butler schemas for sessions linked to a request
ingestion_event_rollup          — aggregate cost/token totals from the fan-out result
ingestion_event_replay_request  — mark a filtered event as replay_pending
"""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

import asyncpg

from butlers.api.db import DatabaseManager
from butlers.api.pricing import PricingConfig, estimate_session_cost

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Column definitions for the unified ingestion event SELECT lists
#
# The UNION ALL query in ingestion_events_list merges shared.ingestion_events
# and connectors.filtered_events.  The two tables share some column names, use
# different names for equivalent columns, and each has columns absent from the
# other.  Rather than maintaining two hardcoded SELECT strings in parallel, we
# derive them from a single ordered spec (_UNION_COLUMN_SPEC) that records each
# output column once and declares how it is expressed on each side of the UNION.
#
# _UNION_COLUMN_SPEC entries: (output_alias, ingested_expr, filtered_expr)
#   output_alias    — the result column name (shared by both UNION branches)
#   ingested_expr   — expression used in the shared.ingestion_events SELECT;
#                     a plain column name, a renamed column, or a SQL literal
#   filtered_expr   — expression used in the connectors.filtered_events SELECT;
#                     a plain column name (possibly renamed), or NULL::text
#
# Adding a new column to shared.ingestion_events only requires adding one entry
# here; both SELECT lists are rebuilt automatically at import time.
# ---------------------------------------------------------------------------

_UNION_COLUMN_SPEC: tuple[tuple[str, str, str], ...] = (
    # (output_alias,               ingested_expr,               filtered_expr)
    # ── columns present verbatim in both tables ──────────────────────────────
    ("id", "id", "id"),
    ("received_at", "received_at", "received_at"),
    ("source_channel", "source_channel", "source_channel"),
    # ── ingestion_events columns absent from filtered_events ─────────────────
    ("source_provider", "source_provider", "NULL::text"),
    # ── ingestion_events columns renamed in filtered_events ──────────────────
    ("source_endpoint_identity", "source_endpoint_identity", "endpoint_identity"),
    ("source_sender_identity", "source_sender_identity", "sender_identity"),
    # ── more ingestion_events columns absent from filtered_events ────────────
    ("source_thread_identity", "source_thread_identity", "NULL::text"),
    ("external_event_id", "external_event_id", "external_message_id"),
    ("dedupe_key", "dedupe_key", "NULL::text"),
    ("dedupe_strategy", "dedupe_strategy", "NULL::text"),
    ("ingestion_tier", "ingestion_tier", "NULL::text"),
    ("policy_tier", "policy_tier", "NULL::text"),
    ("triage_decision", "triage_decision", "NULL::text"),
    ("triage_target", "triage_target", "NULL::text"),
    # ── columns present only in filtered_events (synthetic on ingested side) ─
    ("status", "'ingested'::text", "status"),
    ("filter_reason", "NULL::text", "filter_reason"),
    ("error_detail", "NULL::text", "error_detail"),
)


def _build_union_select(expr_index: int) -> str:
    """Build a SELECT column list from _UNION_COLUMN_SPEC.

    Args:
        expr_index: 1 for the ingested (ingestion_events) expression, 2 for the
            filtered (filtered_events) expression.

    Each entry is emitted as ``<expr> AS <alias>`` unless the expression already
    equals the alias, in which case just ``<expr>`` is emitted.
    """
    parts: list[str] = []
    for entry in _UNION_COLUMN_SPEC:
        alias = entry[0]
        expr = entry[expr_index]
        if expr == alias:
            parts.append(expr)
        else:
            parts.append(f"{expr} AS {alias}")
    return ", ".join(parts)


# Pre-built column lists (computed once at import time)
_INGESTED_COLS: str = _build_union_select(1)
_FILTERED_COLS: str = _build_union_select(2)

# Columns returned for each ingestion_event row (point lookups — single-table queries).
# This is every ingestion_events column in spec order, excluding the filtered-events-only
# synthetic entries (those whose ingested_expr is a SQL literal, not a column name).
# A column name is a bare identifier: contains no spaces, quotes, colons, or parens.
_EVENT_COLUMNS: str = ", ".join(
    alias for alias, ingested_expr, _ in _UNION_COLUMN_SPEC if ingested_expr.isidentifier()
)

# Columns fetched from each butler's sessions table during fan-out
_SESSION_COLUMNS = (
    "id, trigger_source, started_at, completed_at, success, "
    "input_tokens, output_tokens, cost, trace_id, model"
)


def _decode_event_row(row: asyncpg.Record) -> dict[str, Any]:
    """Convert an asyncpg Record for an ingestion_event to a plain dict."""
    d = dict(row)
    # Ensure UUID is serialisable
    if "id" in d and isinstance(d["id"], UUID):
        d["id"] = str(d["id"])
    return d


def _decode_session_row(row: asyncpg.Record, butler_name: str) -> dict[str, Any]:
    """Convert an asyncpg Record for a session row to a plain dict, adding butler_name."""
    d = dict(row)
    # asyncpg returns UUID columns as uuid.UUID; stringify for Pydantic
    for key in ("id", "trace_id"):
        if key in d and isinstance(d[key], UUID):
            d[key] = str(d[key])
    # Deserialise cost JSONB if returned as a string
    if "cost" in d and isinstance(d["cost"], str):
        d["cost"] = json.loads(d["cost"])
    d["butler_name"] = butler_name
    return d


async def ingestion_event_get(
    pool: asyncpg.Pool,
    event_id: str | UUID,
) -> dict[str, Any] | None:
    """Return a single ingestion event by its UUID, or None if not found.

    Performs a unified lookup: checks ``shared.ingestion_events`` first (the
    common path for accepted/ingested events), then falls back to
    ``connectors.filtered_events`` so that filtered events are also retrievable
    by ID.  Both result shapes are normalised to the same dict structure,
    including explicit ``status`` and ``filter_reason`` fields.

    Args:
        pool: asyncpg connection pool that can resolve both
            ``shared.ingestion_events`` and ``connectors.filtered_events``.
        event_id: UUID of the ingestion event to fetch.

    Returns:
        The event as a plain dict, or ``None`` if no row with that id exists
        in either table.
    """
    if isinstance(event_id, str):
        event_id = UUID(event_id)

    # 1. Try shared.ingestion_events first (happy path for accepted events).
    row = await pool.fetchrow(
        f"SELECT {_INGESTED_COLS} FROM shared.ingestion_events WHERE id = $1",
        event_id,
    )
    if row is not None:
        return _decode_event_row(row)

    # 2. Fall back to connectors.filtered_events for filtered/errored events.
    row = await pool.fetchrow(
        f"SELECT {_FILTERED_COLS} FROM connectors.filtered_events WHERE id = $1",
        event_id,
    )
    if row is None:
        return None
    return _decode_event_row(row)


async def ingestion_events_list(
    pool: asyncpg.Pool,
    limit: int = 20,
    offset: int = 0,
    source_channel: str | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    """Return a paginated list of ingestion events (unified stream), newest first.

    Merges ``shared.ingestion_events`` (status='ingested', filter_reason=null)
    with ``connectors.filtered_events`` (status/filter_reason from their own
    columns) and returns results ordered by ``received_at DESC``.

    When ``status='ingested'`` is passed, only ``shared.ingestion_events`` rows
    are queried.  Any other ``status`` value queries only
    ``connectors.filtered_events``.  When ``status`` is ``None`` both tables are
    included via a UNION ALL.

    Args:
        pool: asyncpg connection pool that can resolve ``shared.ingestion_events``
            and ``connectors.filtered_events``.
        limit: Maximum number of rows to return (default 20).
        offset: Number of rows to skip for pagination (default 0).
        source_channel: Optional filter; when provided only events whose
            ``source_channel`` matches this value are returned.
        status: Optional status filter. ``'ingested'`` → ingestion_events only;
            other values → filtered_events only; ``None`` → both tables.

    Returns:
        List of event dicts ordered by ``received_at DESC``.  Each dict includes
        a ``status`` field (``'ingested'`` or a filtered_events status value),
        a ``filter_reason`` field (``None`` for ingested events), and
        an ``error_detail`` field (``None`` for ingested events; set on error-status rows).
    """
    args: list[Any] = []

    if status == "ingested":
        # Only query shared.ingestion_events
        where_parts: list[str] = []
        if source_channel is not None:
            where_parts.append("source_channel = $1")
            args.append(source_channel)
        where_clause = " WHERE " + " AND ".join(where_parts) if where_parts else ""
        args.extend([limit, offset])
        n_limit = len(args) - 1
        n_offset = len(args)
        sql = (
            f"SELECT {_INGESTED_COLS} FROM shared.ingestion_events"
            f"{where_clause} "
            f"ORDER BY received_at DESC "
            f"LIMIT ${n_limit} OFFSET ${n_offset}"
        )
    elif status is not None:
        # Only query connectors.filtered_events
        where_parts = ["status = $1"]
        args.append(status)
        if source_channel is not None:
            where_parts.append("source_channel = $2")
            args.append(source_channel)
        where_clause = " WHERE " + " AND ".join(where_parts)
        args.extend([limit, offset])
        n_limit = len(args) - 1
        n_offset = len(args)
        sql = (
            f"SELECT {_FILTERED_COLS} FROM connectors.filtered_events"
            f"{where_clause} "
            f"ORDER BY received_at DESC "
            f"LIMIT ${n_limit} OFFSET ${n_offset}"
        )
    else:
        # UNION ALL both tables
        if source_channel is not None:
            args.append(source_channel)
            ch_n = 1
            ingested_where = f" WHERE source_channel = ${ch_n}"
            filtered_where = f" WHERE source_channel = ${ch_n}"
        else:
            ingested_where = ""
            filtered_where = ""

        args.extend([limit, offset])
        n_limit = len(args) - 1
        n_offset = len(args)
        sql = (
            f"SELECT * FROM ("
            f"SELECT {_INGESTED_COLS} FROM shared.ingestion_events{ingested_where} "
            f"UNION ALL "
            f"SELECT {_FILTERED_COLS} FROM connectors.filtered_events{filtered_where}"
            f") AS combined "
            f"ORDER BY received_at DESC "
            f"LIMIT ${n_limit} OFFSET ${n_offset}"
        )

    rows = await pool.fetch(sql, *args)
    return [_decode_event_row(row) for row in rows]


async def ingestion_events_count(
    pool: asyncpg.Pool,
    source_channel: str | None = None,
    status: str | None = None,
) -> int:
    """Return the total number of ingestion events matching the given filters.

    Mirrors the status/channel branching logic of :func:`ingestion_events_list`
    so that both functions stay in sync: any future filter change only needs to
    be applied here (core) rather than duplicated into the API router.

    Args:
        pool: asyncpg connection pool that can resolve ``shared.ingestion_events``
            and ``connectors.filtered_events``.
        source_channel: Optional filter; when provided only events whose
            ``source_channel`` matches this value are counted.
        status: Optional status filter. ``'ingested'`` → ingestion_events only;
            other values → filtered_events only; ``None`` → both tables.

    Returns:
        Integer count of rows matching the filter combination.
    """
    if status == "ingested":
        # Only count shared.ingestion_events
        if source_channel is not None:
            return (
                await pool.fetchval(
                    "SELECT count(*) FROM shared.ingestion_events WHERE source_channel = $1",
                    source_channel,
                )
                or 0
            )
        return await pool.fetchval("SELECT count(*) FROM shared.ingestion_events") or 0
    elif status is not None:
        # Only count connectors.filtered_events
        if source_channel is not None:
            return (
                await pool.fetchval(
                    "SELECT count(*) FROM connectors.filtered_events "
                    "WHERE status = $1 AND source_channel = $2",
                    status,
                    source_channel,
                )
                or 0
            )
        return (
            await pool.fetchval(
                "SELECT count(*) FROM connectors.filtered_events WHERE status = $1",
                status,
            )
            or 0
        )
    else:
        # Both tables
        if source_channel is not None:
            return (
                await pool.fetchval(
                    "SELECT ("
                    "  SELECT count(*) FROM shared.ingestion_events WHERE source_channel = $1"
                    ") + ("
                    "  SELECT count(*) FROM connectors.filtered_events WHERE source_channel = $1"
                    ")",
                    source_channel,
                )
                or 0
            )
        return (
            await pool.fetchval(
                "SELECT ("
                "  SELECT count(*) FROM shared.ingestion_events"
                ") + ("
                "  SELECT count(*) FROM connectors.filtered_events"
                ")"
            )
            or 0
        )


async def ingestion_event_replay_request(
    pool: asyncpg.Pool,
    event_id: str | UUID,
) -> dict[str, Any]:
    """Request replay of a filtered event by setting its status to ``replay_pending``.

    The transition is performed atomically via a single
    ``UPDATE … WHERE status = ANY(replayable) RETURNING id`` statement, which
    eliminates the TOCTOU race that would occur with a separate SELECT followed
    by an UPDATE.  A second SELECT is issued only on the miss path (UPDATE
    matched zero rows) to distinguish *not_found* from *conflict*.

    Allowed transitions:
    - ``filtered`` → ``replay_pending``
    - ``error``    → ``replay_pending``
    - ``replay_failed`` → ``replay_pending``  (re-replay)

    Non-replayable statuses (``replay_pending``, ``replay_complete``) return a
    ``conflict`` result so the caller can return HTTP 409.

    Args:
        pool: asyncpg connection pool that can resolve ``connectors.filtered_events``.
        event_id: UUID of the filtered event to replay.

    Returns:
        A dict with ``outcome`` key:
        - ``"ok"``        → status was updated; dict also contains ``id``.
        - ``"not_found"`` → no row with that id in connectors.filtered_events.
        - ``"conflict"``  → row exists but current status is not replayable;
                            dict also contains ``current_status``.
    """
    if isinstance(event_id, str):
        event_id = UUID(event_id)

    replayable = ("filtered", "error", "replay_failed")

    # Atomic UPDATE: only transitions rows whose status is in the replayable set.
    # RETURNING id on success; no row returned means either not_found or conflict.
    row = await pool.fetchrow(
        """
        UPDATE connectors.filtered_events
        SET
            status = 'replay_pending',
            replay_requested_at = now(),
            error_detail = NULL
        WHERE id = $1
          AND status = ANY($2)
        RETURNING id
        """,
        event_id,
        list(replayable),
    )

    if row is not None:
        # Update succeeded — row was in a replayable state and is now replay_pending.
        return {"outcome": "ok", "id": str(row["id"])}

    # Miss path: determine whether the row doesn't exist (not_found) or exists but
    # is already in a non-replayable state (conflict).  A separate SELECT is safe
    # here because the write already failed — the only information we're reading is
    # the current status for a meaningful error response.
    current_status = await pool.fetchval(
        "SELECT status FROM connectors.filtered_events WHERE id = $1",
        event_id,
    )

    if current_status is None:
        return {"outcome": "not_found"}

    return {"outcome": "conflict", "current_status": current_status}


async def ingestion_event_sessions(
    db: DatabaseManager,
    request_id: str,
) -> list[dict[str, Any]]:
    """Fan-out across all butler schemas and return sessions matching request_id.

    Queries every registered butler pool concurrently (via
    :meth:`DatabaseManager.fan_out`) for sessions whose ``request_id`` equals
    the given value.  Rows from each butler are augmented with ``butler_name``.

    Args:
        db: DatabaseManager with all butler pools registered.
        request_id: The request_id (UUIDv7 string) to look up.

    Returns:
        List of session dicts sorted by ``started_at`` ascending.  Each dict
        contains the fields listed in ``_SESSION_COLUMNS`` plus ``butler_name``.
    """
    sql = f"SELECT {_SESSION_COLUMNS} FROM sessions WHERE request_id = $1"
    fan_results: dict[str, list[asyncpg.Record]] = await db.fan_out(sql, (request_id,))

    sessions: list[dict[str, Any]] = []
    for butler_name, rows in fan_results.items():
        for row in rows:
            sessions.append(_decode_session_row(row, butler_name))

    # Sort by started_at ascending so the timeline is chronological
    sessions.sort(key=lambda s: s.get("started_at") or "")
    return sessions


def ingestion_event_rollup(
    request_id: str,
    sessions: list[dict[str, Any]],
    pricing: PricingConfig | None = None,
) -> dict[str, Any]:
    """Aggregate cost and token totals from a list of fan-out session dicts.

    Args:
        request_id: The request_id these sessions belong to.
        sessions: List of session dicts as returned by
            :func:`ingestion_event_sessions`.
        pricing: Optional pricing config for computing costs from tokens + model.
            When provided, costs are estimated via ``estimate_session_cost()``.
            Falls back to reading the ``cost`` JSONB column (legacy path).

    Returns:
        A rollup dict with:
        - ``request_id`` — echoed back for correlation
        - ``total_sessions`` — number of sessions
        - ``total_input_tokens`` — sum of ``input_tokens`` (NULL treated as 0)
        - ``total_output_tokens`` — sum of ``output_tokens`` (NULL treated as 0)
        - ``total_cost`` — sum of estimated or stored costs across all sessions
        - ``by_butler`` — dict mapping butler_name to per-butler breakdown
    """
    total_sessions = len(sessions)
    total_input_tokens = 0
    total_output_tokens = 0
    total_cost = 0.0
    by_butler: dict[str, dict[str, Any]] = {}

    for session in sessions:
        butler = session.get("butler_name", "unknown")
        if butler not in by_butler:
            by_butler[butler] = {
                "sessions": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cost": 0.0,
            }

        entry = by_butler[butler]
        entry["sessions"] += 1

        in_tok = session.get("input_tokens") or 0
        out_tok = session.get("output_tokens") or 0
        total_input_tokens += in_tok
        total_output_tokens += out_tok
        entry["input_tokens"] += in_tok
        entry["output_tokens"] += out_tok

        # Compute cost: prefer pricing-based estimation, fall back to stored cost
        session_cost = 0.0
        if pricing is not None:
            model = session.get("model") or ""
            if model and (in_tok or out_tok):
                session_cost = estimate_session_cost(pricing, model, in_tok, out_tok)
        if session_cost == 0.0:
            # Legacy fallback: read from cost JSONB column
            cost = session.get("cost")
            if isinstance(cost, dict):
                usd = cost.get("total_usd")
                if usd is not None:
                    try:
                        session_cost = float(usd)
                    except (TypeError, ValueError):
                        pass
        total_cost += session_cost
        entry["cost"] += session_cost

    return {
        "request_id": request_id,
        "total_sessions": total_sessions,
        "total_input_tokens": total_input_tokens,
        "total_output_tokens": total_output_tokens,
        "total_cost": total_cost,
        "by_butler": by_butler,
    }
