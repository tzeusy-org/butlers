"""Extraction confirmation queue — holds low-confidence extractions for user review.

Low/medium confidence signal extractions are queued here instead of being
dispatched directly. The user (or a runtime instance during a tick) can review
pending items and confirm or dismiss them.

Statuses: pending -> confirmed | dismissed | expired
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)

# Valid queue statuses
VALID_STATUSES = {"pending", "confirmed", "dismissed", "expired"}

# Valid resolution actions
VALID_ACTIONS = {"confirm", "dismiss"}

# Default TTL in days before auto-expiry
DEFAULT_TTL_DAYS = 7


async def extraction_queue_add(
    pool: asyncpg.Pool,
    source_message: str,
    extraction_type: str,
    extraction_data: dict[str, Any],
    confidence: str,
    ttl_days: int = DEFAULT_TTL_DAYS,
) -> dict[str, Any]:
    """Add an extraction to the confirmation queue.

    Parameters
    ----------
    pool:
        Database connection pool.
    source_message:
        The original message that triggered the extraction.
    extraction_type:
        Type of extraction (e.g., 'interaction', 'contact', 'date').
    extraction_data:
        JSONB payload with extraction details.
    confidence:
        Confidence level (e.g., 'low', 'medium').
    ttl_days:
        Number of days before auto-expiry. Defaults to 7.

    Returns
    -------
    dict with the created queue entry.
    """
    row = await pool.fetchrow(
        """
        INSERT INTO extraction_queue
            (source_message, extraction_type, extraction_data, confidence, ttl_days)
        VALUES ($1, $2, $3::jsonb, $4, $5)
        RETURNING *
        """,
        source_message,
        extraction_type,
        json.dumps(extraction_data),
        confidence,
        ttl_days,
    )
    return _parse_row(row)


async def extraction_queue_list(
    pool: asyncpg.Pool,
    status: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """List extraction queue entries, optionally filtered by status.

    Parameters
    ----------
    pool:
        Database connection pool.
    status:
        If provided, filter to this status only. Must be a valid status.
    limit:
        Maximum number of entries to return. Defaults to 50.

    Returns
    -------
    List of queue entries, ordered by creation time (newest first).
    """
    if status is not None:
        if status not in VALID_STATUSES:
            raise ValueError(f"Invalid status '{status}'. Must be one of {sorted(VALID_STATUSES)}")
        rows = await pool.fetch(
            """
            SELECT * FROM extraction_queue
            WHERE status = $1
            ORDER BY created_at DESC
            LIMIT $2
            """,
            status,
            limit,
        )
    else:
        rows = await pool.fetch(
            """
            SELECT * FROM extraction_queue
            ORDER BY created_at DESC
            LIMIT $1
            """,
            limit,
        )
    return [_parse_row(row) for row in rows]


async def extraction_queue_resolve(
    pool: asyncpg.Pool,
    entry_id: uuid.UUID,
    action: str,
    resolved_by: str = "user",
    *,
    dispatch_fn: Any | None = None,
) -> dict[str, Any]:
    """Resolve a pending extraction by confirming or dismissing it.

    Parameters
    ----------
    pool:
        Database connection pool.
    entry_id:
        UUID of the queue entry to resolve.
    action:
        Either 'confirm' or 'dismiss'.
    resolved_by:
        Who resolved this (e.g., 'user', 'auto-expiry', butler name).
    dispatch_fn:
        Optional async callable invoked when action is 'confirm'.
        Signature: ``async (extraction_type, extraction_data) -> Any``.
        Used to dispatch confirmed extractions to the Relationship butler.

    Returns
    -------
    dict with the updated queue entry.

    Raises
    ------
    ValueError
        If the entry is not found, not pending, or action is invalid.
    """
    if action not in VALID_ACTIONS:
        raise ValueError(f"Invalid action '{action}'. Must be one of {sorted(VALID_ACTIONS)}")

    # Fetch the entry
    row = await pool.fetchrow(
        "SELECT * FROM extraction_queue WHERE id = $1",
        entry_id,
    )
    if row is None:
        raise ValueError(f"Extraction queue entry {entry_id} not found")
    if row["status"] != "pending":
        raise ValueError(
            f"Cannot resolve entry {entry_id}: status is '{row['status']}', expected 'pending'"
        )

    new_status = "confirmed" if action == "confirm" else "dismissed"

    updated = await pool.fetchrow(
        """
        UPDATE extraction_queue
        SET status = $2, resolved_at = now(), resolved_by = $3
        WHERE id = $1
        RETURNING *
        """,
        entry_id,
        new_status,
        resolved_by,
    )
    result = _parse_row(updated)

    # If confirmed, dispatch to the Relationship butler
    if action == "confirm" and dispatch_fn is not None:
        try:
            await dispatch_fn(result["extraction_type"], result["extraction_data"])
        except Exception:
            logger.exception(
                "Failed to dispatch confirmed extraction %s to Relationship butler",
                entry_id,
            )

    return result


async def extraction_queue_stats(pool: asyncpg.Pool) -> dict[str, int]:
    """Get counts of queue entries grouped by status.

    Returns
    -------
    dict mapping status names to their counts.
    """
    rows = await pool.fetch(
        """
        SELECT status, COUNT(*) as count
        FROM extraction_queue
        GROUP BY status
        ORDER BY status
        """
    )
    stats: dict[str, int] = {s: 0 for s in sorted(VALID_STATUSES)}
    for row in rows:
        stats[row["status"]] = row["count"]
    return stats


async def extraction_queue_expire(
    pool: asyncpg.Pool,
    now: datetime | None = None,
) -> int:
    """Expire pending entries older than their TTL.

    Each entry has its own ``ttl_days`` field. Entries whose
    ``created_at + ttl_days`` is in the past are marked as 'expired'.

    Parameters
    ----------
    pool:
        Database connection pool.
    now:
        Current time override (useful for testing). Defaults to UTC now.

    Returns
    -------
    Number of entries expired.
    """
    if now is None:
        now = datetime.now(UTC)

    result = await pool.execute(
        """
        UPDATE extraction_queue
        SET status = 'expired', resolved_at = $1, resolved_by = 'auto-expiry'
        WHERE status = 'pending'
          AND created_at + (ttl_days || ' days')::interval < $1
        """,
        now,
    )
    # asyncpg returns "UPDATE N" string
    count = int(result.split()[-1])
    logger.info("Expired %d extraction queue entries", count)
    return count


async def extraction_queue_get(
    pool: asyncpg.Pool,
    entry_id: uuid.UUID,
) -> dict[str, Any]:
    """Get a single extraction queue entry by ID.

    Raises
    ------
    ValueError
        If the entry is not found.
    """
    row = await pool.fetchrow(
        "SELECT * FROM extraction_queue WHERE id = $1",
        entry_id,
    )
    if row is None:
        raise ValueError(f"Extraction queue entry {entry_id} not found")
    return _parse_row(row)


async def extraction_queue_pending_count(pool: asyncpg.Pool) -> int:
    """Return the count of pending extractions.

    Useful for the Switchboard tick handler to surface pending count
    so a runtime instance can proactively ask the user about them.
    """
    count = await pool.fetchval("SELECT COUNT(*) FROM extraction_queue WHERE status = 'pending'")
    return count


def _parse_row(row: asyncpg.Record) -> dict[str, Any]:
    """Convert a queue row to a dict, parsing JSONB extraction_data."""
    d = dict(row)
    if isinstance(d.get("extraction_data"), str):
        d["extraction_data"] = json.loads(d["extraction_data"])
    return d
