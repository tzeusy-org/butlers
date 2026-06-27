"""Tests: module-declared safety-critical args gate standing-rule auto-approval.

Covers bu-7x8ab: a module declares safety-critical tool arguments via
``Module.tool_metadata()`` (``ToolMeta.arg_sensitivities``), and the approval
gate honors them — a standing rule may only auto-approve a gated tool when it
*pins* every safety-critical argument present in the call.  An otherwise
matching rule that leaves a safety-critical argument unconstrained (``any``)
falls through to parking.  Heuristics and existing fail-closed behavior are
unchanged; this only makes gating more precise.

These exercise the real gate decision path (real ``match_standing_rule`` +
real ``_unpinned_safety_critical_args``), with a real ``ToolMeta`` — not a
mock of ``tool_metadata()``.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.modules.approvals.executor import ExecutionResult
from butlers.modules.approvals.gate import (
    _make_gate_wrapper,
    _unpinned_safety_critical_args,
    apply_approval_gates,
)
from butlers.modules.base import ToolMeta

pytestmark = pytest.mark.unit

TOOL = "email_send_message"
RECIPIENT = "friend@example.com"


def _non_owner_contact():
    from butlers.identity import ResolvedContact

    return ResolvedContact(
        contact_id=uuid.uuid4(),
        entity_id=uuid.uuid4(),
        name="Friend",
        roles=["contact"],
    )


def _rule(arg_constraints: dict[str, Any]) -> dict[str, Any]:
    """Build a minimal active standing-rule row for ``email_send_message``."""
    return {
        "id": str(uuid.uuid4()),
        "tool_name": TOOL,
        "arg_constraints": arg_constraints,
    }


def _make_pool(rules: list[dict[str, Any]]) -> AsyncMock:
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=rules)
    pool.fetchrow = AsyncMock(return_value=None)
    pool.execute = AsyncMock(return_value=None)
    return pool


def _original_fn() -> AsyncMock:
    fn = AsyncMock(return_value={"status": "sent"})
    fn.__name__ = TOOL
    fn.__qualname__ = TOOL
    return fn


async def _call_gate(
    *,
    tool_meta: ToolMeta | None,
    rules: list[dict[str, Any]],
    tool_args: dict[str, Any],
    original_fn: AsyncMock | None = None,
) -> dict[str, Any]:
    """Drive the real gate wrapper against a non-owner target + standing rules."""
    if original_fn is None:
        original_fn = _original_fn()
    pool = _make_pool(rules)

    wrapper = _make_gate_wrapper(
        tool_name=TOOL,
        original_fn=original_fn,
        pool=pool,
        expiry_hours=72,
        risk_tier=MagicMock(value="medium"),
        rule_precedence=("contact_role", "standing_rule"),
        tool_meta=tool_meta,
    )

    with (
        patch(
            "butlers.modules.approvals.gate._resolve_target_contact",
            new=AsyncMock(return_value=_non_owner_contact()),
        ),
        patch("butlers.modules.approvals.gate.record_approval_event", new=AsyncMock()),
        patch(
            "butlers.modules.approvals.gate.execute_approved_action",
            new=AsyncMock(return_value=ExecutionResult(success=True, result={"status": "sent"})),
        ),
    ):
        return await wrapper(**tool_args)


# ---------------------------------------------------------------------------
# Helper unit tests
# ---------------------------------------------------------------------------


class TestUnpinnedSafetyCriticalArgs:
    def test_unpinned_when_constraint_is_any(self) -> None:
        meta = ToolMeta(arg_sensitivities={"to": True})
        rule = _rule({"to": {"type": "any"}})
        assert _unpinned_safety_critical_args({"to": RECIPIENT}, meta, rule) == ["to"]

    def test_pinned_when_constraint_is_exact(self) -> None:
        meta = ToolMeta(arg_sensitivities={"to": True})
        rule = _rule({"to": {"type": "exact", "value": RECIPIENT}})
        assert _unpinned_safety_critical_args({"to": RECIPIENT}, meta, rule) == []

    def test_pinned_when_constraint_is_pattern(self) -> None:
        meta = ToolMeta(arg_sensitivities={"to": True})
        rule = _rule({"to": {"type": "pattern", "value": "*@example.com"}})
        assert _unpinned_safety_critical_args({"to": RECIPIENT}, meta, rule) == []

    def test_unpinned_when_constraint_absent(self) -> None:
        meta = ToolMeta(arg_sensitivities={"to": True})
        rule = _rule({"subject": {"type": "any"}})
        assert _unpinned_safety_critical_args({"to": RECIPIENT}, meta, rule) == ["to"]

    def test_no_meta_returns_empty(self) -> None:
        rule = _rule({"to": {"type": "any"}})
        assert _unpinned_safety_critical_args({"to": RECIPIENT}, None, rule) == []

    def test_non_critical_flag_ignored(self) -> None:
        meta = ToolMeta(arg_sensitivities={"to": False})
        rule = _rule({"to": {"type": "any"}})
        assert _unpinned_safety_critical_args({"to": RECIPIENT}, meta, rule) == []

    def test_arg_absent_from_call_not_flagged(self) -> None:
        meta = ToolMeta(arg_sensitivities={"to": True})
        rule = _rule({"to": {"type": "any"}})
        assert _unpinned_safety_critical_args({"subject": "hi"}, meta, rule) == []

    def test_malformed_constraints_fail_closed(self) -> None:
        meta = ToolMeta(arg_sensitivities={"to": True})
        rule = _rule("{ not valid json")
        assert _unpinned_safety_critical_args({"to": RECIPIENT}, meta, rule) == ["to"]

    def test_non_dict_constraints_fail_closed(self) -> None:
        """Valid JSON that decodes to a non-object (list) must fail closed, not crash."""
        meta = ToolMeta(arg_sensitivities={"to": True})
        rule = _rule("[1, 2]")
        assert _unpinned_safety_critical_args({"to": RECIPIENT}, meta, rule) == ["to"]


# ---------------------------------------------------------------------------
# Real gate-path decision tests
# ---------------------------------------------------------------------------


class TestGateHonorsSafetyCriticalArgs:
    async def test_unpinned_safety_critical_arg_parks(self) -> None:
        """Rule matches but leaves declared safety-critical `to` unpinned -> park."""
        original_fn = _original_fn()
        result = await _call_gate(
            tool_meta=ToolMeta(arg_sensitivities={"to": True}),
            rules=[_rule({"to": {"type": "any"}})],
            tool_args={"to": RECIPIENT, "subject": "Hi", "body": "x"},
            original_fn=original_fn,
        )
        assert result.get("status") == "pending_approval"
        original_fn.assert_not_awaited()

    async def test_pinned_safety_critical_arg_auto_approves(self) -> None:
        """Rule pins the declared safety-critical `to` -> auto-approve."""
        result = await _call_gate(
            tool_meta=ToolMeta(arg_sensitivities={"to": True}),
            rules=[_rule({"to": {"type": "exact", "value": RECIPIENT}})],
            tool_args={"to": RECIPIENT, "subject": "Hi", "body": "x"},
        )
        assert result == {"status": "sent"}

    async def test_no_metadata_unaffected(self) -> None:
        """Without module metadata, an any-rule still auto-approves (baseline)."""
        result = await _call_gate(
            tool_meta=None,
            rules=[_rule({"to": {"type": "any"}})],
            tool_args={"to": RECIPIENT, "subject": "Hi", "body": "x"},
        )
        assert result == {"status": "sent"}

    async def test_non_critical_declaration_unaffected(self) -> None:
        """A non-safety-critical declaration does not tighten the gate."""
        result = await _call_gate(
            tool_meta=ToolMeta(arg_sensitivities={"to": False}),
            rules=[_rule({"to": {"type": "any"}})],
            tool_args={"to": RECIPIENT, "subject": "Hi", "body": "x"},
        )
        assert result == {"status": "sent"}


# ---------------------------------------------------------------------------
# Registration wiring: apply_approval_gates threads tool_metadata to the gate
# ---------------------------------------------------------------------------


def _make_mock_mcp() -> Any:
    mock_mcp = MagicMock()
    _tools: dict[str, Any] = {}

    class FakeTool:
        def __init__(self, name: str, fn: Any):
            self.name = name
            self.fn = fn

    async def get_tool(name: str) -> Any:
        return _tools.get(name)

    mock_mcp.get_tool = get_tool

    def tool_decorator(*_a: Any, **_kw: Any):
        def dec(fn: Any):
            _tools[fn.__name__] = FakeTool(fn.__name__, fn)
            return fn

        return dec

    mock_mcp.tool = tool_decorator
    return mock_mcp


class TestApplyApprovalGatesThreadsMetadata:
    async def _run(self, *, tool_metadata: dict[str, ToolMeta] | None) -> dict[str, Any]:
        from butlers.config import ApprovalConfig, ApprovalRiskTier, GatedToolConfig

        mcp = _make_mock_mcp()

        @mcp.tool()
        async def email_send_message(to: str, subject: str, body: str) -> dict:
            return {"status": "sent", "to": to}

        config = ApprovalConfig(
            enabled=True,
            gated_tools={TOOL: GatedToolConfig(risk_tier=ApprovalRiskTier.MEDIUM)},
        )
        pool = _make_pool([_rule({"to": {"type": "any"}})])

        with (
            patch(
                "butlers.modules.approvals.gate._resolve_target_contact",
                new=AsyncMock(return_value=_non_owner_contact()),
            ),
            patch("butlers.modules.approvals.gate.record_approval_event", new=AsyncMock()),
            patch(
                "butlers.modules.approvals.gate.execute_approved_action",
                new=AsyncMock(
                    return_value=ExecutionResult(success=True, result={"status": "sent"})
                ),
            ),
        ):
            await apply_approval_gates(mcp, config, pool, "messenger", tool_metadata=tool_metadata)
            tool = await mcp.get_tool(TOOL)
            return await tool.fn(to=RECIPIENT, subject="Hi", body="x")

    async def test_metadata_threaded_parks_unpinned(self) -> None:
        result = await self._run(tool_metadata={TOOL: ToolMeta(arg_sensitivities={"to": True})})
        assert result.get("status") == "pending_approval"

    async def test_without_metadata_auto_approves(self) -> None:
        result = await self._run(tool_metadata=None)
        assert result == {"status": "sent"}


# ---------------------------------------------------------------------------
# Real module declaration
# ---------------------------------------------------------------------------


class TestEmailModuleDeclaration:
    def test_email_module_declares_to_safety_critical(self) -> None:
        from butlers.modules.email import EmailModule

        meta = EmailModule().tool_metadata()
        assert meta[TOOL].arg_sensitivities["to"] is True
        assert meta["email_reply_to_thread"].arg_sensitivities["to"] is True
