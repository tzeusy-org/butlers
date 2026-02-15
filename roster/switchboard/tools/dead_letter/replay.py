"""Dead-letter replay with lineage preservation."""

from __future__ import annotations

import json
import uuid
from typing import Any

import asyncpg


async def replay_dead_letter_request(
    conn: asyncpg.Connection,
    *,
    dead_letter_id: uuid.UUID,
    operator_identity: str,
    reason: str,
) -> dict[str, Any]:
    """Replay a dead-lettered request with lineage preservation.

    This function:
    1. Fetches the dead-letter entry
    2. Re-ingests the request with original request_id preserved
    3. Updates the dead-letter entry with replay status
    4. Logs the replay action in operator_audit_log

    Args:
        conn: Database connection
        dead_letter_id: UUID of the dead-letter entry to replay
        operator_identity: Identity of the operator performing the replay
        reason: Reason for replay

    Returns:
        Result dict with replay outcome
    """
    # Fetch dead-letter entry
    dead_letter = await conn.fetchrow(
        """
        SELECT
            id,
            original_request_id,
            source_table,
            original_payload,
            request_context,
            replay_eligible,
            replayed_at
        FROM dead_letter_queue
        WHERE id = $1
        """,
        dead_letter_id,
    )

    if not dead_letter:
        return {
            "success": False,
            "error": "dead_letter_not_found",
            "message": f"No dead-letter entry found with id {dead_letter_id}",
        }

    if not dead_letter["replay_eligible"]:
        return {
            "success": False,
            "error": "not_replay_eligible",
            "message": "This request is not eligible for replay",
        }

    if dead_letter["replayed_at"]:
        return {
            "success": False,
            "error": "already_replayed",
            "message": f"This request was already replayed at {dead_letter['replayed_at']}",
        }

    # Re-ingest with original request_id preserved in request_context
    try:
        # Insert into message_inbox with replay metadata
        new_request_id = uuid.uuid4()
        # Parse JSONB fields (asyncpg returns them as strings)
        request_context_raw = dead_letter["request_context"]
        original_payload_raw = dead_letter["original_payload"]

        request_context = (
            json.loads(request_context_raw)
            if isinstance(request_context_raw, str)
            else request_context_raw
        )
        original_payload = (
            json.loads(original_payload_raw)
            if isinstance(original_payload_raw, str)
            else original_payload_raw
        )

        request_context = request_context.copy()
        request_context["replay_metadata"] = {
            "is_replay": True,
            "original_request_id": str(dead_letter["original_request_id"]),
            "dead_letter_id": str(dead_letter_id),
            "replay_operator": operator_identity,
            "replay_reason": reason,
        }

        await conn.execute(
            """
            INSERT INTO message_inbox (
                id,
                request_context,
                raw_payload,
                normalized_text,
                lifecycle_state,
                processing_metadata
            )
            VALUES ($1, $2::jsonb, $3::jsonb, $4, 'accepted', $5::jsonb)
            """,
            new_request_id,
            json.dumps(request_context),
            json.dumps(original_payload),
            original_payload.get("content", ""),
            json.dumps(
                {
                    "replayed_from_dead_letter": str(dead_letter_id),
                    "original_request_id": str(dead_letter["original_request_id"]),
                }
            ),
        )

        # Update dead-letter entry
        await conn.execute(
            """
            UPDATE dead_letter_queue
            SET
                replayed_at = now(),
                replayed_request_id = $1,
                replay_outcome = 'success',
                updated_at = now()
            WHERE id = $2
            """,
            new_request_id,
            dead_letter_id,
        )

        # Log in operator audit log
        await conn.execute(
            """
            INSERT INTO operator_audit_log (
                action_type,
                target_request_id,
                target_table,
                operator_identity,
                reason,
                action_payload,
                outcome,
                outcome_details
            )
            VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7, $8::jsonb)
            """,
            "controlled_replay",
            dead_letter["original_request_id"],
            "dead_letter_queue",
            operator_identity,
            reason,
            json.dumps(
                {
                    "dead_letter_id": str(dead_letter_id),
                    "new_request_id": str(new_request_id),
                }
            ),
            "success",
            json.dumps(
                {
                    "replayed_request_id": str(new_request_id),
                }
            ),
        )

        return {
            "success": True,
            "replayed_request_id": str(new_request_id),
            "original_request_id": str(dead_letter["original_request_id"]),
            "dead_letter_id": str(dead_letter_id),
        }

    except Exception as e:
        # Log failed replay attempt
        await conn.execute(
            """
            UPDATE dead_letter_queue
            SET
                replay_outcome = 'failed',
                updated_at = now()
            WHERE id = $1
            """,
            dead_letter_id,
        )

        await conn.execute(
            """
            INSERT INTO operator_audit_log (
                action_type,
                target_request_id,
                target_table,
                operator_identity,
                reason,
                action_payload,
                outcome,
                outcome_details
            )
            VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7, $8::jsonb)
            """,
            "controlled_replay",
            dead_letter["original_request_id"],
            "dead_letter_queue",
            operator_identity,
            reason,
            json.dumps({"dead_letter_id": str(dead_letter_id)}),
            "failed",
            json.dumps({"error": str(e)}),
        )

        return {
            "success": False,
            "error": "replay_failed",
            "message": f"Replay failed: {str(e)}",
        }


async def list_replay_eligible_requests(
    conn: asyncpg.Connection,
    *,
    limit: int = 100,
    failure_category: str | None = None,
) -> list[dict[str, Any]]:
    """List dead-letter requests eligible for replay.

    Args:
        conn: Database connection
        limit: Maximum number of results to return
        failure_category: Optional filter by failure category

    Returns:
        List of replay-eligible dead-letter entries
    """
    where_clause = "WHERE replay_eligible = true AND replayed_at IS NULL"
    params: list[Any] = [limit]

    if failure_category:
        where_clause += " AND failure_category = $2"
        params.insert(1, failure_category)

    rows = await conn.fetch(
        f"""
        SELECT
            id,
            original_request_id,
            source_table,
            failure_reason,
            failure_category,
            retry_count,
            created_at,
            request_context
        FROM dead_letter_queue
        {where_clause}
        ORDER BY created_at DESC
        LIMIT $1
        """,
        *params,
    )

    return [dict(row) for row in rows]
