"""Query functions for public.ingestion_events — the canonical ingestion event registry.

Each ingestion event is a first-class record of an accepted ingest envelope.
The UUID7 primary key (``id``) matches the ``request_id`` returned to connectors
and propagated to all downstream butler sessions.

Additional functions
---------------------
ingestion_window_rollup         — aggregate event/session counts for a filter window

Functions
---------
ingestion_event_get             — fetch a single event by id
ingestion_events_list           — paginated list; sort="recent" keyset, sort="cost" offset
encode_cursor                   — encode (received_at, id) tuple to opaque cursor string
decode_cursor                   — decode opaque cursor string to (received_at, id) tuple
encode_cost_cursor              — encode page offset for cost-sort cursor
decode_cost_cursor              — decode cost-sort cursor back to page offset
ingestion_event_set_cost_usd    — write computed cost_usd back to public.ingestion_events
ingestion_event_sessions        — fan-out across butler schemas for sessions linked to a request
ingestion_event_rollup          — aggregate cost/token totals from the fan-out result
ingestion_event_mark_replay_complete — transition replay_pending → ingested on success
ingestion_event_replay_request  — mark a filtered event as replay_pending
ingestion_event_replay_history  — query public.audit_log for replay attempts on an event
"""

from __future__ import annotations

import base64
import json
import logging
from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg

from butlers.api.db import DatabaseManager
from butlers.api.pricing import PricingConfig, estimate_session_cost

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Column definitions for the unified ingestion event SELECT lists
#
# The UNION ALL query in ingestion_events_list merges public.ingestion_events
# and connectors.filtered_events.  The two tables share some column names, use
# different names for equivalent columns, and each has columns absent from the
# other.  Rather than maintaining two hardcoded SELECT strings in parallel, we
# derive them from a single ordered spec (_UNION_COLUMN_SPEC) that records each
# output column once and declares how it is expressed on each side of the UNION.
#
# _UNION_COLUMN_SPEC entries: (output_alias, ingested_expr, filtered_expr)
#   output_alias    — the result column name (shared by both UNION branches)
#   ingested_expr   — expression used in the public.ingestion_events SELECT;
#                     a plain column name, a renamed column, or a SQL literal
#   filtered_expr   — expression used in the connectors.filtered_events SELECT;
#                     a plain column name (possibly renamed), or NULL::text
#
# Adding a new column to public.ingestion_events only requires adding one entry
# here; both SELECT lists are rebuilt automatically at import time.
# ---------------------------------------------------------------------------

