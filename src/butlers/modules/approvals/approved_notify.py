"""Deliver an owner-approved ``notify`` from its owning daemon's executor.

Re-running ``notify()`` would re-check the recipient and park the approved call
again, so the executor sends the stored call's canonical ``notify.v1`` envelope
directly through Switchboard ``deliver``. Where the trusted cohost installed an
authenticated hop, the envelope carries a single-use source proof that lets
Messenger honour the owner's approval instead of parking it twice (RFC 0038
D13). Without that hop it is an ordinary delivery and Messenger's recipient
gate applies, parking a native, previewable command.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
from typing import Any

from butlers.core.approval_delivery_authority import (
    ApprovedNotifyOutcomeUnknown,
    approved_notify_deliver_deadline,
)
from butlers.core.approved_delivery import approved_notify_request
from butlers.modules.approvals.delivery_failure import (
    SendFailure,
    delivery_rejected_result,
    provider_rejected,
    send_failure_code,
)
from butlers.modules.approvals.execution_context import get_approval_execution_context
from butlers.modules.approvals.executor import send_outcome_unknown

logger = logging.getLogger(__name__)


def _delivery_result(raw: Any) -> dict[str, Any]:
    """Normalize ``deliver``'s reply; any unsuccessful or parked outcome raises."""
    if getattr(raw, "is_error", False):
        raise RuntimeError("switchboard deliver rejected the notification")
    result = getattr(raw, "data", raw)
    if not isinstance(result, dict):
        raise RuntimeError("switchboard deliver returned an invalid result")
    if result.get("status") == "pending_approval":
        # A second park is not a delivery; the approved row stays retryable.
        raise RuntimeError("delivery re-entered the approval gate")
    if result.get("error") or result.get("success") is False or result.get("status") == "failed":
        if send_outcome_unknown(result):
            # An uncertain or partial send may already have reached the recipient: hand it
            # to the executor unraised so it is recorded executed/outcome_unknown, never
            # left retryable or shown as a plain failure.
            return result
        if provider_rejected(result):
            # Proven rejected: terminal and never delivered. The executor records it
            # as a distinct outcome instead of leaving a silently retryable row.
            return delivery_rejected_result(send_failure_code(result))
        raise SendFailure(send_failure_code(result))
    return result


async def deliver_approved_notify(daemon: Any, tool_args: dict[str, Any]) -> dict[str, Any]:
    """Send one approved ``notify`` call; only the shared executor may invoke this.

    The executor's task-bound context (exact action, tool and stored args) is
    the only authority; without it nothing is sent. Attribution is the owning
    daemon, never a stored argument.
    """
    context = get_approval_execution_context(tool_name="notify", tool_args=tool_args)
    if context is None:
        raise RuntimeError("approved notify requires its executor-bound approval")
    origin = daemon.config.name
    notify_request = approved_notify_request(origin, tool_args)

    dispatch = getattr(daemon, "_approved_notify_dispatch", None)
    if dispatch is not None:
        try:
            raw = await dispatch(context.action_id, context.tool_args_digest, notify_request)
        except ApprovedNotifyOutcomeUnknown:
            # Switchboard consumed the proof but no reply arrived, so the send
            # may have reached the recipient. Record the action as executed with
            # an unknown outcome: a retry would risk a second message to a
            # non-owner, and an unknown provider outcome never authorizes a
            # blind resend. The owner can check the channel and send again.
            logger.warning(
                "approved notify %s outcome unknown after its proof was consumed",
                context.action_id,
            )
            return {"status": "outcome_unknown", "delivery_confirmed": False}
        return _delivery_result(raw)

    deliver_args = {"source_butler": origin, "notify_request": notify_request}
    client = getattr(daemon, "switchboard_client", None)
    if client is None:
        if origin != "switchboard" or daemon.db is None:
            raise RuntimeError("switchboard deliver is unreachable")
        # Switchboard's roster tools are installed under a runtime alias.
        delivery = importlib.import_module("butlers.tools.switchboard.notification.deliver")
        return _delivery_result(await delivery.deliver(daemon.db.pool, **deliver_args))
    logger.info(
        "approved notify %s has no authenticated hop; Messenger's recipient gate applies",
        context.action_id,
    )
    # Same ordering as the protected hop: inside the API's 30 s dispatch deadline.
    raw = await asyncio.wait_for(
        client.call_tool("deliver", deliver_args), approved_notify_deliver_deadline()
    )
    return _delivery_result(raw)


__all__ = ["deliver_approved_notify"]
