"""route_inbox — durable work queue for async route dispatch (Section 4.4).

When route.execute is called on a target butler, the request is persisted here
before returning {"status": "accepted"} to the switchboard.  A background task
then processes the request by calling spawner.trigger().

Lifecycle states:
    accepted   — persisted, not yet processed
    processing — background task has started (set before trigger())
    processed  — trigger() completed successfully
    errored    — trigger() raised an exception; error column populated

Crash recovery: on startup the daemon scans for rows in 'accepted' state and
re-dispatches them.  The scanner uses the same received_at grace period as
the DurableBuffer scanner to avoid racing the hot path.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)

# Lifecycle states
STATE_ACCEPTED = "accepted"
STATE_PROCESSING = "processing"
STATE_PROCESSED = "processed"
STATE_ERRORED = "errored"

# Default grace period before crash-recovery scanner picks up stuck rows (seconds)
_DEFAULT_RECOVERY_GRACE_S = 10
# Default scanner batch size
_DEFAULT_RECOVERY_BATCH = 50


async def route_inbox_insert(
    pool: asyncpg.Pool,
    *,
    route_envelope: dict[str, Any],
) -> uuid.UUID:
    """Insert a new route_inbox row in 'accepted' state.

    Parameters
    ----------
    pool:
        asyncpg connection pool for this butler's database.
    route_envelope:
        The full validated route envelope dict (JSON-serialisable).

    Returns
    -------
    uuid.UUID
        The newly created row id.
    """
    import json

    row_id = uuid.uuid4()
    envelope_json = json.dumps(route_envelope)
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO route_inbox (id, route_envelope, lifecycle_state)
            VALUES ($1, $2::jsonb, $3)
            """,
            row_id,
            envelope_json,
            STATE_ACCEPTED,
        )
    logger.debug("route_inbox: inserted id=%s", row_id)
    return row_id


async def route_inbox_mark_processing(
    pool: asyncpg.Pool,
    row_id: uuid.UUID,
) -> None:
    """Transition a route_inbox row from 'accepted' to 'processing'."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE route_inbox
            SET lifecycle_state = $1
            WHERE id = $2
              AND lifecycle_state = $3
            """,
            STATE_PROCESSING,
            row_id,
            STATE_ACCEPTED,
        )


async def route_inbox_mark_processed(
    pool: asyncpg.Pool,
    row_id: uuid.UUID,
    session_id: uuid.UUID | None,
) -> None:
    """Transition a route_inbox row to 'processed' on success."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE route_inbox
            SET lifecycle_state = $1,
                processed_at = now(),
                session_id = $2
            WHERE id = $3
            """,
            STATE_PROCESSED,
            session_id,
            row_id,
        )
    logger.debug("route_inbox: processed id=%s session_id=%s", row_id, session_id)


async def route_inbox_mark_errored(
    pool: asyncpg.Pool,
    row_id: uuid.UUID,
    error: str,
) -> None:
    """Transition a route_inbox row to 'errored' and store the error message."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE route_inbox
            SET lifecycle_state = $1,
                processed_at = now(),
                error = $2
            WHERE id = $3
            """,
            STATE_ERRORED,
            error,
            row_id,
        )
    logger.warning("route_inbox: errored id=%s error=%s", row_id, error[:200])


async def route_inbox_scan_unprocessed(
    pool: asyncpg.Pool,
    *,
    grace_s: int = _DEFAULT_RECOVERY_GRACE_S,
    batch_size: int = _DEFAULT_RECOVERY_BATCH,
) -> list[dict[str, Any]]:
    """Scan for route_inbox rows stuck in 'accepted' or 'processing' state.

    Returns rows older than *grace_s* seconds that have not completed
    processing.  Used for crash recovery on startup.

    Both 'accepted' and 'processing' rows are included because a daemon crash
    or graceful shutdown (which cancels in-flight background tasks) can leave
    rows in 'processing' state with no task to complete them.

    Parameters
    ----------
    pool:
        asyncpg connection pool for this butler's database.
    grace_s:
        Minimum age in seconds before a row is considered stuck.
    batch_size:
        Maximum number of rows to return per call.

    Returns
    -------
    list[dict]
        Each dict has keys: id, received_at, route_envelope.
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, received_at, route_envelope
            FROM route_inbox
            WHERE lifecycle_state = ANY($1)
              AND received_at < now() - ($2 * interval '1 second')
            ORDER BY received_at ASC
            LIMIT $3
            """,
            [STATE_ACCEPTED, STATE_PROCESSING],
            grace_s,
            batch_size,
        )

    result = []
    for row in rows:
        result.append(
            {
                "id": row["id"],
                "received_at": row["received_at"],
                "route_envelope": dict(row["route_envelope"]),
            }
        )
    logger.debug("route_inbox scan: found %d unprocessed row(s)", len(result))
    return result


async def route_inbox_recovery_sweep(
    pool: asyncpg.Pool,
    *,
    grace_s: int = _DEFAULT_RECOVERY_GRACE_S,
    batch_size: int = _DEFAULT_RECOVERY_BATCH,
    dispatch_fn: Any,
) -> int:
    """Recover and re-dispatch stuck route_inbox rows.

    Called on startup (and optionally periodically) to process rows that were
    accepted but never processed due to a crash or restart.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    grace_s:
        Minimum age for a row to be considered stuck.
    batch_size:
        Maximum rows per sweep.
    dispatch_fn:
        Async callable with signature ``dispatch_fn(row_id, route_envelope) -> None``.
        Typically wraps the spawner.trigger() call.

    Returns
    -------
    int
        Number of rows recovered (dispatched for re-processing).
    """
    rows = await route_inbox_scan_unprocessed(pool, grace_s=grace_s, batch_size=batch_size)
    if not rows:
        return 0

    recovered = 0
    now = datetime.now(UTC)
    for row in rows:
        row_id = row["id"]
        route_envelope = row["route_envelope"]
        age_s = (now - row["received_at"].replace(tzinfo=UTC)).total_seconds()
        logger.info(
            "route_inbox recovery: re-dispatching id=%s (age=%.0fs)",
            row_id,
            age_s,
        )
        try:
            await dispatch_fn(row_id=row_id, route_envelope=route_envelope)
            recovered += 1
        except Exception:
            logger.exception("route_inbox recovery: dispatch failed for id=%s", row_id)

    if recovered:
        logger.info("route_inbox recovery sweep: recovered %d row(s)", recovered)
    return recovered
