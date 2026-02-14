"""Tests for standing approval rules engine and CRUD tools.

Covers all acceptance criteria from butlers-clc.6:
1. Rule matching correctly evaluates exact, pattern, and any constraints
2. Most-specific rule wins when multiple rules match
3. Expired and max-uses-exceeded rules are skipped
4. Constraint suggestion correctly classifies sensitive vs non-sensitive args
5. create_rule_from_action generates rule from pending action with smart defaults
6. All 6 CRUD tools work end-to-end
7. Tests cover: matching priority, constraint types, expiry, max_uses, suggestion logic
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel

from butlers.config import ApprovalConfig, ApprovalRiskTier, GatedToolConfig
from butlers.modules.approvals.models import ApprovalRule
from butlers.modules.approvals.module import ApprovalsModule
from butlers.modules.approvals.rules import (
    _args_match_constraints,
    _constraint_specificity,
    _evaluate_constraint,
    _rule_specificity,
    match_rules_from_list,
)
from butlers.modules.approvals.sensitivity import suggest_constraints
from butlers.modules.base import Module, ToolMeta

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Mock DB helper — extended from test_module_approvals to support rules
# ---------------------------------------------------------------------------


class MockDB:
    """In-memory mock of an asyncpg connection pool for rules tests.

    Supports pending_actions and approval_rules tables.
    """

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

    def _insert_rule(self, **kwargs: Any) -> uuid.UUID:
        """Helper to seed an approval rule for testing."""
        rule_id = kwargs.get("id", uuid.uuid4())
        if isinstance(rule_id, str):
            rule_id = uuid.UUID(rule_id)
        row = {
            "id": rule_id,
            "tool_name": kwargs.get("tool_name", "test_tool"),
            "arg_constraints": json.dumps(kwargs.get("arg_constraints", {})),
            "description": kwargs.get("description", "test rule"),
            "created_from": kwargs.get("created_from"),
            "created_at": kwargs.get("created_at", datetime.now(UTC)),
            "expires_at": kwargs.get("expires_at"),
            "max_uses": kwargs.get("max_uses"),
            "use_count": kwargs.get("use_count", 0),
            "active": kwargs.get("active", True),
        }
        self.approval_rules[rule_id] = row
        return rule_id

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        """Simulate asyncpg fetch()."""
        if "GROUP BY status" in query:
            counts: dict[str, int] = {}
            for row in self.pending_actions.values():
                s = row["status"]
                counts[s] = counts.get(s, 0) + 1
            return [{"status": s, "count": c} for s, c in counts.items()]

        if "pending_actions" in query and "expires_at" in query:
            status_arg = args[0] if args else "pending"
            now_arg = args[1] if len(args) > 1 else datetime.now(UTC)
            results = []
            for row in self.pending_actions.values():
                if (
                    row["status"] == status_arg
                    and row["expires_at"] is not None
                    and row["expires_at"] < now_arg
                ):
                    results.append(dict(row))
            return results

        if "approval_rules" in query:
            rows = list(self.approval_rules.values())

            # Filter by tool_name if present
            if "tool_name = $1" in query:
                tool_name = args[0] if args else None
                if tool_name:
                    rows = [r for r in rows if r["tool_name"] == tool_name]

            # Filter by active
            if "active = true" in query:
                rows = [r for r in rows if r["active"]]

            # Sort by created_at descending
            rows.sort(key=lambda r: r["created_at"], reverse=True)
            return [dict(r) for r in rows]

        if "pending_actions" in query:
            rows = list(self.pending_actions.values())
            if "WHERE status = $1" in query:
                status_filter = args[0]
                rows = [r for r in rows if r["status"] == status_filter]
                limit = args[1] if len(args) > 1 else 50
            else:
                limit = args[0] if args else 50
            rows.sort(key=lambda r: r["requested_at"], reverse=True)
            return [dict(r) for r in rows[:limit]]

        return []

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        """Simulate asyncpg fetchrow()."""
        if "pending_actions" in query and args:
            action_id = args[0]
            if isinstance(action_id, str):
                action_id = uuid.UUID(action_id)
            row = self.pending_actions.get(action_id)
            return dict(row) if row else None

        if "approval_rules" in query and args:
            rule_id = args[0]
            if isinstance(rule_id, str):
                rule_id = uuid.UUID(rule_id)
            row = self.approval_rules.get(rule_id)
            return dict(row) if row else None

        return None

    async def execute(self, query: str, *args: Any) -> None:
        """Simulate asyncpg execute()."""
        if "INSERT INTO approval_rules" in query:
            rule_id = args[0]
            row = {
                "id": args[0],
                "tool_name": args[1],
                "arg_constraints": args[2],
                "description": args[3],
            }
            # Different INSERT signatures have different column layouts.
            if "expires_at" in query:
                # create_approval_rule: id, tool_name, arg_constraints, description,
                #   created_at, expires_at, max_uses, active
                row["created_from"] = None
                row["created_at"] = args[4]
                row["expires_at"] = args[5]
                row["max_uses"] = args[6]
                row["active"] = args[7]
                row["use_count"] = 0
            elif len(args) == 8:
                # create_rule_from_action / approve_action(create_rule):
                #   id, tool_name, arg_constraints, description,
                #   created_from, created_at, max_uses, active
                row["created_from"] = args[4]
                row["created_at"] = args[5]
                row["max_uses"] = args[6]
                row["active"] = args[7]
                row["expires_at"] = None
                row["use_count"] = 0
            elif len(args) == 7:
                # Legacy layout without max_uses.
                row["created_from"] = args[4]
                row["created_at"] = args[5]
                row["active"] = args[6]
                row["expires_at"] = None
                row["max_uses"] = None
                row["use_count"] = 0
            self.approval_rules[rule_id] = row

        elif "UPDATE approval_rules" in query and "active = $1" in query:
            rule_id = args[-1]
            if isinstance(rule_id, str):
                rule_id = uuid.UUID(rule_id)
            if rule_id in self.approval_rules:
                self.approval_rules[rule_id]["active"] = args[0]

        elif "UPDATE pending_actions" in query:
            action_id = args[-1]
            if isinstance(action_id, str):
                action_id = uuid.UUID(action_id)
            if action_id in self.pending_actions:
                row = self.pending_actions[action_id]
                if "status = $1" in query and "decided_by = $2" in query:
                    row["status"] = args[0]
                    row["decided_by"] = args[1]
                    row["decided_at"] = args[2]
                elif "status = $1" in query and "execution_result = $2" in query:
                    row["status"] = args[0]
                    row["execution_result"] = args[1]

        elif "INSERT INTO pending_actions" in query:
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
                "approval_rule_id": None,
                "decided_by": None,
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


@pytest.fixture
def mock_db() -> MockDB:
    return MockDB()


@pytest.fixture
def module() -> ApprovalsModule:
    return ApprovalsModule()


@pytest.fixture
def human_actor() -> dict[str, Any]:
    """Authenticated human actor context for approval decision calls."""
    return {"type": "human", "id": "owner", "authenticated": True}


# ---------------------------------------------------------------------------
# Helper: make rule dicts for testing match_rules_from_list
# ---------------------------------------------------------------------------


def _make_rule(
    tool_name: str = "email_send",
    arg_constraints: dict | None = None,
    *,
    active: bool = True,
    expires_at: datetime | None = None,
    max_uses: int | None = None,
    use_count: int = 0,
) -> dict[str, Any]:
    """Create a rule dict matching the DB schema for testing."""
    return {
        "id": uuid.uuid4(),
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


# ===========================================================================
# Tests: Constraint evaluation
# ===========================================================================


class TestEvaluateConstraint:
    """Test the _evaluate_constraint function with all constraint types."""

    def test_exact_constraint_matches(self):
        constraint = {"type": "exact", "value": "alice@example.com"}
        assert _evaluate_constraint("alice@example.com", constraint) is True

    def test_exact_constraint_no_match(self):
        constraint = {"type": "exact", "value": "alice@example.com"}
        assert _evaluate_constraint("bob@example.com", constraint) is False

    def test_pattern_constraint_matches_glob(self):
        constraint = {"type": "pattern", "value": "*@mycompany.com"}
        assert _evaluate_constraint("alice@mycompany.com", constraint) is True

    def test_pattern_constraint_no_match(self):
        constraint = {"type": "pattern", "value": "*@mycompany.com"}
        assert _evaluate_constraint("alice@other.com", constraint) is False

    def test_pattern_constraint_with_prefix_glob(self):
        constraint = {"type": "pattern", "value": "admin_*"}
        assert _evaluate_constraint("admin_panel", constraint) is True
        assert _evaluate_constraint("user_panel", constraint) is False

    def test_any_constraint_always_matches(self):
        constraint = {"type": "any"}
        assert _evaluate_constraint("anything", constraint) is True
        assert _evaluate_constraint(42, constraint) is True
        assert _evaluate_constraint(None, constraint) is True

    def test_legacy_wildcard_star(self):
        """Legacy '*' string is treated as 'any'."""
        assert _evaluate_constraint("anything", "*") is True

    def test_legacy_exact_value(self):
        """Legacy plain value is treated as exact match."""
        assert _evaluate_constraint("hello", "hello") is True
        assert _evaluate_constraint("hello", "world") is False

    def test_exact_constraint_with_integer(self):
        constraint = {"type": "exact", "value": 42}
        assert _evaluate_constraint(42, constraint) is True
        assert _evaluate_constraint(43, constraint) is False

    def test_pattern_constraint_with_integer(self):
        """Pattern works with string representation of integers."""
        constraint = {"type": "pattern", "value": "4*"}
        assert _evaluate_constraint(42, constraint) is True
        assert _evaluate_constraint(52, constraint) is False


class TestConstraintSpecificity:
    """Test the specificity scoring for constraints."""

    def test_exact_is_most_specific(self):
        assert _constraint_specificity({"type": "exact", "value": "x"}) == 2

    def test_pattern_is_medium(self):
        assert _constraint_specificity({"type": "pattern", "value": "*"}) == 1

    def test_any_is_least_specific(self):
        assert _constraint_specificity({"type": "any"}) == 0

    def test_legacy_star_is_least_specific(self):
        assert _constraint_specificity("*") == 0

    def test_legacy_value_is_most_specific(self):
        assert _constraint_specificity("hello") == 2


class TestRuleSpecificity:
    """Test the _rule_specificity scoring function."""

    def test_empty_constraints(self):
        assert _rule_specificity({}) == 0

    def test_all_exact(self):
        constraints = {
            "to": {"type": "exact", "value": "alice@example.com"},
            "subject": {"type": "exact", "value": "hello"},
        }
        assert _rule_specificity(constraints) == 4

    def test_mixed(self):
        constraints = {
            "to": {"type": "exact", "value": "alice@example.com"},
            "body": {"type": "any"},
        }
        assert _rule_specificity(constraints) == 2

    def test_all_any(self):
        constraints = {
            "to": {"type": "any"},
            "body": {"type": "any"},
        }
        assert _rule_specificity(constraints) == 0


# ===========================================================================
# Tests: _args_match_constraints
# ===========================================================================


class TestArgsMatchConstraints:
    """Test the constraint matching logic against tool args."""

    def test_empty_constraints_match_anything(self):
        assert _args_match_constraints({"to": "alice", "body": "hello"}, {}) is True

    def test_exact_constraint_matches(self):
        constraints = {"to": {"type": "exact", "value": "alice@example.com"}}
        assert _args_match_constraints({"to": "alice@example.com"}, constraints) is True

    def test_exact_constraint_no_match(self):
        constraints = {"to": {"type": "exact", "value": "alice@example.com"}}
        assert _args_match_constraints({"to": "bob@example.com"}, constraints) is False

    def test_pattern_constraint_matches(self):
        constraints = {"to": {"type": "pattern", "value": "*@mycompany.com"}}
        assert _args_match_constraints({"to": "alice@mycompany.com"}, constraints) is True

    def test_any_constraint_matches(self):
        constraints = {"to": {"type": "any"}}
        assert _args_match_constraints({"to": "anything"}, constraints) is True

    def test_missing_arg_fails_for_exact(self):
        constraints = {"to": {"type": "exact", "value": "alice"}}
        assert _args_match_constraints({}, constraints) is False

    def test_missing_arg_ok_for_any(self):
        constraints = {"to": {"type": "any"}}
        assert _args_match_constraints({}, constraints) is True

    def test_extra_args_are_ignored(self):
        constraints = {"to": {"type": "exact", "value": "alice"}}
        assert _args_match_constraints({"to": "alice", "body": "extra"}, constraints) is True

    def test_multiple_constraints_all_must_match(self):
        constraints = {
            "to": {"type": "exact", "value": "alice@example.com"},
            "subject": {"type": "pattern", "value": "Weekly*"},
        }
        assert (
            _args_match_constraints(
                {"to": "alice@example.com", "subject": "Weekly report"},
                constraints,
            )
            is True
        )
        assert (
            _args_match_constraints(
                {"to": "alice@example.com", "subject": "Monthly report"},
                constraints,
            )
            is False
        )

    def test_legacy_star_wildcard(self):
        constraints = {"to": "*"}
        assert _args_match_constraints({"to": "anyone"}, constraints) is True

    def test_legacy_exact_value(self):
        constraints = {"to": "alice"}
        assert _args_match_constraints({"to": "alice"}, constraints) is True
        assert _args_match_constraints({"to": "bob"}, constraints) is False


# ===========================================================================
# Tests: match_rules_from_list (the pure-logic rule matcher)
# ===========================================================================


class TestMatchRulesFromList:
    """Test rule matching from a pre-fetched list."""

    def test_no_rules_returns_none(self):
        result = match_rules_from_list("email_send", {"to": "alice"}, [])
        assert result is None

    def test_exact_match_returns_rule(self):
        rule = _make_rule("email_send", {"to": {"type": "exact", "value": "alice@example.com"}})
        result = match_rules_from_list("email_send", {"to": "alice@example.com"}, [rule])
        assert result is not None
        assert result.id == rule["id"]

    def test_pattern_match_returns_rule(self):
        rule = _make_rule("email_send", {"to": {"type": "pattern", "value": "*@mycompany.com"}})
        result = match_rules_from_list("email_send", {"to": "alice@mycompany.com"}, [rule])
        assert result is not None

    def test_any_match_returns_rule(self):
        rule = _make_rule("email_send", {"to": {"type": "any"}})
        result = match_rules_from_list("email_send", {"to": "anyone"}, [rule])
        assert result is not None

    def test_wrong_tool_name_no_match(self):
        rule = _make_rule("telegram_send", {})
        result = match_rules_from_list("email_send", {}, [rule])
        assert result is None

    def test_expired_rule_skipped(self):
        rule = _make_rule("email_send", {}, expires_at=datetime.now(UTC) - timedelta(hours=1))
        result = match_rules_from_list("email_send", {}, [rule])
        assert result is None

    def test_future_expiry_still_matches(self):
        rule = _make_rule("email_send", {}, expires_at=datetime.now(UTC) + timedelta(hours=24))
        result = match_rules_from_list("email_send", {}, [rule])
        assert result is not None

    def test_max_uses_exhausted_skipped(self):
        rule = _make_rule("email_send", {}, max_uses=5, use_count=5)
        result = match_rules_from_list("email_send", {}, [rule])
        assert result is None

    def test_max_uses_not_exhausted_matches(self):
        rule = _make_rule("email_send", {}, max_uses=5, use_count=4)
        result = match_rules_from_list("email_send", {}, [rule])
        assert result is not None

    def test_inactive_rule_skipped(self):
        rule = _make_rule("email_send", {}, active=False)
        result = match_rules_from_list("email_send", {}, [rule])
        assert result is None

    def test_most_specific_rule_wins(self):
        """When multiple rules match, the one with the highest specificity wins."""
        broad_rule = _make_rule("email_send", {"to": {"type": "any"}})
        pattern_rule = _make_rule(
            "email_send", {"to": {"type": "pattern", "value": "*@mycompany.com"}}
        )
        exact_rule = _make_rule(
            "email_send", {"to": {"type": "exact", "value": "alice@mycompany.com"}}
        )

        result = match_rules_from_list(
            "email_send",
            {"to": "alice@mycompany.com"},
            [broad_rule, pattern_rule, exact_rule],
        )
        assert result is not None
        assert result.id == exact_rule["id"]

    def test_specificity_ranking_with_multiple_args(self):
        """Rule with more pinned args is more specific."""
        one_exact = _make_rule(
            "email_send",
            {
                "to": {"type": "exact", "value": "alice@example.com"},
                "body": {"type": "any"},
            },
        )
        two_exact = _make_rule(
            "email_send",
            {
                "to": {"type": "exact", "value": "alice@example.com"},
                "body": {"type": "exact", "value": "hello"},
            },
        )

        result = match_rules_from_list(
            "email_send",
            {"to": "alice@example.com", "body": "hello"},
            [one_exact, two_exact],
        )
        assert result is not None
        assert result.id == two_exact["id"]

    def test_tie_breaker_prefers_bounded_scope(self):
        """With equal specificity, bounded rules win over unbounded rules."""
        unbounded = _make_rule(
            "email_send",
            {"to": {"type": "exact", "value": "alice@example.com"}},
            max_uses=None,
            expires_at=None,
        )
        bounded = _make_rule(
            "email_send",
            {"to": {"type": "exact", "value": "alice@example.com"}},
            max_uses=5,
            expires_at=None,
        )

        result = match_rules_from_list(
            "email_send",
            {"to": "alice@example.com"},
            [unbounded, bounded],
        )
        assert result is not None
        assert result.id == bounded["id"]

    def test_tie_breaker_prefers_newer_rule(self):
        """With equal specificity and bounds, newer rules win."""
        older = _make_rule(
            "email_send",
            {"to": {"type": "exact", "value": "alice@example.com"}},
            max_uses=5,
        )
        newer = _make_rule(
            "email_send",
            {"to": {"type": "exact", "value": "alice@example.com"}},
            max_uses=5,
        )
        older["created_at"] = datetime.now(UTC) - timedelta(hours=1)
        newer["created_at"] = datetime.now(UTC)

        result = match_rules_from_list(
            "email_send",
            {"to": "alice@example.com"},
            [older, newer],
        )
        assert result is not None
        assert result.id == newer["id"]

    def test_tie_breaker_uses_rule_id_for_full_tie(self):
        """Rule id is a deterministic final tie-breaker."""
        created_at = datetime.now(UTC)
        first = _make_rule(
            "email_send",
            {"to": {"type": "exact", "value": "alice@example.com"}},
            max_uses=5,
        )
        second = _make_rule(
            "email_send",
            {"to": {"type": "exact", "value": "alice@example.com"}},
            max_uses=5,
        )
        first["created_at"] = created_at
        second["created_at"] = created_at
        first["id"] = uuid.UUID("00000000-0000-0000-0000-000000000010")
        second["id"] = uuid.UUID("00000000-0000-0000-0000-000000000099")

        result = match_rules_from_list(
            "email_send",
            {"to": "alice@example.com"},
            [second, first],
        )
        assert result is not None
        assert result.id == first["id"]

    def test_constraint_mismatch_no_match(self):
        rule = _make_rule("email_send", {"to": {"type": "exact", "value": "alice@example.com"}})
        result = match_rules_from_list("email_send", {"to": "bob@example.com"}, [rule])
        assert result is None

    def test_empty_constraints_match_any_args(self):
        rule = _make_rule("email_send", {})
        result = match_rules_from_list("email_send", {"to": "anyone", "body": "anything"}, [rule])
        assert result is not None

    def test_legacy_format_backward_compat(self):
        """Legacy format with plain values and '*' still works."""
        rule = _make_rule("email_send", {"to": "alice@example.com", "body": "*"})
        result = match_rules_from_list(
            "email_send",
            {"to": "alice@example.com", "body": "anything"},
            [rule],
        )
        assert result is not None

    def test_returns_approval_rule_model(self):
        """The returned value should be an ApprovalRule instance."""
        rule = _make_rule("email_send", {})
        result = match_rules_from_list("email_send", {}, [rule])
        assert isinstance(result, ApprovalRule)


# ===========================================================================
# Tests: suggest_constraints
# ===========================================================================


class TestSuggestConstraints:
    """Test the constraint suggestion engine."""

    def test_sensitive_arg_gets_exact(self):
        """A sensitive arg (e.g., 'to') should get an exact constraint."""
        result = suggest_constraints(
            "email_send",
            {"to": "alice@example.com"},
        )
        assert result["to"]["type"] == "exact"
        assert result["to"]["value"] == "alice@example.com"

    def test_non_sensitive_arg_gets_any(self):
        """A non-sensitive arg (e.g., 'body') should get an any constraint."""
        result = suggest_constraints(
            "email_send",
            {"body": "hello world"},
        )
        assert result["body"]["type"] == "any"

    def test_mixed_args(self):
        """Mixed sensitive and non-sensitive args get appropriate constraints."""
        result = suggest_constraints(
            "email_send",
            {"to": "alice@example.com", "body": "hello", "subject": "greetings"},
        )
        assert result["to"]["type"] == "exact"
        assert result["to"]["value"] == "alice@example.com"
        assert result["body"]["type"] == "any"
        assert result["subject"]["type"] == "any"

    def test_amount_is_sensitive(self):
        """'amount' should be heuristically sensitive."""
        result = suggest_constraints(
            "purchase_create",
            {"amount": 99.99, "description": "widget"},
        )
        assert result["amount"]["type"] == "exact"
        assert result["amount"]["value"] == 99.99
        assert result["description"]["type"] == "any"

    def test_url_is_sensitive(self):
        """'url' should be heuristically sensitive."""
        result = suggest_constraints(
            "web_fetch",
            {"url": "https://example.com", "method": "GET"},
        )
        assert result["url"]["type"] == "exact"
        assert result["url"]["value"] == "https://example.com"
        assert result["method"]["type"] == "any"

    def test_with_explicit_module_metadata(self):
        """Module-declared sensitivities should override heuristics."""

        class _EmptyConfig(BaseModel):
            pass

        class _CustomModule(Module):
            @property
            def name(self) -> str:
                return "custom"

            @property
            def config_schema(self) -> type[BaseModel]:
                return _EmptyConfig

            @property
            def dependencies(self) -> list[str]:
                return []

            async def register_tools(self, mcp: Any, config: Any, db: Any) -> None:
                pass

            def migration_revisions(self) -> str | None:
                return None

            async def on_startup(self, config: Any, db: Any) -> None:
                pass

            async def on_shutdown(self) -> None:
                pass

            def tool_metadata(self) -> dict[str, ToolMeta]:
                return {
                    "custom_tool": ToolMeta(arg_sensitivities={"subject": True, "body": False}),
                }

        mod = _CustomModule()
        result = suggest_constraints(
            "custom_tool",
            {"subject": "secret", "body": "open"},
            module=mod,
        )
        assert result["subject"]["type"] == "exact"
        assert result["subject"]["value"] == "secret"
        assert result["body"]["type"] == "any"

    def test_empty_args(self):
        """Empty tool_args should return empty constraints."""
        result = suggest_constraints("some_tool", {})
        assert result == {}


# ===========================================================================
# Tests: CRUD tools on ApprovalsModule
# ===========================================================================


class TestCreateApprovalRule:
    """Test create_approval_rule tool."""

    async def test_create_basic_rule(
        self, module: ApprovalsModule, mock_db: MockDB, human_actor: dict[str, Any]
    ):
        await module.on_startup(config=None, db=mock_db)

        result = await module._create_approval_rule(
            tool_name="email_send",
            arg_constraints={"to": {"type": "exact", "value": "alice@example.com"}},
            description="Allow emails to Alice",
            actor=human_actor,
        )

        assert "id" in result
        assert result["tool_name"] == "email_send"
        assert result["description"] == "Allow emails to Alice"
        assert result["active"] is True
        assert result["use_count"] == 0
        assert result["max_uses"] is None
        assert result["expires_at"] is None

    async def test_create_rule_with_expiry(
        self, module: ApprovalsModule, mock_db: MockDB, human_actor: dict[str, Any]
    ):
        await module.on_startup(config=None, db=mock_db)

        future = datetime.now(UTC) + timedelta(days=7)
        result = await module._create_approval_rule(
            tool_name="email_send",
            arg_constraints={},
            description="Temporary rule",
            expires_at=future.isoformat(),
            actor=human_actor,
        )

        assert result["expires_at"] is not None

    async def test_create_rule_with_max_uses(
        self, module: ApprovalsModule, mock_db: MockDB, human_actor: dict[str, Any]
    ):
        await module.on_startup(config=None, db=mock_db)

        result = await module._create_approval_rule(
            tool_name="email_send",
            arg_constraints={},
            description="Limited rule",
            max_uses=10,
            actor=human_actor,
        )

        assert result["max_uses"] == 10

    async def test_create_rule_invalid_expiry(
        self, module: ApprovalsModule, mock_db: MockDB, human_actor: dict[str, Any]
    ):
        await module.on_startup(config=None, db=mock_db)

        result = await module._create_approval_rule(
            tool_name="email_send",
            arg_constraints={},
            description="test",
            expires_at="not-a-date",
            actor=human_actor,
        )

        assert "error" in result

    async def test_create_rule_stored_in_db(
        self, module: ApprovalsModule, mock_db: MockDB, human_actor: dict[str, Any]
    ):
        await module.on_startup(config=None, db=mock_db)

        result = await module._create_approval_rule(
            tool_name="email_send",
            arg_constraints={"to": "alice"},
            description="test",
            actor=human_actor,
        )

        rule_id = uuid.UUID(result["id"])
        assert rule_id in mock_db.approval_rules

    async def test_create_rule_rejects_non_human_actor(
        self, module: ApprovalsModule, mock_db: MockDB
    ):
        await module.on_startup(config=None, db=mock_db)

        result = await module._create_approval_rule(
            tool_name="email_send",
            arg_constraints={},
            description="test",
            actor={"type": "llm", "id": "runtime", "authenticated": True},
        )
        assert result["error_code"] == "human_actor_required"

    async def test_high_risk_rule_requires_bounded_scope(
        self, module: ApprovalsModule, mock_db: MockDB, human_actor: dict[str, Any]
    ):
        await module.on_startup(config=None, db=mock_db)
        module.set_approval_policy(
            ApprovalConfig(
                enabled=True,
                gated_tools={"wire_transfer": GatedToolConfig(risk_tier=ApprovalRiskTier.HIGH)},
            )
        )

        result = await module._create_approval_rule(
            tool_name="wire_transfer",
            arg_constraints={"to_account": {"type": "exact", "value": "acct_123"}},
            description="unbounded high-risk rule",
            actor=human_actor,
        )

        assert "error" in result
        assert "bounded scope" in result["error"]

    async def test_high_risk_rule_requires_narrow_constraints(
        self, module: ApprovalsModule, mock_db: MockDB, human_actor: dict[str, Any]
    ):
        await module.on_startup(config=None, db=mock_db)
        module.set_approval_policy(
            ApprovalConfig(
                enabled=True,
                gated_tools={"wire_transfer": GatedToolConfig(risk_tier=ApprovalRiskTier.HIGH)},
            )
        )

        result = await module._create_approval_rule(
            tool_name="wire_transfer",
            arg_constraints={"note": {"type": "any"}},
            description="too broad",
            max_uses=1,
            actor=human_actor,
        )

        assert "error" in result
        assert "exact or pattern" in result["error"]

    async def test_create_rule_emits_rule_created_event(
        self, module: ApprovalsModule, mock_db: MockDB, human_actor: dict[str, Any]
    ):
        await module.on_startup(config=None, db=mock_db)

        result = await module._create_approval_rule(
            tool_name="email_send",
            arg_constraints={"to": "alice"},
            description="test",
            actor=human_actor,
        )

        rule_id = uuid.UUID(result["id"])
        event = next(e for e in mock_db.approval_events if e["rule_id"] == rule_id)
        assert event["event_type"] == "rule_created"
        assert event["actor"] == "user:manual"


class TestCreateRuleFromAction:
    """Test create_rule_from_action tool."""

    async def test_create_from_action_basic(
        self, module: ApprovalsModule, mock_db: MockDB, human_actor: dict[str, Any]
    ):
        await module.on_startup(config=None, db=mock_db)

        action_id = mock_db._insert_action(
            tool_name="email_send",
            tool_args={"to": "alice@example.com", "body": "hello"},
        )

        result = await module._create_rule_from_action(str(action_id), actor=human_actor)

        assert "id" in result
        assert result["tool_name"] == "email_send"
        assert result["created_from"] == str(action_id)
        assert result["active"] is True

        # Verify smart constraints: 'to' should be exact (sensitive), 'body' should be any
        constraints = result["arg_constraints"]
        assert constraints["to"]["type"] == "exact"
        assert constraints["to"]["value"] == "alice@example.com"
        assert constraints["body"]["type"] == "any"

    async def test_create_from_action_with_overrides(
        self, module: ApprovalsModule, mock_db: MockDB, human_actor: dict[str, Any]
    ):
        await module.on_startup(config=None, db=mock_db)

        action_id = mock_db._insert_action(
            tool_name="email_send",
            tool_args={"to": "alice@example.com", "body": "hello"},
        )

        result = await module._create_rule_from_action(
            str(action_id),
            constraint_overrides={
                "to": {"type": "pattern", "value": "*@example.com"},
            },
            actor=human_actor,
        )

        constraints = result["arg_constraints"]
        # Override should replace the suggested exact constraint
        assert constraints["to"]["type"] == "pattern"
        assert constraints["to"]["value"] == "*@example.com"
        # Non-overridden constraint should keep suggestion
        assert constraints["body"]["type"] == "any"

    async def test_create_from_action_not_found(
        self, module: ApprovalsModule, mock_db: MockDB, human_actor: dict[str, Any]
    ):
        await module.on_startup(config=None, db=mock_db)

        result = await module._create_rule_from_action(str(uuid.uuid4()), actor=human_actor)
        assert "error" in result
        assert "not found" in result["error"]

    async def test_create_from_action_invalid_uuid(
        self, module: ApprovalsModule, mock_db: MockDB, human_actor: dict[str, Any]
    ):
        await module.on_startup(config=None, db=mock_db)

        result = await module._create_rule_from_action("not-a-uuid", actor=human_actor)
        assert "error" in result
        assert "Invalid action_id" in result["error"]

    async def test_create_from_action_stored_in_db(
        self, module: ApprovalsModule, mock_db: MockDB, human_actor: dict[str, Any]
    ):
        await module.on_startup(config=None, db=mock_db)

        action_id = mock_db._insert_action(
            tool_name="email_send",
            tool_args={"to": "alice@example.com"},
        )

        result = await module._create_rule_from_action(str(action_id), actor=human_actor)

        rule_id = uuid.UUID(result["id"])
        assert rule_id in mock_db.approval_rules

    async def test_create_from_action_high_risk_defaults_to_bounded_rule(
        self, module: ApprovalsModule, mock_db: MockDB, human_actor: dict[str, Any]
    ):
        await module.on_startup(config=None, db=mock_db)
        module.set_approval_policy(
            ApprovalConfig(
                enabled=True,
                gated_tools={"wire_transfer": GatedToolConfig(risk_tier=ApprovalRiskTier.HIGH)},
            )
        )

        action_id = mock_db._insert_action(
            tool_name="wire_transfer",
            tool_args={"to_account": "acct_123", "amount": 10.0},
        )

        result = await module._create_rule_from_action(str(action_id), actor=human_actor)
        assert "error" not in result
        assert result["max_uses"] == 1

    async def test_create_from_action_high_risk_rejects_broad_overrides(
        self, module: ApprovalsModule, mock_db: MockDB, human_actor: dict[str, Any]
    ):
        await module.on_startup(config=None, db=mock_db)
        module.set_approval_policy(
            ApprovalConfig(
                enabled=True,
                gated_tools={"wire_transfer": GatedToolConfig(risk_tier=ApprovalRiskTier.HIGH)},
            )
        )

        action_id = mock_db._insert_action(
            tool_name="wire_transfer",
            tool_args={"to_account": "acct_123", "amount": 10.0},
        )

        result = await module._create_rule_from_action(
            str(action_id),
            constraint_overrides={
                "to_account": {"type": "any"},
                "amount": {"type": "any"},
            },
            actor=human_actor,
        )

        assert "error" in result
        assert "exact or pattern" in result["error"]


class TestListApprovalRules:
    """Test list_approval_rules tool."""

    async def test_empty_list(self, module: ApprovalsModule, mock_db: MockDB):
        await module.on_startup(config=None, db=mock_db)
        result = await module._list_approval_rules()
        assert result == []

    async def test_lists_active_rules(self, module: ApprovalsModule, mock_db: MockDB):
        await module.on_startup(config=None, db=mock_db)

        mock_db._insert_rule(tool_name="email_send", description="rule 1")
        mock_db._insert_rule(tool_name="telegram_send", description="rule 2")
        mock_db._insert_rule(tool_name="email_send", description="revoked", active=False)

        result = await module._list_approval_rules()
        assert len(result) == 2

    async def test_filter_by_tool_name(self, module: ApprovalsModule, mock_db: MockDB):
        await module.on_startup(config=None, db=mock_db)

        mock_db._insert_rule(tool_name="email_send", description="rule 1")
        mock_db._insert_rule(tool_name="telegram_send", description="rule 2")

        result = await module._list_approval_rules(tool_name="email_send")
        assert len(result) == 1
        assert result[0]["tool_name"] == "email_send"

    async def test_include_inactive(self, module: ApprovalsModule, mock_db: MockDB):
        await module.on_startup(config=None, db=mock_db)

        mock_db._insert_rule(tool_name="email_send", description="active")
        mock_db._insert_rule(tool_name="email_send", description="revoked", active=False)

        result = await module._list_approval_rules(active_only=False)
        assert len(result) == 2

    async def test_filter_tool_name_and_inactive(self, module: ApprovalsModule, mock_db: MockDB):
        await module.on_startup(config=None, db=mock_db)

        mock_db._insert_rule(tool_name="email_send", description="active")
        mock_db._insert_rule(tool_name="email_send", description="revoked", active=False)
        mock_db._insert_rule(tool_name="telegram_send", description="other", active=False)

        result = await module._list_approval_rules(tool_name="email_send", active_only=False)
        assert len(result) == 2
        for r in result:
            assert r["tool_name"] == "email_send"


class TestShowApprovalRule:
    """Test show_approval_rule tool."""

    async def test_show_existing_rule(self, module: ApprovalsModule, mock_db: MockDB):
        await module.on_startup(config=None, db=mock_db)

        rule_id = mock_db._insert_rule(
            tool_name="email_send",
            arg_constraints={"to": {"type": "exact", "value": "alice@example.com"}},
            description="Allow emails to Alice",
            max_uses=10,
            use_count=3,
        )

        result = await module._show_approval_rule(str(rule_id))
        assert result["id"] == str(rule_id)
        assert result["tool_name"] == "email_send"
        assert result["description"] == "Allow emails to Alice"
        assert result["max_uses"] == 10
        assert result["use_count"] == 3

    async def test_show_nonexistent_rule(self, module: ApprovalsModule, mock_db: MockDB):
        await module.on_startup(config=None, db=mock_db)
        result = await module._show_approval_rule(str(uuid.uuid4()))
        assert "error" in result
        assert "not found" in result["error"]

    async def test_show_invalid_uuid(self, module: ApprovalsModule, mock_db: MockDB):
        await module.on_startup(config=None, db=mock_db)
        result = await module._show_approval_rule("not-a-uuid")
        assert "error" in result
        assert "Invalid rule_id" in result["error"]


class TestRevokeApprovalRule:
    """Test revoke_approval_rule tool."""

    async def test_revoke_active_rule(
        self, module: ApprovalsModule, mock_db: MockDB, human_actor: dict[str, Any]
    ):
        await module.on_startup(config=None, db=mock_db)

        rule_id = mock_db._insert_rule(
            tool_name="email_send",
            description="Active rule",
            active=True,
        )

        result = await module._revoke_approval_rule(str(rule_id), actor=human_actor)
        assert result["active"] is False

    async def test_revoke_already_revoked(
        self, module: ApprovalsModule, mock_db: MockDB, human_actor: dict[str, Any]
    ):
        await module.on_startup(config=None, db=mock_db)

        rule_id = mock_db._insert_rule(
            tool_name="email_send",
            description="Revoked rule",
            active=False,
        )

        result = await module._revoke_approval_rule(str(rule_id), actor=human_actor)
        assert "error" in result
        assert "already revoked" in result["error"]

    async def test_revoke_nonexistent_rule(
        self, module: ApprovalsModule, mock_db: MockDB, human_actor: dict[str, Any]
    ):
        await module.on_startup(config=None, db=mock_db)
        result = await module._revoke_approval_rule(str(uuid.uuid4()), actor=human_actor)
        assert "error" in result
        assert "not found" in result["error"]

    async def test_revoke_invalid_uuid(
        self, module: ApprovalsModule, mock_db: MockDB, human_actor: dict[str, Any]
    ):
        await module.on_startup(config=None, db=mock_db)
        result = await module._revoke_approval_rule("bad-uuid", actor=human_actor)
        assert "error" in result
        assert "Invalid rule_id" in result["error"]

    async def test_revoke_rejects_missing_actor(self, module: ApprovalsModule, mock_db: MockDB):
        await module.on_startup(config=None, db=mock_db)
        rule_id = mock_db._insert_rule(tool_name="email_send", active=True)
        result = await module._revoke_approval_rule(str(rule_id))
        assert result["error_code"] == "human_actor_required"

    async def test_revoke_emits_rule_revoked_event(
        self, module: ApprovalsModule, mock_db: MockDB, human_actor: dict[str, Any]
    ):
        await module.on_startup(config=None, db=mock_db)

        rule_id = mock_db._insert_rule(
            tool_name="email_send",
            description="Active rule",
            active=True,
        )
        await module._revoke_approval_rule(str(rule_id), actor=human_actor)

        event = next(e for e in mock_db.approval_events if e["rule_id"] == rule_id)
        assert event["event_type"] == "rule_revoked"
        assert event["actor"] == "user:manual"


class TestSuggestRuleConstraints:
    """Test suggest_rule_constraints tool."""

    async def test_suggest_for_existing_action(self, module: ApprovalsModule, mock_db: MockDB):
        await module.on_startup(config=None, db=mock_db)

        action_id = mock_db._insert_action(
            tool_name="email_send",
            tool_args={"to": "alice@example.com", "body": "hello", "subject": "greetings"},
        )

        result = await module._suggest_rule_constraints(str(action_id))

        assert result["action_id"] == str(action_id)
        assert result["tool_name"] == "email_send"
        assert "suggested_constraints" in result

        constraints = result["suggested_constraints"]
        # 'to' is sensitive -> exact
        assert constraints["to"]["type"] == "exact"
        assert constraints["to"]["value"] == "alice@example.com"
        # 'body' is not sensitive -> any
        assert constraints["body"]["type"] == "any"
        # 'subject' is not sensitive -> any
        assert constraints["subject"]["type"] == "any"

    async def test_suggest_for_nonexistent_action(self, module: ApprovalsModule, mock_db: MockDB):
        await module.on_startup(config=None, db=mock_db)
        result = await module._suggest_rule_constraints(str(uuid.uuid4()))
        assert "error" in result
        assert "not found" in result["error"]

    async def test_suggest_for_invalid_uuid(self, module: ApprovalsModule, mock_db: MockDB):
        await module.on_startup(config=None, db=mock_db)
        result = await module._suggest_rule_constraints("not-a-uuid")
        assert "error" in result
        assert "Invalid action_id" in result["error"]

    async def test_suggest_includes_tool_args(self, module: ApprovalsModule, mock_db: MockDB):
        await module.on_startup(config=None, db=mock_db)

        action_id = mock_db._insert_action(
            tool_name="purchase_create",
            tool_args={"amount": 42.50, "description": "widget"},
        )

        result = await module._suggest_rule_constraints(str(action_id))

        assert result["tool_args"] == {"amount": 42.50, "description": "widget"}
        constraints = result["suggested_constraints"]
        assert constraints["amount"]["type"] == "exact"
        assert constraints["amount"]["value"] == 42.50
        assert constraints["description"]["type"] == "any"


# ===========================================================================
# Tests: Tool registration (updated count)
# ===========================================================================


class TestRegisterToolsCount:
    """Verify register_tools creates all 13 tools."""

    async def test_registers_thirteen_tools(self, module: ApprovalsModule, mock_db: MockDB):
        mcp = MagicMock()
        mcp.tool.return_value = lambda fn: fn

        await module.register_tools(mcp=mcp, config=None, db=mock_db)
        assert mcp.tool.call_count == 13

    async def test_all_tool_names(self, module: ApprovalsModule, mock_db: MockDB):
        mcp = MagicMock()
        registered_tools: dict[str, Any] = {}

        def capture_tool():
            def decorator(fn):
                registered_tools[fn.__name__] = fn
                return fn

            return decorator

        mcp.tool.side_effect = capture_tool

        await module.register_tools(mcp=mcp, config=None, db=mock_db)

        expected = {
            # Original 6 queue tools + audit tool
            "list_pending_actions",
            "show_pending_action",
            "approve_action",
            "reject_action",
            "pending_action_count",
            "expire_stale_actions",
            "list_executed_actions",
            # 6 rules CRUD tools
            "create_approval_rule",
            "create_rule_from_action",
            "list_approval_rules",
            "show_approval_rule",
            "revoke_approval_rule",
            "suggest_rule_constraints",
        }
        assert set(registered_tools.keys()) == expected

    async def test_all_new_tools_are_async(self, module: ApprovalsModule, mock_db: MockDB):
        import asyncio

        mcp = MagicMock()
        registered_tools: dict[str, Any] = {}

        def capture_tool():
            def decorator(fn):
                registered_tools[fn.__name__] = fn
                return fn

            return decorator

        mcp.tool.side_effect = capture_tool

        await module.register_tools(mcp=mcp, config=None, db=mock_db)

        new_tools = [
            "create_approval_rule",
            "create_rule_from_action",
            "list_approval_rules",
            "show_approval_rule",
            "revoke_approval_rule",
            "suggest_rule_constraints",
        ]
        for name in new_tools:
            assert asyncio.iscoroutinefunction(registered_tools[name]), f"{name} should be async"


# ===========================================================================
# Tests: End-to-end lifecycle
# ===========================================================================


class TestRulesLifecycle:
    """End-to-end lifecycle: create action -> suggest -> create rule -> list -> revoke."""

    async def test_full_lifecycle(
        self, module: ApprovalsModule, mock_db: MockDB, human_actor: dict[str, Any]
    ):
        await module.on_startup(config=None, db=mock_db)

        # Step 1: Create a pending action
        action_id = mock_db._insert_action(
            tool_name="email_send",
            tool_args={"to": "alice@example.com", "body": "weekly update"},
            status="pending",
        )

        # Step 2: Get suggested constraints
        suggestions = await module._suggest_rule_constraints(str(action_id))
        assert suggestions["suggested_constraints"]["to"]["type"] == "exact"
        assert suggestions["suggested_constraints"]["body"]["type"] == "any"

        # Step 3: Create a rule from the action
        rule_result = await module._create_rule_from_action(str(action_id), actor=human_actor)
        assert rule_result["active"] is True
        rule_id = rule_result["id"]

        # Step 4: List rules and verify it appears
        rules = await module._list_approval_rules(tool_name="email_send")
        assert len(rules) == 1
        assert rules[0]["id"] == rule_id

        # Step 5: Show rule details
        detail = await module._show_approval_rule(rule_id)
        assert detail["tool_name"] == "email_send"
        assert detail["created_from"] == str(action_id)

        # Step 6: Revoke the rule
        revoked = await module._revoke_approval_rule(rule_id, actor=human_actor)
        assert revoked["active"] is False

        # Step 7: List active only — should be empty
        active_rules = await module._list_approval_rules(tool_name="email_send", active_only=True)
        assert len(active_rules) == 0

        # Step 8: List all — revoked rule should appear
        all_rules = await module._list_approval_rules(tool_name="email_send", active_only=False)
        assert len(all_rules) == 1
        assert all_rules[0]["active"] is False

    async def test_create_rule_directly_then_show(
        self, module: ApprovalsModule, mock_db: MockDB, human_actor: dict[str, Any]
    ):
        """Create a rule directly (not from action) and verify it can be shown."""
        await module.on_startup(config=None, db=mock_db)

        result = await module._create_approval_rule(
            tool_name="telegram_send",
            arg_constraints={"chat_id": {"type": "exact", "value": 123}},
            description="Allow telegram to chat 123",
            max_uses=50,
            actor=human_actor,
        )

        rule_id = result["id"]
        detail = await module._show_approval_rule(rule_id)
        assert detail["tool_name"] == "telegram_send"
        assert detail["max_uses"] == 50
        assert detail["use_count"] == 0
        assert detail["active"] is True
