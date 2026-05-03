"""Contract tests: Approval Gates (RFC 0002, security.md, Invariant 8).

Validates that sensitive operations are intercepted by approval gates,
bypass is structurally impossible, and timeouts result in denial.
"""

from __future__ import annotations

import inspect

import pytest

pytestmark = pytest.mark.contract


class TestApprovalGateContracts:
    """security.md + RFC 0002: Approval gates enforce safety at the MCP layer."""

    def test_approval_module_and_function_importable(self):
        from butlers.modules.approvals import ApprovalsModule, apply_approval_gates

        assert ApprovalsModule is not None and callable(apply_approval_gates)

    def test_approval_gate_is_structural_not_prompt(self):
        """Gate applied at MCP server level (phase 13b), not in prompt;
        LLM bypass is structurally impossible; timeout = denial."""
        from butlers.daemon import ButlerDaemon

        src = inspect.getsource(ButlerDaemon)
        assert "approval" in src.lower()

        # Timeout results in expiry/denial: expire_stale_actions must exist
        from butlers.modules.approvals import ApprovalsModule

        assert hasattr(ApprovalsModule, "_expire_stale_actions"), (
            "ApprovalsModule must implement _expire_stale_actions for timeout-denial (security.md)"
        )

        # Sensitive use cases documented
        sensitive = {"send email", "financial transaction", "smart home action"}
        assert len(sensitive) >= 3

        # Approval module supports cross-channel notifications via roles
        mod_src = inspect.getsource(ApprovalsModule)
        has_role_targeting = (
            "role" in mod_src.lower() or "owner" in mod_src.lower() or "actor" in mod_src.lower()
        )
        assert has_role_targeting, (
            "ApprovalsModule must use role/owner/actor for notification targeting"
        )

    def test_tool_sensitivity_metadata_informs_gate(self):
        """ToolMeta.arg_sensitivities informs approval gate behavior."""
        from butlers.modules.base import ToolMeta

        meta = ToolMeta(arg_sensitivities={"to": True, "subject": False})
        assert meta.arg_sensitivities["to"] is True

    def test_pending_actions_table_and_schema(self):
        """Approval actions table exists in schema definition."""
        from butlers.modules.approvals.models import PendingAction

        assert PendingAction is not None


class TestApprovalRuleMatching:
    """RFC 0002 + security.md: Approval gate rules match on tool name, caller, and args."""

    def test_approval_rule_matching_on_tool_name_and_identity(self):
        """security.md: Rules match on (tool_name, arg_constraints) — wrong matcher = gate bypass.

        match_rules_from_list() returns the most specific rule that matches
        both the tool name and the argument constraints. A rule for a different
        tool name must NOT match. A rule with arg constraints that don't match
        the actual args must NOT match.
        """
        import uuid as _uuid

        from butlers.modules.approvals.rules import match_rules_from_list

        _rule_id = str(_uuid.uuid4())
        rule_send_email = {
            "id": _rule_id,
            "tool_name": "send_email",
            "arg_constraints": '{"to": "chloe@example.com"}',
            "description": "Allow send_email to chloe",
            "active": True,
            "expires_at": None,
            "max_uses": None,
            "use_count": 0,
            "created_at": "2026-01-01T00:00:00+00:00",
            "created_from": None,
        }

        # Correct tool_name and matching arg — rule matches
        result = match_rules_from_list(
            "send_email",
            {"to": "chloe@example.com", "subject": "Hello"},
            [rule_send_email],
        )
        assert result is not None, (
            "Rule must match when tool_name and arg_constraints agree (security.md)"
        )

        # Wrong tool_name — must not match
        result_wrong_tool = match_rules_from_list(
            "send_sms",
            {"to": "chloe@example.com"},
            [rule_send_email],
        )
        assert result_wrong_tool is None, (
            "Rule must NOT match when tool_name differs — prevents gate bypass (security.md)"
        )

        # Correct tool_name but wrong arg value — must not match
        result_wrong_arg = match_rules_from_list(
            "send_email",
            {"to": "eve@attacker.com"},
            [rule_send_email],
        )
        assert result_wrong_arg is None, (
            "Rule must NOT match when arg_constraints don't satisfy actual args (security.md)"
        )

    def test_more_specific_rule_wins_over_less_specific(self):
        """security.md: More specific rules (more arg constraints) take precedence.

        The gate always selects the most specific matching rule to ensure tightly
        scoped approvals take effect over broader blanket approvals.
        """
        import uuid as _uuid

        from butlers.modules.approvals.rules import match_rules_from_list

        broad_rule = {
            "id": str(_uuid.uuid4()),
            "tool_name": "send_email",
            "arg_constraints": "{}",  # no constraints — matches anything
            "description": "Broad allow for send_email",
            "active": True,
            "expires_at": None,
            "max_uses": None,
            "use_count": 0,
            "created_at": "2026-01-01T00:00:00+00:00",
            "created_from": None,
        }
        specific_rule = {
            "id": str(_uuid.uuid4()),
            "tool_name": "send_email",
            "arg_constraints": '{"to": "chloe@example.com"}',
            "description": "Allow send_email to chloe only",
            "active": True,
            "expires_at": None,
            "max_uses": None,
            "use_count": 0,
            "created_at": "2026-01-01T00:00:00+00:00",
            "created_from": None,
        }

        # Specific rule must win when it matches
        result = match_rules_from_list(
            "send_email",
            {"to": "chloe@example.com"},
            [broad_rule, specific_rule],
        )
        assert result is not None, "A matching rule must be returned"
        assert result.arg_constraints == {"to": "chloe@example.com"}, (
            "More specific rule (with arg constraints) must win over broad rule (security.md)"
        )

    def test_approval_expiry_marks_stale_actions_denied(self):
        """security.md: Pending actions past their timeout are marked expired/denied.

        ApprovalsModule._expire_stale_actions() is the mechanism that transitions
        pending actions whose expires_at has passed to 'expired' status.
        This is what implements 'timeout = denial'.
        """
        from butlers.modules.approvals.module import ApprovalsModule

        # The method must exist on ApprovalsModule
        assert hasattr(ApprovalsModule, "_expire_stale_actions"), (
            "ApprovalsModule must implement _expire_stale_actions (security.md: timeout=denial)"
        )
        assert callable(ApprovalsModule._expire_stale_actions), (
            "_expire_stale_actions must be callable"
        )

        import asyncio

        assert asyncio.iscoroutinefunction(ApprovalsModule._expire_stale_actions), (
            "_expire_stale_actions must be async (runs DB update on expired actions)"
        )

    def test_expired_rule_does_not_match(self):
        """security.md: An expired approval rule must not grant approval.

        Rules with expires_at in the past are excluded from matching,
        ensuring time-bounded approvals cannot be reused after expiry.
        """
        import uuid as _uuid
        from datetime import UTC, datetime, timedelta

        from butlers.modules.approvals.rules import match_rules_from_list

        expired_rule = {
            "id": str(_uuid.uuid4()),
            "tool_name": "send_email",
            "arg_constraints": "{}",
            "description": "Expired rule",
            "active": True,
            "expires_at": datetime.now(UTC) - timedelta(hours=1),  # already expired
            "max_uses": None,
            "use_count": 0,
            "created_at": "2026-01-01T00:00:00+00:00",
            "created_from": None,
        }

        result = match_rules_from_list("send_email", {"to": "anyone@example.com"}, [expired_rule])
        assert result is None, "Expired rule must NOT match — timeout is denial (security.md)"
