"""Operator control functions for manual interventions."""

from __future__ import annotations

import json
import uuid
from typing import Any

import asyncpg


async def manual_reroute_request(
    conn: asyncpg.Connection,
    *,
    request_id: uuid.UUID,
    new_target_butler: str,
    operator_identity: str,
    reason: str,
) -> dict[str, Any]:
    """Manually reroute a request to a different butler.

    Args:
        conn: Database connection
        request_id: UUID of the request to reroute
        new_target_butler: Target butler name to reroute to
        operator_identity: Identity of the operator performing the reroute
        reason: Reason for manual reroute

    Returns:
        Result dict with reroute outcome
    """
    # Fetch current request state
    request = await conn.fetchrow(
        """
        SELECT
            id,
            lifecycle_state,
            dispatch_outcomes,
            request_context
        FROM message_inbox
        WHERE id = $1
        """,
        request_id,
    )

    if not request:
        return {
            "success": False,
            "error": "request_not_found",
            "message": f"No request found with id {request_id}",
        }

    if request["lifecycle_state"] in ("completed", "failed"):
        return {
            "success": False,
            "error": "request_already_terminal",
            "message": f"Request is in terminal state: {request['lifecycle_state']}",
        }

    try:
        # Update request with manual reroute annotation
        # Parse JSONB fields (asyncpg returns them as strings)
        request_context_raw = request["request_context"]
        request_context = (
            json.loads(request_context_raw)
            if isinstance(request_context_raw, str)
            else request_context_raw
        ).copy()

        request_context["manual_reroute"] = {
            "operator": operator_identity,
            "reason": reason,
            "original_target": (request.get("dispatch_outcomes") or {}).get("target"),
            "new_target": new_target_butler,
        }

        await conn.execute(
            """
            UPDATE message_inbox
            SET
                request_context = $1::jsonb,
                lifecycle_state = 'rerouted',
                updated_at = now()
            WHERE id = $2
            """,
            json.dumps(request_context),
            request_id,
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
            "manual_reroute",
            request_id,
            "message_inbox",
            operator_identity,
            reason,
            json.dumps(
                {
                    "new_target_butler": new_target_butler,
                }
            ),
            "success",
            json.dumps(
                {
                    "lifecycle_state": "rerouted",
                }
            ),
        )

        return {
            "success": True,
            "request_id": str(request_id),
            "new_target": new_target_butler,
            "lifecycle_state": "rerouted",
        }

    except Exception as e:
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
            "manual_reroute",
            request_id,
            "message_inbox",
            operator_identity,
            reason,
            json.dumps({"new_target_butler": new_target_butler}),
            "failed",
            json.dumps({"error": str(e)}),
        )

        return {
            "success": False,
            "error": "reroute_failed",
            "message": f"Reroute failed: {str(e)}",
        }


async def cancel_request(
    conn: asyncpg.Connection,
    *,
    request_id: uuid.UUID,
    operator_identity: str,
    reason: str,
) -> dict[str, Any]:
    """Cancel an in-flight request (safe cancellation).

    Args:
        conn: Database connection
        request_id: UUID of the request to cancel
        operator_identity: Identity of the operator performing the cancel
        reason: Reason for cancellation

    Returns:
        Result dict with cancel outcome
    """
    request = await conn.fetchrow(
        """
        SELECT
            id,
            lifecycle_state
        FROM message_inbox
        WHERE id = $1
        """,
        request_id,
    )

    if not request:
        return {
            "success": False,
            "error": "request_not_found",
            "message": f"No request found with id {request_id}",
        }

    if request["lifecycle_state"] in ("completed", "failed", "cancelled"):
        return {
            "success": False,
            "error": "request_already_terminal",
            "message": f"Request is in terminal state: {request['lifecycle_state']}",
        }

    try:
        await conn.execute(
            """
            UPDATE message_inbox
            SET
                lifecycle_state = 'cancelled',
                final_state_at = now(),
                response_summary = $1,
                updated_at = now()
            WHERE id = $2
            """,
            f"Cancelled by operator: {reason}",
            request_id,
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
            "cancel_request",
            request_id,
            "message_inbox",
            operator_identity,
            reason,
            json.dumps({}),
            "success",
            json.dumps({"lifecycle_state": "cancelled"}),
        )

        return {
            "success": True,
            "request_id": str(request_id),
            "lifecycle_state": "cancelled",
        }

    except Exception as e:
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
            "cancel_request",
            request_id,
            "message_inbox",
            operator_identity,
            reason,
            json.dumps({}),
            "failed",
            json.dumps({"error": str(e)}),
        )

        return {
            "success": False,
            "error": "cancel_failed",
            "message": f"Cancel failed: {str(e)}",
        }


