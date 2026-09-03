"""Integration test: propose-then-act through ``route_to_butler`` [bu-0ynlk.1].

Wires the real ``route_to_butler`` switchboard tool (which injects the
STATEMENT/ACTION-REQUEST confirm block built by
``_build_dashboard_confirm_block``) to a fake target butler that plays the
role of a routed session correctly following that block's contract:

- A STATEMENT applies its domain write directly and replies with a
  confirm-style message.
- An ACTION REQUEST never writes directly — it calls the real approvals
  gate (``apply_approval_gates`` / ``gate.py``), which parks the call as a
  ``pending_actions`` row, and replies with a proposal, never a completion
  claim.

This is the seam ``about/heart-and-soul/security.md:103-105`` describes:
the approval gate is enforced independently of the LLM session (here,
independently of the fake target's own choices) — the *prompt* contract
under test is that the injected block tells a compliant routed session to
use that gate rather than writing around it. Runtime enforcement against a
non-compliant session is out of scope (see bu-0ynlk.8 receipts).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from butlers.config import ApprovalConfig, ApprovalRiskTier, GatedToolConfig
from butlers.modules.approvals.gate import apply_approval_gates
from tests.daemon.test_dashboard_lane_tools import (
    _clear_routing_context,
    _make_switchboard_dir,
    _patch_infra,
    _set_dashboard_routing_context,
    _start_switchboard_and_capture_tools,
)
from tests.modules.test_module_approvals import MockDB

pytestmark = pytest.mark.unit


def _make_fake_target_mcp() -> tuple[Any, dict[str, Any]]:
    """Minimal fake FastMCP surface for a target butler's own tools."""
    from unittest.mock import MagicMock

    class FakeTool:
        def __init__(self, name: str, fn: Any):
            self.name = name
            self.fn = fn

    tools: dict[str, Any] = {}
    mcp = MagicMock()

    def get_tool(name: str) -> Any:
        return tools.get(name)

    async def _get_tool_async(name: str) -> Any:
        return tools.get(name)

    mcp.get_tool = _get_tool_async

    def tool_decorator(*_a, **_kw):
        def dec(fn):
            tools[fn.__name__] = FakeTool(fn.__name__, fn)
            return fn

        return dec

    mcp.tool = tool_decorator
    return mcp, tools


async def test_action_request_parks_zero_writes_no_completion_claim(tmp_path: Path) -> None:
    """A fake target butler that follows the ACTION-REQUEST branch of the
    injected confirm block: calls its gated tool (which parks, since the
    target contact is unresolvable), never touches the domain write, and
    replies with a proposal rather than a completion claim."""
    domain_writes: list[dict[str, Any]] = []
    replies: list[str] = []

    target_pool = MockDB()
    target_mcp, _tools = _make_fake_target_mcp()

    @target_mcp.tool()
    async def finance_send_payment_reminder(to: str, amount: float) -> dict:
        domain_writes.append({"to": to, "amount": amount})
        return {"status": "sent", "to": to}

    approval_config = ApprovalConfig(
        enabled=True,
        gated_tools={
            "finance_send_payment_reminder": GatedToolConfig(risk_tier=ApprovalRiskTier.MEDIUM),
        },
    )
    await apply_approval_gates(target_mcp, approval_config, target_pool)
    gated_tool = await target_mcp.get_tool("finance_send_payment_reminder")

    async def _fake_target_dispatch(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        envelope = kwargs["args"]
        context_text = envelope["input"]["context"]
        # The envelope must actually carry the ACTION-REQUEST branch this
        # fake target is about to follow — proves the confirm block, not
        # test scaffolding, is driving the behavior asserted below.
        assert "ACTION REQUEST" in context_text
        assert "Do NOT write directly" in context_text

        gate_result = await gated_tool.fn(
            to="unknown-recipient@example.com",
            amount=42.0,
            _why="Owner asked to send a payment reminder.",
            _evidence=[],
        )
        assert gate_result["status"] == "pending_approval"
        replies.append(
            f"I've queued that payment reminder for your approval "
            f"(action_id={gate_result['action_id']})."
        )
        return {"result": {"status": "accepted"}}

    patches = _patch_infra()
    butler_dir = _make_switchboard_dir(tmp_path)
    _, tools = await _start_switchboard_and_capture_tools(
        butler_dir, patches, mock_route=AsyncMock(side_effect=_fake_target_dispatch)
    )
    fn = tools["route_to_butler"]

    _set_dashboard_routing_context()
    try:
        result = await fn(butler="finance", prompt="Send Alice a payment reminder for $42")
    finally:
        _clear_routing_context()

    assert result["status"] == "accepted"

    pending_rows = list(target_pool.pending_actions.values())
    assert len(pending_rows) == 1
    assert pending_rows[0]["status"] == "pending"
    assert pending_rows[0]["tool_name"] == "finance_send_payment_reminder"

    assert domain_writes == []

    assert len(replies) == 1
    reply_text = replies[0].lower()
    for completion_word in ("sent", "delivered", "done", "completed"):
        assert completion_word not in reply_text
    assert "queued" in reply_text and "approval" in reply_text


async def test_statement_applies_write_and_replies_with_confirmation(tmp_path: Path) -> None:
    """A fake target butler that follows the STATEMENT branch: applies the
    domain write directly (no gate involved) and replies with a
    confirm-style message describing what was recorded."""
    domain_writes: list[dict[str, Any]] = []
    replies: list[str] = []

    target_mcp, _tools = _make_fake_target_mcp()

    @target_mcp.tool()
    async def finance_record_transaction(merchant: str, amount: float) -> dict:
        domain_writes.append({"merchant": merchant, "amount": amount})
        return {"status": "recorded"}

    write_tool = await target_mcp.get_tool("finance_record_transaction")

    async def _fake_target_dispatch(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        envelope = kwargs["args"]
        context_text = envelope["input"]["context"]
        assert "STATEMENT" in context_text
        assert "Apply it" in context_text

        write_result = await write_tool.fn(merchant="Amazon", amount=45.99)
        assert write_result["status"] == "recorded"
        replies.append("Recorded: Amazon transaction for $45.99 — correct?")
        return {"result": {"status": "accepted"}}

    patches = _patch_infra()
    butler_dir = _make_switchboard_dir(tmp_path)
    _, tools = await _start_switchboard_and_capture_tools(
        butler_dir, patches, mock_route=AsyncMock(side_effect=_fake_target_dispatch)
    )
    fn = tools["route_to_butler"]

    _set_dashboard_routing_context()
    try:
        result = await fn(butler="finance", prompt="I spent $45.99 at Amazon")
    finally:
        _clear_routing_context()

    assert result["status"] == "accepted"

    assert domain_writes == [{"merchant": "Amazon", "amount": 45.99}]

    assert len(replies) == 1
    assert "Recorded" in replies[0]
