"""Query functions for shared.ingestion_events — the canonical ingestion event registry.

Each ingestion event is a first-class record of an accepted ingest envelope.
The UUID7 primary key (``id``) matches the ``request_id`` returned to connectors
and propagated to all downstream butler sessions.

Functions
---------
ingestion_event_get         — fetch a single event by id
ingestion_events_list       — paginated list, newest first, optional channel filter
ingestion_event_sessions    — fan-out across all butler schemas for sessions tied to a request_id
ingestion_event_rollup      — aggregate cost/token totals from the fan-out result
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

# Columns returned for each ingestion_event row
_EVENT_COLUMNS = (
    "id, received_at, source_channel, source_provider, source_endpoint_identity, "
    "source_sender_identity, source_thread_identity, external_event_id, dedupe_key, "
    "dedupe_strategy, ingestion_tier, policy_tier, triage_decision, triage_target"
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

    Args:
        pool: asyncpg connection pool scoped to a database that can resolve
            ``shared.ingestion_events`` (i.e. search_path includes ``shared``).
        event_id: UUID of the ingestion event to fetch.

    Returns:
        The event as a plain dict, or ``None`` if no row with that id exists.
    """
    if isinstance(event_id, str):
        event_id = UUID(event_id)

    row = await pool.fetchrow(
        f"SELECT {_EVENT_COLUMNS} FROM shared.ingestion_events WHERE id = $1",
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
) -> list[dict[str, Any]]:
    """Return a paginated list of ingestion events, newest first.

    Args:
        pool: asyncpg connection pool that can resolve ``shared.ingestion_events``.
        limit: Maximum number of rows to return (default 20).
        offset: Number of rows to skip for pagination (default 0).
        source_channel: Optional filter; when provided only events whose
            ``source_channel`` matches this value are returned.

    Returns:
        List of event dicts ordered by ``received_at DESC``.
    """
    if source_channel is not None:
        sql = (
            f"SELECT {_EVENT_COLUMNS} FROM shared.ingestion_events "
            f"WHERE source_channel = $1 "
            f"ORDER BY received_at DESC "
            f"LIMIT $2 OFFSET $3"
        )
        rows = await pool.fetch(sql, source_channel, limit, offset)
    else:
        sql = (
            f"SELECT {_EVENT_COLUMNS} FROM shared.ingestion_events "
            f"ORDER BY received_at DESC "
            f"LIMIT $1 OFFSET $2"
        )
        rows = await pool.fetch(sql, limit, offset)

    return [_decode_event_row(row) for row in rows]


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
