"""Tests for the Approvals module — MCP tools for approval queue management.

Unit tests verify:
- Module ABC compliance
- Tool registration
- Status transition validation
- Full lifecycle: create -> list -> approve -> execute
- Full lifecycle: create -> reject
- Full lifecycle: create -> expire
- Error handling for invalid IDs and transitions
"""

from __future__ import annotations

import asyncio
import inspect
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel, ValidationError

from butlers.modules.approvals.models import ActionStatus
from butlers.modules.approvals.module import (
    _VALID_TRANSITIONS,
    ApprovalsConfig,
    ApprovalsModule,
    InvalidTransitionError,
    validate_transition,
)
from butlers.modules.base import Module

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Mock DB helper — simulates asyncpg pool with in-memory storage
# ---------------------------------------------------------------------------


class MockDB:
    """In-memory mock of an asyncpg connection pool.

    Supports the subset of operations used by the approvals module:
    fetch, fetchrow, execute. Uses a simple dict-of-lists storage.
    """

    def __init__(self) -> None:
        self.pending_actions: dict[uuid.UUID, dict[str, Any]] = {}
        self.approval_rules: dict[uuid.UUID, dict[str, Any]] = {}

    def _insert_action(self, **kwargs: Any) -> None:
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

    def _update_pending_action(self, query: str, *args: Any) -> dict[str, Any] | None:
        """Apply UPDATE pending_actions statements used in module/executor tests."""
        if "AND status = $5" in query:
            action_id = args[3]
            expected_status = args[4]
        else:
            action_id = args[-1]
            expected_status = None

        if isinstance(action_id, str):
            action_id = uuid.UUID(action_id)

        row = self.pending_actions.get(action_id)
        if row is None:
            return None
        if expected_status is not None and row["status"] != expected_status:
            return None

        if "status = $1" in query and "decided_by = $2" in query:
            row["status"] = args[0]
            row["decided_by"] = args[1]
            row["decided_at"] = args[2]
        elif (
            "status = $1" in query
            and "execution_result = $2" in query
            and "decided_at = $3" in query
        ):
            row["status"] = args[0]
            row["execution_result"] = args[1]
            row["decided_at"] = args[2]
        elif "status = $1" in query and "execution_result = $2" in query:
            row["status"] = args[0]
            row["execution_result"] = args[1]

        return dict(row)

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        """Simulate asyncpg fetch()."""
        if "GROUP BY status" in query:
            # pending_action_count query
            counts: dict[str, int] = {}
            for row in self.pending_actions.values():
                s = row["status"]
                counts[s] = counts.get(s, 0) + 1
            return [{"status": s, "count": c} for s, c in counts.items()]

        if "pending_actions" in query and "expires_at" in query:
            # expire_stale_actions query
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

        if "pending_actions" in query:
            rows = list(self.pending_actions.values())
            if "WHERE status = $1" in query:
                status_filter = args[0]
                rows = [r for r in rows if r["status"] == status_filter]
                limit = args[1] if len(args) > 1 else 50
            else:
                limit = args[0] if args else 50
            # Sort by requested_at descending
            rows.sort(key=lambda r: r["requested_at"], reverse=True)
            return [dict(r) for r in rows[:limit]]

        return []

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        """Simulate asyncpg fetchrow()."""
        if "UPDATE pending_actions" in query:
            updated = self._update_pending_action(query, *args)
            if updated is None:
                return None
            if "RETURNING id" in query:
                return {"id": updated["id"]}
            return updated

        if "pending_actions" in query and args:
            action_id = args[0]
            if isinstance(action_id, str):
                action_id = uuid.UUID(action_id)
            row = self.pending_actions.get(action_id)
            return dict(row) if row else None
        return None

    async def execute(self, query: str, *args: Any) -> None:
        """Simulate asyncpg execute()."""
        if "UPDATE pending_actions" in query:
            self._update_pending_action(query, *args)

        elif "UPDATE approval_rules" in query and "use_count" in query:
            # Executor uses: use_count = use_count + 1 WHERE id = $1
            rule_id = args[0]
            if rule_id in self.approval_rules:
                current = self.approval_rules[rule_id].get("use_count", 0)
                self.approval_rules[rule_id]["use_count"] = current + 1

        elif "INSERT INTO approval_rules" in query:
            rule_id = args[0]
            self.approval_rules[rule_id] = {
                "id": args[0],
                "tool_name": args[1],
                "arg_constraints": args[2],
                "description": args[3],
                "created_from": args[4],
                "created_at": args[5],
                "active": args[6],
            }


