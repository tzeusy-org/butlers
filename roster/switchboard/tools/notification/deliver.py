"""Notification delivery — deliver notifications via channels."""

from __future__ import annotations

import json
import logging
import secrets
import uuid
from datetime import UTC, datetime
from typing import Any

import asyncpg
from opentelemetry import trace
from pydantic import ValidationError

from butlers.tools.switchboard.notification.log import log_notification
from butlers.tools.switchboard.routing.contracts import (
    NotifyRequestV1,
    RouteRequestContextV1,
    parse_notify_request,
    parse_route_envelope,
)
from butlers.tools.switchboard.routing.route import route

logger = logging.getLogger(__name__)
MESSENGER_BUTLER_NAME = "messenger"
_NOTIFY_ROUTE_PROMPT = "Execute outbound delivery request through Messenger."
_DEFAULT_NOTIFY_SOURCE_CHANNEL = "mcp"

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


def _uuid7_string() -> str:
    """Generate a UUIDv7-compatible string."""
    timestamp_ms = int(datetime.now(UTC).timestamp() * 1000) & ((1 << 48) - 1)
    rand_a = secrets.randbits(12)
    rand_b = secrets.randbits(62)

    value = timestamp_ms << 80
    value |= 0x7 << 76
    value |= rand_a << 64
    value |= 0b10 << 62
    value |= rand_b
    return str(uuid.UUID(int=value))


def _default_notify_request_context(source_butler: str) -> RouteRequestContextV1:
    return RouteRequestContextV1.model_validate(
        {
            "request_id": _uuid7_string(),
            "received_at": datetime.now(UTC).isoformat(),
            "source_channel": _DEFAULT_NOTIFY_SOURCE_CHANNEL,
            "source_endpoint_identity": f"butler:{source_butler}",
            "source_sender_identity": source_butler,
        }
    )


def _build_notify_route_envelope(
    notify_request: NotifyRequestV1,
    *,
    request_context: RouteRequestContextV1,
) -> dict[str, Any]:
    return {
        "schema_version": "route.v1",
        "request_context": request_context.model_dump(mode="json"),
        "target": {"butler": MESSENGER_BUTLER_NAME, "tool": "route.execute"},
        "input": {
            "prompt": _NOTIFY_ROUTE_PROMPT,
            "context": {"notify_request": notify_request.model_dump(mode="json")},
        },
    }


def _extract_notify_response(route_result: Any) -> dict[str, Any] | None:
    if not isinstance(route_result, dict):
        return None

    if isinstance(route_result.get("notify_response"), dict):
        return route_result["notify_response"]

    nested_result = route_result.get("result")
    if isinstance(nested_result, dict) and isinstance(nested_result.get("notify_response"), dict):
        return nested_result["notify_response"]

    return None


async def _write_outbound_message_inbox(
    pool: asyncpg.Pool,
    *,
    notify_request: NotifyRequestV1,
    delivered_at: datetime,
) -> None:
    """Write a delivered outbound message to message_inbox for conversation history.

    Only writes rows when source_thread_identity is available (i.e., reply-intent
    messages where the thread context is known). Send-intent messages without a
    thread identity are skipped because they cannot be correlated with inbound history.

    Errors are logged but never propagate — the delivery has already succeeded.
    """
    ctx = notify_request.request_context
    if ctx is None or ctx.source_thread_identity is None:
        return

    thread_identity = ctx.source_thread_identity
    channel = notify_request.delivery.channel
    origin_butler = notify_request.origin_butler
    message_text = notify_request.delivery.message

    request_context_payload = {
        "source_channel": channel,
        "source_endpoint_identity": f"butler:{origin_butler}",
        "source_sender_identity": origin_butler,
        "source_thread_identity": thread_identity,
    }
    raw_payload = {
        "content": message_text,
        "metadata": {"origin_butler": origin_butler},
    }

    try:
        await pool.execute(
            """
            INSERT INTO message_inbox (
                received_at,
                request_context,
                raw_payload,
                normalized_text,
                direction,
                lifecycle_state,
                schema_version
            ) VALUES (
                $1, $2::jsonb, $3::jsonb, $4, 'outbound', 'completed', 'message_inbox.v2'
            )
            """,
            delivered_at,
            json.dumps(request_context_payload),
            json.dumps(raw_payload),
            message_text,
        )
    except Exception:
        logger.exception(
            "Failed to write outbound message to message_inbox",
            extra={
                "origin_butler": origin_butler,
                "channel": channel,
                "thread_identity": thread_identity,
            },
        )


