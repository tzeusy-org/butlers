"""Delivery tracking tools for Messenger butler.

Query delivery state and history from the durable delivery persistence tables.
See docs/roles/messenger_butler.md section 5.1.1 for the spec.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import asyncpg


async def messenger_delivery_status(
    pool: asyncpg.Pool,
    delivery_id: str,
) -> dict[str, Any]:
    """Return the current terminal or in-flight status of a single delivery.

    Parameters
    ----------
    pool:
        Database connection pool.
    delivery_id:
        UUID of the delivery request.

    Returns
    -------
    dict:
        Delivery status with latest attempt outcome and provider delivery ID
        when available. Returns {"error": "..."} if delivery_id not found.
    """
    try:
        delivery_uuid = uuid.UUID(delivery_id)
    except ValueError:
        return {"error": f"Invalid delivery_id format: {delivery_id}"}

    # Fetch delivery request
    delivery_row = await pool.fetchrow(
        """
        SELECT id, idempotency_key, request_id, origin_butler, channel, intent,
               target_identity, status, terminal_error_class, terminal_error_message,
               created_at, updated_at, terminal_at
        FROM delivery_requests
        WHERE id = $1
        """,
        delivery_uuid,
    )

    if delivery_row is None:
        return {"error": f"Delivery not found: {delivery_id}"}

    delivery_dict = dict(delivery_row)

    # Fetch latest attempt
    latest_attempt = await pool.fetchrow(
        """
        SELECT attempt_number, started_at, completed_at, latency_ms,
               outcome, error_class, error_message
        FROM delivery_attempts
        WHERE delivery_request_id = $1
        ORDER BY attempt_number DESC
        LIMIT 1
        """,
        delivery_uuid,
    )

    if latest_attempt:
        delivery_dict["latest_attempt"] = dict(latest_attempt)
    else:
        delivery_dict["latest_attempt"] = None

    # Fetch provider delivery ID from receipts if available
    receipt_row = await pool.fetchrow(
        """
        SELECT provider_delivery_id
        FROM delivery_receipts
        WHERE delivery_request_id = $1 AND provider_delivery_id IS NOT NULL
        ORDER BY received_at DESC
        LIMIT 1
        """,
        delivery_uuid,
    )

    if receipt_row and receipt_row["provider_delivery_id"]:
        delivery_dict["provider_delivery_id"] = receipt_row["provider_delivery_id"]
    else:
        delivery_dict["provider_delivery_id"] = None

    return delivery_dict


async def messenger_delivery_search(
    pool: asyncpg.Pool,
    origin_butler: str | None = None,
    channel: str | None = None,
    intent: str | None = None,
    status: str | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Search delivery history with filters.

    Parameters
    ----------
    pool:
        Database connection pool.
    origin_butler:
        Filter by origin butler name.
    channel:
        Filter by channel (e.g., "telegram", "email").
    intent:
        Filter by intent ("send" or "reply").
    status:
        Filter by status ("pending", "in_progress", "delivered", "failed", "dead_lettered").
    since:
        ISO timestamp - only include deliveries created on or after this time.
    until:
        ISO timestamp - only include deliveries created before or at this time.
    limit:
        Maximum number of results (default 50, max 500).

    Returns
    -------
    dict:
        Paginated delivery summaries sorted by recency (newest first).
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

    if origin_butler is not None:
        conditions.append(f"origin_butler = ${param_idx}")
        params.append(origin_butler)
        param_idx += 1

    if channel is not None:
        conditions.append(f"channel = ${param_idx}")
        params.append(channel)
        param_idx += 1

    if intent is not None:
        conditions.append(f"intent = ${param_idx}")
        params.append(intent)
        param_idx += 1

    if status is not None:
        conditions.append(f"status = ${param_idx}")
        params.append(status)
        param_idx += 1

    if since is not None:
        try:
            since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
            conditions.append(f"created_at >= ${param_idx}")
            params.append(since_dt)
            param_idx += 1
        except ValueError:
            return {"error": f"Invalid since timestamp format: {since}"}

    if until is not None:
        try:
            until_dt = datetime.fromisoformat(until.replace("Z", "+00:00"))
            conditions.append(f"created_at <= ${param_idx}")
            params.append(until_dt)
            param_idx += 1
        except ValueError:
            return {"error": f"Invalid until timestamp format: {until}"}

    where_clause = " AND ".join(conditions) if conditions else "TRUE"

    # Add limit
    params.append(limit)
    limit_clause = f"${param_idx}"

    query = f"""
        SELECT id, idempotency_key, request_id, origin_butler, channel, intent,
               target_identity, status, terminal_error_class,
               created_at, updated_at, terminal_at
        FROM delivery_requests
        WHERE {where_clause}
        ORDER BY created_at DESC
        LIMIT {limit_clause}
    """

    rows = await pool.fetch(query, *params)
    deliveries = [dict(row) for row in rows]

    return {
        "deliveries": deliveries,
        "count": len(deliveries),
        "limit": limit,
    }


async def messenger_delivery_attempts(
    pool: asyncpg.Pool,
    delivery_id: str,
) -> dict[str, Any]:
    """Return the full attempt log for a delivery.

    Parameters
    ----------
    pool:
        Database connection pool.
    delivery_id:
        UUID of the delivery request.

    Returns
    -------
    dict:
        Full attempt log with timestamps, outcomes, latencies, error classes,
        and retryability. Essential for diagnosing flaky provider behavior.
    """
    try:
        delivery_uuid = uuid.UUID(delivery_id)
    except ValueError:
        return {"error": f"Invalid delivery_id format: {delivery_id}"}

    # Verify delivery exists
    delivery_exists = await pool.fetchval(
        "SELECT EXISTS(SELECT 1 FROM delivery_requests WHERE id = $1)",
        delivery_uuid,
    )

    if not delivery_exists:
        return {"error": f"Delivery not found: {delivery_id}"}

    # Fetch all attempts ordered by attempt number
    attempts = await pool.fetch(
        """
        SELECT id, attempt_number, started_at, completed_at, latency_ms,
               outcome, error_class, error_message, provider_response
        FROM delivery_attempts
        WHERE delivery_request_id = $1
        ORDER BY attempt_number ASC
        """,
        delivery_uuid,
    )

    attempts_list = [dict(row) for row in attempts]

    return {
        "delivery_id": delivery_id,
        "attempts": attempts_list,
        "total_attempts": len(attempts_list),
    }


async def messenger_delivery_trace(
    pool: asyncpg.Pool,
    request_id: str,
) -> dict[str, Any]:
    """Reconstruct full lineage for a request.

    Traces from the originating butler's notify.v1 envelope through Switchboard
    routing, Messenger admission, validation, target resolution, provider
    attempts, and terminal outcome.

    Parameters
    ----------
    pool:
        Database connection pool.
    request_id:
        UUID of the request to trace.

    Returns
    -------
    dict:
        Full delivery lineage joined across delivery_requests, delivery_attempts,
        and delivery_receipts. Returns {"error": "..."} if request_id not found
        or if no deliveries match.
    """
    try:
        request_uuid = uuid.UUID(request_id)
    except ValueError:
        return {"error": f"Invalid request_id format: {request_id}"}

    # Fetch delivery requests for this request_id
    delivery_rows = await pool.fetch(
        """
        SELECT id, idempotency_key, request_id, origin_butler, channel, intent,
               target_identity, message_content, subject, request_envelope,
               status, terminal_error_class, terminal_error_message,
               created_at, updated_at, terminal_at
        FROM delivery_requests
        WHERE request_id = $1
        ORDER BY created_at ASC
        """,
        request_uuid,
    )

    if not delivery_rows:
        return {"error": f"No deliveries found for request_id: {request_id}"}

    deliveries: list[dict[str, Any]] = []

    for delivery_row in delivery_rows:
        delivery_dict = dict(delivery_row)
        delivery_id = delivery_dict["id"]

        # Fetch attempts for this delivery
        attempts = await pool.fetch(
            """
            SELECT id, attempt_number, started_at, completed_at, latency_ms,
                   outcome, error_class, error_message, provider_response
            FROM delivery_attempts
            WHERE delivery_request_id = $1
            ORDER BY attempt_number ASC
            """,
            delivery_id,
        )
        delivery_dict["attempts"] = [dict(row) for row in attempts]

        # Fetch receipts for this delivery
        receipts = await pool.fetch(
            """
            SELECT id, provider_delivery_id, receipt_type, received_at, metadata
            FROM delivery_receipts
            WHERE delivery_request_id = $1
            ORDER BY received_at ASC
            """,
            delivery_id,
        )
        delivery_dict["receipts"] = [dict(row) for row in receipts]

        deliveries.append(delivery_dict)

    return {
        "request_id": request_id,
        "deliveries": deliveries,
        "delivery_count": len(deliveries),
    }