@pytest.fixture
def mock_db() -> MockDB:
    """Provide a fresh MockDB for each test."""
    return MockDB()


@pytest.fixture
def module() -> ApprovalsModule:
    """Provide a fresh ApprovalsModule instance."""
    return ApprovalsModule()


@pytest.fixture
def human_actor() -> dict[str, Any]:
    """Authenticated human actor context for approval decision calls."""
    return {"type": "human", "id": "owner", "authenticated": True}


# ---------------------------------------------------------------------------
# Module ABC compliance
# ---------------------------------------------------------------------------


class TestModuleABC:
    """Verify ApprovalsModule satisfies the Module abstract base class."""

    def test_is_subclass_of_module(self):
        assert issubclass(ApprovalsModule, Module)

    def test_instantiates(self):
        mod = ApprovalsModule()
        assert isinstance(mod, Module)

    def test_name(self):
        mod = ApprovalsModule()
        assert mod.name == "approvals"

    def test_config_schema(self):
        mod = ApprovalsModule()
        assert mod.config_schema is ApprovalsConfig
        assert issubclass(mod.config_schema, BaseModel)

    def test_dependencies_empty(self):
        mod = ApprovalsModule()
        assert mod.dependencies == []

    def test_migration_revisions(self):
        mod = ApprovalsModule()
        assert mod.migration_revisions() == "approvals"


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


class TestApprovalsConfig:
    """Verify config schema defaults and custom values."""

    def test_defaults(self):
        cfg = ApprovalsConfig()
        assert cfg.default_limit == 50

    def test_custom_limit(self):
        cfg = ApprovalsConfig(default_limit=100)
        assert cfg.default_limit == 100


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


class TestLifecycle:
    """Verify startup and shutdown lifecycle hooks."""

    async def test_on_startup_stores_config(self, module: ApprovalsModule, mock_db: MockDB):
        await module.on_startup(config={"default_limit": 25}, db=mock_db)
        assert module._config.default_limit == 25
        assert module._db is mock_db

    async def test_on_startup_with_none_config(self, module: ApprovalsModule, mock_db: MockDB):
        await module.on_startup(config=None, db=mock_db)
        assert module._config.default_limit == 50

    async def test_on_shutdown_completes(self, module: ApprovalsModule, mock_db: MockDB):
        await module.on_startup(config=None, db=mock_db)
        await module.on_shutdown()


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


class TestRegisterTools:
    """Verify that register_tools creates the expected MCP tools."""

    async def test_registers_thirteen_tools(self, module: ApprovalsModule, mock_db: MockDB):
        mcp = MagicMock()
        mcp.tool.return_value = lambda fn: fn

        await module.register_tools(mcp=mcp, config=None, db=mock_db)

        assert mcp.tool.call_count == 13

    async def test_tool_names(self, module: ApprovalsModule, mock_db: MockDB):
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
            "list_pending_actions",
            "show_pending_action",
            "approve_action",
            "reject_action",
            "pending_action_count",
            "expire_stale_actions",
            "list_executed_actions",
            "create_approval_rule",
            "create_rule_from_action",
            "list_approval_rules",
            "show_approval_rule",
            "revoke_approval_rule",
            "suggest_rule_constraints",
        }
        assert set(registered_tools.keys()) == expected

    async def test_registered_tools_are_async(self, module: ApprovalsModule, mock_db: MockDB):
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

        for tool_name, tool_fn in registered_tools.items():
            assert asyncio.iscoroutinefunction(tool_fn), f"{tool_name} should be async"

    async def test_decision_tools_do_not_expose_actor_kwarg(
        self, module: ApprovalsModule, mock_db: MockDB
    ):
        mcp = MagicMock()
        registered_tools: dict[str, Any] = {}

        def capture_tool():
            def decorator(fn):
                registered_tools[fn.__name__] = fn
                return fn

            return decorator

        mcp.tool.side_effect = capture_tool
        await module.register_tools(mcp=mcp, config=None, db=mock_db)

        decision_tools = {
            "approve_action",
            "reject_action",
            "create_approval_rule",
            "create_rule_from_action",
            "revoke_approval_rule",
        }
        for tool_name in decision_tools:
            params = inspect.signature(registered_tools[tool_name]).parameters
            assert "_actor" not in params


# ---------------------------------------------------------------------------
# Status transition validation
# ---------------------------------------------------------------------------