async def _deliver_via_notify_request(
    pool: asyncpg.Pool,
    *,
    notify_request: NotifyRequestV1,
    request_context: RouteRequestContextV1,
    source_butler: str,
    metadata: dict[str, Any] | None,
    call_fn: Any | None,
) -> dict[str, Any]:
    channel = notify_request.delivery.channel
    recipient = notify_request.delivery.recipient or ""
    message = notify_request.delivery.message
    log_metadata = dict(metadata or {})
    log_metadata["notify_request"] = notify_request.model_dump(mode="json")
    log_metadata["request_context"] = request_context.model_dump(mode="json")

    # Create a switchboard-scoped context for the route.v1 envelope so the
    # messenger's authz check sees source_endpoint_identity="switchboard".
    # The original ingestion context is already preserved inside
    # input.context.notify_request for the messenger's reply targeting.
    route_context = RouteRequestContextV1.model_validate(
        {
            "request_id": str(request_context.request_id),
            "received_at": (request_context.received_at or datetime.now(UTC)).isoformat(),
            "source_channel": "mcp",
            "source_endpoint_identity": "switchboard",
            "source_sender_identity": source_butler,
        }
    )

    route_payload = _build_notify_route_envelope(
        notify_request,
        request_context=route_context,
    )
    parse_route_envelope(route_payload)

    route_result = await route(
        pool,
        target_butler=MESSENGER_BUTLER_NAME,
        tool_name="route.execute",
        args=route_payload,
        source_butler=source_butler,
        call_fn=call_fn,
    )

    if "error" in route_result:
        error_msg = str(route_result["error"])
        notification_id = await log_notification(
            pool,
            source_butler=source_butler,
            channel=channel,
            recipient=recipient,
            message=message,
            metadata=log_metadata,
            status="failed",
            error=error_msg,
        )
        return {"notification_id": notification_id, "status": "failed", "error": error_msg}

    notify_response = _extract_notify_response(route_result.get("result"))
    if isinstance(notify_response, dict) and notify_response.get("status") == "error":
        error_payload = notify_response.get("error")
        if isinstance(error_payload, dict):
            error_msg = str(error_payload.get("message") or "Messenger delivery failed.")
        else:
            error_msg = "Messenger delivery failed."
        notification_id = await log_notification(
            pool,
            source_butler=source_butler,
            channel=channel,
            recipient=recipient,
            message=message,
            metadata=log_metadata,
            status="failed",
            error=error_msg,
        )
        return {"notification_id": notification_id, "status": "failed", "error": error_msg}

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
        metadata=log_metadata,
        status="sent",
        trace_id=current_trace_id,
    )

    # Write outbound row to message_inbox so it appears in conversation history.
    await _write_outbound_message_inbox(
        pool,
        notify_request=notify_request,
        delivered_at=datetime.now(UTC),
    )

    return {
        "notification_id": notification_id,
        "status": "sent",
        "result": notify_response or route_result.get("result"),
    }


