"""Notification logging: log notification deliveries.

Valid ``notifications.status`` values (enforced by DB CHECK constraint):

  sent:   notification delivered successfully; the initial / "unread" state
  failed: delivery attempt failed; ``error`` column will be populated
  read:   user has acknowledged/dismissed the notification (set via API)

The default on INSERT is ``'sent'``.  Do not use ``'unread'``: that value
belongs to the mailbox module, not the notifications table.
"""

from __future__ import annotations

import json
from typing import Any

import asyncpg


async def log_notification(
    pool: asyncpg.Pool,
    source_butler: str,
    channel: str,
    recipient: str,
    message: str,
    metadata: dict[str, Any] | None = None,
    status: str = "sent",
    error: str | None = None,
    session_id: str | None = None,
    trace_id: str | None = None,
) -> str:
    """Log a notification delivery to the notifications table.

    Returns the UUID of the created notification log entry.

    Parameters
    ----------
    pool:
        Database connection pool.
    source_butler:
        Name of the butler that initiated the delivery.
    channel:
        Delivery channel (e.g., "telegram", "email").
    recipient:
        Recipient identifier (chat_id, email address, etc.).
    message:
        The notification message body.
    metadata:
        Optional additional metadata dict.
    status:
        Delivery status (e.g., "sent", "failed").
    error:
        Error message if delivery failed.
    session_id:
        Optional session UUID for tracing.
    trace_id:
        Optional OpenTelemetry trace ID.
    """
    # Schema-qualified: log_notification() is invoked from deliver()'s cross-
    # butler delivery path (e.g. secrets_lifecycle running against a
    # public-only search_path pool), so a bare `notifications` reference must
    # not depend on the caller's search_path including the switchboard schema.
    # Normalize non-JSON-native values in memory, then bind the resulting
    # mapping directly. The registered asyncpg JSONB codec serializes that
    # mapping once; passing json.dumps(...) here would double-encode it as a
    # JSONB string instead of an object.
    safe_metadata: dict[str, Any] = (
        json.loads(json.dumps(metadata, default=str)) if metadata else {}
    )

    row = await pool.fetchrow(
        """
        INSERT INTO switchboard.notifications
            (source_butler, channel, recipient, message, metadata, status, error,
             session_id, trace_id)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        RETURNING id
        """,
        source_butler,
        channel,
        recipient,
        message,
        safe_metadata,
        status,
        error,
        session_id,
        trace_id,
    )
    return str(row["id"])