class TestStatusTransitions:
    """Verify status transition validation logic."""

    def test_pending_to_approved(self):
        validate_transition(ActionStatus.PENDING, ActionStatus.APPROVED)

    def test_pending_to_rejected(self):
        validate_transition(ActionStatus.PENDING, ActionStatus.REJECTED)

    def test_pending_to_expired(self):
        validate_transition(ActionStatus.PENDING, ActionStatus.EXPIRED)

    def test_approved_to_executed(self):
        validate_transition(ActionStatus.APPROVED, ActionStatus.EXECUTED)

    def test_invalid_pending_to_executed(self):
        with pytest.raises(InvalidTransitionError):
            validate_transition(ActionStatus.PENDING, ActionStatus.EXECUTED)

    def test_invalid_rejected_to_approved(self):
        with pytest.raises(InvalidTransitionError):
            validate_transition(ActionStatus.REJECTED, ActionStatus.APPROVED)

    def test_invalid_expired_to_pending(self):
        with pytest.raises(InvalidTransitionError):
            validate_transition(ActionStatus.EXPIRED, ActionStatus.PENDING)

    def test_invalid_executed_to_pending(self):
        with pytest.raises(InvalidTransitionError):
            validate_transition(ActionStatus.EXECUTED, ActionStatus.PENDING)

    def test_no_backward_transitions(self):
        """No terminal state can transition back to pending."""
        for terminal in (ActionStatus.REJECTED, ActionStatus.EXPIRED, ActionStatus.EXECUTED):
            with pytest.raises(InvalidTransitionError):
                validate_transition(terminal, ActionStatus.PENDING)

    def test_all_statuses_covered_in_transitions(self):
        """Every ActionStatus has an entry in the transitions dict."""
        for status in ActionStatus:
            assert status in _VALID_TRANSITIONS


# ---------------------------------------------------------------------------
# list_pending_actions
# ---------------------------------------------------------------------------


class TestListPendingActions:
    """Test list_pending_actions tool."""

    async def test_empty_list(self, module: ApprovalsModule, mock_db: MockDB):
        await module.on_startup(config=None, db=mock_db)
        result = await module._list_pending_actions()
        assert result == []

    async def test_lists_actions(self, module: ApprovalsModule, mock_db: MockDB):
        await module.on_startup(config=None, db=mock_db)

        mock_db._insert_action(
            id=uuid.uuid4(),
            tool_name="email_send",
            tool_args={"to": "alice@example.com"},
            status="pending",
        )
        mock_db._insert_action(
            id=uuid.uuid4(),
            tool_name="telegram_send",
            tool_args={"chat_id": 123},
            status="pending",
        )

        result = await module._list_pending_actions()
        assert len(result) == 2

    async def test_filter_by_status(self, module: ApprovalsModule, mock_db: MockDB):
        await module.on_startup(config=None, db=mock_db)

        mock_db._insert_action(id=uuid.uuid4(), tool_name="tool_a", status="pending")
        mock_db._insert_action(id=uuid.uuid4(), tool_name="tool_b", status="rejected")

        result = await module._list_pending_actions(status="pending")
        assert len(result) == 1
        assert result[0]["tool_name"] == "tool_a"

    async def test_invalid_status_filter(self, module: ApprovalsModule, mock_db: MockDB):
        await module.on_startup(config=None, db=mock_db)
        result = await module._list_pending_actions(status="bogus")
        assert len(result) == 1
        assert "error" in result[0]

    async def test_respects_limit(self, module: ApprovalsModule, mock_db: MockDB):
        await module.on_startup(config=None, db=mock_db)

        for _ in range(5):
            mock_db._insert_action(id=uuid.uuid4(), tool_name="tool")

        result = await module._list_pending_actions(limit=3)
        assert len(result) == 3

    async def test_default_limit_from_config(self, module: ApprovalsModule, mock_db: MockDB):
        await module.on_startup(config={"default_limit": 2}, db=mock_db)

        for _ in range(5):
            mock_db._insert_action(id=uuid.uuid4(), tool_name="tool")

        result = await module._list_pending_actions()
        assert len(result) == 2


# ---------------------------------------------------------------------------
# show_pending_action
# ---------------------------------------------------------------------------


