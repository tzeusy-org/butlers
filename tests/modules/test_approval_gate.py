"""Tests for MCP tool dispatch interception for approval-gated tools.

Unit tests verify:
- Gated tools are transparently wrapped at MCP registration time
- Non-gated tools are completely unaffected
- Wrapped tools serialize call into PendingAction and persist to DB
- Standing rule check happens before parking (auto-approve path works)
- CC receives structured pending_approval response with action ID
- Original tool function is preserved for later direct invocation
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest

from butlers.config import (
    DEFAULT_APPROVAL_RULE_PRECEDENCE,
    ApprovalConfig,
    ApprovalRiskTier,
    GatedToolConfig,
)
from butlers.modules.approvals.gate import (
    apply_approval_gates,
    match_standing_rule,
)
from butlers.modules.approvals.models import ActionStatus

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Mock DB helper — simulates asyncpg pool for approval gating
# ---------------------------------------------------------------------------


class MockPool:
    """In-memory mock of an asyncpg connection pool for gate tests.

    Supports insert/fetch of pending_actions and approval_rules.
    """

    def __init__(self) -> None:
        self.pending_actions: dict[uuid.UUID, dict[str, Any]] = {}
        self.approval_rules: list[dict[str, Any]] = []
        self.approval_events: list[dict[str, Any]] = []
        self.execute_calls: list[tuple[str, tuple]] = []

    def add_rule(
        self,
        tool_name: str,
        arg_constraints: dict[str, Any] | None = None,
        *,
        active: bool = True,
        expires_at: datetime | None = None,
        max_uses: int | None = None,
        use_count: int = 0,
    ) -> uuid.UUID:
        """Add a standing approval rule and return its ID."""
        rule_id = uuid.uuid4()
        self.approval_rules.append(
            {
                "id": rule_id,
                "tool_name": tool_name,
                "arg_constraints": json.dumps(arg_constraints or {}),
                "description": f"Rule for {tool_name}",
                "created_from": None,
                "created_at": datetime.now(UTC),
                "expires_at": expires_at,
                "max_uses": max_uses,
                "use_count": use_count,
                "active": active,
            }
        )
        return rule_id

    async def execute(self, query: str, *args: Any) -> None:
        """Simulate asyncpg execute()."""
        self.execute_calls.append((query, args))
        if "INSERT INTO pending_actions" in query:
            action_id = args[0]
            self.pending_actions[action_id] = {
                "id": action_id,
                "tool_name": args[1],
                "tool_args": args[2],
                "agent_summary": args[3],
                "session_id": args[4] if len(args) > 4 else None,
                "status": args[5] if len(args) > 5 else "pending",
                "requested_at": args[6] if len(args) > 6 else datetime.now(UTC),
                "expires_at": args[7] if len(args) > 7 else None,
                "approval_rule_id": args[8] if len(args) > 8 else None,
                "decided_by": args[9] if len(args) > 9 else None,
                "decided_at": None,
                "execution_result": None,
            }
        elif "INSERT INTO approval_events" in query:
            self.approval_events.append(
                {
                    "event_type": args[0],
                    "action_id": args[1],
                    "rule_id": args[2],
                    "actor": args[3],
                    "reason": args[4],
                    "event_metadata": json.loads(args[5]),
                    "occurred_at": args[6],
                }
            )
        elif "UPDATE pending_actions" in query and "status" in query:
            # Update from executor or gate
            if "AND status = $5" in query:
                action_id = args[3]
                expected_status = args[4]
            else:
                action_id = args[-1]
                expected_status = None
            if action_id in self.pending_actions:
                if (
                    expected_status is not None
                    and self.pending_actions[action_id]["status"] != expected_status
                ):
                    return
                self.pending_actions[action_id]["status"] = args[0]
                if "execution_result" in query:
                    self.pending_actions[action_id]["execution_result"] = args[1]
                elif "approval_rule_id" in query and len(args) > 2:
                    self.pending_actions[action_id]["approval_rule_id"] = args[1]
        elif "UPDATE approval_rules" in query and "use_count" in query:
            # Increment use_count (executor uses use_count + 1)
            rule_id = args[0]
            for rule in self.approval_rules:
                if rule["id"] == rule_id:
                    rule["use_count"] = rule.get("use_count", 0) + 1

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        """Simulate asyncpg fetch()."""
        if "approval_rules" in query:
            tool_name = args[0] if args else None
            results = []
            for rule in self.approval_rules:
                if tool_name and rule["tool_name"] != tool_name:
                    continue
                if not rule["active"]:
                    continue
                results.append(dict(rule))
            return results
        return []

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        """Simulate asyncpg fetchrow() for pending_actions lookups."""
        if "pending_actions" in query and args:
            action_id = args[0]
            row = self.pending_actions.get(action_id)
            return dict(row) if row else None
        return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_mcp(tools: dict[str, Any] | None = None) -> MagicMock:
    """Create a mock FastMCP server that tracks registered tools.

    If tools dict is provided, it will be populated with tool_name -> handler
    as tools get registered.
    """
    if tools is None:
        tools = {}

    mock_mcp = MagicMock()
    mock_mcp._tool_manager = MagicMock()

    # Track tools in a dict
    _tools_dict: dict[str, Any] = {}

    class FakeTool:
        def __init__(self, name: str, fn: Any):
            self.name = name
            self.fn = fn

    def get_tools():
        return _tools_dict

    mock_mcp._tool_manager.get_tools.return_value = _tools_dict

    def tool_decorator(*_decorator_args, **decorator_kwargs):
        declared_name = decorator_kwargs.get("name")

        def decorator(fn):
            tool_name = declared_name or fn.__name__
            _tools_dict[tool_name] = FakeTool(tool_name, fn)
            tools[tool_name] = fn
            return fn

        return decorator

    mock_mcp.tool = tool_decorator
    return mock_mcp


def _make_approval_config(
    gated_tools: dict[str, GatedToolConfig] | None = None,
    default_expiry_hours: int = 48,
) -> ApprovalConfig:
    """Create an ApprovalConfig for testing."""
    return ApprovalConfig(
        enabled=True,
        default_expiry_hours=default_expiry_hours,
        gated_tools=gated_tools or {},
    )


# ---------------------------------------------------------------------------
# Tests: match_standing_rule
# ---------------------------------------------------------------------------


class TestMatchStandingRule:
    """Test the standing rule matching logic."""

    def test_no_rules_returns_none(self):
        """No rules means no match."""
        result = match_standing_rule("email_send", {"to": "alice@example.com"}, [])
        assert result is None

    def test_exact_match_returns_rule(self):
        """Exact arg match should return the matching rule."""
        rule_id = uuid.uuid4()
        rules = [
            {
                "id": rule_id,
                "tool_name": "email_send",
                "arg_constraints": json.dumps({"to": "alice@example.com"}),
                "active": True,
                "expires_at": None,
                "max_uses": None,
                "use_count": 0,
            }
        ]
        result = match_standing_rule("email_send", {"to": "alice@example.com"}, rules)
        assert result is not None
        assert result["id"] == rule_id

    def test_empty_constraints_matches_any(self):
        """Empty arg_constraints should match any args for the tool."""
        rule_id = uuid.uuid4()
        rules = [
            {
                "id": rule_id,
                "tool_name": "email_send",
                "arg_constraints": json.dumps({}),
                "active": True,
                "expires_at": None,
                "max_uses": None,
                "use_count": 0,
            }
        ]
        result = match_standing_rule("email_send", {"to": "bob@example.com"}, rules)
        assert result is not None
        assert result["id"] == rule_id

    def test_partial_constraint_match(self):
        """Constraint on a subset of args should match if those args are present."""
        rule_id = uuid.uuid4()
        rules = [
            {
                "id": rule_id,
                "tool_name": "email_send",
                "arg_constraints": json.dumps({"to": "alice@example.com"}),
                "active": True,
                "expires_at": None,
                "max_uses": None,
                "use_count": 0,
            }
        ]
        # Tool called with extra args — should still match
        result = match_standing_rule(
            "email_send",
            {"to": "alice@example.com", "subject": "Hello"},
            rules,
        )
        assert result is not None

    def test_constraint_mismatch(self):
        """If a constraint value doesn't match, rule should not match."""
        rules = [
            {
                "id": uuid.uuid4(),
                "tool_name": "email_send",
                "arg_constraints": json.dumps({"to": "alice@example.com"}),
                "active": True,
                "expires_at": None,
                "max_uses": None,
                "use_count": 0,
            }
        ]
        result = match_standing_rule("email_send", {"to": "bob@example.com"}, rules)
        assert result is None

    def test_pattern_constraint_with_wildcard(self):
        """Constraint value '*' should match any value for that key."""
        rule_id = uuid.uuid4()
        rules = [
            {
                "id": rule_id,
                "tool_name": "email_send",
                "arg_constraints": json.dumps({"to": "*"}),
                "active": True,
                "expires_at": None,
                "max_uses": None,
                "use_count": 0,
            }
        ]
        result = match_standing_rule("email_send", {"to": "anyone@example.com"}, rules)
        assert result is not None

    def test_expired_rule_not_matched(self):
        """An expired rule (expires_at in the past) should not match."""
        rules = [
            {
                "id": uuid.uuid4(),
                "tool_name": "email_send",
                "arg_constraints": json.dumps({}),
                "active": True,
                "expires_at": datetime.now(UTC) - timedelta(hours=1),
                "max_uses": None,
                "use_count": 0,
            }
        ]
        result = match_standing_rule("email_send", {}, rules)
        assert result is None

    def test_max_uses_exhausted_not_matched(self):
        """A rule with use_count >= max_uses should not match."""
        rules = [
            {
                "id": uuid.uuid4(),
                "tool_name": "email_send",
                "arg_constraints": json.dumps({}),
                "active": True,
                "expires_at": None,
                "max_uses": 5,
                "use_count": 5,
            }
        ]
        result = match_standing_rule("email_send", {}, rules)
        assert result is None

    def test_max_uses_not_yet_exhausted(self):
        """A rule with use_count < max_uses should still match."""
        rule_id = uuid.uuid4()
        rules = [
            {
                "id": rule_id,
                "tool_name": "email_send",
                "arg_constraints": json.dumps({}),
                "active": True,
                "expires_at": None,
                "max_uses": 5,
                "use_count": 4,
            }
        ]
        result = match_standing_rule("email_send", {}, rules)
        assert result is not None

    def test_wrong_tool_name_not_matched(self):
        """A rule for a different tool should not match."""
        rules = [
            {
                "id": uuid.uuid4(),
                "tool_name": "telegram_send",
                "arg_constraints": json.dumps({}),
                "active": True,
                "expires_at": None,
                "max_uses": None,
                "use_count": 0,
            }
        ]
        result = match_standing_rule("email_send", {}, rules)
        assert result is None

    def test_precedence_prefers_more_specific_rule(self):
        broad_rule = {
            "id": uuid.uuid4(),
            "tool_name": "email_send",
            "arg_constraints": json.dumps({"to": "*"}),
            "active": True,
            "created_at": datetime.now(UTC),
            "expires_at": None,
            "max_uses": None,
            "use_count": 0,
        }
        exact_rule = {
            "id": uuid.uuid4(),
            "tool_name": "email_send",
            "arg_constraints": json.dumps({"to": "alice@example.com"}),
            "active": True,
            "created_at": datetime.now(UTC),
            "expires_at": None,
            "max_uses": None,
            "use_count": 0,
        }

        result = match_standing_rule(
            "email_send",
            {"to": "alice@example.com"},
            [broad_rule, exact_rule],
        )
        assert result is not None
        assert result["id"] == exact_rule["id"]

    def test_precedence_prefers_bounded_rule_when_specificity_ties(self):
        now = datetime.now(UTC)
        unbounded = {
            "id": uuid.uuid4(),
            "tool_name": "email_send",
            "arg_constraints": json.dumps({"to": "alice@example.com"}),
            "active": True,
            "created_at": now,
            "expires_at": None,
            "max_uses": None,
            "use_count": 0,
        }
        bounded = {
            "id": uuid.uuid4(),
            "tool_name": "email_send",
            "arg_constraints": json.dumps({"to": "alice@example.com"}),
            "active": True,
            "created_at": now,
            "expires_at": now + timedelta(hours=1),
            "max_uses": None,
            "use_count": 0,
        }

        result = match_standing_rule(
            "email_send",
            {"to": "alice@example.com"},
            [unbounded, bounded],
        )
        assert result is not None
        assert result["id"] == bounded["id"]