# Display status for ingestion_events rows: events that matched a `skip`
# triage rule are stored with status='ingested' (they are fully persisted and
# replayable) but were deliberately not dispatched to any butler.  The unified
# timeline surfaces them as the synthetic status 'skipped' so the dashboard can
# distinguish — and filter out — skip-triaged noise (e.g. home_assistant sensor
# streams) without conflating it with genuinely dispatched events.
_SKIP_AWARE_STATUS = (
    "CASE WHEN status = 'ingested' AND triage_decision = 'skip' THEN 'skipped' ELSE status END"
)

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
    # ── status/error columns (real on both sides now) ──────────────────────
    ("status", _SKIP_AWARE_STATUS, "status"),
    ("filter_reason", "NULL::text", "filter_reason"),
    ("error_detail", "error_detail", "error_detail"),
    # ── cost denormalization (core_126) ─────────────────────────────────────
    # cost_usd is written lazily by the rollup endpoint; NULL until first fetched.
    # filtered_events have no sessions and therefore no cost.
    ("cost_usd", "cost_usd", "NULL::numeric"),
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

    Performs a unified lookup: checks ``public.ingestion_events`` first (the
    common path for accepted/ingested events), then falls back to
    ``connectors.filtered_events`` so that filtered events are also retrievable
    by ID.  Both result shapes are normalised to the same dict structure,
    including explicit ``status`` and ``filter_reason`` fields.

    Args:
        pool: asyncpg connection pool that can resolve both
            ``public.ingestion_events`` and ``connectors.filtered_events``.
        event_id: UUID of the ingestion event to fetch.

    Returns:
        The event as a plain dict, or ``None`` if no row with that id exists
        in either table.
    """
    if isinstance(event_id, str):
        event_id = UUID(event_id)

    # 1. Try public.ingestion_events first (happy path for accepted events).
    row = await pool.fetchrow(
        f"SELECT {_INGESTED_COLS} FROM public.ingestion_events WHERE id = $1",
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


def encode_cursor(received_at: datetime, row_id: UUID | str) -> str:
    """Encode a keyset position into an opaque cursor string.

    The cursor encodes the ``(received_at, id)`` tuple of the last row returned.
    It is base64url-encoded JSON so it is safe to use as a query parameter.

    Args:
        received_at: Timestamp of the last row (``received_at`` column).
        row_id: Primary key of the last row.

    Returns:
        An opaque string suitable for the ``cursor`` query parameter.
    """
    payload = {
        "ra": received_at.isoformat(),
        "id": str(row_id),
    }
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()


def encode_cost_cursor(offset: int) -> str:
    """Encode an offset-based cursor for cost-sorted pagination.

    Cost sort uses offset pagination (not keyset) because the ORDER BY
    includes a nullable column (cost_usd) where keyset comparisons with
    NULLs are complex and the issue brief explicitly permits offset for
    this view.

    The payload type sentinel ``"c"`` distinguishes cost cursors from the
    default keyset cursors (which carry ``"ra"`` and ``"id"``).
    """
    return base64.urlsafe_b64encode(json.dumps({"t": "c", "o": offset}).encode()).decode()


def decode_cost_cursor(cursor: str) -> int:
    """Decode a cost cursor back to its page offset.

    Raises:
        ValueError: If the cursor is not a valid cost cursor.
    """
    try:
        raw = base64.urlsafe_b64decode(cursor.encode())
        payload = json.loads(raw)
        if not isinstance(payload, dict) or payload.get("t") != "c":
            raise ValueError("not a cost cursor")
        offset = int(payload["o"])
        if offset < 0:
            raise ValueError("offset cannot be negative")
        return offset
    except (KeyError, ValueError, TypeError, AttributeError) as exc:
        raise ValueError(f"Invalid cost cursor: {exc}") from exc


def decode_cursor(cursor: str) -> tuple[datetime, str]:
    """Decode an opaque cursor string back to ``(received_at, id)``.

    Args:
        cursor: Opaque cursor string as returned by :func:`encode_cursor`.

    Returns:
        A ``(received_at, row_id)`` tuple.

    Raises:
        ValueError: If the cursor is malformed or cannot be decoded.
    """
    try:
        raw = base64.urlsafe_b64decode(cursor.encode())
        payload = json.loads(raw)
        received_at = datetime.fromisoformat(payload["ra"])
        row_id: str = payload["id"]
        return received_at, row_id
    except (KeyError, ValueError) as exc:
        raise ValueError(f"Invalid cursor: {exc}") from exc


async def ingestion_event_set_cost_usd(
    pool: asyncpg.Pool,
    event_id: str | UUID,
    cost_usd: float,
) -> None:
    """Write a computed cost_usd back to public.ingestion_events (lazy write-through).

    Called by the rollup endpoint after aggregating cross-butler session costs.
    Only updates ingestion_events rows (not connectors.filtered_events, which
    have no sessions and no cost).  Safe to call multiple times — the latest
    value overwrites the previous one.

    Args:
        pool: asyncpg pool with write access to public.ingestion_events.
        event_id: The ingestion event UUID (must exist in public.ingestion_events).
        cost_usd: Computed total cost in USD across all butler sessions.
    """
    if isinstance(event_id, str):
        event_id = UUID(event_id)
    await pool.execute(
        "UPDATE public.ingestion_events SET cost_usd = $1 WHERE id = $2",
        cost_usd,
        event_id,
    )


async def ingestion_events_list(
    pool: asyncpg.Pool,
    limit: int = 20,
    cursor: str | None = None,
    channels: list[str] | None = None,
    status: str | None = None,
    statuses: list[str] | None = None,
    q: str | None = None,
    from_dt: datetime | None = None,
    to_dt: datetime | None = None,
    sort: str | None = None,
) -> dict[str, Any]:
    """Return a paginated list of ingestion events (unified stream).

    Default sort (sort=None or sort="recent") uses keyset pagination ordered
    ``received_at DESC, id DESC``.

    Cost sort (sort="cost") orders by ``cost_usd DESC NULLS LAST, received_at
    DESC, id DESC`` and uses offset-based pagination (the issue brief explicitly
    permits offset for the cost view; keyset pagination with a nullable sort key
    is non-trivial and offers little benefit for a read-heavy admin analytics view).

    Replaces the old offset/total approach with cursor pagination using an indexed
    ``(received_at DESC, id DESC)`` keyset.  The cursor encodes the ``(received_at, id)``
    position of the last row returned on the previous page.

    Always UNION ALLs ``public.ingestion_events`` and
    ``connectors.filtered_events``, applying optional ``status``,
    ``channels``, and ``q`` (text search) filters in the outer query.

    Args:
        pool: asyncpg connection pool.
        limit: Maximum number of rows to return (default 20).
        cursor: Opaque cursor from the previous page's ``next_cursor`` field.
            When ``None``, returns the first page.
        channels: Optional list of source_channel values to include.
            Generates a ``source_channel = ANY($N::text[])`` clause.
        status: Optional filter by a single ``status``. Ignored when
            ``statuses`` is provided.
        statuses: Optional list of status values to include.  Generates a
            ``status = ANY($N::text[])`` clause and takes precedence over
            ``status``.
        q: Optional freetext search (ILIKE %q%) against the event id (as text),
            source_channel, source_sender_identity, source_endpoint_identity,
            external_event_id, triage_target (butler routing destination),
            triage_decision, filter_reason, and error_detail.  Searching a
            visible event ID prefix always returns that row.
        from_dt: Inclusive lower bound on ``received_at``.  ``None`` = no lower bound.
        to_dt: Exclusive upper bound on ``received_at``.  ``None`` = no upper bound.
        sort: Sort mode.  ``None`` or ``"recent"`` → keyset pagination on
            ``(received_at DESC, id DESC)``.  ``"cost"`` → offset pagination on
            ``(cost_usd DESC NULLS LAST, received_at DESC, id DESC)``.

    Returns:
        A dict with:
        - ``items``: list of event dicts
        - ``next_cursor``: opaque cursor string for the next page, or ``None`` if last page
        - ``has_more``: ``True`` when there are more rows after this page
    """
    args: list[Any] = []
    where_parts: list[str] = []

    if statuses:
        args.append(statuses)
        where_parts.append(f"status = ANY(${len(args)}::text[])")
    elif status is not None:
        args.append(status)
        where_parts.append(f"status = ${len(args)}")
    if channels:
        args.append(channels)
        where_parts.append(f"source_channel = ANY(${len(args)}::text[])")
    if q is not None:
        q_pattern = f"%{q}%"
        args.append(q_pattern)
        n = len(args)
        where_parts.append(
            f"(id::text ILIKE ${n}"
            f" OR source_channel ILIKE ${n}"
            f" OR source_sender_identity ILIKE ${n}"
            f" OR source_endpoint_identity ILIKE ${n}"
            f" OR external_event_id ILIKE ${n}"
            f" OR triage_target ILIKE ${n}"
            f" OR triage_decision ILIKE ${n}"
            f" OR filter_reason ILIKE ${n}"
            f" OR error_detail ILIKE ${n})"
        )
    if from_dt is not None:
        args.append(from_dt)
        where_parts.append(f"received_at >= ${len(args)}")
    if to_dt is not None:
        args.append(to_dt)
        where_parts.append(f"received_at < ${len(args)}")

    if sort == "cost":
        # ── cost sort: offset-based pagination ───────────────────────────────
        # Offset pagination is used for the cost view because ORDER BY on a
        # nullable column makes keyset comparisons complex, and the issue brief
        # explicitly permits offset for this view.  The cursor encodes the page
        # offset so callers see a consistent opaque cursor interface.
        page_offset = 0
        if cursor is not None:
            page_offset = decode_cost_cursor(cursor)

        where_clause = (" WHERE " + " AND ".join(where_parts)) if where_parts else ""

        args.append(limit + 1)
        n_limit = len(args)
        args.append(page_offset)
        n_offset = len(args)

        sql = (
            f"SELECT * FROM ("
            f"SELECT {_INGESTED_COLS} FROM public.ingestion_events "
            f"UNION ALL "
            f"SELECT {_FILTERED_COLS} FROM connectors.filtered_events"
            f") AS combined"
            f"{where_clause} "
            f"ORDER BY cost_usd DESC NULLS LAST, received_at DESC, id DESC "
            f"LIMIT ${n_limit} OFFSET ${n_offset}"
        )

        rows = await pool.fetch(sql, *args)
        has_more = len(rows) > limit
        page_rows = rows[:limit]
        items = [_decode_event_row(row) for row in page_rows]

        next_cursor: str | None = None
        if has_more:
            next_cursor = encode_cost_cursor(page_offset + limit)

        return {"items": items, "next_cursor": next_cursor, "has_more": has_more}

    # ── default sort: keyset pagination on (received_at DESC, id DESC) ───────
    if cursor is not None:
        cursor_received_at, cursor_id = decode_cursor(cursor)
        args.append(cursor_received_at)
        args.append(UUID(cursor_id))
        n_ra = len(args) - 1
        n_id = len(args)
        # Descending keyset: strictly older than cursor position
        where_parts.append(f"(received_at, id) < (${n_ra}, ${n_id})")

    where_clause = (" WHERE " + " AND ".join(where_parts)) if where_parts else ""

    # Fetch limit+1 rows to determine whether a next page exists.
    args.append(limit + 1)
    n_limit = len(args)

    sql = (
        f"SELECT * FROM ("
        f"SELECT {_INGESTED_COLS} FROM public.ingestion_events "
        f"UNION ALL "
        f"SELECT {_FILTERED_COLS} FROM connectors.filtered_events"
        f") AS combined"
        f"{where_clause} "
        f"ORDER BY received_at DESC, id DESC "
        f"LIMIT ${n_limit}"
    )

    rows = await pool.fetch(sql, *args)
    has_more = len(rows) > limit
    page_rows = rows[:limit]

    items = [_decode_event_row(row) for row in page_rows]

    next_cursor_out: str | None = None
    if has_more and page_rows:
        last = page_rows[-1]
        last_id = last["id"]
        last_ra = last["received_at"]
        if isinstance(last_id, str):
            last_id_uuid = UUID(last_id)
        else:
            last_id_uuid = last_id
        if isinstance(last_ra, str):
            last_ra_dt = datetime.fromisoformat(last_ra)
        else:
            last_ra_dt = last_ra
        next_cursor_out = encode_cursor(last_ra_dt, last_id_uuid)

    return {"items": items, "next_cursor": next_cursor_out, "has_more": has_more}


async def ingestion_event_get_inbox_lifecycle(
    pool: asyncpg.Pool,
    event_id: str | UUID,
) -> dict[str, Any] | None:
    """Fetch ``lifecycle_state`` and ``decomposition_output`` from ``message_inbox``.

    ``message_inbox.id`` equals the ingestion event's ``request_id`` (UUID7),
    so this is a direct primary-key lookup.  The table is partitioned by
    ``received_at``; without a known partition key we search across all
    partitions.  The switchboard pool's ``search_path`` already includes the
    switchboard schema so no schema prefix is required.

    Args:
        pool: asyncpg connection pool scoped to the switchboard schema (i.e.
            ``search_path`` includes the switchboard schema so that
            ``message_inbox`` is visible without a schema prefix).
        event_id: UUID of the ingestion event (matches ``message_inbox.id``).

    Returns:
        A dict with ``lifecycle_state`` (str) and ``decomposition_output``
        (dict | None) keys, or ``None`` if no matching row is found.
    """
    if isinstance(event_id, str):
        event_id = UUID(event_id)

    row = await pool.fetchrow(
        """
        SELECT lifecycle_state, decomposition_output
        FROM message_inbox
        WHERE id = $1
        LIMIT 1
        """,
        event_id,
    )
    if row is None:
        return None

    decomposition_output = row["decomposition_output"]
    if isinstance(decomposition_output, str):
        decomposition_output = json.loads(decomposition_output)

    return {
        "lifecycle_state": row["lifecycle_state"],
        "decomposition_output": decomposition_output,
    }


async def ingestion_event_mark_failed(
    pool: asyncpg.Pool,
    event_id: str | UUID,
    error_detail: str | None = None,
) -> bool:
    """Mark an ingestion event as ``failed`` after routing did not succeed.

    Transitions:
    - ``ingested``       → ``failed``        (first-time failure)
    - ``replay_pending`` → ``replay_failed`` (replay attempt failed)

    Skips rows that are already ``failed`` or ``replay_failed``.

    Args:
        pool: asyncpg connection pool with access to ``public.ingestion_events``.
        event_id: UUID of the ingestion event (matches ``request_id``).
        error_detail: Human-readable description of the routing failure.

    Returns:
        ``True`` if the row was updated, ``False`` if not found or already
        in a terminal failure state.
    """
    if isinstance(event_id, str):
        event_id = UUID(event_id)

    result = await pool.execute(
        """
        UPDATE public.ingestion_events
        SET status = CASE
                WHEN status = 'replay_pending' THEN 'replay_failed'
                ELSE 'failed'
            END,
            error_detail = $2
        WHERE id = $1
          AND status IN ('ingested', 'replay_pending')
        """,
        event_id,
        error_detail,
    )
    # asyncpg returns e.g. "UPDATE 1" or "UPDATE 0"
    return result.endswith("1")


async def ingestion_event_mark_replay_complete(
    pool: asyncpg.Pool,
    event_id: str | UUID,
) -> bool:
    """Transition a ``replay_pending`` ingestion event back to ``ingested``.

    Called after the DurableBuffer scanner successfully re-routes a replayed
    event.  Only matches ``replay_pending`` rows so it is safe to call
    unconditionally after every successful routing — it is a no-op for events
    that were not being replayed.

    Returns:
        ``True`` if the row was updated, ``False`` otherwise.
    """
    if isinstance(event_id, str):
        event_id = UUID(event_id)

    result = await pool.execute(
        """
        UPDATE public.ingestion_events
        SET status = 'ingested',
            error_detail = NULL
        WHERE id = $1
          AND status = 'replay_pending'
        """,
        event_id,
    )
    return result.endswith("1")


async def ingestion_event_replay_request(
    pool: asyncpg.Pool,
    event_id: str | UUID,
    *,
    switchboard_pool: asyncpg.Pool | None = None,
) -> dict[str, Any]:
    """Request replay of a failed or filtered event.

    Checks ``public.ingestion_events`` first (for routing-failed events), then
    falls back to ``connectors.filtered_events`` (for connector-filtered events).

    For failed ingestion events, resets status to ``'ingested'`` so the
    pipeline can re-route.  For already-ingested events, sets status to
    ``'replay_pending'`` and resets the corresponding ``message_inbox`` row
    to ``'accepted'`` so the DurableBuffer scanner re-routes it.
    For filtered events, sets status to ``'replay_pending'`` for the existing
    replay worker.

    Allowed transitions:
    - ``failed``          → ``ingested``        (ingestion event — ready for re-route)
    - ``ingested``        → ``replay_pending``  (re-process successful event via scanner)
    - ``filtered``        → ``replay_pending``  (connector filter)
    - ``error``           → ``replay_pending``  (connector error)
    - ``replay_failed``   → ``replay_pending``  (re-replay)
    - ``replay_complete`` → ``replay_pending``  (re-replay completed event)

    Args:
        pool: asyncpg connection pool that can resolve both
            ``public.ingestion_events`` and ``connectors.filtered_events``.
        event_id: UUID of the event to replay.
        switchboard_pool: Optional asyncpg pool scoped to the switchboard schema.
            Required for replaying ``ingested`` events (resets ``message_inbox``
            lifecycle so the DurableBuffer scanner re-routes the message).

    Returns:
        A dict with ``outcome`` key:
        - ``"ok"``        → status was updated; dict also contains ``id`` and ``source``.
        - ``"not_found"`` → no row with that id in either table.
        - ``"conflict"``  → row exists but current status is not replayable;
                            dict also contains ``current_status``.
    """
    if isinstance(event_id, str):
        event_id = UUID(event_id)

    # 1. Try public.ingestion_events first (routing-failed events).
    row = await pool.fetchrow(
        """
        UPDATE public.ingestion_events
        SET status = 'ingested', error_detail = NULL
        WHERE id = $1 AND status = 'failed'
        RETURNING id
        """,
        event_id,
    )
    if row is not None:
        return {"outcome": "ok", "id": str(row["id"]), "source": "ingestion_events"}

    # 1b. Try public.ingestion_events for already-ingested events.
    # These events were already routed successfully.  To replay, we mark them
    # replay_pending and reset the message_inbox row to 'accepted' so the
    # DurableBuffer scanner re-enqueues them for re-routing.
    ingestion_replayable = ("ingested", "replay_failed")
    row = await pool.fetchrow(
        """
        UPDATE public.ingestion_events
        SET status = 'replay_pending'
        WHERE id = $1 AND status = ANY($2)
        RETURNING id
        """,
        event_id,
        list(ingestion_replayable),
    )
    if row is not None:
        # Reset message_inbox lifecycle so the scanner picks it up.
        if switchboard_pool is not None:
            try:
                await switchboard_pool.execute(
                    """
                    UPDATE message_inbox
                    SET lifecycle_state = 'accepted',
                        updated_at = now()
                    WHERE id = $1
                    """,
                    event_id,
                )
            except Exception:
                logger.warning(
                    "replay: failed to reset message_inbox lifecycle for %s "
                    "(scanner may not re-route until manual intervention)",
                    event_id,
                )
        else:
            logger.warning(
                "replay: switchboard_pool not available; message_inbox not reset for %s",
                event_id,
            )
        return {"outcome": "ok", "id": str(row["id"]), "source": "ingestion_events"}

    # 2. Try connectors.filtered_events (any status except replay_pending).
    filtered_replayable = (
        "filtered",
        "error",
        "replay_failed",
        "ingested",
        "replay_complete",
    )
    row = await pool.fetchrow(
        """
        UPDATE connectors.filtered_events
        SET status = 'replay_pending', replay_requested_at = now(), error_detail = NULL
        WHERE id = $1 AND status = ANY($2)
        RETURNING id
        """,
        event_id,
        list(filtered_replayable),
    )
    if row is not None:
        return {"outcome": "ok", "id": str(row["id"]), "source": "filtered_events"}

    # 3. Check both tables for non-replayable status.
    for table in ("public.ingestion_events", "connectors.filtered_events"):
        current_status = await pool.fetchval(
            f"SELECT status FROM {table} WHERE id = $1",
            event_id,
        )
        if current_status is not None:
            return {"outcome": "conflict", "current_status": current_status}

    return {"outcome": "not_found"}


def _compute_session_cost_usd(
    session: dict[str, Any],
    pricing: PricingConfig | None = None,
) -> float | None:
    """Compute the numeric USD cost for a single session dict.

    Prefers pricing-based estimation from token counts and model when
    ``pricing`` is supplied.  Falls back to the legacy ``cost`` JSONB column
    (``cost["total_usd"]``).  Returns ``None`` when neither source yields a
    value (pricing absent and no stored cost).

    Args:
        session: Session dict as returned by :func:`ingestion_event_sessions`
            (includes ``model``, ``input_tokens``, ``output_tokens``, ``cost``).
        pricing: Optional pricing config.  When provided, cost is estimated
            from token counts and model via :func:`estimate_session_cost`.

    Returns:
        Numeric USD cost, or ``None`` when unavailable.
    """
    in_tok = session.get("input_tokens") or 0
    out_tok = session.get("output_tokens") or 0

    if pricing is not None:
        model = session.get("model") or ""
        if model and (in_tok or out_tok):
            session_cost = estimate_session_cost(pricing, model, in_tok, out_tok)
            if session_cost != 0.0:
                return session_cost
            # estimate_session_cost returns 0.0 for unknown models — fall
            # through to JSONB fallback so stored cost_usd is not lost.
            # Mirrors the precedence used by ingestion_event_rollup.
        # Pricing is available but we have no model/tokens — fall through to
        # JSONB fallback rather than returning 0.0, which would mask a real
        # stored value.

    # Legacy fallback: read from the cost JSONB column
    cost = session.get("cost")
    if isinstance(cost, dict):
        usd = cost.get("total_usd")
        if usd is not None:
            try:
                return float(usd)
            except (TypeError, ValueError):
                pass

    return None


async def ingestion_event_sessions(
    db: DatabaseManager,
    request_id: str,
    pricing: PricingConfig | None = None,
) -> list[dict[str, Any]]:
    """Fan-out across all butler schemas and return sessions matching request_id.

    Queries every registered butler pool concurrently (via
    :meth:`DatabaseManager.fan_out`) for sessions whose ``request_id`` equals
    the given value.  Rows from each butler are augmented with ``butler_name``
    and a computed ``cost_usd`` field (numeric USD cost, or ``None``).

    Args:
        db: DatabaseManager with all butler pools registered.
        request_id: The request_id (UUIDv7 string) to look up.
        pricing: Optional pricing config.  When provided, ``cost_usd`` is
            estimated from token counts and model.  Falls back to the legacy
            ``cost`` JSONB column when pricing is absent or token data is
            unavailable.

    Returns:
        List of session dicts sorted by ``started_at`` ascending.  Each dict
        contains the fields listed in ``_SESSION_COLUMNS`` plus ``butler_name``
        and ``cost_usd``.
    """
    sql = f"SELECT {_SESSION_COLUMNS} FROM sessions WHERE request_id = $1"
    fan_results: dict[str, list[asyncpg.Record]] = await db.fan_out(sql, (request_id,))

    sessions: list[dict[str, Any]] = []
    for butler_name, rows in fan_results.items():
        for row in rows:
            session = _decode_session_row(row, butler_name)
            session["cost_usd"] = _compute_session_cost_usd(session, pricing)
            sessions.append(session)

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


async def ingestion_window_rollup(
    pool: asyncpg.Pool,
    *,
    from_dt: datetime | None = None,
    to_dt: datetime | None = None,
    channels: list[str] | None = None,
    statuses: list[str] | None = None,
    q: str | None = None,
    db: DatabaseManager | None = None,
    pricing: PricingConfig | None = None,
) -> dict[str, Any]:
    """Return aggregate event/session counts for the active filter window.

    Counts events from the unified timeline (public.ingestion_events UNION ALL
    connectors.filtered_events) filtered by the same parameters as
    ingestion_events_list.  Session count is derived by summing sessions across
    all registered butler schemas (fan-out via db.fan_out).

    When ``pricing`` is supplied, ``cost`` is populated by summing per-model
    token counts across all matching sessions and estimating USD cost via
    ``estimate_session_cost``.  Models not found in the pricing catalog
    contribute $0.  When ``pricing`` is ``None``, ``cost`` is returned as
    ``None``.

    Args:
        pool:      asyncpg pool for the shared credentials database.
        from_dt:   Inclusive lower bound on ``received_at``. ``None`` = no lower bound.
        to_dt:     Exclusive upper bound on ``received_at``. ``None`` = no upper bound.
        channels:  Optional list of source_channel values to include.
        statuses:  Optional list of status values to include.
        q:         Optional freetext search (ILIKE %q%) against event id, source_channel,
                   source_sender_identity, source_endpoint_identity, external_event_id,
                   triage_target, triage_decision, filter_reason, and error_detail.
                   ``None`` = no text filter.
        db:        DatabaseManager for the cross-butler session fan-out.
                   When ``None``, session count is omitted (returns 0) and cost is ``None``.
        pricing:   Optional pricing config for cost estimation.  When provided, cost is
                   computed by summing per-model token totals across all linked sessions.
                   When ``None``, cost is returned as ``None``.

    Returns:
        Dict with:
        - ``events``:   int — total matching events
        - ``sessions``: int — total sessions linked to matching events
        - ``cost``:     float | None — estimated USD cost, or None when pricing unavailable
        - ``window``:   dict with ``from`` (ISO str | None) and ``to`` (ISO str | None)
    """
    args: list[Any] = []
    where_parts: list[str] = []

    if from_dt is not None:
        args.append(from_dt)
        where_parts.append(f"received_at >= ${len(args)}")
    if to_dt is not None:
        args.append(to_dt)
        where_parts.append(f"received_at < ${len(args)}")
    if channels:
        args.append(channels)
        where_parts.append(f"source_channel = ANY(${len(args)}::text[])")
    if statuses:
        args.append(statuses)
        where_parts.append(f"status = ANY(${len(args)}::text[])")
    if q:
        q_pattern = f"%{q}%"
        args.append(q_pattern)
        n = len(args)
        where_parts.append(
            f"(id::text ILIKE ${n}"
            f" OR source_channel ILIKE ${n}"
            f" OR source_sender_identity ILIKE ${n}"
            f" OR source_endpoint_identity ILIKE ${n}"
            f" OR external_event_id ILIKE ${n}"
            f" OR triage_target ILIKE ${n}"
            f" OR triage_decision ILIKE ${n}"
            f" OR filter_reason ILIKE ${n}"
            f" OR error_detail ILIKE ${n})"
        )

    where_clause = (" WHERE " + " AND ".join(where_parts)) if where_parts else ""

    event_count_sql = (
        f"SELECT COUNT(*) FROM ("
        f"SELECT {_INGESTED_COLS} FROM public.ingestion_events "
        f"UNION ALL "
        f"SELECT {_FILTERED_COLS} FROM connectors.filtered_events"
        f") AS combined"
        f"{where_clause}"
    )

    try:
        event_count: int = await pool.fetchval(event_count_sql, *args)
    except Exception:
        logger.debug("ingestion_window_rollup: event count query failed", exc_info=True)
        event_count = 0

    # Session count + cost: fan-out to all registered butler schemas.
    # We fetch only the event IDs (not full rows) and cap the array to avoid
    # transferring unbounded data to the application layer.
    # A cap of 10,000 IDs is sufficient for rollup accuracy in typical windows;
    # very large windows return an approximate count and approximate cost.
    _SESSION_COUNT_ID_CAP = 10_000
    session_count = 0
    total_cost: float | None = None
    if db is not None and event_count > 0:
        id_sql = (
            f"SELECT id FROM ("
            f"SELECT {_INGESTED_COLS} FROM public.ingestion_events "
            f"UNION ALL "
            f"SELECT {_FILTERED_COLS} FROM connectors.filtered_events"
            f") AS combined"
            f"{where_clause}"
            f" LIMIT {_SESSION_COUNT_ID_CAP}"
        )
        try:
            id_rows = await pool.fetch(id_sql, *args)
            event_ids = [str(row["id"]) for row in id_rows]
        except Exception:
            logger.debug("ingestion_window_rollup: event ID fetch failed", exc_info=True)
            event_ids = []

        if event_ids:
            try:
                fan_results: dict[str, list] = await db.fan_out(
                    """
                    SELECT
                        COUNT(*) AS cnt,
                        COALESCE(model, '') AS model,
                        COALESCE(SUM(input_tokens), 0)::bigint AS input_tokens,
                        COALESCE(SUM(output_tokens), 0)::bigint AS output_tokens
                    FROM sessions
                    WHERE request_id = ANY($1::text[])
                    GROUP BY model
                    """,
                    (event_ids,),
                )
                for rows in fan_results.values():
                    for row in rows:
                        session_count += int(row.get("cnt") or 0)
                        if pricing is not None:
                            model = row.get("model") or ""
                            in_tok = int(row.get("input_tokens") or 0)
                            out_tok = int(row.get("output_tokens") or 0)
                            if model and (in_tok or out_tok):
                                if total_cost is None:
                                    total_cost = 0.0
                                total_cost += estimate_session_cost(pricing, model, in_tok, out_tok)
                # Pricing present but all sessions have unknown/empty model or zero tokens:
                # initialise to 0.0 so callers can distinguish "pricing unavailable" (None)
                # from "sessions exist but zero chargeable tokens" (0.0).
                if pricing is not None and session_count > 0 and total_cost is None:
                    total_cost = 0.0
            except Exception:
                logger.debug("ingestion_window_rollup: session fan-out failed", exc_info=True)

    return {
        "events": event_count,
        "sessions": session_count,
        "cost": total_cost,
        "window": {
            "from": from_dt.isoformat() if from_dt is not None else None,
            "to": to_dt.isoformat() if to_dt is not None else None,
        },
    }


_PAYLOAD_TRUNCATION_BYTES = 64 * 1024  # 64 KiB display cap


async def ingestion_event_get_payload(
    pool: asyncpg.Pool,
    switchboard_pool: asyncpg.Pool,
    event_id: str | UUID,
) -> dict[str, Any] | None:
    """Fetch the raw inbound payload for an ingestion event.

    The payload lives in ``switchboard.message_inbox.raw_payload`` (a JSONB
    column with structure ``{"content": <text>, "metadata": {...}}``) keyed on
    ``message_inbox.id == ingestion_event.id`` (both are UUID7).

    The event must first be confirmed to exist in the shared credentials pool
    (``public.ingestion_events`` or ``connectors.filtered_events``) — if the
    event does not exist there, the caller should raise 404.  If the event
    exists but has no ``message_inbox`` row (pruned, or a filtered event with no
    inbox entry), returns a sentinel result with ``{"missing": True}``.

    Args:
        pool: asyncpg pool for the shared credentials database (used to confirm
            the event exists before exposing the payload).
        switchboard_pool: asyncpg pool scoped to the switchboard schema so that
            ``message_inbox`` is visible without a schema prefix.
        event_id: UUID of the ingestion event.

    Returns:
        A dict with keys ``content``, ``bytes``, ``truncated``, ``channel``,
        or ``{"missing": True}`` when the inbox row has been pruned/never
        written, or ``None`` when the event does not exist at all.
    """
    if isinstance(event_id, str):
        event_id = UUID(event_id)

    # Confirm the event exists in the canonical registry.
    event = await ingestion_event_get(pool, event_id)
    if event is None:
        return None  # Caller raises 404.

    # Retrieve the raw_payload from message_inbox (switchboard schema).
    # source_channel is stored inside request_context JSONB, not as a top-level column.
    row = await switchboard_pool.fetchrow(
        """
        SELECT raw_payload,
               request_context ->> 'source_channel' AS source_channel
        FROM message_inbox
        WHERE id = $1
        LIMIT 1
        """,
        event_id,
    )
    if row is None:
        # Event exists but no inbox row — filtered event or pruned.
        return {"missing": True}

    raw = row["raw_payload"]
    # raw_payload is stored as JSONB; asyncpg may return it as dict or str.
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            pass

    # Extract the human-readable text content. Messaging connectors store the
    # inbound text under a top-level ``content`` key. Other connectors (e.g.
    # home_assistant) deliver a structured envelope keyed
    # ``event/sender/source/control/payload`` with no ``content`` key — in that
    # case fall back to pretty-printing the whole payload so the raw tab never
    # reports a misleading 0 bytes when data is in fact present.
    if isinstance(raw, dict):
        content = raw.get("content")
        if not content:
            content = raw if raw else ""
    else:
        content = str(raw) if raw is not None else ""

    if not isinstance(content, str):
        content = json.dumps(content, ensure_ascii=False, indent=2)

    full_bytes = len(content.encode())
    truncated = full_bytes > _PAYLOAD_TRUNCATION_BYTES
    if truncated:
        content = content.encode()[:_PAYLOAD_TRUNCATION_BYTES].decode(errors="replace")

    # Prefer source_channel from message_inbox (most authoritative); fall back
    # to the ingestion_events row we already fetched.
    source_channel = (row.get("source_channel") or event.get("source_channel")) or None

    return {
        "content": content,
        "bytes": full_bytes,
        "truncated": truncated,
        "channel": source_channel,
    }


async def ingestion_event_replay_history(
    pool: asyncpg.Pool,
    event_id: str | UUID,
) -> list[dict[str, Any]]:
    """Return the replay attempt history for an ingestion event.

    Queries ``public.audit_log`` for rows where ``action='ingestion.event.replay'``
    and ``target`` equals the event UUID.  Results are returned in chronological
    order (oldest first).

    Each entry includes ``ts``, ``actor``, ``result``, and ``cost`` fields.
    ``result`` and ``cost`` are extracted from the ``note`` JSON payload when
    present; both default to ``None`` if the payload is absent or malformed.

    This function is safe to call even if ``public.audit_log`` has not yet
    been migrated — it returns an empty list gracefully.

    Args:
        pool: asyncpg connection pool for the shared credentials database.
        event_id: UUID of the ingestion event.

    Returns:
        List of dicts with keys ``ts``, ``actor``, ``result``, ``cost``.
        Empty list when no replay attempts are recorded.
    """
    if isinstance(event_id, str):
        try:
            event_id = UUID(event_id)
        except ValueError:
            return []

    try:
        rows = await pool.fetch(
            """
            SELECT ts, actor, note
            FROM public.audit_log
            WHERE action = 'ingestion.event.replay'
              AND target = $1
            ORDER BY ts ASC
            """,
            str(event_id),
        )
    except Exception:
        logger.debug(
            "ingestion_event_replay_history: DB query failed (table may not exist); returning []",
            exc_info=True,
        )
        return []

    entries: list[dict[str, Any]] = []
    for row in rows:
        result_val: str | None = None
        cost_val: float | None = None
        note = row["note"]
        if note:
            try:
                note_data = note if isinstance(note, dict) else json.loads(note)
                result_val = note_data.get("result")
                cost_val = note_data.get("cost")
            except (json.JSONDecodeError, TypeError, AttributeError):
                pass
        entries.append(
            {
                "ts": row["ts"],
                "actor": row["actor"],
                "result": result_val,
                "cost": cost_val,
            }
        )
    return entries