class TestShowPendingAction:
    """Test show_pending_action tool."""

    async def test_show_existing_action(self, module: ApprovalsModule, mock_db: MockDB):
        await module.on_startup(config=None, db=mock_db)

        action_id = uuid.uuid4()
        mock_db._insert_action(
            id=action_id,
            tool_name="email_send",
            tool_args={"to": "bob@example.com"},
            agent_summary="Send email to Bob",
        )

        result = await module._show_pending_action(str(action_id))
        assert result["id"] == str(action_id)
        assert result["tool_name"] == "email_send"
        assert result["agent_summary"] == "Send email to Bob"

    async def test_show_nonexistent_action(self, module: ApprovalsModule, mock_db: MockDB):
        await module.on_startup(config=None, db=mock_db)
        result = await module._show_pending_action(str(uuid.uuid4()))
        assert "error" in result
        assert "not found" in result["error"]

    async def test_show_invalid_uuid(self, module: ApprovalsModule, mock_db: MockDB):
        await module.on_startup(config=None, db=mock_db)
        result = await module._show_pending_action("not-a-uuid")
        assert "error" in result
        assert "Invalid action_id" in result["error"]


# ---------------------------------------------------------------------------
# approve_action
# ---------------------------------------------------------------------------


class TestApproveAction:
    """Test approve_action tool."""

    async def test_approve_tool_rejects_spoofed_actor_kwarg(
        self, module: ApprovalsModule, mock_db: MockDB
    ):
        from fastmcp import FastMCP

        await module.on_startup(config=None, db=mock_db)
        action_id = uuid.uuid4()
        mock_db._insert_action(id=action_id, tool_name="test_tool", status="pending")

        mcp = FastMCP("test-approvals")
        await module.register_tools(mcp=mcp, config=None, db=mock_db)
        tool = await mcp._tool_manager.get_tool("approve_action")

        with pytest.raises(ValidationError, match="_actor"):
            await tool.run(
                {
                    "action_id": str(action_id),
                    "_actor": {"type": "human", "id": "attacker", "authenticated": True},
                }
            )

    async def test_approve_pending_action(
        self, module: ApprovalsModule, mock_db: MockDB, human_actor: dict[str, Any]
    ):
        await module.on_startup(config=None, db=mock_db)

        action_id = uuid.uuid4()
        mock_db._insert_action(
            id=action_id,
            tool_name="email_send",
            tool_args={"to": "alice@example.com"},
            status="pending",
        )

        result = await module._approve_action(str(action_id), actor=human_actor)
        assert result["status"] == "executed"
        assert result["id"] == str(action_id)

    async def test_approve_executes_tool(
        self, module: ApprovalsModule, mock_db: MockDB, human_actor: dict[str, Any]
    ):
        await module.on_startup(config=None, db=mock_db)

        action_id = uuid.uuid4()
        mock_db._insert_action(
            id=action_id,
            tool_name="email_send",
            tool_args={"to": "alice@example.com"},
            status="pending",
        )

        executed_calls: list[tuple[str, dict]] = []

        async def mock_executor(tool_name: str, tool_args: dict[str, Any]) -> dict[str, Any]:
            executed_calls.append((tool_name, tool_args))
            return {"status": "sent"}

        module.set_tool_executor(mock_executor)
        result = await module._approve_action(str(action_id), actor=human_actor)

        assert len(executed_calls) == 1
        assert executed_calls[0][0] == "email_send"
        assert executed_calls[0][1] == {"to": "alice@example.com"}
        assert result["status"] == "executed"

    async def test_approve_stores_execution_result(
        self, module: ApprovalsModule, mock_db: MockDB, human_actor: dict[str, Any]
    ):
        await module.on_startup(config=None, db=mock_db)

        action_id = uuid.uuid4()
        mock_db._insert_action(id=action_id, tool_name="test_tool", status="pending")

        async def mock_executor(tool_name: str, tool_args: dict[str, Any]) -> dict[str, Any]:
            return {"ok": True, "data": "result"}

        module.set_tool_executor(mock_executor)
        result = await module._approve_action(str(action_id), actor=human_actor)

        assert result["status"] == "executed"
        # The execution_result should be stored in the DB row (ExecutionResult format)
        stored = mock_db.pending_actions[action_id]
        assert stored["execution_result"] is not None
        parsed_result = json.loads(stored["execution_result"])
        assert parsed_result["success"] is True
        assert parsed_result["result"]["ok"] is True

    async def test_approve_without_executor(
        self, module: ApprovalsModule, mock_db: MockDB, human_actor: dict[str, Any]
    ):
        """When no executor is set, action is still marked as executed."""
        await module.on_startup(config=None, db=mock_db)

        action_id = uuid.uuid4()
        mock_db._insert_action(id=action_id, tool_name="test_tool", status="pending")

        result = await module._approve_action(str(action_id), actor=human_actor)
        assert result["status"] == "executed"

    async def test_approve_with_create_rule(
        self, module: ApprovalsModule, mock_db: MockDB, human_actor: dict[str, Any]
    ):
        await module.on_startup(config=None, db=mock_db)

        action_id = uuid.uuid4()
        mock_db._insert_action(
            id=action_id,
            tool_name="email_send",
            tool_args={"to": "alice@example.com"},
            status="pending",
        )

        result = await module._approve_action(str(action_id), create_rule=True, actor=human_actor)
        assert "created_rule" in result
        rule = result["created_rule"]
        assert rule["tool_name"] == "email_send"
        assert rule["arg_constraints"] == {"to": "alice@example.com"}
        assert rule["created_from"] == str(action_id)
        assert rule["active"] is True

        # Verify rule was stored in DB
        assert len(mock_db.approval_rules) == 1

    async def test_approve_nonexistent_action(
        self, module: ApprovalsModule, mock_db: MockDB, human_actor: dict[str, Any]
    ):
        await module.on_startup(config=None, db=mock_db)
        result = await module._approve_action(str(uuid.uuid4()), actor=human_actor)
        assert "error" in result
        assert "not found" in result["error"]

    async def test_approve_already_rejected(
        self, module: ApprovalsModule, mock_db: MockDB, human_actor: dict[str, Any]
    ):
        await module.on_startup(config=None, db=mock_db)

        action_id = uuid.uuid4()
        mock_db._insert_action(id=action_id, tool_name="test_tool", status="rejected")

        result = await module._approve_action(str(action_id), actor=human_actor)
        assert "error" in result
        assert "Cannot transition" in result["error"]

    async def test_approve_already_executed(
        self, module: ApprovalsModule, mock_db: MockDB, human_actor: dict[str, Any]
    ):
        await module.on_startup(config=None, db=mock_db)

        action_id = uuid.uuid4()
        mock_db._insert_action(id=action_id, tool_name="test_tool", status="executed")

        result = await module._approve_action(str(action_id), actor=human_actor)
        assert "error" in result
        assert "Cannot transition" in result["error"]

    async def test_approve_already_expired(
        self, module: ApprovalsModule, mock_db: MockDB, human_actor: dict[str, Any]
    ):
        await module.on_startup(config=None, db=mock_db)

        action_id = uuid.uuid4()
        mock_db._insert_action(id=action_id, tool_name="test_tool", status="expired")

        result = await module._approve_action(str(action_id), actor=human_actor)
        assert "error" in result
        assert "Cannot transition" in result["error"]

    async def test_approve_invalid_uuid(
        self, module: ApprovalsModule, mock_db: MockDB, human_actor: dict[str, Any]
    ):
        await module.on_startup(config=None, db=mock_db)
        result = await module._approve_action("not-a-uuid", actor=human_actor)
        assert "error" in result
        assert "Invalid action_id" in result["error"]

    async def test_approve_handles_executor_error(
        self, module: ApprovalsModule, mock_db: MockDB, human_actor: dict[str, Any]
    ):
        """If executor raises, error is captured and action still moves to executed."""
        await module.on_startup(config=None, db=mock_db)

        action_id = uuid.uuid4()
        mock_db._insert_action(id=action_id, tool_name="test_tool", status="pending")

        async def failing_executor(tool_name: str, tool_args: dict[str, Any]) -> dict[str, Any]:
            raise RuntimeError("Tool crashed")

        module.set_tool_executor(failing_executor)
        result = await module._approve_action(str(action_id), actor=human_actor)

        # Action should still be marked as executed with error result
        assert result["status"] == "executed"
        stored = mock_db.pending_actions[action_id]
        parsed_result = json.loads(stored["execution_result"])
        assert parsed_result["success"] is False
        assert "Tool crashed" in parsed_result["error"]

    async def test_concurrent_decisions_converge_to_single_terminal_outcome(
        self,
        module: ApprovalsModule,
        mock_db: MockDB,
        human_actor: dict[str, Any],
    ):
        """Concurrent approve/reject attempts should settle on exactly one terminal state."""
        await module.on_startup(config=None, db=mock_db)

        action_id = uuid.uuid4()
        mock_db._insert_action(id=action_id, tool_name="test_tool", status="pending")

        executed_calls: list[str] = []

        async def slow_executor(tool_name: str, tool_args: dict[str, Any]) -> dict[str, Any]:
            executed_calls.append(tool_name)
            await asyncio.sleep(0.01)
            return {"ok": True}

        module.set_tool_executor(slow_executor)

        approve_result, reject_result = await asyncio.gather(
            module._approve_action(str(action_id), actor=human_actor),
            module._reject_action(str(action_id), reason="race", actor=human_actor),
        )

        final = await module._show_pending_action(str(action_id))
        assert final["status"] in {"executed", "rejected"}

        successful_results = [r for r in (approve_result, reject_result) if "status" in r]
        assert len(successful_results) == 1
        assert successful_results[0]["status"] == final["status"]

        errored_results = [r for r in (approve_result, reject_result) if "error" in r]
        assert len(errored_results) == 1
        assert "Cannot transition" in errored_results[0]["error"]

        if final["status"] == "executed":
            assert len(executed_calls) == 1
        else:
            assert len(executed_calls) == 0

    async def test_approve_rejects_missing_actor(self, module: ApprovalsModule, mock_db: MockDB):
        await module.on_startup(config=None, db=mock_db)
        action_id = uuid.uuid4()
        mock_db._insert_action(id=action_id, tool_name="test_tool", status="pending")

        result = await module._approve_action(str(action_id))
        assert result["error_code"] == "human_actor_required"

    async def test_approve_rejects_non_human_actor(self, module: ApprovalsModule, mock_db: MockDB):
        await module.on_startup(config=None, db=mock_db)
        action_id = uuid.uuid4()
        mock_db._insert_action(id=action_id, tool_name="test_tool", status="pending")

        result = await module._approve_action(
            str(action_id),
            actor={"type": "system", "id": "daemon", "authenticated": True},
        )
        assert result["error_code"] == "human_actor_required"


