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
from typing import Any, Literal

import asyncpg

from butlers.core.attention_ledger import check_owner_notify_suppression, record_attention_event
from butlers.core.tool_call_capture import get_current_switchboard_client
from butlers.credential_store import resolve_owner_telegram_recipient

logger = logging.getLogger(__name__)

_NOTIFY_TIMEOUT_S = 30

DELIVERED = "delivered"
SUPPRESSED = "suppressed"
FAILED = "failed"

NotifyOutcome = Literal["delivered", "suppressed", "failed"]


async def send_owner_telegram(
    pool: asyncpg.Pool,
    *,
    origin_butler: str,
    message: str,
    log_context: str,
) -> NotifyOutcome:
    """Send ``message`` to the owner's Telegram chat and return the recorded outcome."""

    async def _record(outcome: str, reason: str | None = None, ref: Any = None) -> None:
        await record_attention_event(
            pool,
            origin_butler=origin_butler,
            source="notify",
            outcome=outcome,
            channel="telegram",
            intent="send",
            priority="medium",
            reason=reason,
            notification_ref=ref,
        )

    reason = await check_owner_notify_suppression(pool, log_context=log_context)
    if reason is not None:
        await _record(SUPPRESSED, reason)
        return SUPPRESSED
    recipient = await resolve_owner_telegram_recipient(pool)
    if not recipient:
        await _record(FAILED, "no_recipient_configured")
        return FAILED
    client = get_current_switchboard_client()
    if client is None:
        await _record(FAILED, "switchboard_client_unavailable")
        return FAILED
    notify_request = {
        "schema_version": "notify.v1",
        "origin_butler": origin_butler,
        "delivery": {
            "intent": "send",
            "channel": "telegram",
            "message": message,
            "recipient": recipient,
        },
    }
    try:
        result = await asyncio.wait_for(
            client.call_tool(
                "deliver", {"source_butler": origin_butler, "notify_request": notify_request}
            ),
            timeout=_NOTIFY_TIMEOUT_S,
        )
    except Exception as exc:
        logger.warning("%s: deliver() call failed: %s", log_context, type(exc).__name__)
        await _record(FAILED, f"delivery_error:{type(exc).__name__}")
        return FAILED
    data = result.data
    if result.is_error or (isinstance(data, dict) and data.get("status") == "failed"):
        await _record(FAILED, "delivery_error")
        return FAILED
    await _record(DELIVERED, ref=data.get("notification_id") if isinstance(data, dict) else None)
    return DELIVERED
