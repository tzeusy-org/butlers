"""Notification delivery — deliver notifications via channels."""

from __future__ import annotations

import json
import logging
from typing import Any

import asyncpg
from opentelemetry import trace

from butlers.tools.switchboard.notification.log import log_notification
from butlers.tools.switchboard.routing.route import route

logger = logging.getLogger(__name__)

# Maps channel names to (module_name, tool_name) tuples.
_CHANNEL_DISPATCH: dict[str, tuple[str, str]] = {
    "telegram": ("telegram", "bot_telegram_send_message"),
    "email": ("email", "bot_email_send_message"),
}

# Supported channels for validation
SUPPORTED_CHANNELS = frozenset(_CHANNEL_DISPATCH.keys())


def _build_channel_args(
    channel: str,
    message: str,
    recipient: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build module-specific tool arguments from deliver() parameters.

    Each channel has its own expected argument shape:
    - telegram: ``{"chat_id": recipient, "text": message}``
    - email: ``{"to": recipient, "subject": <from metadata or default>, "body": message}``
    """
    if channel == "telegram":
        return {"chat_id": recipient, "text": message}
    elif channel == "email":
        subject = (metadata or {}).get("subject", "Notification")
        return {"to": recipient, "subject": subject, "body": message}
    else:
        raise ValueError(f"Unsupported channel: {channel}")


async def deliver(
    pool: asyncpg.Pool,
    channel: str,
    message: str,
    recipient: str | None = None,
    metadata: dict[str, Any] | None = None,
    source_butler: str = "switchboard",
    *,
    call_fn: Any | None = None,
) -> dict[str, Any]:
    """Deliver a notification through the specified channel.

    Dispatches to the appropriate module (telegram/email), logs the delivery
    to the notifications table, and returns the result.  This is distinct from
    ``route()`` which forwards MCP tool calls to other butlers.

    Parameters
    ----------
    pool:
        Database connection pool.
    channel:
        Delivery channel — must be one of ``"telegram"`` or ``"email"``.
    message:
        The notification message to deliver.
    recipient:
        Recipient identifier (Telegram chat_id, email address, etc.).
        Required for all current channels.
    metadata:
        Optional metadata dict.  For email, ``metadata["subject"]`` sets
        the email subject line (defaults to ``"Notification"``).
    source_butler:
        Name of the butler initiating the delivery.
    call_fn:
        Optional callable for testing; forwarded to :func:`route`.

    Returns
    -------
    dict
        ``{"notification_id": "<uuid>", "status": "sent", "result": <route_result>}``
        on success, or ``{"notification_id": "<uuid>", "status": "failed",
        "error": "<description>"}`` on failure.
    """
    tracer = trace.get_tracer("butlers")
    with tracer.start_as_current_span("switchboard.deliver") as span:
        span.set_attribute("channel", channel)
        span.set_attribute("source_butler", source_butler)

        # 1. Validate channel
        if channel not in SUPPORTED_CHANNELS:
            error_msg = (
                f"Unsupported channel '{channel}'. "
                f"Supported channels: {', '.join(sorted(SUPPORTED_CHANNELS))}"
            )
            span.set_status(trace.StatusCode.ERROR, error_msg)
            return {"error": error_msg, "status": "failed"}

        # 2. Validate recipient
        if not recipient:
            error_msg = "Recipient is required for delivery"
            span.set_status(trace.StatusCode.ERROR, error_msg)
            return {"error": error_msg, "status": "failed"}

        # 3. Look up a butler that has the required module
        module_name, tool_name = _CHANNEL_DISPATCH[channel]

        row = await pool.fetchrow(
            """
            SELECT name FROM butler_registry
            WHERE modules::jsonb @> $1::jsonb
            ORDER BY name
            LIMIT 1
            """,
            json.dumps([module_name]),
        )

        if row is None:
            error_msg = f"No butler with '{module_name}' module found in registry"
            span.set_status(trace.StatusCode.ERROR, error_msg)
            notification_id = await log_notification(
                pool,
                source_butler=source_butler,
                channel=channel,
                recipient=recipient,
                message=message,
                metadata=metadata,
                status="failed",
                error=error_msg,
            )
            return {"notification_id": notification_id, "status": "failed", "error": error_msg}

        target_butler = row["name"]
        span.set_attribute("target_butler", target_butler)

        # 4. Build channel-specific args and route
        try:
            tool_args = _build_channel_args(channel, message, recipient, metadata)
        except ValueError as exc:
            error_msg = str(exc)
            span.set_status(trace.StatusCode.ERROR, error_msg)
            notification_id = await log_notification(
                pool,
                source_butler=source_butler,
                channel=channel,
                recipient=recipient,
                message=message,
                metadata=metadata,
                status="failed",
                error=error_msg,
            )
            return {"notification_id": notification_id, "status": "failed", "error": error_msg}

        route_result = await route(
            pool,
            target_butler=target_butler,
            tool_name=tool_name,
            args=tool_args,
            source_butler=source_butler,
            call_fn=call_fn,
        )

        # 5. Determine success and log
        if "error" in route_result:
            error_msg = route_result["error"]
            span.set_status(trace.StatusCode.ERROR, error_msg)
            notification_id = await log_notification(
                pool,
                source_butler=source_butler,
                channel=channel,
                recipient=recipient,
                message=message,
                metadata=metadata,
                status="failed",
                error=error_msg,
            )
            return {"notification_id": notification_id, "status": "failed", "error": error_msg}

        # Extract trace_id from current span context if available
        current_trace_id = None
        current_span = trace.get_current_span()
        if current_span and current_span.get_span_context().trace_id:
            current_trace_id = format(current_span.get_span_context().trace_id, "032x")

        notification_id = await log_notification(
            pool,
            source_butler=source_butler,
            channel=channel,
            recipient=recipient,
            message=message,
            metadata=metadata,
            status="sent",
            trace_id=current_trace_id,
        )

        return {
            "notification_id": notification_id,
            "status": "sent",
            "result": route_result.get("result"),
        }