# ---------------------------------------------------------------------------
# reject_action
# ---------------------------------------------------------------------------


class TestRejectAction:
    """Test reject_action tool."""

    async def test_reject_pending_action(
        self, module: ApprovalsModule, mock_db: MockDB, human_actor: dict[str, Any]
    ):
        await module.on_startup(config=None, db=mock_db)

        action_id = uuid.uuid4()
        mock_db._insert_action(id=action_id, tool_name="email_send", status="pending")

        result = await module._reject_action(str(action_id), actor=human_actor)
        assert result["status"] == "rejected"
        assert result["decided_by"] == "human:owner"

    async def test_reject_with_reason(
        self, module: ApprovalsModule, mock_db: MockDB, human_actor: dict[str, Any]
    ):
        await module.on_startup(config=None, db=mock_db)

        action_id = uuid.uuid4()
        mock_db._insert_action(id=action_id, tool_name="email_send", status="pending")

        result = await module._reject_action(
            str(action_id), reason="Not appropriate", actor=human_actor
        )
        assert result["status"] == "rejected"
        assert "Not appropriate" in result["decided_by"]

    async def test_reject_with_reason_escapes_html(
        self, module: ApprovalsModule, mock_db: MockDB, human_actor: dict[str, Any]
    ):
        await module.on_startup(config=None, db=mock_db)

        action_id = uuid.uuid4()
        mock_db._insert_action(id=action_id, tool_name="email_send", status="pending")

        result = await module._reject_action(
            str(action_id),
            reason="<script>alert('xss')</script>",
            actor=human_actor,
        )
        assert result["status"] == "rejected"
        assert "<script>" not in result["decided_by"]
        assert "</script>" not in result["decided_by"]
        assert "&lt;script&gt;" in result["decided_by"]
        assert "&lt;/script&gt;" in result["decided_by"]

    async def test_reject_records_timestamp(
        self, module: ApprovalsModule, mock_db: MockDB, human_actor: dict[str, Any]
    ):
        await module.on_startup(config=None, db=mock_db)

        action_id = uuid.uuid4()
        mock_db._insert_action(id=action_id, tool_name="test_tool", status="pending")

        result = await module._reject_action(str(action_id), actor=human_actor)
        assert result["decided_at"] is not None

    async def test_reject_nonexistent_action(
        self, module: ApprovalsModule, mock_db: MockDB, human_actor: dict[str, Any]
    ):
        await module.on_startup(config=None, db=mock_db)
        result = await module._reject_action(str(uuid.uuid4()), actor=human_actor)
        assert "error" in result
        assert "not found" in result["error"]

    async def test_reject_already_approved(
        self, module: ApprovalsModule, mock_db: MockDB, human_actor: dict[str, Any]
    ):
        await module.on_startup(config=None, db=mock_db)

        action_id = uuid.uuid4()
        mock_db._insert_action(id=action_id, tool_name="test_tool", status="approved")

        result = await module._reject_action(str(action_id), actor=human_actor)
        assert "error" in result
        assert "Cannot transition" in result["error"]

    async def test_reject_already_rejected(
        self, module: ApprovalsModule, mock_db: MockDB, human_actor: dict[str, Any]
    ):
        await module.on_startup(config=None, db=mock_db)

        action_id = uuid.uuid4()
        mock_db._insert_action(id=action_id, tool_name="test_tool", status="rejected")

        result = await module._reject_action(str(action_id), actor=human_actor)
        assert "error" in result
        assert "Cannot transition" in result["error"]

    async def test_reject_invalid_uuid(
        self, module: ApprovalsModule, mock_db: MockDB, human_actor: dict[str, Any]
    ):
        await module.on_startup(config=None, db=mock_db)
        result = await module._reject_action("bad-uuid", actor=human_actor)
        assert "error" in result
        assert "Invalid action_id" in result["error"]

    async def test_reject_rejects_missing_actor(self, module: ApprovalsModule, mock_db: MockDB):
        await module.on_startup(config=None, db=mock_db)
        action_id = uuid.uuid4()
        mock_db._insert_action(id=action_id, tool_name="test_tool", status="pending")

        result = await module._reject_action(str(action_id))
        assert result["error_code"] == "human_actor_required"