async def deliver(
    pool: asyncpg.Pool,
    channel: str | None = None,
    message: str | None = None,
    recipient: str | None = None,
    metadata: dict[str, Any] | None = None,
    source_butler: str = "switchboard",
    notify_request: dict[str, Any] | None = None,
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
        Direct delivery channel field for switchboard-initiated dispatch.
    message:
        Direct notification message body for switchboard-initiated dispatch.
    recipient:
        Recipient identifier for direct channel delivery.
    metadata:
        Optional metadata dict.
    source_butler:
        Name of the butler initiating the delivery.
    notify_request:
        Versioned `notify.v1` request envelope. When provided, Switchboard
        terminates the notify control-plane request and dispatches through
        ``messenger`` via ``route.execute``.
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
        span.set_attribute("source_butler", source_butler)

        envelope_payload: dict[str, Any] | None = notify_request
        if envelope_payload is None and source_butler != "switchboard":
            error_msg = (
                "notify.v1 envelope required for specialist delivery. "
                "Provide notify_request with schema_version='notify.v1'."
            )
            span.set_status(trace.StatusCode.ERROR, error_msg)
            return {"error": error_msg, "status": "failed"}

        if envelope_payload is not None:
            try:
                parsed_notify = parse_notify_request(envelope_payload)
            except ValidationError as exc:
                error_msg = f"Invalid notify.v1 envelope: {exc}"
                span.set_status(trace.StatusCode.ERROR, error_msg)
                return {"error": error_msg, "status": "failed"}

            if parsed_notify.origin_butler != source_butler:
                error_msg = (
                    "notify.v1 origin_butler must match source_butler. "
                    f"Received origin_butler='{parsed_notify.origin_butler}', "
                    f"source_butler='{source_butler}'."
                )
                span.set_status(trace.StatusCode.ERROR, error_msg)
                return {"error": error_msg, "status": "failed"}

            request_context = parsed_notify.request_context or _default_notify_request_context(
                source_butler
            )
            span.set_attribute("channel", parsed_notify.delivery.channel)
            span.set_attribute("target_butler", MESSENGER_BUTLER_NAME)

            return await _deliver_via_notify_request(
                pool,
                notify_request=parsed_notify,
                request_context=request_context,
                source_butler=source_butler,
                metadata=metadata,
                call_fn=call_fn,
            )

        if channel not in SUPPORTED_CHANNELS:
            error_msg = (
                f"Unsupported channel '{channel}'. "
                f"Supported channels: {', '.join(sorted(SUPPORTED_CHANNELS))}"
            )
            span.set_status(trace.StatusCode.ERROR, error_msg)
            return {"error": error_msg, "status": "failed"}

        if not recipient:
            error_msg = "Recipient is required for delivery"
            span.set_status(trace.StatusCode.ERROR, error_msg)
            return {"error": error_msg, "status": "failed"}

        span.set_attribute("channel", channel)
        module_name, tool_name = _CHANNEL_DISPATCH[channel]

        rows = await pool.fetch(
            """
            SELECT name FROM butler_registry
            WHERE modules::jsonb @> $1::jsonb
            ORDER BY name
            """,
            json.dumps([module_name]),
        )

        if not rows:
            error_msg = f"No butler with '{module_name}' module found in registry"
            span.set_status(trace.StatusCode.ERROR, error_msg)
            notification_id = await log_notification(
                pool,
                source_butler=source_butler,
                channel=channel,
                recipient=recipient,
                message=message or "",
                metadata=metadata,
                status="failed",
                error=error_msg,
            )
            return {"notification_id": notification_id, "status": "failed", "error": error_msg}

        # 4. Build channel-specific args and route
        try:
            tool_args = _build_channel_args(channel, message or "", recipient, metadata)
        except ValueError as exc:
            error_msg = str(exc)
            span.set_status(trace.StatusCode.ERROR, error_msg)
            notification_id = await log_notification(
                pool,
                source_butler=source_butler,
                channel=channel,
                recipient=recipient,
                message=message or "",
                metadata=metadata,
                status="failed",
                error=error_msg,
            )
            return {"notification_id": notification_id, "status": "failed", "error": error_msg}

        route_result: dict[str, Any] | None = None
        route_errors: list[str] = []
        target_butler: str | None = None
        for row in rows:
            candidate = str(row["name"])
            candidate_result = await route(
                pool,
                target_butler=candidate,
                tool_name=tool_name,
                args=tool_args,
                source_butler=source_butler,
                call_fn=call_fn,
            )
            if "error" in candidate_result:
                route_errors.append(str(candidate_result["error"]))
                continue
            target_butler = candidate
            route_result = candidate_result
            break

        if route_result is None:
            error_msg = (
                route_errors[0]
                if route_errors
                else (f"No eligible butler with '{module_name}' module found for routing")
            )
            span.set_status(trace.StatusCode.ERROR, error_msg)
            notification_id = await log_notification(
                pool,
                source_butler=source_butler,
                channel=channel,
                recipient=recipient,
                message=message or "",
                metadata=metadata,
                status="failed",
                error=error_msg,
            )
            return {"notification_id": notification_id, "status": "failed", "error": error_msg}

        span.set_attribute("target_butler", target_butler)

        # 5. Determine success and log
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
            message=message or "",
            metadata=metadata,
            status="sent",
            trace_id=current_trace_id,
        )

        return {
            "notification_id": notification_id,
            "status": "sent",
            "result": route_result.get("result"),
        }
