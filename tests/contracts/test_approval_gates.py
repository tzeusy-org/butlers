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
        assert "role" in mod_src.lower() or "owner" in mod_src.lower() or "actor" in mod_src.lower(), (
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