# ---------------------------------------------------------------------------
# pending_action_count
# ---------------------------------------------------------------------------


class TestPendingActionCount:
    """Test pending_action_count tool."""

    async def test_count_empty(self, module: ApprovalsModule, mock_db: MockDB):
        await module.on_startup(config=None, db=mock_db)
        result = await module._pending_action_count()
        assert result["total"] == 0
        assert result["by_status"] == {}

    async def test_count_by_status(self, module: ApprovalsModule, mock_db: MockDB):
        await module.on_startup(config=None, db=mock_db)

        mock_db._insert_action(id=uuid.uuid4(), tool_name="t1", status="pending")
        mock_db._insert_action(id=uuid.uuid4(), tool_name="t2", status="pending")
        mock_db._insert_action(id=uuid.uuid4(), tool_name="t3", status="rejected")

        result = await module._pending_action_count()
        assert result["total"] == 3
        assert result["by_status"]["pending"] == 2
        assert result["by_status"]["rejected"] == 1


# ---------------------------------------------------------------------------
# expire_stale_actions
# ---------------------------------------------------------------------------


class TestExpireStaleActions:
    """Test expire_stale_actions tool."""

    async def test_expire_past_due(self, module: ApprovalsModule, mock_db: MockDB):
        await module.on_startup(config=None, db=mock_db)

        action_id = uuid.uuid4()
        mock_db._insert_action(
            id=action_id,
            tool_name="test_tool",
            status="pending",
            expires_at=datetime.now(UTC) - timedelta(hours=1),
        )

        result = await module._expire_stale_actions()
        assert result["expired_count"] == 1
        assert str(action_id) in result["expired_ids"]

        # Verify the DB was updated
        stored = mock_db.pending_actions[action_id]
        assert stored["status"] == "expired"
        assert stored["decided_by"] == "system:expiry"

    async def test_does_not_expire_future(self, module: ApprovalsModule, mock_db: MockDB):
        await module.on_startup(config=None, db=mock_db)

        action_id = uuid.uuid4()
        mock_db._insert_action(
            id=action_id,
            tool_name="test_tool",
            status="pending",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )

        result = await module._expire_stale_actions()
        assert result["expired_count"] == 0

        # Status should remain pending
        stored = mock_db.pending_actions[action_id]
        assert stored["status"] == "pending"

    async def test_does_not_expire_no_expiry(self, module: ApprovalsModule, mock_db: MockDB):
        await module.on_startup(config=None, db=mock_db)

        mock_db._insert_action(
            id=uuid.uuid4(),
            tool_name="test_tool",
            status="pending",
            expires_at=None,
        )

        result = await module._expire_stale_actions()
        assert result["expired_count"] == 0

    async def test_does_not_expire_non_pending(self, module: ApprovalsModule, mock_db: MockDB):
        await module.on_startup(config=None, db=mock_db)

        mock_db._insert_action(
            id=uuid.uuid4(),
            tool_name="test_tool",
            status="rejected",
            expires_at=datetime.now(UTC) - timedelta(hours=1),
        )

        result = await module._expire_stale_actions()
        assert result["expired_count"] == 0

    async def test_expire_multiple(self, module: ApprovalsModule, mock_db: MockDB):
        await module.on_startup(config=None, db=mock_db)

        past = datetime.now(UTC) - timedelta(hours=1)
        ids = []
        for _ in range(3):
            aid = uuid.uuid4()
            ids.append(aid)
            mock_db._insert_action(id=aid, tool_name="tool", status="pending", expires_at=past)

        # One action that should NOT expire (future)
        mock_db._insert_action(
            id=uuid.uuid4(),
            tool_name="tool",
            status="pending",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )

        result = await module._expire_stale_actions()
        assert result["expired_count"] == 3
        for aid in ids:
            assert str(aid) in result["expired_ids"]


