"""Owner Telegram delivery for deterministic scheduled jobs (bu-idc5mx.4).

A job runs outside any LLM session, so it cannot call the ``notify`` MCP tool. This is the
same owner-notify boundary ``health_visit_brief.send_owner_brief`` and the Home jobs use,
parameterised by origin butler:

1. Quiet-hours / context-bus suppression (``check_owner_notify_suppression``). A suppressed
   send is dropped and recorded, not deferred: the job's next run re-evaluates.
2. The recipient is the owner's Telegram chat (``resolve_owner_telegram_recipient``), never
   anything taken from job input.
3. A ``notify.v1`` send through the Switchboard ``deliver`` tool on the client
   ``background.dispatch_scheduled_task`` binds for the handler call, with a bounded timeout.
   No client means ``failed``; there is no direct provider fallback.
4. Exactly one ``public.attention_ledger`` row per call, whatever the outcome. A failed or
   uncertain delivery is recorded and never retried here.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

import asyncpg

from butlers.core.attention_ledger import check_owner_notify_suppression, record_attention_event
from butlers.core.tool_call_capture import get_current_switchboard_client
from butlers.credential_store import resolve_owner_telegram_recipient
from butlers.telegram_api import terminal_telegram_metadata

logger = logging.getLogger(__name__)

_NOTIFY_TIMEOUT_S = 30

DELIVERED = "delivered"
SUPPRESSED = "suppressed"
FAILED = "failed"

NotifyOutcome = Literal[
    "delivered", "suppressed", "failed", "partial_delivery", "delivery_uncertain"
]


@dataclass(frozen=True)
class DeliveryResult:
    """One Switchboard attempt; classification is not permission or retry authority."""

    outcome: NotifyOutcome
    data: dict[str, Any]
    reason: str | None = None
    exception_type: str | None = None
    retryable: bool = False

    @property
    def delivered(self) -> bool:
        return self.outcome == DELIVERED

    @property
    def notification_ref(self) -> Any:
        return self.data.get("notification_id")

    @property
    def error(self) -> Any:
        # Structured remote errors remain part of the public response contract.
        # Exception messages never enter this payload or the attention ledger.
        return self.data.get("error") or "Delivery failed"

    def require_delivered(self) -> None:
        if not self.delivered:
            raise RuntimeError(self.reason or "delivery_error")


def owner_notify_request(
    *,
    origin_butler: str,
    message: str,
    recipient: str | None = None,
    channel: str | None = "telegram",
) -> dict[str, Any]:
    """Construct the wire envelope after caller-owned recipient/policy validation."""
    delivery = {"intent": "send", "message": message}
    if channel is not None:
        delivery["channel"] = channel
    if recipient is not None:
        delivery["recipient"] = recipient
    return {"schema_version": "notify.v1", "origin_butler": origin_butler, "delivery": delivery}


async def deliver_notification(
    send: Callable[[], Awaitable[Any]],
    *,
    timeout_s: float = _NOTIFY_TIMEOUT_S,
    propagate_errors: bool = False,
) -> DeliveryResult:
    """Execute exactly once through an explicitly supplied Switchboard transport.

    This does not choose a route, resolve an actor, retry, defer or write a second
    attention outcome. Those decisions belong to the caller's existing boundary.
    Cancellation propagates; ordinary transport failures carry only their type.
    """
    try:
        response = await asyncio.wait_for(send(), timeout=timeout_s)
    except Exception as exc:
        terminal = terminal_telegram_metadata(exc)
        name = type(exc).__name__
        logger.warning("Owner notification delivery failed: %s", name)
        if propagate_errors and not terminal:
            raise
        if terminal:
            data = {"status": "failed", **terminal}
        else:
            return DeliveryResult(
                FAILED,
                {"status": "failed", "error": name},
                "delivery_error:" + name,
                name,
                isinstance(exc, (TimeoutError, ConnectionError, OSError)),
            )
    else:
        if isinstance(response, dict):
            data = response
            is_error = False
        else:
            data = getattr(response, "data", None)
            is_error = getattr(response, "is_error", False) is True
        terminal = terminal_telegram_metadata(data)
        if not isinstance(data, dict):
            # MCP's structured error vocabulary is returned to callers, never logged.
            content = getattr(response, "content", None) if is_error else None
            error = getattr(content[0], "text", None) if content else None
            data = {"status": "failed", "error": error or "Delivery failed"}
        if not terminal and (is_error or data.get("status") not in ("sent", "ok")):
            return DeliveryResult(
                FAILED, data, "delivery_error", retryable=data.get("retryable") is True
            )
    if terminal:
        outcome = "partial_delivery" if terminal.get("partial_delivery") else "delivery_uncertain"
        return DeliveryResult(outcome, data, "telegram_" + outcome)
    return DeliveryResult(DELIVERED, data)


@dataclass(frozen=True)
class OwnerNotifyBoundary:
    """Explicit caller bindings; no policy, identity or transport fallback is inferred."""

    suppression: Callable[..., Awaitable[str | None]] | None
    recipient: Callable[..., Awaitable[str | None]]
    client: Callable[[], Any]
    record: Callable[..., Awaitable[Any]]
    failure_reason: Callable[[DeliveryResult], str | None] = lambda result: result.reason


async def send_owner_telegram(
    pool: asyncpg.Pool,
    *,
    origin_butler: str,
    message: str,
    log_context: str,
    boundary: OwnerNotifyBoundary | None = None,
    priority: str = "medium",
    metadata: dict[str, Any] | None = None,
) -> NotifyOutcome:
    """Send ``message`` to the owner's Telegram chat and return the recorded outcome."""

    bound = boundary or OwnerNotifyBoundary(
        check_owner_notify_suppression,
        resolve_owner_telegram_recipient,
        get_current_switchboard_client,
        record_attention_event,
    )

    async def _record(outcome: str, reason: str | None = None, ref: Any = None) -> None:
        await bound.record(
            pool,
            origin_butler=origin_butler,
            source="notify",
            outcome=outcome,
            channel="telegram",
            intent="send",
            priority=priority,
            reason=reason,
            notification_ref=ref,
            **({"metadata": metadata} if metadata is not None else {}),
        )

    recipient = None
    client = None
    try:
        reason = (
            await bound.suppression(pool, log_context=log_context)
            if bound.suppression is not None
            else None
        )
        if reason is None:
            recipient = await bound.recipient(pool)
            if recipient:
                client = bound.client()
    except Exception as exc:
        logger.warning("Owner notification preparation failed: %s", type(exc).__name__)
        await _record(FAILED, "preparation_error:" + type(exc).__name__)
        return FAILED
    if reason is not None:
        await _record(SUPPRESSED, reason)
        return SUPPRESSED
    if not recipient:
        await _record(FAILED, "no_recipient_configured")
        return FAILED
    if client is None:
        await _record(FAILED, "switchboard_client_unavailable")
        return FAILED
    notify_request = owner_notify_request(
        origin_butler=origin_butler,
        message=message,
        recipient=recipient,
    )
    result = await deliver_notification(
        lambda: client.call_tool(
            "deliver", {"source_butler": origin_butler, "notify_request": notify_request}
        )
    )
    await _record(
        DELIVERED if result.delivered else FAILED,
        bound.failure_reason(result),
        result.notification_ref,
    )
    return result.outcome
