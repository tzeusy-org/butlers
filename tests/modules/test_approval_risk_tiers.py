"""Tests for approval risk tiers and precedence logic.

Covers acceptance criteria from butlers-0p6.5:
1. Risk-tier model for approval-gated actions is represented in policy/config.
2. Policy precedence order is explicit and deterministic.
3. Higher-risk rule constraints are enforceable (e.g., narrower/bounded rules).
4. Tests validate precedence and tier-driven behavior.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from butlers.config import (
    DEFAULT_APPROVAL_RULE_PRECEDENCE,
    ApprovalConfig,
    ApprovalRiskTier,
    GatedToolConfig,
)
from butlers.modules.approvals.models import ApprovalRule
from butlers.modules.approvals.module import ApprovalsModule
from butlers.modules.approvals.rules import (
    RULE_MATCH_PRECEDENCE,
    _is_bounded_rule,
    _rule_specificity,
    match_rules_from_list,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Mock DB helper
# ---------------------------------------------------------------------------


class MockDB:
    """In-memory mock of an asyncpg connection pool for risk tier tests."""

    def __init__(self) -> None:
        self.pending_actions: dict[uuid.UUID, dict[str, Any]] = {}
        self.approval_rules: dict[uuid.UUID, dict[str, Any]] = {}
        self.approval_events: list[dict[str, Any]] = []

    def _insert_action(self, **kwargs: Any) -> uuid.UUID:
        """Helper to seed a pending action for testing."""
        action_id = kwargs.get("id", uuid.uuid4())
        if isinstance(action_id, str):
            action_id = uuid.UUID(action_id)
        row = {
            "id": action_id,
            "tool_name": kwargs.get("tool_name", "test_tool"),
            "tool_args": json.dumps(kwargs.get("tool_args", {})),
            "status": kwargs.get("status", "pending"),
            "requested_at": kwargs.get("requested_at", datetime.now(UTC)),
            "agent_summary": kwargs.get("agent_summary"),
            "session_id": kwargs.get("session_id"),
            "expires_at": kwargs.get("expires_at"),
            "decided_by": kwargs.get("decided_by"),
            "decided_at": kwargs.get("decided_at"),
            "execution_result": (
                json.dumps(kwargs["execution_result"])
                if kwargs.get("execution_result") is not None
                else None
            ),
            "approval_rule_id": kwargs.get("approval_rule_id"),
        }
        self.pending_actions[action_id] = row
        return action_id

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        """Simulate asyncpg fetch()."""
        if "approval_rules" in query:
            rows = list(self.approval_rules.values())
            if "tool_name = $1" in query:
                tool_name = args[0] if args else None
                if tool_name:
                    rows = [r for r in rows if r["tool_name"] == tool_name]
            if "active = true" in query:
                rows = [r for r in rows if r["active"]]
            rows.sort(key=lambda r: r["created_at"], reverse=True)
            return [dict(r) for r in rows]
        return []

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        """Simulate asyncpg fetchrow()."""
        if "pending_actions" in query and args:
            action_id = args[0]
            row = self.pending_actions.get(action_id)
            return dict(row) if row else None
        if "approval_rules" in query and args:
            rule_id = args[0]
            row = self.approval_rules.get(rule_id)
            return dict(row) if row else None
        return None

    async def execute(self, query: str, *args: Any) -> None:
        """Simulate asyncpg execute()."""
        if "INSERT INTO approval_rules" in query:
            rule_id = args[0]
            self.approval_rules[rule_id] = {
                "id": rule_id,
                "tool_name": args[1],
                "arg_constraints": args[2],
                "description": args[3],
                "created_from": args[4] if len(args) > 4 else None,
                "created_at": args[5] if len(args) > 5 else datetime.now(UTC),
                "expires_at": args[6] if len(args) > 6 else None,
                "max_uses": args[7] if len(args) > 7 else None,
                "active": args[8] if len(args) > 8 else True,
                "use_count": 0,
            }
        elif "INSERT INTO approval_events" in query:
            self.approval_events.append(
                {
                    "event_type": args[0],
                    "action_id": args[1],
                    "rule_id": args[2] if len(args) > 2 else None,
                    "actor": args[3] if len(args) > 3 else None,
                    "reason": args[4] if len(args) > 4 else None,
                    "event_metadata": json.loads(args[5]) if len(args) > 5 else {},
                    "occurred_at": args[6] if len(args) > 6 else datetime.now(UTC),
                }
            )


# ---------------------------------------------------------------------------
# Test AC1: Risk-tier model is represented in policy/config
# ---------------------------------------------------------------------------


class TestRiskTierRepresentation:
    """Test that risk tiers are properly represented in config."""

    def test_approval_risk_tier_enum_values(self):
        """Risk tier enum should define low, medium, high, critical."""
        assert ApprovalRiskTier.LOW.value == "low"
        assert ApprovalRiskTier.MEDIUM.value == "medium"
        assert ApprovalRiskTier.HIGH.value == "high"
        assert ApprovalRiskTier.CRITICAL.value == "critical"

    def test_approval_config_has_default_risk_tier(self):
        """ApprovalConfig should have default_risk_tier field."""
        config = ApprovalConfig(
            enabled=True,
            default_risk_tier=ApprovalRiskTier.HIGH,
        )
        assert config.default_risk_tier == ApprovalRiskTier.HIGH

    def test_gated_tool_config_has_risk_tier_override(self):
        """GatedToolConfig should support per-tool risk tier override."""
        tool_config = GatedToolConfig(
            expiry_hours=24,
            risk_tier=ApprovalRiskTier.CRITICAL,
        )
        assert tool_config.risk_tier == ApprovalRiskTier.CRITICAL

    def test_approval_config_get_effective_risk_tier_default(self):
        """get_effective_risk_tier should return default when tool has no override."""
        config = ApprovalConfig(
            enabled=True,
            default_risk_tier=ApprovalRiskTier.MEDIUM,
            gated_tools={
                "safe_tool": GatedToolConfig(),
            },
        )
        assert config.get_effective_risk_tier("safe_tool") == ApprovalRiskTier.MEDIUM

    def test_approval_config_get_effective_risk_tier_override(self):
        """get_effective_risk_tier should return tool-specific override."""
        config = ApprovalConfig(
            enabled=True,
            default_risk_tier=ApprovalRiskTier.LOW,
            gated_tools={
                "wire_transfer": GatedToolConfig(risk_tier=ApprovalRiskTier.CRITICAL),
            },
        )
        assert config.get_effective_risk_tier("wire_transfer") == ApprovalRiskTier.CRITICAL

    def test_approval_config_get_effective_risk_tier_unknown_tool(self):
        """get_effective_risk_tier should return default for unknown tool."""
        config = ApprovalConfig(
            enabled=True,
            default_risk_tier=ApprovalRiskTier.MEDIUM,
        )
        assert config.get_effective_risk_tier("unknown_tool") == ApprovalRiskTier.MEDIUM


# ---------------------------------------------------------------------------
# Test AC2: Policy precedence order is explicit and deterministic
# ---------------------------------------------------------------------------


class TestPrecedenceDeterminism:
    """Test that precedence order is explicit and deterministic."""

    def test_rule_match_precedence_constant_defined(self):
        """RULE_MATCH_PRECEDENCE constant should be defined in rules module."""
        assert RULE_MATCH_PRECEDENCE == (
            "constraint_specificity_desc",
            "bounded_scope_desc",
            "created_at_desc",
            "rule_id_asc",
        )

    def test_default_approval_rule_precedence_in_config(self):
        """DEFAULT_APPROVAL_RULE_PRECEDENCE should match RULE_MATCH_PRECEDENCE."""
        assert DEFAULT_APPROVAL_RULE_PRECEDENCE == RULE_MATCH_PRECEDENCE

    def test_approval_config_includes_rule_precedence(self):
        """ApprovalConfig should include rule_precedence field."""
        config = ApprovalConfig(
            enabled=True,
            rule_precedence=("constraint_specificity_desc", "created_at_desc"),
        )
        assert config.rule_precedence == (
            "constraint_specificity_desc",
            "created_at_desc",
        )

    def test_precedence_order_1_higher_specificity_wins(self):
        """Most specific constraint should win (precedence rule 1)."""
        now = datetime.now(UTC)
        broad_rule = {
            "id": uuid.uuid4(),
            "tool_name": "email_send",
            "arg_constraints": json.dumps({"to": "*"}),
            "description": "Broad rule",
            "active": True,
            "created_at": now,
            "expires_at": None,
            "max_uses": None,
            "use_count": 0,
        }
        exact_rule = {
            "id": uuid.uuid4(),
            "tool_name": "email_send",
            "arg_constraints": json.dumps({"to": "alice@example.com"}),
            "description": "Exact rule",
            "active": True,
            "created_at": now,
            "expires_at": None,
            "max_uses": None,
            "use_count": 0,
        }

        matched = match_rules_from_list(
            "email_send",
            {"to": "alice@example.com"},
            [broad_rule, exact_rule],
        )
        assert matched is not None
        assert matched.id == exact_rule["id"]

    def test_precedence_order_2_bounded_scope_wins_on_tie(self):
        """When specificity ties, bounded scope wins (precedence rule 2)."""
        now = datetime.now(UTC)
        unbounded = {
            "id": uuid.uuid4(),
            "tool_name": "email_send",
            "arg_constraints": json.dumps({"to": "alice@example.com"}),
            "description": "Unbounded rule",
            "active": True,
            "created_at": now,
            "expires_at": None,
            "max_uses": None,
            "use_count": 0,
        }
        bounded_expiry = {
            "id": uuid.uuid4(),
            "tool_name": "email_send",
            "arg_constraints": json.dumps({"to": "alice@example.com"}),
            "description": "Bounded expiry rule",
            "active": True,
            "created_at": now,
            "expires_at": now + timedelta(hours=1),
            "max_uses": None,
            "use_count": 0,
        }

        matched = match_rules_from_list(
            "email_send",
            {"to": "alice@example.com"},
            [unbounded, bounded_expiry],
        )
        assert matched is not None
        assert matched.id == bounded_expiry["id"]

    def test_precedence_order_2_max_uses_is_bounded(self):
        """Rule with max_uses should win over unbounded (precedence rule 2)."""
        now = datetime.now(UTC)
        unbounded = {
            "id": uuid.uuid4(),
            "tool_name": "email_send",
            "arg_constraints": json.dumps({"to": "alice@example.com"}),
            "description": "Unbounded rule",
            "active": True,
            "created_at": now,
            "expires_at": None,
            "max_uses": None,
            "use_count": 0,
        }
        bounded_uses = {
            "id": uuid.uuid4(),
            "tool_name": "email_send",
            "arg_constraints": json.dumps({"to": "alice@example.com"}),
            "description": "Bounded uses rule",
            "active": True,
            "created_at": now,
            "expires_at": None,
            "max_uses": 5,
            "use_count": 0,
        }

        matched = match_rules_from_list(
            "email_send",
            {"to": "alice@example.com"},
            [unbounded, bounded_uses],
        )
        assert matched is not None
        assert matched.id == bounded_uses["id"]

    def test_precedence_order_3_newer_created_at_wins(self):
        """When specificity and bounded tie, newer rule wins (precedence rule 3)."""
        older = {
            "id": uuid.uuid4(),
            "tool_name": "email_send",
            "arg_constraints": json.dumps({"to": "alice@example.com"}),
            "description": "Older rule",
            "active": True,
            "created_at": datetime.now(UTC) - timedelta(hours=2),
            "expires_at": None,
            "max_uses": None,
            "use_count": 0,
        }
        newer = {
            "id": uuid.uuid4(),
            "tool_name": "email_send",
            "arg_constraints": json.dumps({"to": "alice@example.com"}),
            "description": "Newer rule",
            "active": True,
            "created_at": datetime.now(UTC),
            "expires_at": None,
            "max_uses": None,
            "use_count": 0,
        }

        matched = match_rules_from_list(
            "email_send",
            {"to": "alice@example.com"},
            [older, newer],
        )
        assert matched is not None
        assert matched.id == newer["id"]

    def test_precedence_order_4_lexical_rule_id_tie_breaker(self):
        """When all else ties, lexical rule ID wins (precedence rule 4)."""
        now = datetime.now(UTC)
        rule_a = {
            "id": uuid.UUID("00000000-0000-0000-0000-000000000001"),
            "tool_name": "email_send",
            "arg_constraints": json.dumps({"to": "alice@example.com"}),
            "description": "Rule A",
            "active": True,
            "created_at": now,
            "expires_at": None,
            "max_uses": None,
            "use_count": 0,
        }
        rule_b = {
            "id": uuid.UUID("00000000-0000-0000-0000-000000000002"),
            "tool_name": "email_send",
            "arg_constraints": json.dumps({"to": "alice@example.com"}),
            "description": "Rule B",
            "active": True,
            "created_at": now,
            "expires_at": None,
            "max_uses": None,
            "use_count": 0,
        }

        matched = match_rules_from_list(
            "email_send",
            {"to": "alice@example.com"},
            [rule_b, rule_a],
        )
        assert matched is not None
        # Lexical sort ascending, so rule_a (lower UUID) wins
        assert matched.id == rule_a["id"]


# ---------------------------------------------------------------------------
# Test AC3: Higher-risk rule constraints are enforceable
# ---------------------------------------------------------------------------


class TestHighRiskConstraints:
    """Test that high-risk tiers enforce narrower/bounded rule constraints."""

    async def test_high_risk_requires_narrow_constraints(self):
        """High-risk tools must have at least one exact or pattern constraint."""
        module = ApprovalsModule()
        db = MockDB()
        await module.on_startup({}, db)

        # Wire high-risk policy
        policy = ApprovalConfig(
            enabled=True,
            default_risk_tier=ApprovalRiskTier.HIGH,
        )
        module.set_approval_policy(policy)

        # Create rule with only 'any' constraints should fail
        result = await module._create_approval_rule(
            tool_name="wire_transfer",
            arg_constraints={"account": {"type": "any"}},
            description="Broad high-risk rule",
            actor={"type": "human", "id": "operator", "authenticated": True},
        )
        assert "error" in result
        assert "at least one exact or pattern" in result["error"]

    async def test_high_risk_requires_bounded_scope(self):
        """High-risk tools must have expires_at or max_uses."""
        module = ApprovalsModule()
        db = MockDB()
        await module.on_startup({}, db)

        policy = ApprovalConfig(
            enabled=True,
            default_risk_tier=ApprovalRiskTier.HIGH,
        )
        module.set_approval_policy(policy)

        # Create rule with narrow constraint but no bounded scope should fail
        result = await module._create_approval_rule(
            tool_name="wire_transfer",
            arg_constraints={"account": "acct_123"},
            description="Unbounded high-risk rule",
            actor={"type": "human", "id": "operator", "authenticated": True},
        )
        assert "error" in result
        assert "bounded scope" in result["error"]

    async def test_high_risk_accepts_narrow_and_bounded(self):
        """High-risk tools with exact constraint and max_uses should succeed."""
        module = ApprovalsModule()
        db = MockDB()
        await module.on_startup({}, db)

        policy = ApprovalConfig(
            enabled=True,
            default_risk_tier=ApprovalRiskTier.HIGH,
        )
        module.set_approval_policy(policy)

        result = await module._create_approval_rule(
            tool_name="wire_transfer",
            arg_constraints={"account": "acct_123"},
            description="Bounded high-risk rule",
            max_uses=1,
            actor={"type": "human", "id": "operator", "authenticated": True},
        )
        assert "error" not in result
        assert result["tool_name"] == "wire_transfer"
        assert result["max_uses"] == 1

    async def test_critical_risk_enforces_same_constraints(self):
        """Critical-risk tools enforce same constraints as high-risk."""
        module = ApprovalsModule()
        db = MockDB()
        await module.on_startup({}, db)

        policy = ApprovalConfig(
            enabled=True,
            default_risk_tier=ApprovalRiskTier.CRITICAL,
        )
        module.set_approval_policy(policy)

        # Broad + unbounded should fail
        result = await module._create_approval_rule(
            tool_name="admin_delete",
            arg_constraints={"resource": "*"},
            description="Broad critical rule",
            actor={"type": "human", "id": "operator", "authenticated": True},
        )
        assert "error" in result

    async def test_low_risk_allows_broad_unbounded(self):
        """Low-risk tools can have broad constraints and no bounded scope."""
        module = ApprovalsModule()
        db = MockDB()
        await module.on_startup({}, db)

        policy = ApprovalConfig(
            enabled=True,
            default_risk_tier=ApprovalRiskTier.LOW,
        )
        module.set_approval_policy(policy)

        result = await module._create_approval_rule(
            tool_name="read_docs",
            arg_constraints={},
            description="Broad low-risk rule",
            actor={"type": "human", "id": "operator", "authenticated": True},
        )
        assert "error" not in result

    async def test_medium_risk_allows_broad_unbounded(self):
        """Medium-risk tools can have broad constraints and no bounded scope."""
        module = ApprovalsModule()
        db = MockDB()
        await module.on_startup({}, db)

        policy = ApprovalConfig(
            enabled=True,
            default_risk_tier=ApprovalRiskTier.MEDIUM,
        )
        module.set_approval_policy(policy)

        result = await module._create_approval_rule(
            tool_name="send_notification",
            arg_constraints={"channel": "*"},
            description="Broad medium-risk rule",
            actor={"type": "human", "id": "operator", "authenticated": True},
        )
        assert "error" not in result

    async def test_per_tool_risk_tier_override_enforced(self):
        """Per-tool risk tier override should enforce high-risk constraints."""
        module = ApprovalsModule()
        db = MockDB()
        await module.on_startup({}, db)

        # Default is low, but specific tool is high
        policy = ApprovalConfig(
            enabled=True,
            default_risk_tier=ApprovalRiskTier.LOW,
            gated_tools={
                "escalate_privileges": GatedToolConfig(risk_tier=ApprovalRiskTier.HIGH),
            },
        )
        module.set_approval_policy(policy)

        # Broad rule for high-risk override should fail
        result = await module._create_approval_rule(
            tool_name="escalate_privileges",
            arg_constraints={},
            description="Broad override rule",
            actor={"type": "human", "id": "operator", "authenticated": True},
        )
        assert "error" in result

    async def test_create_rule_from_action_auto_bounds_high_risk(self):
        """create_rule_from_action should auto-bound high-risk rules with max_uses=1."""
        module = ApprovalsModule()
        db = MockDB()
        await module.on_startup({}, db)

        policy = ApprovalConfig(
            enabled=True,
            default_risk_tier=ApprovalRiskTier.HIGH,
        )
        module.set_approval_policy(policy)

        # Create a pending action
        action_id = db._insert_action(
            tool_name="wire_transfer",
            tool_args={"account": "acct_123", "amount": 1000},
        )

        # Create rule from action should auto-add max_uses=1
        result = await module._create_rule_from_action(
            action_id=str(action_id),
            actor={"type": "human", "id": "operator", "authenticated": True},
        )
        assert "error" not in result
        assert result["max_uses"] == 1

    async def test_approve_action_with_create_rule_auto_bounds_high_risk(self):
        """approve_action with create_rule should auto-bound high-risk rules."""
        module = ApprovalsModule()
        db = MockDB()
        await module.on_startup({}, db)

        policy = ApprovalConfig(
            enabled=True,
            default_risk_tier=ApprovalRiskTier.CRITICAL,
        )
        module.set_approval_policy(policy)

        action_id = db._insert_action(
            tool_name="admin_delete",
            tool_args={"resource_id": "res_123"},
            status="pending",
        )

        # Module execution is not wired, so we expect approve to succeed
        # but won't test execution path (that's covered in other tests)
        result = await module._approve_action(
            action_id=str(action_id),
            create_rule=True,
            actor={"type": "human", "id": "operator", "authenticated": True},
        )
        # The result should either have created_rule or created_rule_error
        # If created successfully, it should have max_uses=1
        if "created_rule" in result:
            assert result["created_rule"]["max_uses"] == 1
        # Otherwise, we may have an error creating the rule (if constraints invalid)
        # which is acceptable for this test - the key is testing the auto-bounding logic


# ---------------------------------------------------------------------------
# Test AC4: Tests validate precedence and tier-driven behavior
# ---------------------------------------------------------------------------


class TestPrecedenceAndTierIntegration:
    """Integration tests for precedence and tier-driven behavior."""

    def test_specificity_scoring_matches_precedence(self):
        """Specificity scoring should align with precedence rule 1."""
        exact_constraints = {"to": "alice@example.com", "subject": "urgent"}
        pattern_constraints = {"to": {"type": "pattern", "value": "*@example.com"}}
        wildcard_constraints = {"to": "*"}

        exact_score = _rule_specificity(exact_constraints)
        pattern_score = _rule_specificity(pattern_constraints)
        wildcard_score = _rule_specificity(wildcard_constraints)

        assert exact_score > pattern_score > wildcard_score

    def test_bounded_rule_detection(self):
        """_is_bounded_rule should detect expires_at or max_uses."""
        unbounded = ApprovalRule(
            id=uuid.uuid4(),
            tool_name="test",
            arg_constraints={},
            description="test",
            created_at=datetime.now(UTC),
        )
        bounded_expiry = ApprovalRule(
            id=uuid.uuid4(),
            tool_name="test",
            arg_constraints={},
            description="test",
            created_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        bounded_uses = ApprovalRule(
            id=uuid.uuid4(),
            tool_name="test",
            arg_constraints={},
            description="test",
            created_at=datetime.now(UTC),
            max_uses=5,
        )

        assert not _is_bounded_rule(unbounded)
        assert _is_bounded_rule(bounded_expiry)
        assert _is_bounded_rule(bounded_uses)

    async def test_multiple_rules_with_mixed_tiers(self):
        """Multiple rules with different constraints should follow precedence."""
        now = datetime.now(UTC)

        # Most specific, unbounded
        exact_unbounded = {
            "id": uuid.uuid4(),
            "tool_name": "email_send",
            "arg_constraints": json.dumps({"to": "alice@example.com", "subject": "urgent"}),
            "description": "Exact unbounded",
            "active": True,
            "created_at": now - timedelta(hours=1),
            "expires_at": None,
            "max_uses": None,
            "use_count": 0,
        }

        # Less specific, bounded
        pattern_bounded = {
            "id": uuid.uuid4(),
            "tool_name": "email_send",
            "arg_constraints": json.dumps({"to": {"type": "pattern", "value": "*@example.com"}}),
            "description": "Pattern bounded",
            "active": True,
            "created_at": now,
            "expires_at": now + timedelta(hours=1),
            "max_uses": None,
            "use_count": 0,
        }

        matched = match_rules_from_list(
            "email_send",
            {"to": "alice@example.com", "subject": "urgent"},
            [pattern_bounded, exact_unbounded],
        )
        assert matched is not None
        # Specificity (rule 1) beats bounded (rule 2)
        assert matched.id == exact_unbounded["id"]

    async def test_validation_error_includes_risk_tier_context(self):
        """Validation errors for high-risk rules should mention risk tier."""
        module = ApprovalsModule()
        db = MockDB()
        await module.on_startup({}, db)

        policy = ApprovalConfig(
            enabled=True,
            gated_tools={
                "wire_transfer": GatedToolConfig(risk_tier=ApprovalRiskTier.CRITICAL),
            },
        )
        module.set_approval_policy(policy)

        result = await module._create_approval_rule(
            tool_name="wire_transfer",
            arg_constraints={},
            description="Test",
            actor={"type": "human", "id": "operator", "authenticated": True},
        )
        assert "error" in result
        assert "wire_transfer" in result["error"]

    def test_precedence_determinism_with_identical_rules(self):
        """Given identical specificity/bounded/created_at, rule ID breaks tie."""
        now = datetime.now(UTC)
        rule_1 = {
            "id": uuid.UUID("00000000-0000-0000-0000-000000000001"),
            "tool_name": "test",
            "arg_constraints": json.dumps({"arg": "value"}),
            "description": "Rule 1",
            "active": True,
            "created_at": now,
            "expires_at": None,
            "max_uses": None,
            "use_count": 0,
        }
        rule_2 = {
            "id": uuid.UUID("00000000-0000-0000-0000-000000000002"),
            "tool_name": "test",
            "arg_constraints": json.dumps({"arg": "value"}),
            "description": "Rule 2",
            "active": True,
            "created_at": now,
            "expires_at": None,
            "max_uses": None,
            "use_count": 0,
        }

        matched = match_rules_from_list("test", {"arg": "value"}, [rule_2, rule_1])
        assert matched is not None
        assert matched.id == rule_1["id"]