# ---------------------------------------------------------------------------
# Full lifecycle tests
# ---------------------------------------------------------------------------


class TestFullLifecycle:
    """End-to-end lifecycle tests covering create -> list -> approve/reject/expire."""

    async def test_create_list_approve_execute(
        self, module: ApprovalsModule, mock_db: MockDB, human_actor: dict[str, Any]
    ):
        """Full lifecycle: create -> list -> approve -> execute."""
        await module.on_startup(config=None, db=mock_db)

        # Step 1: Create (seed) a pending action
        action_id = uuid.uuid4()
        mock_db._insert_action(
            id=action_id,
            tool_name="email_send",
            tool_args={"to": "alice@example.com", "body": "hello"},
            agent_summary="Send greeting email to Alice",
            status="pending",
        )

        # Step 2: List and verify it appears
        actions = await module._list_pending_actions()
        assert len(actions) == 1
        assert actions[0]["id"] == str(action_id)
        assert actions[0]["status"] == "pending"

        # Step 3: Show detail
        detail = await module._show_pending_action(str(action_id))
        assert detail["tool_name"] == "email_send"
        assert detail["agent_summary"] == "Send greeting email to Alice"

        # Step 4: Check count
        count = await module._pending_action_count()
        assert count["total"] == 1
        assert count["by_status"]["pending"] == 1

        # Step 5: Approve and execute
        executed_calls: list[tuple] = []

        async def mock_executor(tool_name: str, tool_args: dict[str, Any]) -> dict[str, Any]:
            executed_calls.append((tool_name, tool_args))
            return {"status": "sent", "message_id": "msg-123"}

        module.set_tool_executor(mock_executor)
        result = await module._approve_action(str(action_id), actor=human_actor)

        assert result["status"] == "executed"
        assert len(executed_calls) == 1
        assert executed_calls[0][0] == "email_send"

        # Step 6: Verify final state
        final = await module._show_pending_action(str(action_id))
        assert final["status"] == "executed"
        assert final["decided_by"] is not None

    async def test_create_reject(
        self, module: ApprovalsModule, mock_db: MockDB, human_actor: dict[str, Any]
    ):
        """Full lifecycle: create -> reject."""
        await module.on_startup(config=None, db=mock_db)

        action_id = uuid.uuid4()
        mock_db._insert_action(
            id=action_id,
            tool_name="telegram_send",
            tool_args={"chat_id": 999, "text": "spam"},
            status="pending",
        )

        # Reject with reason
        result = await module._reject_action(
            str(action_id), reason="This looks like spam", actor=human_actor
        )
        assert result["status"] == "rejected"
        assert "spam" in result["decided_by"]

        # Cannot approve after rejection
        approve_result = await module._approve_action(str(action_id), actor=human_actor)
        assert "error" in approve_result

    async def test_create_expire(
        self, module: ApprovalsModule, mock_db: MockDB, human_actor: dict[str, Any]
    ):
        """Full lifecycle: create -> expire."""
        await module.on_startup(config=None, db=mock_db)

        action_id = uuid.uuid4()
        mock_db._insert_action(
            id=action_id,
            tool_name="calendar_create",
            tool_args={"title": "Meeting"},
            status="pending",
            expires_at=datetime.now(UTC) - timedelta(minutes=30),
        )

        # Expire stale actions
        result = await module._expire_stale_actions()
        assert result["expired_count"] == 1
        assert str(action_id) in result["expired_ids"]

        # Cannot approve after expiry
        approve_result = await module._approve_action(str(action_id), actor=human_actor)
        assert "error" in approve_result
        assert "Cannot transition" in approve_result["error"]

        # Cannot reject after expiry
        reject_result = await module._reject_action(str(action_id), actor=human_actor)
        assert "error" in reject_result
        assert "Cannot transition" in reject_result["error"]


# ---------------------------------------------------------------------------
# set_tool_executor
# ---------------------------------------------------------------------------


class TestSetToolExecutor:
    """Test the tool executor callback mechanism."""

    def test_default_executor_is_none(self, module: ApprovalsModule):
        assert module._tool_executor is None

    def test_set_executor(self, module: ApprovalsModule):
        async def my_executor(tool_name: str, tool_args: dict[str, Any]) -> dict[str, Any]:
            return {}

        module.set_tool_executor(my_executor)
        assert module._tool_executor is my_executor
