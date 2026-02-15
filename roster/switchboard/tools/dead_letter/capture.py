"""Dead-letter capture for exhausted/failed requests."""

from __future__ import annotations

import json
import uuid
from typing import Any

import asyncpg


async def capture_to_dead_letter(
    conn: asyncpg.Connection,
    *,
    original_request_id: uuid.UUID,
    source_table: str,
    failure_reason: str,
    failure_category: str,
    retry_count: int,
    last_retry_at: str | None,
    original_payload: dict[str, Any],
    request_context: dict[str, Any],
    error_details: dict[str, Any],
    replay_eligible: bool = True,
) -> uuid.UUID:
    """Capture a failed request to dead-letter queue.

    Args:
        conn: Database connection
        original_request_id: UUID of the original request
        source_table: Table where the request originated (e.g., 'message_inbox')
        failure_reason: Human-readable failure description
        failure_category: Category from allowed set (timeout, retry_exhausted, etc.)
        retry_count: Number of retry attempts made
        last_retry_at: Timestamp of last retry attempt (ISO format or None)
        original_payload: Original request payload (JSONB)
        request_context: Request context metadata (JSONB)
        error_details: Additional error information (JSONB)
        replay_eligible: Whether this request can be replayed (default: True)

    Returns:
        UUID of the created dead-letter entry
    """
    result = await conn.fetchrow(
        """
        INSERT INTO dead_letter_queue (
            original_request_id,
            source_table,
            failure_reason,
            failure_category,
            retry_count,
            last_retry_at,
            original_payload,
            request_context,
            error_details,
            replay_eligible
        ) VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8::jsonb, $9::jsonb, $10)
        RETURNING id
        """,
        original_request_id,
        source_table,
        failure_reason,
        failure_category,
        retry_count,
        last_retry_at,
        json.dumps(original_payload),
        json.dumps(request_context),
        json.dumps(error_details),
        replay_eligible,
    )
    return result["id"]


async def get_dead_letter_stats(
    conn: asyncpg.Connection,
    *,
    since: str | None = None,
) -> dict[str, Any]:
    """Get dead-letter queue statistics.

    Args:
        conn: Database connection
        since: Optional ISO timestamp to filter from

    Returns:
        Statistics dict with counts by category and replay status
    """
    where_clause = "WHERE created_at >= $1" if since else ""
    params = [since] if since else []

    rows = await conn.fetch(
        f"""
        SELECT
            failure_category,
            replay_eligible,
            COUNT(*) as count,
            COUNT(*) FILTER (WHERE replayed_at IS NOT NULL) as replayed_count
        FROM dead_letter_queue
        {where_clause}
        GROUP BY failure_category, replay_eligible
        ORDER BY failure_category, replay_eligible
        """,
        *params,
    )

    return {
        "by_category": [
            {
                "category": row["failure_category"],
                "replay_eligible": row["replay_eligible"],
                "total": row["count"],
                "replayed": row["replayed_count"],
            }
            for row in rows
        ],
        "total": sum(row["count"] for row in rows),
    }
