"""Tests for the post-approval tool executor.

Unit tests verify:
- Successful execution persists result with success=true
- Failed execution persists result with success=false and error message
- PendingAction DB row is updated to status='executed'
- Auto-approve execution increments rule use_count
- Execution without rule does not attempt rule increment
- list_executed_actions returns correct results with filtering
- list_executed_actions handles empty result sets
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from butlers.modules.approvals.executor import (
    ExecutionResult,
    execute_approved_action,
    list_executed_actions,
)
from butlers.modules.approvals.models import ActionStatus

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Mock DB helper — simulates asyncpg pool for executor tests
# ---------------------------------------------------------------------------


class MockPool:
    """In-memory mock of an asyncpg connection pool for executor tests.

    Tracks execute() calls and supports fetch/fetchrow for list queries.
    """

    def __init__(self) -> None:
        self.pending_actions: dict[uuid.UUID, dict[str, Any]] = {}
        self.approval_rules: dict[uuid.UUID, dict[str, Any]] = {}
        self.approval_events: list[dict[str, Any]] = []
        self.execute_calls: list[tuple[str, tuple]] = []

    def seed_action(
        self,
        action_id: uuid.UUID,
        tool_name: str = "test_tool",
        tool_args: dict[str, Any] | None = None,
        status: str = "approved",
        approval_rule_id: uuid.UUID | None = None,
        decided_at: datetime | None = None,
    ) -> None:
        """Seed a pending action for testing."""
        self.pending_actions[action_id] = {
            "id": action_id,
            "tool_name": tool_name,
            "tool_args": json.dumps(tool_args or {}),
            "status": status,
            "requested_at": datetime.now(UTC),
            "agent_summary": f"Tool '{tool_name}' call",
            "session_id": None,
            "expires_at": None,
            "decided_by": None,
            "decided_at": decided_at,
            "execution_result": None,
            "approval_rule_id": approval_rule_id,
        }

    def seed_rule(
        self,
        rule_id: uuid.UUID,
        tool_name: str = "test_tool",
        use_count: int = 0,
    ) -> None:
        """Seed an approval rule for testing."""
        self.approval_rules[rule_id] = {
            "id": rule_id,
            "tool_name": tool_name,
            "arg_constraints": json.dumps({}),
            "description": f"Rule for {tool_name}",
            "created_from": None,
            "created_at": datetime.now(UTC),
            "expires_at": None,
            "max_uses": None,
            "use_count": use_count,
            "active": True,
        }

    async def execute(self, query: str, *args: Any) -> None:
        """Simulate asyncpg execute()."""
        self.execute_calls.append((query, args))

        if "UPDATE pending_actions" in query and "status" in query:
            # Executor updates use CAS on approved status.
            if "AND status = $5" in query:
                action_id = args[3]
                expected_status = args[4]
            else:
                action_id = args[-1]
                expected_status = None

            if action_id in self.pending_actions:
                row = self.pending_actions[action_id]
                if expected_status is not None and row["status"] != expected_status:
                    return
                row["status"] = args[0]
                if "execution_result" in query:
                    row["execution_result"] = args[1]
                if "decided_at" in query:
                    # decided_at is the arg before the action_id
                    row["decided_at"] = args[-2]

        elif "UPDATE approval_rules" in query and "use_count" in query:
            # use_count = use_count + 1 — increment the rule
            rule_id = args[0]
            if rule_id in self.approval_rules:
                self.approval_rules[rule_id]["use_count"] += 1
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

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        """Simulate asyncpg fetch() for list_executed_actions queries."""
        if "pending_actions" not in query:
            return []

        # Start with all actions
        rows = list(self.pending_actions.values())

        # Apply filters based on the query params
        param_idx = 0
        if "status = $" in query:
            status_val = args[param_idx]
            rows = [r for r in rows if r["status"] == status_val]
            param_idx += 1

        if "tool_name = $" in query:
            tool_val = args[param_idx]
            rows = [r for r in rows if r["tool_name"] == tool_val]
            param_idx += 1

        if "approval_rule_id = $" in query:
            rule_val = args[param_idx]
            rows = [r for r in rows if r.get("approval_rule_id") == rule_val]
            param_idx += 1

        if "decided_at >= $" in query:
            since_val = args[param_idx]
            rows = [
                r for r in rows if r.get("decided_at") is not None and r["decided_at"] >= since_val
            ]
            param_idx += 1

        # The last param is always the limit
        limit = args[-1] if args else 50
        rows.sort(
            key=lambda r: r.get("decided_at") or datetime.min.replace(tzinfo=UTC),
            reverse=True,
        )
        return [dict(r) for r in rows[:limit]]

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        """Simulate asyncpg fetchrow()."""
        if "pending_actions" in query and args:
            action_id = args[0]
            row = self.pending_actions.get(action_id)
            return dict(row) if row else None
        return None


@pytest.fixture
def pool() -> MockPool:
    """Provide a fresh MockPool for each test."""
    return MockPool()


# ---------------------------------------------------------------------------
# ExecutionResult dataclass
# ---------------------------------------------------------------------------


class TestExecutionResult:
    """Verify ExecutionResult serialisation."""

    def test_success_to_dict(self):
        now = datetime.now(UTC)
        er = ExecutionResult(success=True, result={"data": 42}, executed_at=now)
        d = er.to_dict()
        assert d["success"] is True
        assert d["result"] == {"data": 42}
        assert "error" not in d
        assert d["executed_at"] == now.isoformat()

    def test_failure_to_dict(self):
        er = ExecutionResult(success=False, error="boom")
        d = er.to_dict()
        assert d["success"] is False
        assert d["error"] == "boom"
        assert "result" not in d

    def test_default_executed_at(self):
        er = ExecutionResult(success=True)
        assert er.executed_at is not None


# ---------------------------------------------------------------------------
# execute_approved_action — success
# ---------------------------------------------------------------------------


class TestExecuteSuccess:
    """Test successful tool execution."""

    async def test_returns_success_result(self, pool: MockPool):
        action_id = uuid.uuid4()
        pool.seed_action(action_id, tool_name="email_send", tool_args={"to": "a@b.com"})

        async def tool_fn(**kwargs: Any) -> dict:
            return {"status": "sent", "to": kwargs["to"]}

        result = await execute_approved_action(
            pool=pool,
            action_id=action_id,
            tool_name="email_send",
            tool_args={"to": "a@b.com"},
            tool_fn=tool_fn,
        )

        assert result.success is True
        assert result.result == {"status": "sent", "to": "a@b.com"}
        assert result.error is None
        assert result.executed_at is not None

    async def test_updates_pending_action_to_executed(self, pool: MockPool):
        action_id = uuid.uuid4()
        pool.seed_action(action_id)

        async def tool_fn(**kwargs: Any) -> dict:
            return {"ok": True}

        await execute_approved_action(
            pool=pool,
            action_id=action_id,
            tool_name="test_tool",
            tool_args={},
            tool_fn=tool_fn,
        )

        stored = pool.pending_actions[action_id]
        assert stored["status"] == ActionStatus.EXECUTED.value
        assert stored["execution_result"] is not None
        parsed = json.loads(stored["execution_result"])
        assert parsed["success"] is True

    async def test_decided_at_is_set(self, pool: MockPool):
        action_id = uuid.uuid4()
        pool.seed_action(action_id)

        async def tool_fn(**kwargs: Any) -> dict:
            return {}

        await execute_approved_action(
            pool=pool,
            action_id=action_id,
            tool_name="test_tool",
            tool_args={},
            tool_fn=tool_fn,
        )

        stored = pool.pending_actions[action_id]
        assert stored["decided_at"] is not None

    async def test_sync_tool_fn_supported(self, pool: MockPool):
        """Synchronous tool functions should also work."""
        action_id = uuid.uuid4()
        pool.seed_action(action_id)

        def sync_tool(**kwargs: Any) -> dict:
            return {"sync": True}

        result = await execute_approved_action(
            pool=pool,
            action_id=action_id,
            tool_name="test_tool",
            tool_args={},
            tool_fn=sync_tool,
        )

        assert result.success is True
        assert result.result == {"sync": True}

    async def test_success_emits_execution_succeeded_event(self, pool: MockPool):
        action_id = uuid.uuid4()
        rule_id = uuid.uuid4()
        pool.seed_action(action_id, approval_rule_id=rule_id)

        async def tool_fn(**kwargs: Any) -> dict:
            return {"ok": True}

        await execute_approved_action(
            pool=pool,
            action_id=action_id,
            tool_name="test_tool",
            tool_args={},
            tool_fn=tool_fn,
            approval_rule_id=rule_id,
        )

        event = next(e for e in pool.approval_events if e["action_id"] == action_id)
        assert event["event_type"] == "action_execution_succeeded"
        assert event["rule_id"] == rule_id
        assert event["actor"] == "system:executor"

    async def test_retry_returns_stored_result_without_reinvoking_tool(self, pool: MockPool):
        action_id = uuid.uuid4()
        stored_result = {
            "success": True,
            "result": {"ok": True, "from": "first-run"},
            "executed_at": datetime.now(UTC).isoformat(),
        }
        pool.seed_action(
            action_id,
            status="executed",
            decided_at=datetime.now(UTC),
        )
        pool.pending_actions[action_id]["execution_result"] = json.dumps(stored_result)

        calls = 0

        async def tool_fn(**kwargs: Any) -> dict[str, Any]:
            nonlocal calls
            calls += 1
            return {"unexpected": True}

        result = await execute_approved_action(
            pool=pool,
            action_id=action_id,
            tool_name="test_tool",
            tool_args={},
            tool_fn=tool_fn,
        )

        assert calls == 0
        assert result.success is True
        assert result.result == {"ok": True, "from": "first-run"}

    async def test_concurrent_invocation_executes_tool_once(self, pool: MockPool):
        action_id = uuid.uuid4()
        pool.seed_action(action_id)

        calls = 0

        async def slow_tool(**kwargs: Any) -> dict[str, Any]:
            nonlocal calls
            calls += 1
            await asyncio.sleep(0.01)
            return {"ok": True}

        first, second = await asyncio.gather(
            execute_approved_action(
                pool=pool,
                action_id=action_id,
                tool_name="test_tool",
                tool_args={},
                tool_fn=slow_tool,
            ),
            execute_approved_action(
                pool=pool,
                action_id=action_id,
                tool_name="test_tool",
                tool_args={},
                tool_fn=slow_tool,
            ),
        )

        assert calls == 1
        assert first.success is True
        assert second.success is True
        assert pool.pending_actions[action_id]["status"] == ActionStatus.EXECUTED.value


# ---------------------------------------------------------------------------
# execute_approved_action — failure
# ---------------------------------------------------------------------------


class TestExecuteFailure:
    """Test tool execution that raises an exception."""

    async def test_returns_failure_result(self, pool: MockPool):
        action_id = uuid.uuid4()
        pool.seed_action(action_id)

        async def failing_tool(**kwargs: Any) -> dict:
            raise RuntimeError("Network timeout")

        result = await execute_approved_action(
            pool=pool,
            action_id=action_id,
            tool_name="test_tool",
            tool_args={},
            tool_fn=failing_tool,
        )

        assert result.success is False
        assert result.error == "Network timeout"
        assert result.result is None

    async def test_failure_still_marks_executed(self, pool: MockPool):
        action_id = uuid.uuid4()
        pool.seed_action(action_id)

        async def failing_tool(**kwargs: Any) -> dict:
            raise ValueError("bad input")

        await execute_approved_action(
            pool=pool,
            action_id=action_id,
            tool_name="test_tool",
            tool_args={},
            tool_fn=failing_tool,
        )

        stored = pool.pending_actions[action_id]
        assert stored["status"] == ActionStatus.EXECUTED.value
        parsed = json.loads(stored["execution_result"])
        assert parsed["success"] is False
        assert "bad input" in parsed["error"]

    async def test_failure_persists_error_message(self, pool: MockPool):
        action_id = uuid.uuid4()
        pool.seed_action(action_id)

        async def failing_tool(**kwargs: Any) -> dict:
            raise ConnectionError("SMTP connection refused")

        result = await execute_approved_action(
            pool=pool,
            action_id=action_id,
            tool_name="email_send",
            tool_args={"to": "a@b.com"},
            tool_fn=failing_tool,
        )

        assert "SMTP connection refused" in result.error

    async def test_failure_emits_execution_failed_event(self, pool: MockPool):
        action_id = uuid.uuid4()
        pool.seed_action(action_id)

        async def failing_tool(**kwargs: Any) -> dict:
            raise RuntimeError("tool exploded")

        await execute_approved_action(
            pool=pool,
            action_id=action_id,
            tool_name="test_tool",
            tool_args={},
            tool_fn=failing_tool,
        )

        event = next(e for e in pool.approval_events if e["action_id"] == action_id)
        assert event["event_type"] == "action_execution_failed"
        assert event["actor"] == "system:executor"
        assert event["reason"] == "tool exploded"


# ---------------------------------------------------------------------------
# execute_approved_action — rule use_count
# ---------------------------------------------------------------------------


class TestExecuteRuleIncrement:
    """Test that auto-approved executions increment rule use_count."""

    async def test_increments_rule_use_count(self, pool: MockPool):
        action_id = uuid.uuid4()
        rule_id = uuid.uuid4()
        pool.seed_action(action_id, approval_rule_id=rule_id)
        pool.seed_rule(rule_id, use_count=3)

        async def tool_fn(**kwargs: Any) -> dict:
            return {"ok": True}

        await execute_approved_action(
            pool=pool,
            action_id=action_id,
            tool_name="test_tool",
            tool_args={},
            tool_fn=tool_fn,
            approval_rule_id=rule_id,
        )

        assert pool.approval_rules[rule_id]["use_count"] == 4

    async def test_no_rule_no_increment(self, pool: MockPool):
        """When approval_rule_id is None, no rule increment should occur."""
        action_id = uuid.uuid4()
        rule_id = uuid.uuid4()
        pool.seed_action(action_id)
        pool.seed_rule(rule_id, use_count=5)

        async def tool_fn(**kwargs: Any) -> dict:
            return {"ok": True}

        await execute_approved_action(
            pool=pool,
            action_id=action_id,
            tool_name="test_tool",
            tool_args={},
            tool_fn=tool_fn,
            approval_rule_id=None,
        )

        # Rule use_count should be unchanged
        assert pool.approval_rules[rule_id]["use_count"] == 5

    async def test_increments_even_on_failure(self, pool: MockPool):
        """Rule use_count should be incremented even if execution fails."""
        action_id = uuid.uuid4()
        rule_id = uuid.uuid4()
        pool.seed_action(action_id, approval_rule_id=rule_id)
        pool.seed_rule(rule_id, use_count=0)

        async def failing_tool(**kwargs: Any) -> dict:
            raise RuntimeError("crash")

        await execute_approved_action(
            pool=pool,
            action_id=action_id,
            tool_name="test_tool",
            tool_args={},
            tool_fn=failing_tool,
            approval_rule_id=rule_id,
        )

        assert pool.approval_rules[rule_id]["use_count"] == 1

    async def test_concurrent_retry_increments_rule_once(self, pool: MockPool):
        action_id = uuid.uuid4()
        rule_id = uuid.uuid4()
        pool.seed_action(action_id, approval_rule_id=rule_id)
        pool.seed_rule(rule_id, use_count=0)

        async def slow_tool(**kwargs: Any) -> dict[str, Any]:
            await asyncio.sleep(0.01)
            return {"ok": True}

        await asyncio.gather(
            execute_approved_action(
                pool=pool,
                action_id=action_id,
                tool_name="test_tool",
                tool_args={},
                tool_fn=slow_tool,
                approval_rule_id=rule_id,
            ),
            execute_approved_action(
                pool=pool,
                action_id=action_id,
                tool_name="test_tool",
                tool_args={},
                tool_fn=slow_tool,
                approval_rule_id=rule_id,
            ),
        )

        assert pool.approval_rules[rule_id]["use_count"] == 1


# ---------------------------------------------------------------------------
# list_executed_actions
# ---------------------------------------------------------------------------


class TestListExecutedActions:
    """Test the audit query for executed actions."""

    async def test_empty_result(self, pool: MockPool):
        result = await list_executed_actions(pool)
        assert result == []

    async def test_returns_executed_only(self, pool: MockPool):
        """Only actions with status='executed' should be returned."""
        exec_id = uuid.uuid4()
        pending_id = uuid.uuid4()

        pool.seed_action(exec_id, status="executed", decided_at=datetime.now(UTC))
        pool.seed_action(pending_id, status="pending")

        result = await list_executed_actions(pool)
        assert len(result) == 1
        assert result[0]["id"] == str(exec_id)

    async def test_filter_by_tool_name(self, pool: MockPool):
        id_email = uuid.uuid4()
        id_telegram = uuid.uuid4()

        pool.seed_action(
            id_email, tool_name="email_send", status="executed", decided_at=datetime.now(UTC)
        )
        pool.seed_action(
            id_telegram,
            tool_name="telegram_send",
            status="executed",
            decided_at=datetime.now(UTC),
        )

        result = await list_executed_actions(pool, tool_name="email_send")
        assert len(result) == 1
        assert result[0]["tool_name"] == "email_send"

    async def test_filter_by_rule_id(self, pool: MockPool):
        rule_id = uuid.uuid4()
        id_with_rule = uuid.uuid4()
        id_without_rule = uuid.uuid4()

        pool.seed_action(
            id_with_rule,
            status="executed",
            approval_rule_id=rule_id,
            decided_at=datetime.now(UTC),
        )
        pool.seed_action(id_without_rule, status="executed", decided_at=datetime.now(UTC))

        result = await list_executed_actions(pool, rule_id=rule_id)
        assert len(result) == 1
        assert result[0]["id"] == str(id_with_rule)

    async def test_filter_by_since(self, pool: MockPool):
        old_time = datetime.now(UTC) - timedelta(hours=48)
        recent_time = datetime.now(UTC) - timedelta(hours=1)
        cutoff = datetime.now(UTC) - timedelta(hours=24)

        id_old = uuid.uuid4()
        id_recent = uuid.uuid4()

        pool.seed_action(id_old, status="executed", decided_at=old_time)
        pool.seed_action(id_recent, status="executed", decided_at=recent_time)

        result = await list_executed_actions(pool, since=cutoff)
        assert len(result) == 1
        assert result[0]["id"] == str(id_recent)

    async def test_respects_limit(self, pool: MockPool):
        for i in range(5):
            aid = uuid.uuid4()
            pool.seed_action(
                aid,
                status="executed",
                decided_at=datetime.now(UTC) + timedelta(seconds=i),
            )

        result = await list_executed_actions(pool, limit=3)
        assert len(result) == 3

    async def test_combined_filters(self, pool: MockPool):
        """Test combining tool_name, rule_id, and since filters."""
        rule_id = uuid.uuid4()
        now = datetime.now(UTC)

        # Match: correct tool, correct rule, recent
        match_id = uuid.uuid4()
        pool.seed_action(
            match_id,
            tool_name="email_send",
            status="executed",
            approval_rule_id=rule_id,
            decided_at=now - timedelta(hours=1),
        )

        # No match: wrong tool
        wrong_tool = uuid.uuid4()
        pool.seed_action(
            wrong_tool,
            tool_name="telegram_send",
            status="executed",
            approval_rule_id=rule_id,
            decided_at=now - timedelta(hours=1),
        )

        # No match: no rule
        no_rule = uuid.uuid4()
        pool.seed_action(
            no_rule,
            tool_name="email_send",
            status="executed",
            decided_at=now - timedelta(hours=1),
        )

        # No match: too old
        too_old = uuid.uuid4()
        pool.seed_action(
            too_old,
            tool_name="email_send",
            status="executed",
            approval_rule_id=rule_id,
            decided_at=now - timedelta(days=7),
        )

        result = await list_executed_actions(
            pool,
            tool_name="email_send",
            rule_id=rule_id,
            since=now - timedelta(hours=24),
        )
        assert len(result) == 1
        assert result[0]["id"] == str(match_id)