async def abort_request(
    conn: asyncpg.Connection,
    *,
    request_id: uuid.UUID,
    operator_identity: str,
    reason: str,
) -> dict[str, Any]:
    """Abort an in-flight request (forceful termination).

    Similar to cancel but marks as 'aborted' to distinguish forceful termination.

    Args:
        conn: Database connection
        request_id: UUID of the request to abort
        operator_identity: Identity of the operator performing the abort
        reason: Reason for abort

    Returns:
        Result dict with abort outcome
    """
    request = await conn.fetchrow(
        """
        SELECT
            id,
            lifecycle_state
        FROM message_inbox
        WHERE id = $1
        """,
        request_id,
    )

    if not request:
        return {
            "success": False,
            "error": "request_not_found",
            "message": f"No request found with id {request_id}",
        }

    try:
        await conn.execute(
            """
            UPDATE message_inbox
            SET
                lifecycle_state = 'aborted',
                final_state_at = now(),
                response_summary = $1,
                updated_at = now()
            WHERE id = $2
            """,
            f"Aborted by operator: {reason}",
            request_id,
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
            "abort_request",
            request_id,
            "message_inbox",
            operator_identity,
            reason,
            json.dumps({}),
            "success",
            json.dumps({"lifecycle_state": "aborted"}),
        )

        return {
            "success": True,
            "request_id": str(request_id),
            "lifecycle_state": "aborted",
        }

    except Exception as e:
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
            "abort_request",
            request_id,
            "message_inbox",
            operator_identity,
            reason,
            json.dumps({}),
            "failed",
            json.dumps({"error": str(e)}),
        )

        return {
            "success": False,
            "error": "abort_failed",
            "message": f"Abort failed: {str(e)}",
        }


async def force_complete_request(
    conn: asyncpg.Connection,
    *,
    request_id: uuid.UUID,
    operator_identity: str,
    reason: str,
    completion_summary: str,
) -> dict[str, Any]:
    """Force-complete a request with explicit operator annotation.

    Args:
        conn: Database connection
        request_id: UUID of the request to force-complete
        operator_identity: Identity of the operator performing the force-complete
        reason: Reason for force-complete
        completion_summary: Summary to record as the completion result

    Returns:
        Result dict with force-complete outcome
    """
    request = await conn.fetchrow(
        """
        SELECT
            id,
            lifecycle_state
        FROM message_inbox
        WHERE id = $1
        """,
        request_id,
    )

    if not request:
        return {
            "success": False,
            "error": "request_not_found",
            "message": f"No request found with id {request_id}",
        }

    if request["lifecycle_state"] in ("completed", "failed"):
        return {
            "success": False,
            "error": "request_already_terminal",
            "message": f"Request is in terminal state: {request['lifecycle_state']}",
        }

    try:
        await conn.execute(
            """
            UPDATE message_inbox
            SET
                lifecycle_state = 'completed',
                final_state_at = now(),
                response_summary = $1,
                updated_at = now()
            WHERE id = $2
            """,
            f"Force-completed by operator ({operator_identity}): {completion_summary}",
            request_id,
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
            "force_complete",
            request_id,
            "message_inbox",
            operator_identity,
            reason,
            json.dumps({"completion_summary": completion_summary}),
            "success",
            json.dumps({"lifecycle_state": "completed"}),
        )

        return {
            "success": True,
            "request_id": str(request_id),
            "lifecycle_state": "completed",
            "completion_summary": completion_summary,
        }

    except Exception as e:
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
            "force_complete",
            request_id,
            "message_inbox",
            operator_identity,
            reason,
            json.dumps({"completion_summary": completion_summary}),
            "failed",
            json.dumps({"error": str(e)}),
        )

        return {
            "success": False,
            "error": "force_complete_failed",
            "message": f"Force-complete failed: {str(e)}",
        }
