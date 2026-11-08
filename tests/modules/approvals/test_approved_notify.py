"""Owner-approved ``notify`` delivery from the owning daemon (bu-dhloi1.168).

The executor-bound approval context is the only authority: without it nothing
is sent. With the trusted cohost hop installed, the daemon hands that hop the
exact action id, stored-call digest and canonical envelope its proof binds;
without it the delivery is ordinary and Messenger's recipient gate applies.
"""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from butlers.core.approved_delivery import approved_notify_request
from butlers.modules.approvals.approved_notify import deliver_approved_notify
from butlers.modules.approvals.execution_context import (
    ApprovalExecutionContext,
    approval_tool_args_digest,
    reset_approval_execution_context,
    set_approval_execution_context,
)

pytestmark = pytest.mark.unit

_TOOL_ARGS: dict[str, Any] = {
    "intent": "send",
    "channel": "telegram",
    "message": "Approved words",
    "recipient": "42",
    "request_context": {
        "request_id": "019c2710-0000-7000-8000-000000000001",
        "source_channel": "telegram_bot",
        "source_endpoint_identity": "telegram:bot:1",
        "source_sender_identity": "42",
    },
}


async def _with_executor_context(action_id: uuid.UUID, tool_args: dict[str, Any], call):
    token = set_approval_execution_context(
        ApprovalExecutionContext(
            action_id=action_id,
            session_id=None,
            actor="human:owner",
            tool_name="notify",
            tool_args_digest=approval_tool_args_digest(tool_args),
            authorized_task=asyncio.current_task(),
        )
    )
    try:
        return await call()
    finally:
        reset_approval_execution_context(token)


def _daemon(**overrides: Any) -> SimpleNamespace:
    values: dict[str, Any] = {
        "config": SimpleNamespace(name="relationship"),
        "_approved_notify_dispatch": None,
        "switchboard_client": None,
        "db": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


async def test_without_executor_context_nothing_is_sent() -> None:
    dispatch = AsyncMock()
    client = SimpleNamespace(call_tool=AsyncMock())
    daemon = _daemon(_approved_notify_dispatch=dispatch, switchboard_client=client)

    with pytest.raises(RuntimeError, match="executor-bound approval"):
        await deliver_approved_notify(daemon, dict(_TOOL_ARGS))

    dispatch.assert_not_awaited()
    client.call_tool.assert_not_awaited()


async def test_context_for_different_args_sends_nothing() -> None:
    dispatch = AsyncMock()
    daemon = _daemon(_approved_notify_dispatch=dispatch)
    tampered = {**_TOOL_ARGS, "message": "Different words"}

    with pytest.raises(RuntimeError, match="executor-bound approval"):
        await _with_executor_context(
            uuid.uuid4(), _TOOL_ARGS, lambda: deliver_approved_notify(daemon, tampered)
        )

    dispatch.assert_not_awaited()


async def test_authenticated_hop_receives_exact_proof_binding() -> None:
    action_id = uuid.uuid4()
    dispatch = AsyncMock(return_value={"status": "ok", "message_id": "m-1"})
    client = SimpleNamespace(call_tool=AsyncMock())
    daemon = _daemon(_approved_notify_dispatch=dispatch, switchboard_client=client)

    result = await _with_executor_context(
        action_id, _TOOL_ARGS, lambda: deliver_approved_notify(daemon, dict(_TOOL_ARGS))
    )

    assert result == {"status": "ok", "message_id": "m-1"}
    dispatch.assert_awaited_once()
    sent_action, sent_digest, envelope = dispatch.await_args.args
    assert sent_action == action_id
    assert sent_digest == approval_tool_args_digest(_TOOL_ARGS)
    assert envelope == approved_notify_request("relationship", _TOOL_ARGS)
    # Attribution is the owning daemon; request_context is hoisted, not delivered.
    assert envelope["origin_butler"] == "relationship"
    assert envelope["request_context"] == _TOOL_ARGS["request_context"]
    assert "request_context" not in envelope["delivery"]
    # The authenticated hop replaces the ordinary deliver call entirely.
    client.call_tool.assert_not_awaited()


@pytest.mark.parametrize(
    "reply",
    [
        {"status": "pending_approval", "action_id": str(uuid.uuid4())},
        {"status": "failed", "error": "Approved notification authority rejected."},
        {"error": "SMTP unavailable"},
        {"success": False},
        "not-a-dict",
    ],
    ids=["second-park", "authority-refused", "error", "unsuccessful", "invalid"],
)
async def test_unsuccessful_or_parked_delivery_raises_so_row_stays_retryable(reply) -> None:
    dispatch = AsyncMock(return_value=reply)
    daemon = _daemon(_approved_notify_dispatch=dispatch)

    with pytest.raises(RuntimeError):
        await _with_executor_context(
            uuid.uuid4(), _TOOL_ARGS, lambda: deliver_approved_notify(daemon, dict(_TOOL_ARGS))
        )


async def test_without_authenticated_hop_falls_back_to_ordinary_deliver() -> None:
    client = SimpleNamespace(call_tool=AsyncMock(return_value=SimpleNamespace(data={"ok": True})))
    daemon = _daemon(switchboard_client=client)

    result = await _with_executor_context(
        uuid.uuid4(), _TOOL_ARGS, lambda: deliver_approved_notify(daemon, dict(_TOOL_ARGS))
    )

    assert result == {"ok": True}
    client.call_tool.assert_awaited_once_with(
        "deliver",
        {
            "source_butler": "relationship",
            "notify_request": approved_notify_request("relationship", _TOOL_ARGS),
        },
    )


async def test_fallback_park_is_reported_not_counted_as_delivery() -> None:
    parked = SimpleNamespace(data={"status": "pending_approval", "action_id": "x"})
    client = SimpleNamespace(call_tool=AsyncMock(return_value=parked))
    daemon = _daemon(switchboard_client=client)

    with pytest.raises(RuntimeError, match="re-entered the approval gate"):
        await _with_executor_context(
            uuid.uuid4(), _TOOL_ARGS, lambda: deliver_approved_notify(daemon, dict(_TOOL_ARGS))
        )


async def test_consumed_proof_without_reply_records_unknown_outcome_not_retry() -> None:
    """A send that may have happened is never left retryable (no second message)."""
    from butlers.core.approval_delivery_authority import ApprovedNotifyOutcomeUnknown

    dispatch = AsyncMock(side_effect=ApprovedNotifyOutcomeUnknown("unknown"))
    daemon = _daemon(_approved_notify_dispatch=dispatch)

    result = await _with_executor_context(
        uuid.uuid4(), _TOOL_ARGS, lambda: deliver_approved_notify(daemon, dict(_TOOL_ARGS))
    )

    # Returned (not raised): the executor marks the row executed with this result.
    assert result == {"status": "outcome_unknown", "delivery_confirmed": False}


async def test_timeout_before_verification_raises_so_row_stays_retryable() -> None:
    from butlers.core.approval_delivery_authority import ApprovalCallTimeout

    dispatch = AsyncMock(side_effect=ApprovalCallTimeout("deadline"))
    daemon = _daemon(_approved_notify_dispatch=dispatch)

    with pytest.raises(ApprovalCallTimeout):
        await _with_executor_context(
            uuid.uuid4(), _TOOL_ARGS, lambda: deliver_approved_notify(daemon, dict(_TOOL_ARGS))
        )
