"""Tests for the approvals module integration hooks, MCP tools, events, and config.

Covers tasks 7–10:
- 7.1 post-approval tracker hook in _approve_action
- 7.2 post-execution demotion hook in executor
- 7.3 rule-creation supersede hook in _create_approval_rule / _create_rule_from_action
- 8.1–8.4 MCP tool registration (list/confirm/dismiss_promotion_suggestion; total=16)
- 9.1–9.2 ApprovalEventType enum has 7 new values
- 10.1–10.2 config parsing (promotion_threshold, velocity_window, suggestion_cooldown_days)
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.modules.approvals.events import ApprovalEventType
from butlers.modules.approvals.module import ApprovalsConfig, ApprovalsModule

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# 9.1 — New ApprovalEventType enum values
# ---------------------------------------------------------------------------


def test_approval_event_type_has_promotion_values():
    """ApprovalEventType has all 7 new promotion/demotion event types."""
    assert ApprovalEventType.PROMOTION_SUGGESTED == "promotion_suggested"
    assert ApprovalEventType.PROMOTION_CONFIRMED == "promotion_confirmed"
    assert ApprovalEventType.PROMOTION_DISMISSED == "promotion_dismissed"
    assert ApprovalEventType.PROMOTION_SUPERSEDED == "promotion_superseded"
    assert ApprovalEventType.DEMOTION_SUGGESTED == "demotion_suggested"
    assert ApprovalEventType.DEMOTION_CONFIRMED == "demotion_confirmed"
    assert ApprovalEventType.DEMOTION_DISMISSED == "demotion_dismissed"


def test_approval_event_type_has_all_original_values():
    """Original event types still exist."""
    assert ApprovalEventType.ACTION_APPROVED == "action_approved"
    assert ApprovalEventType.ACTION_REJECTED == "action_rejected"
    assert ApprovalEventType.RULE_CREATED == "rule_created"
    assert ApprovalEventType.RULE_REVOKED == "rule_revoked"


# ---------------------------------------------------------------------------
# 10.1–10.2 — Config parsing
# ---------------------------------------------------------------------------


def test_approvals_config_default_values():
    """ApprovalsConfig has correct default values for tracker config."""
    config = ApprovalsConfig()
    assert config.promotion_threshold == 5
    assert config.velocity_window == 10
    assert config.suggestion_cooldown_days == 30


def test_approvals_config_custom_values():
    """ApprovalsConfig accepts custom values for tracker config."""
    config = ApprovalsConfig(
        promotion_threshold=3,
        velocity_window=5,
        suggestion_cooldown_days=14,
    )
    assert config.promotion_threshold == 3
    assert config.velocity_window == 5
    assert config.suggestion_cooldown_days == 14


def test_approvals_config_extra_fields_ignored():
    """ApprovalsConfig ignores extra fields (for daemon compat)."""
    config = ApprovalsConfig.model_validate(
        {
            "default_limit": 100,
            "promotion_threshold": 7,
            "velocity_window": 15,
            "suggestion_cooldown_days": 60,
            "gated_tools": ["my_tool"],  # extra field, should be ignored
        }
    )
    assert config.promotion_threshold == 7
    assert config.velocity_window == 15
    assert config.suggestion_cooldown_days == 60


def test_approvals_config_defaults_when_absent():
    """ApprovalsConfig uses defaults when tracker keys are absent."""
    config = ApprovalsConfig.model_validate({})
    assert config.promotion_threshold == 5
    assert config.velocity_window == 10
    assert config.suggestion_cooldown_days == 30


# ---------------------------------------------------------------------------
# 8.4 — MCP tool count is 16
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register_tools_creates_16_tools():
    """register_tools registers exactly 16 MCP tools."""
    module = ApprovalsModule()
    mcp = MagicMock()
    mcp.tool = MagicMock(return_value=lambda fn: fn)

    # Minimal MockDB that won't be called during registration
    class _MockDB:
        pass

    await module.register_tools(mcp=mcp, config=None, db=_MockDB())

    assert mcp.tool.call_count == 16


@pytest.mark.asyncio
async def test_register_tools_includes_suggestion_tools():
    """The 3 new suggestion tools are among the registered tools."""
    module = ApprovalsModule()
    registered_names: list[str] = []

    def capture_tool():
        def decorator(fn: Any) -> Any:
            registered_names.append(fn.__name__)
            return fn

        return decorator

    mcp = MagicMock()
    mcp.tool = capture_tool

    class _MockDB:
        pass

    await module.register_tools(mcp=mcp, config=None, db=_MockDB())

    assert "list_promotion_suggestions" in registered_names
    assert "confirm_promotion_suggestion" in registered_names
    assert "dismiss_promotion_suggestion" in registered_names


# ---------------------------------------------------------------------------
# MockDB for integration hook tests
# ---------------------------------------------------------------------------


class MockDB:
    """Mock asyncpg pool that tracks calls for integration hook tests."""

    def __init__(self) -> None:
        self.pending_actions: dict[uuid.UUID, dict[str, Any]] = {}
        self.approval_rules: dict[uuid.UUID, dict[str, Any]] = {}
        self.approval_events: list[dict[str, Any]] = []
        self.history_rows: list[dict[str, Any]] = []
        self.suggestion_rows: list[dict[str, Any]] = []

    def _insert_action(self, **kwargs: Any) -> uuid.UUID:
        action_id = kwargs.get("id", uuid.uuid4())
        if isinstance(action_id, str):
            action_id = uuid.UUID(action_id)
        row = {
            "id": action_id,
            "tool_name": kwargs.get("tool_name", "test_tool"),
            "tool_args": json.dumps(kwargs.get("tool_args", {})),
            "status": kwargs.get("status", "pending"),
            "requested_at": kwargs.get("requested_at", datetime.now(UTC)),
            "agent_summary": None,
            "session_id": None,
            "expires_at": kwargs.get("expires_at"),
            "decided_by": kwargs.get("decided_by"),
            "decided_at": kwargs.get("decided_at"),
            "execution_result": None,
            "approval_rule_id": kwargs.get("approval_rule_id"),
        }
        self.pending_actions[action_id] = row
        return action_id

    async def execute(self, query: str, *args: Any) -> None:
        if "INSERT INTO autonomy_approval_history" in query:
            self.history_rows.append({"pattern_fingerprint": args[1], "tool_name": args[2]})
        elif "INSERT INTO autonomy_suggestions" in query:
            self.suggestion_rows.append(
                {
                    "id": args[0],
                    "suggestion_type": args[1],
                    "pattern_fingerprint": args[2],
                    "status": args[5],
                }
            )
        elif "INSERT INTO approval_events" in query:
            self.approval_events.append({"event_type": args[0]})
        elif "INSERT INTO approval_rules" in query:
            self.approval_rules[args[0]] = {
                "id": args[0],
                "tool_name": args[1],
                "active": True,
                "arg_constraints": args[2],
            }
        elif "UPDATE pending_actions" in query and "use_count" not in query:
            self._update_action(query, args)
        elif "UPDATE approval_rules" in query and "use_count" in query:
            rule_id = args[0]
            if rule_id in self.approval_rules:
                self.approval_rules[rule_id]["use_count"] = (
                    self.approval_rules[rule_id].get("use_count", 0) + 1
                )
        elif "UPDATE approval_rules" in query:
            rule_id = args[-1]
            if rule_id in self.approval_rules:
                self.approval_rules[rule_id]["active"] = False
        elif "UPDATE autonomy_suggestions" in query:
            pass

    def _update_action(self, query: str, args: Any) -> None:
        if len(args) >= 5:
            action_id = args[3]
            expected_status = args[4]
        else:
            action_id = args[-1]
            expected_status = None
        if isinstance(action_id, str):
            action_id = uuid.UUID(action_id)
        row = self.pending_actions.get(action_id)
        if row is None:
            return
        if expected_status and row["status"] != expected_status:
            return
        if "status = $1" in query and "decided_by = $2" in query:
            row["status"] = args[0]
            row["decided_by"] = args[1]
            row["decided_at"] = args[2]
        elif "status = $1" in query and "execution_result = $2" in query:
            row["status"] = args[0]
            row["execution_result"] = args[1]
            row["decided_at"] = args[2]

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        if "UPDATE pending_actions" in query:
            self._update_action(query, args)
            action_id = args[3] if len(args) >= 4 else args[-1]
            if isinstance(action_id, str):
                action_id = uuid.UUID(action_id)
            row = self.pending_actions.get(action_id)
            if row and "RETURNING" in query:
                return dict(row)
            return None

        if "pending_actions" in query and "SELECT" in query:
            action_id = args[0]
            if isinstance(action_id, str):
                action_id = uuid.UUID(action_id)
            row = self.pending_actions.get(action_id)
            return dict(row) if row else None

        if "SELECT COUNT" in query and "autonomy_approval_history" in query:
            fp = args[0]
            count = sum(1 for r in self.history_rows if r.get("pattern_fingerprint") == fp)
            return {"cnt": count}

        if "approval_rules" in query and "SELECT id FROM approval_rules" in query:
            return None  # No existing rules by default

        if "autonomy_suggestions" in query:
            return None  # No existing suggestions by default

        return None

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        if "approval_rules" in query and "arg_constraints" in query:
            return []
        if "autonomy_suggestions" in query:
            tool = args[0] if args else None
            results = [
                r
                for r in self.suggestion_rows
                if r.get("tool_name") == tool and r.get("status") == "pending"
            ]
            return [dict(r) for r in results]
        return []


# ---------------------------------------------------------------------------
# 7.1 — Post-approval tracker hook
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approve_action_calls_record_approval():
    """After manual approval, record_approval is called for the tracker."""
    module = ApprovalsModule()
    db = MockDB()
    action_id = db._insert_action(tool_name="send_telegram", tool_args={"chat_id": "mom"})
    module._db = db
    module._config = ApprovalsConfig()

    human_actor = {"type": "human", "id": "owner", "authenticated": True}

    record_approval_calls: list[Any] = []

    async def mock_record_approval(pool, action):
        record_approval_calls.append(action)

    with (
        patch(
            "butlers.modules.approvals.module._record_approval",
            side_effect=mock_record_approval,
        ),
        patch(
            "butlers.modules.approvals.module._check_promotion_threshold",
            new_callable=AsyncMock,
        ),
    ):
        await module._approve_action(str(action_id), actor=human_actor)

    assert len(record_approval_calls) == 1


@pytest.mark.asyncio
async def test_approve_action_tracker_failure_does_not_block():
    """If the tracker hook raises an exception, the approval still succeeds."""
    module = ApprovalsModule()
    db = MockDB()
    action_id = db._insert_action(tool_name="test_tool", tool_args={})
    module._db = db
    module._config = ApprovalsConfig()

    human_actor = {"type": "human", "id": "owner", "authenticated": True}

    async def mock_record_approval_fail(pool, action):
        raise RuntimeError("DB error")

    with patch(
        "butlers.modules.approvals.module._record_approval",
        side_effect=mock_record_approval_fail,
    ):
        result = await module._approve_action(str(action_id), actor=human_actor)

    # Approval succeeded despite tracker failure
    assert "error" not in result


# ---------------------------------------------------------------------------
# 7.3 — Rule-creation supersede hook
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_approval_rule_calls_supersede_hook():
    """Creating a rule calls supersede_matching_suggestions."""
    module = ApprovalsModule()
    db = MockDB()
    module._db = db
    module._config = ApprovalsConfig()

    human_actor = {"type": "human", "id": "owner", "authenticated": True}
    supersede_calls: list[Any] = []

    async def mock_supersede(pool, tool_name, arg_constraints):
        supersede_calls.append((tool_name, arg_constraints))

    with patch(
        "butlers.modules.approvals.module._supersede_matching_suggestions",
        side_effect=mock_supersede,
    ):
        await module._create_approval_rule(
            tool_name="send_telegram",
            arg_constraints={"chat_id": {"type": "exact", "value": "mom"}},
            description="Test rule",
            actor=human_actor,
        )

    assert len(supersede_calls) == 1
    assert supersede_calls[0][0] == "send_telegram"


# ---------------------------------------------------------------------------
# 7.2 — Post-execution demotion hook in executor
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_executor_demotion_hook_on_failure():
    """execute_approved_action creates a demotion suggestion on failure with a rule."""
    from butlers.modules.approvals.executor import execute_approved_action

    pool = MockDB()
    action_id = pool._insert_action(
        tool_name="send_email",
        tool_args={"to": "mom@example.com"},
        status="approved",
        approval_rule_id=uuid.uuid4(),
    )
    rule_id = uuid.uuid4()

    demotion_calls: list[Any] = []

    async def mock_create_demotion(pool, action, rule_id, error_details):
        demotion_calls.append((action, rule_id, error_details))

    async def failing_tool_fn(**kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("Email service unavailable")

    with patch(
        "butlers.modules.approvals.autonomy_suggestions.create_demotion_suggestion",
        side_effect=mock_create_demotion,
    ):
        result = await execute_approved_action(
            pool=pool,
            action_id=action_id,
            tool_name="send_email",
            tool_args={"to": "mom@example.com"},
            tool_fn=failing_tool_fn,
            approval_rule_id=rule_id,
        )

    assert result.success is False
    assert len(demotion_calls) == 1
    assert demotion_calls[0][2] == "Email service unavailable"


@pytest.mark.asyncio
async def test_executor_no_demotion_hook_on_success():
    """execute_approved_action does NOT create a demotion suggestion on success."""
    from butlers.modules.approvals.executor import execute_approved_action

    pool = MockDB()
    action_id = pool._insert_action(
        tool_name="test_tool",
        tool_args={},
        status="approved",
    )
    rule_id = uuid.uuid4()

    demotion_calls: list[Any] = []

    async def mock_create_demotion(pool, action, rule_id, error_details):
        demotion_calls.append(True)

    async def success_tool_fn(**kwargs: Any) -> dict[str, Any]:
        return {"ok": True}

    with patch(
        "butlers.modules.approvals.autonomy_suggestions.create_demotion_suggestion",
        side_effect=mock_create_demotion,
    ):
        result = await execute_approved_action(
            pool=pool,
            action_id=action_id,
            tool_name="test_tool",
            tool_args={},
            tool_fn=success_tool_fn,
            approval_rule_id=rule_id,
        )

    assert result.success is True
    assert len(demotion_calls) == 0


@pytest.mark.asyncio
async def test_executor_no_demotion_hook_without_rule():
    """No demotion suggestion if approval_rule_id is None (manual approval)."""
    from butlers.modules.approvals.executor import execute_approved_action

    pool = MockDB()
    action_id = pool._insert_action(
        tool_name="test_tool",
        tool_args={},
        status="approved",
    )

    demotion_calls: list[Any] = []

    async def mock_create_demotion(pool, action, rule_id, error_details):
        demotion_calls.append(True)

    async def failing_tool_fn(**kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("oops")

    with patch(
        "butlers.modules.approvals.autonomy_suggestions.create_demotion_suggestion",
        side_effect=mock_create_demotion,
    ):
        await execute_approved_action(
            pool=pool,
            action_id=action_id,
            tool_name="test_tool",
            tool_args={},
            tool_fn=failing_tool_fn,
            approval_rule_id=None,  # no rule
        )

    assert len(demotion_calls) == 0
