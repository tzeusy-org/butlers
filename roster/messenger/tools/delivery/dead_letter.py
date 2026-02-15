"""Dead letter management tools for Messenger butler.

Manage deliveries that exhausted retries or were manually quarantined.
See docs/roles/messenger_butler.md section 5.1.2 for the spec.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import asyncpg


async def messenger_dead_letter_list(
    pool: asyncpg.Pool,
    channel: str | None = None,
    origin_butler: str | None = None,
    error_class: str | None = None,
    since: str | None = None,
    limit: int = 50,
    include_discarded: bool = False,
) -> dict[str, Any]:
    """List dead-lettered deliveries with filters.

    Returns enough context (origin, channel, error class, failure summary) to
    triage without inspecting each one. Excludes discarded dead letters by default.

    Parameters
    ----------
    pool:
        Database connection pool.
    channel:
        Filter by channel (e.g., "telegram", "email").
    origin_butler:
        Filter by origin butler name.
    error_class:
        Filter by terminal error class.
    since:
        ISO timestamp - only include dead letters created on or after this time.
    limit:
        Maximum number of results (default 50, max 500).
    include_discarded:
        If True, include discarded dead letters (default False).

    Returns
    -------
    dict:
        Paginated dead letter summaries sorted by recency (newest first).
    """
    # Validate and cap limit
    if limit < 1:
        limit = 50
    if limit > 500:
        limit = 500

    # Build WHERE clauses
    conditions: list[str] = []
    params: list[Any] = []
    param_idx = 1

    # Exclude discarded by default
    if not include_discarded:
        conditions.append("discarded_at IS NULL")

    if channel is not None:
        # Join with delivery_requests to filter by channel
        conditions.append(f"dr.channel = ${param_idx}")
        params.append(channel)
        param_idx += 1

    if origin_butler is not None:
        conditions.append(f"dr.origin_butler = ${param_idx}")
        params.append(origin_butler)
        param_idx += 1

    if error_class is not None:
        conditions.append(f"ddl.error_class = ${param_idx}")
        params.append(error_class)
        param_idx += 1

    if since is not None:
        try:
            since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
            conditions.append(f"ddl.created_at >= ${param_idx}")
            params.append(since_dt)
            param_idx += 1
        except ValueError:
            return {"error": f"Invalid since timestamp format: {since}"}

    where_clause = " AND ".join(conditions) if conditions else "TRUE"

    # Add limit
    params.append(limit)
    limit_clause = f"${param_idx}"

    query = f"""
        SELECT
            ddl.id,
            ddl.delivery_request_id,
            dr.origin_butler,
            dr.channel,
            dr.intent,
            ddl.quarantine_reason,
            ddl.error_class,
            ddl.error_summary,
            ddl.total_attempts,
            ddl.first_attempt_at,
            ddl.last_attempt_at,
            ddl.replay_eligible,
            ddl.replay_count,
            ddl.discarded_at,
            ddl.created_at
        FROM delivery_dead_letter ddl
        JOIN delivery_requests dr ON ddl.delivery_request_id = dr.id
        WHERE {where_clause}
        ORDER BY ddl.created_at DESC
        LIMIT {limit_clause}
    """

    rows = await pool.fetch(query, *params)
    dead_letters = [dict(row) for row in rows]

    return {
        "dead_letters": dead_letters,
        "count": len(dead_letters),
        "limit": limit,
        "include_discarded": include_discarded,
    }


async def messenger_dead_letter_inspect(
    pool: asyncpg.Pool,
    dead_letter_id: str,
) -> dict[str, Any]:
    """Return the full dead letter record.

    Includes original request envelope, all attempt outcomes, quarantine reason,
    and replay eligibility assessment.

    Parameters
    ----------
    pool:
        Database connection pool.
    dead_letter_id:
        UUID of the dead letter record.

    Returns
    -------
    dict:
        Full dead letter record with original request envelope, all attempt
        outcomes, quarantine reason, and replay eligibility. Returns {"error": "..."}
        if dead_letter_id not found.
    """
    try:
        dead_letter_uuid = uuid.UUID(dead_letter_id)
    except ValueError:
        return {"error": f"Invalid dead_letter_id format: {dead_letter_id}"}

    # Fetch dead letter record with joined delivery_requests data
    row = await pool.fetchrow(
        """
        SELECT
            ddl.id,
            ddl.delivery_request_id,
            ddl.quarantine_reason,
            ddl.error_class,
            ddl.error_summary,
            ddl.total_attempts,
            ddl.first_attempt_at,
            ddl.last_attempt_at,
            ddl.original_request_envelope,
            ddl.all_attempt_outcomes,
            ddl.replay_eligible,
            ddl.replay_count,
            ddl.discarded_at,
            ddl.discard_reason,
            ddl.created_at,
            dr.origin_butler,
            dr.channel,
            dr.intent,
            dr.target_identity,
            dr.idempotency_key
        FROM delivery_dead_letter ddl
        JOIN delivery_requests dr ON ddl.delivery_request_id = dr.id
        WHERE ddl.id = $1
        """,
        dead_letter_uuid,
    )

    if row is None:
        return {"error": f"Dead letter not found: {dead_letter_id}"}

    dead_letter_dict = dict(row)

    # Add replay eligibility assessment
    dead_letter_dict["replay_eligibility_assessment"] = _assess_replay_eligibility(dead_letter_dict)

    return dead_letter_dict


async def messenger_dead_letter_replay(
    pool: asyncpg.Pool,
    dead_letter_id: str,
) -> dict[str, Any]:
    """Re-submit a dead-lettered delivery through the standard delivery pipeline.

    The replayed delivery gets a new attempt chain but preserves the original
    idempotency key lineage.

    Parameters
    ----------
    pool:
        Database connection pool.
    dead_letter_id:
        UUID of the dead letter record to replay.

    Returns
    -------
    dict:
        New delivery outcome with delivery_id for the replayed request.
        Returns {"error": "..."} if dead_letter_id not found or replay not eligible.
    """
    try:
        dead_letter_uuid = uuid.UUID(dead_letter_id)
    except ValueError:
        return {"error": f"Invalid dead_letter_id format: {dead_letter_id}"}

    async with pool.acquire() as conn:
        async with conn.transaction():
            # Fetch dead letter record
            dead_letter = await conn.fetchrow(
                """
                SELECT
                    ddl.id,
                    ddl.delivery_request_id,
                    ddl.original_request_envelope,
                    ddl.replay_eligible,
                    ddl.replay_count,
                    ddl.discarded_at,
                    dr.origin_butler,
                    dr.channel,
                    dr.intent,
                    dr.target_identity,
                    dr.message_content,
                    dr.subject,
                    dr.idempotency_key
                FROM delivery_dead_letter ddl
                JOIN delivery_requests dr ON ddl.delivery_request_id = dr.id
                WHERE ddl.id = $1
                FOR UPDATE
                """,
                dead_letter_uuid,
            )

            if dead_letter is None:
                return {"error": f"Dead letter not found: {dead_letter_id}"}

            # Check replay eligibility
            if not dead_letter["replay_eligible"]:
                return {
                    "error": "Dead letter is not eligible for replay",
                    "reason": "replay_eligible is false",
                }

            if dead_letter["discarded_at"] is not None:
                return {
                    "error": "Dead letter is not eligible for replay",
                    "reason": "dead letter has been discarded",
                }

            # Create new delivery request with replay lineage
            # Preserve original idempotency key lineage but add replay suffix
            new_idempotency_key = (
                f"{dead_letter['idempotency_key']}::replay-{dead_letter['replay_count'] + 1}"
            )

            new_delivery_id = await conn.fetchval(
                """
                INSERT INTO delivery_requests (
                    idempotency_key,
                    request_id,
                    origin_butler,
                    channel,
                    intent,
                    target_identity,
                    message_content,
                    subject,
                    request_envelope,
                    status
                )
                SELECT
                    $1,
                    request_id,
                    origin_butler,
                    channel,
                    intent,
                    target_identity,
                    message_content,
                    subject,
                    request_envelope,
                    'pending'
                FROM delivery_requests
                WHERE id = $2
                RETURNING id
                """,
                new_idempotency_key,
                dead_letter["delivery_request_id"],
            )

            # Increment replay count
            await conn.execute(
                """
                UPDATE delivery_dead_letter
                SET replay_count = replay_count + 1
                WHERE id = $1
                """,
                dead_letter_uuid,
            )

            return {
                "status": "ok",
                "replayed_delivery_id": str(new_delivery_id),
                "original_dead_letter_id": dead_letter_id,
                "replay_number": dead_letter["replay_count"] + 1,
                "message": "Dead letter replayed successfully. "
                "New delivery admitted to pending queue.",
            }


async def messenger_dead_letter_discard(
    pool: asyncpg.Pool,
    dead_letter_id: str,
    reason: str,
) -> dict[str, Any]:
    """Permanently mark a dead letter as discarded.

    Discarded dead letters are excluded from replay eligibility and list
    queries by default.

    Parameters
    ----------
    pool:
        Database connection pool.
    dead_letter_id:
        UUID of the dead letter record to discard.
    reason:
        Human-readable reason for discarding this dead letter.

    Returns
    -------
    dict:
        Confirmation of discard operation. Returns {"error": "..."}
        if dead_letter_id not found or already discarded.
    """
    try:
        dead_letter_uuid = uuid.UUID(dead_letter_id)
    except ValueError:
        return {"error": f"Invalid dead_letter_id format: {dead_letter_id}"}

    if not reason or not reason.strip():
        return {"error": "Discard reason is required and cannot be empty"}

    async with pool.acquire() as conn:
        async with conn.transaction():
            # Check if dead letter exists and is not already discarded
            existing = await conn.fetchrow(
                """
                SELECT id, discarded_at, discard_reason
                FROM delivery_dead_letter
                WHERE id = $1
                FOR UPDATE
                """,
                dead_letter_uuid,
            )

            if existing is None:
                return {"error": f"Dead letter not found: {dead_letter_id}"}

            if existing["discarded_at"] is not None:
                return {
                    "error": "Dead letter is already discarded",
                    "discarded_at": existing["discarded_at"],
                    "discard_reason": existing["discard_reason"],
                }

            # Mark as discarded
            discarded_at = await conn.fetchval(
                """
                UPDATE delivery_dead_letter
                SET
                    discarded_at = now(),
                    discard_reason = $2,
                    replay_eligible = false
                WHERE id = $1
                RETURNING discarded_at
                """,
                dead_letter_uuid,
                reason.strip(),
            )

            return {
                "status": "ok",
                "dead_letter_id": dead_letter_id,
                "discarded_at": discarded_at,
                "discard_reason": reason.strip(),
                "message": "Dead letter permanently discarded",
            }


def _assess_replay_eligibility(dead_letter: dict[str, Any]) -> dict[str, Any]:
    """Assess replay eligibility for a dead letter record.

    Parameters
    ----------
    dead_letter:
        Dead letter record dict.

    Returns
    -------
    dict:
        Replay eligibility assessment with eligible flag and reasons.
    """
    eligible = True
    reasons: list[str] = []

    if not dead_letter["replay_eligible"]:
        eligible = False
        reasons.append("replay_eligible flag is false")

    if dead_letter["discarded_at"] is not None:
        eligible = False
        reasons.append(f"discarded at {dead_letter['discarded_at']}")

    # Additional eligibility checks can be added here
    # e.g., max replay count, time-based expiry, error class blocklist

    return {
        "eligible": eligible,
        "reasons": reasons if not eligible else [],
        "current_replay_count": dead_letter["replay_count"],
    }