# ---------------------------------------------------------------------------
# Tests: apply_approval_gates
# ---------------------------------------------------------------------------


class TestApplyApprovalGates:
    """Test that apply_approval_gates correctly wraps gated tools on the MCP."""

    async def test_non_gated_tool_unaffected(self):
        """A tool not in gated_tools should not be wrapped."""
        tools: dict[str, Any] = {}
        mock_mcp = _make_mock_mcp(tools)
        pool = MockPool()

        # Register a tool
        @mock_mcp.tool()
        async def safe_tool(arg: str) -> dict:
            return {"result": arg}

        config = _make_approval_config(gated_tools={})

        originals = apply_approval_gates(mock_mcp, config, pool)

        # The tool should not appear in the originals dict (not wrapped)
        assert "safe_tool" not in originals

    async def test_gated_tool_is_wrapped(self):
        """A gated tool should be replaced with a wrapper function."""
        tools: dict[str, Any] = {}
        mock_mcp = _make_mock_mcp(tools)
        pool = MockPool()

        @mock_mcp.tool()
        async def email_send(to: str, body: str) -> dict:
            return {"status": "sent"}

        config = _make_approval_config(
            gated_tools={"email_send": GatedToolConfig()},
        )

        original_fn = tools["email_send"]
        originals = apply_approval_gates(mock_mcp, config, pool)

        # The original should be preserved
        assert "email_send" in originals
        assert originals["email_send"] is original_fn

        # The tool in the MCP should now be the wrapper (different from original)
        wrapper = mock_mcp._tool_manager.get_tools()["email_send"].fn
        assert wrapper is not original_fn

    async def test_wrapped_tool_returns_pending_approval(self):
        """Calling a wrapped gated tool without matching rule returns pending_approval."""
        tools: dict[str, Any] = {}
        mock_mcp = _make_mock_mcp(tools)
        pool = MockPool()

        @mock_mcp.tool()
        async def email_send(to: str, body: str) -> dict:
            return {"status": "sent"}

        config = _make_approval_config(
            gated_tools={"email_send": GatedToolConfig()},
        )

        apply_approval_gates(mock_mcp, config, pool)

        # Call the wrapper
        wrapper = mock_mcp._tool_manager.get_tools()["email_send"].fn
        result = await wrapper(to="alice@example.com", body="hello")

        assert result["status"] == "pending_approval"
        assert "action_id" in result
        assert "message" in result
        assert result["risk_tier"] == "medium"
        assert tuple(result["rule_precedence"]) == DEFAULT_APPROVAL_RULE_PRECEDENCE
        # Verify UUID format
        uuid.UUID(result["action_id"])

    async def test_wrapped_tool_uses_tool_risk_tier_override(self):
        tools: dict[str, Any] = {}
        mock_mcp = _make_mock_mcp(tools)
        pool = MockPool()

        @mock_mcp.tool()
        async def wire_transfer(to_account: str, amount: float) -> dict:
            return {"status": "submitted"}

        config = _make_approval_config(
            gated_tools={"wire_transfer": GatedToolConfig(risk_tier=ApprovalRiskTier.HIGH)},
        )

        apply_approval_gates(mock_mcp, config, pool)
        wrapper = mock_mcp._tool_manager.get_tools()["wire_transfer"].fn
        result = await wrapper(to_account="acct_123", amount=10.0)

        assert result["status"] == "pending_approval"
        assert result["risk_tier"] == "high"

    async def test_wrapped_tool_persists_pending_action(self):
        """Calling a wrapped gated tool should persist a PendingAction to DB."""
        tools: dict[str, Any] = {}
        mock_mcp = _make_mock_mcp(tools)
        pool = MockPool()

        @mock_mcp.tool()
        async def email_send(to: str, body: str) -> dict:
            return {"status": "sent"}

        config = _make_approval_config(
            gated_tools={"email_send": GatedToolConfig()},
        )

        apply_approval_gates(mock_mcp, config, pool)

        wrapper = mock_mcp._tool_manager.get_tools()["email_send"].fn
        result = await wrapper(to="alice@example.com", body="hello")

        # Verify it was persisted
        action_id = uuid.UUID(result["action_id"])
        assert action_id in pool.pending_actions
        stored = pool.pending_actions[action_id]
        assert stored["tool_name"] == "email_send"
        tool_args = json.loads(stored["tool_args"])
        assert tool_args["to"] == "alice@example.com"
        assert tool_args["body"] == "hello"

    async def test_pending_action_has_correct_status(self):
        """Persisted PendingAction should have status='pending'."""
        tools: dict[str, Any] = {}
        mock_mcp = _make_mock_mcp(tools)
        pool = MockPool()

        @mock_mcp.tool()
        async def email_send(to: str) -> dict:
            return {"status": "sent"}

        config = _make_approval_config(
            gated_tools={"email_send": GatedToolConfig()},
        )

        apply_approval_gates(mock_mcp, config, pool)

        wrapper = mock_mcp._tool_manager.get_tools()["email_send"].fn
        result = await wrapper(to="alice@example.com")

        action_id = uuid.UUID(result["action_id"])
        stored = pool.pending_actions[action_id]
        assert stored["status"] == ActionStatus.PENDING.value

    async def test_pending_action_has_expiry(self):
        """Persisted PendingAction should have expires_at based on config."""
        tools: dict[str, Any] = {}
        mock_mcp = _make_mock_mcp(tools)
        pool = MockPool()

        @mock_mcp.tool()
        async def email_send(to: str) -> dict:
            return {"status": "sent"}

        config = _make_approval_config(
            gated_tools={"email_send": GatedToolConfig(expiry_hours=24)},
            default_expiry_hours=48,
        )

        apply_approval_gates(mock_mcp, config, pool)

        wrapper = mock_mcp._tool_manager.get_tools()["email_send"].fn
        result = await wrapper(to="alice@example.com")

        action_id = uuid.UUID(result["action_id"])
        stored = pool.pending_actions[action_id]
        assert stored["expires_at"] is not None

    async def test_pending_path_emits_action_queued_event(self):
        """Parking a gated call should append an action_queued event."""
        tools: dict[str, Any] = {}
        mock_mcp = _make_mock_mcp(tools)
        pool = MockPool()

        @mock_mcp.tool()
        async def email_send(to: str) -> dict:
            return {"status": "sent"}

        config = _make_approval_config(gated_tools={"email_send": GatedToolConfig()})
        apply_approval_gates(mock_mcp, config, pool)

        wrapper = mock_mcp._tool_manager.get_tools()["email_send"].fn
        result = await wrapper(to="alice@example.com")

        action_id = uuid.UUID(result["action_id"])
        event = next(e for e in pool.approval_events if e["action_id"] == action_id)
        assert event["event_type"] == "action_queued"
        assert event["actor"] == "system:approval_gate"

    async def test_auto_approve_via_standing_rule(self):
        """When a standing rule matches, the tool should be auto-approved and executed."""
        tools: dict[str, Any] = {}
        mock_mcp = _make_mock_mcp(tools)
        pool = MockPool()

        executed = []

        @mock_mcp.tool()
        async def email_send(to: str, body: str) -> dict:
            executed.append({"to": to, "body": body})
            return {"status": "sent"}

        config = _make_approval_config(
            gated_tools={"email_send": GatedToolConfig()},
        )

        # Add a standing rule that matches
        pool.add_rule("email_send", arg_constraints={"to": "alice@example.com"})

        apply_approval_gates(mock_mcp, config, pool)

        wrapper = mock_mcp._tool_manager.get_tools()["email_send"].fn
        result = await wrapper(to="alice@example.com", body="hello")

        # Should have been auto-approved and executed
        assert result == {"status": "sent"}
        assert len(executed) == 1
        assert executed[0]["to"] == "alice@example.com"

    async def test_auto_approve_emits_lifecycle_events(self):
        """Auto-approve flow should emit queue, auto-approve, and execution events."""
        tools: dict[str, Any] = {}
        mock_mcp = _make_mock_mcp(tools)
        pool = MockPool()

        @mock_mcp.tool()
        async def email_send(to: str) -> dict:
            return {"status": "sent"}

        config = _make_approval_config(gated_tools={"email_send": GatedToolConfig()})
        pool.add_rule("email_send", arg_constraints={"to": "alice@example.com"})
        apply_approval_gates(mock_mcp, config, pool)

        wrapper = mock_mcp._tool_manager.get_tools()["email_send"].fn
        await wrapper(to="alice@example.com")

        event_types = {event["event_type"] for event in pool.approval_events}
        assert "action_queued" in event_types
        assert "action_auto_approved" in event_types
        assert "action_execution_succeeded" in event_types

    async def test_auto_approve_persists_action_with_rule_id(self):
        """Auto-approved actions should be persisted with approval_rule_id."""
        tools: dict[str, Any] = {}
        mock_mcp = _make_mock_mcp(tools)
        pool = MockPool()

        @mock_mcp.tool()
        async def email_send(to: str) -> dict:
            return {"status": "sent"}

        config = _make_approval_config(
            gated_tools={"email_send": GatedToolConfig()},
        )

        rule_id = pool.add_rule("email_send", arg_constraints={})
        apply_approval_gates(mock_mcp, config, pool)

        wrapper = mock_mcp._tool_manager.get_tools()["email_send"].fn
        await wrapper(to="alice@example.com")

        # The action should have been persisted and auto-approved
        assert len(pool.pending_actions) == 1
        stored = list(pool.pending_actions.values())[0]
        assert stored["status"] == ActionStatus.EXECUTED.value
        assert stored["approval_rule_id"] == rule_id

    async def test_auto_approve_increments_rule_use_count(self):
        """Auto-approve should increment the rule's use_count."""
        tools: dict[str, Any] = {}
        mock_mcp = _make_mock_mcp(tools)
        pool = MockPool()

        @mock_mcp.tool()
        async def email_send(to: str) -> dict:
            return {"status": "sent"}

        config = _make_approval_config(
            gated_tools={"email_send": GatedToolConfig()},
        )

        rule_id = pool.add_rule("email_send", arg_constraints={})
        apply_approval_gates(mock_mcp, config, pool)

        wrapper = mock_mcp._tool_manager.get_tools()["email_send"].fn
        await wrapper(to="alice@example.com")

        # Check that use_count was incremented
        for rule in pool.approval_rules:
            if rule["id"] == rule_id:
                assert rule["use_count"] == 1

    async def test_no_auto_approve_when_rule_doesnt_match(self):
        """When a rule exists but doesn't match args, tool should be parked."""
        tools: dict[str, Any] = {}
        mock_mcp = _make_mock_mcp(tools)
        pool = MockPool()

        @mock_mcp.tool()
        async def email_send(to: str) -> dict:
            return {"status": "sent"}

        config = _make_approval_config(
            gated_tools={"email_send": GatedToolConfig()},
        )

        # Rule only matches alice, but we'll call with bob
        pool.add_rule("email_send", arg_constraints={"to": "alice@example.com"})
        apply_approval_gates(mock_mcp, config, pool)

        wrapper = mock_mcp._tool_manager.get_tools()["email_send"].fn
        result = await wrapper(to="bob@example.com")

        assert result["status"] == "pending_approval"

    async def test_multiple_gated_tools(self):
        """Multiple gated tools should each be independently wrapped."""
        tools: dict[str, Any] = {}
        mock_mcp = _make_mock_mcp(tools)
        pool = MockPool()

        @mock_mcp.tool()
        async def email_send(to: str) -> dict:
            return {"status": "sent"}

        @mock_mcp.tool()
        async def purchase_create(amount: float) -> dict:
            return {"status": "purchased"}

        @mock_mcp.tool()
        async def safe_read() -> dict:
            return {"status": "read"}

        config = _make_approval_config(
            gated_tools={
                "email_send": GatedToolConfig(),
                "purchase_create": GatedToolConfig(expiry_hours=24),
            },
        )

        originals = apply_approval_gates(mock_mcp, config, pool)

        # Both gated tools should be wrapped
        assert "email_send" in originals
        assert "purchase_create" in originals
        # Non-gated tool should not be in originals
        assert "safe_read" not in originals

    async def test_original_preserved_for_later_invocation(self):
        """The original tool function should be callable directly."""
        tools: dict[str, Any] = {}
        mock_mcp = _make_mock_mcp(tools)
        pool = MockPool()

        @mock_mcp.tool()
        async def email_send(to: str) -> dict:
            return {"status": "sent", "to": to}

        config = _make_approval_config(
            gated_tools={"email_send": GatedToolConfig()},
        )

        originals = apply_approval_gates(mock_mcp, config, pool)

        # Call the original directly
        result = await originals["email_send"](to="alice@example.com")
        assert result == {"status": "sent", "to": "alice@example.com"}

    async def test_pending_approval_response_structure(self):
        """Verify the exact structure of the pending_approval response."""
        tools: dict[str, Any] = {}
        mock_mcp = _make_mock_mcp(tools)
        pool = MockPool()

        @mock_mcp.tool()
        async def email_send(to: str, body: str) -> dict:
            return {"status": "sent"}

        config = _make_approval_config(
            gated_tools={"email_send": GatedToolConfig()},
        )

        apply_approval_gates(mock_mcp, config, pool)

        wrapper = mock_mcp._tool_manager.get_tools()["email_send"].fn
        result = await wrapper(to="alice@example.com", body="hello")

        # Required fields
        assert "status" in result
        assert result["status"] == "pending_approval"
        assert "action_id" in result
        assert "message" in result
        # action_id should be a valid UUID string
        parsed = uuid.UUID(result["action_id"])
        assert str(parsed) == result["action_id"]
        # message should reference the tool name
        assert "email_send" in result["message"]

    async def test_serialization_correctness(self):
        """Verify that tool args are correctly serialized as JSON."""
        tools: dict[str, Any] = {}
        mock_mcp = _make_mock_mcp(tools)
        pool = MockPool()

        @mock_mcp.tool()
        async def complex_tool(name: str, count: int, tags: list | None = None) -> dict:
            return {"ok": True}

        config = _make_approval_config(
            gated_tools={"complex_tool": GatedToolConfig()},
        )

        apply_approval_gates(mock_mcp, config, pool)

        wrapper = mock_mcp._tool_manager.get_tools()["complex_tool"].fn
        result = await wrapper(name="test", count=42, tags=["a", "b"])

        action_id = uuid.UUID(result["action_id"])
        stored = pool.pending_actions[action_id]
        tool_args = json.loads(stored["tool_args"])
        assert tool_args == {"name": "test", "count": 42, "tags": ["a", "b"]}

    async def test_disabled_approval_config_no_wrapping(self):
        """When approvals are disabled, no tools should be wrapped."""
        tools: dict[str, Any] = {}
        mock_mcp = _make_mock_mcp(tools)
        pool = MockPool()

        @mock_mcp.tool()
        async def email_send(to: str) -> dict:
            return {"status": "sent"}

        config = ApprovalConfig(
            enabled=False,
            gated_tools={"email_send": GatedToolConfig()},
        )

        originals = apply_approval_gates(mock_mcp, config, pool)
        assert originals == {}

    async def test_none_approval_config_no_wrapping(self):
        """When approval config is None, no tools should be wrapped."""
        tools: dict[str, Any] = {}
        mock_mcp = _make_mock_mcp(tools)
        pool = MockPool()

        @mock_mcp.tool()
        async def email_send(to: str) -> dict:
            return {"status": "sent"}

        originals = apply_approval_gates(mock_mcp, None, pool)
        assert originals == {}

    async def test_agent_summary_in_pending_action(self):
        """The persisted PendingAction should include a human-readable summary."""
        tools: dict[str, Any] = {}
        mock_mcp = _make_mock_mcp(tools)
        pool = MockPool()

        @mock_mcp.tool()
        async def email_send(to: str, body: str) -> dict:
            return {"status": "sent"}

        config = _make_approval_config(
            gated_tools={"email_send": GatedToolConfig()},
        )

        apply_approval_gates(mock_mcp, config, pool)

        wrapper = mock_mcp._tool_manager.get_tools()["email_send"].fn
        result = await wrapper(to="alice@example.com", body="hello")

        action_id = uuid.UUID(result["action_id"])
        stored = pool.pending_actions[action_id]
        assert stored["agent_summary"] is not None
        assert "email_send" in stored["agent_summary"]
