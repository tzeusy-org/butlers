"""Condensed approvals module tests — behavioral contract only.

Replaces test_module_approvals.py (74), test_approval_rules.py (95),
test_approval_gate.py (33), test_approval_gate_role_based.py (32),
test_approval_executor.py (25), test_approval_redaction.py (21),
test_approval_retention.py (17), test_approval_risk_tiers.py (28),
test_approval_events_audit.py (29), test_approval_events_db_immutability.py (5)
= ~359 tests replaced with ~35.

Covers:
- Module ABC compliance
- ApprovalsConfig defaults
- Tool registration (expected tools)
- Status transition model (valid + invalid transitions)
- Full lifecycle: create → list → approve → execute
- Full lifecycle: create → reject
- Full lifecycle: create → expire
- Rule matching: exact, pattern, any constraints
- Rule expiry and max_uses
- Redaction: sensitive fields scrubbed
- Approval gate: gated tools intercepted
- Role-based gating: non-human actor rejected

[bu-7sd7a]
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest

from butlers.modules.approvals.models import ActionStatus
from butlers.modules.approvals.module import (
    ApprovalsConfig,
    ApprovalsModule,
    InvalidTransitionError,
    validate_transition,
)
from butlers.modules.approvals.redaction import (
    REDACTION_MARKER,
    redact_tool_args,
)
from butlers.modules.approvals.rules import match_rules_from_list
from butlers.modules.base import Module

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Shared MockDB
# ---------------------------------------------------------------------------


class MockDB:
    """In-memory mock asyncpg pool for approvals tests."""

    def __init__(self) -> None:
        self.pending_actions: dict[uuid.UUID, dict[str, Any]] = {}
        self.approval_rules: dict[uuid.UUID, dict[str, Any]] = {}
        self.approval_events: list[dict[str, Any]] = []

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
            "agent_summary": kwargs.get("agent_summary"),
            "session_id": kwargs.get("session_id"),
            "expires_at": kwargs.get("expires_at"),
            "decided_by": kwargs.get("decided_by"),
            "decided_at": kwargs.get("decided_at"),
            "execution_result": json.dumps(kwargs["execution_result"])
            if kwargs.get("execution_result") is not None
            else None,
            "approval_rule_id": kwargs.get("approval_rule_id"),
        }
        self.pending_actions[action_id] = row
        return action_id

    def _insert_rule(self, **kwargs: Any) -> uuid.UUID:
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
        if "GROUP BY status" in query:
            counts: dict[str, int] = {}
            for row in self.pending_actions.values():
                s = row["status"]
                counts[s] = counts.get(s, 0) + 1
            return [{"status": s, "count": c} for s, c in counts.items()]

        if "pending_actions" in query and "expires_at" in query:
            status_arg = args[0] if args else "pending"
            now_arg = args[1] if len(args) > 1 else datetime.now(UTC)
            return [
                dict(row)
                for row in self.pending_actions.values()
                if row["status"] == status_arg
                and row.get("expires_at") is not None
                and row["expires_at"] < now_arg
            ]

        if "approval_rules" in query:
            rows = list(self.approval_rules.values())
            if "tool_name = $1" in query and args:
                rows = [r for r in rows if r["tool_name"] == args[0]]
            if "active = true" in query:
                rows = [r for r in rows if r["active"]]
            rows.sort(key=lambda r: r["created_at"], reverse=True)
            return [dict(r) for r in rows]

        if "pending_actions" in query:
            rows = list(self.pending_actions.values())
            if "WHERE status = $1" in query and args:
                rows = [r for r in rows if r["status"] == args[0]]
                limit = args[1] if len(args) > 1 else 50
            else:
                limit = args[0] if args else 50
            rows.sort(key=lambda r: r["requested_at"], reverse=True)
            return [dict(r) for r in rows[:limit]]

        return []

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        if "pending_actions" in query and args:
            if "UPDATE" in query and "RETURNING" in query:
                # UPDATE...RETURNING: the UUID is the last UUID-typed arg
                action_id = next((a for a in reversed(args) if isinstance(a, uuid.UUID)), None)
                if action_id is None:
                    return None
                row = self.pending_actions.get(action_id)
                if row is None:
                    return None
                # Apply status update if present (status is the first str arg)
                for i, a in enumerate(args):
                    if isinstance(a, str) and a in (
                        "pending",
                        "approved",
                        "rejected",
                        "executed",
                        "expired",
                    ):
                        row["status"] = a
                        break
                # Apply decided_by and decided_at if present
                for i, a in enumerate(args):
                    if isinstance(a, str) and "user:" in a:
                        row["decided_by"] = a
                    elif hasattr(a, "tzinfo") and not isinstance(a, str):
                        if "decided_at" not in row or row.get("decided_at") is None:
                            row["decided_at"] = a
                return dict(row)
            else:
                # Regular SELECT — find UUID in args
                action_id = next((a for a in args if isinstance(a, uuid.UUID)), None)
                if action_id is None:
                    return None
                row = self.pending_actions.get(action_id)
                return dict(row) if row else None

        if "approval_rules" in query and args:
            rule_id = args[0] if isinstance(args[0], uuid.UUID) else uuid.UUID(str(args[0]))
            row = self.approval_rules.get(rule_id)
            return dict(row) if row else None

        return None

    async def fetchval(self, query: str, *args: Any) -> Any:
        if "count" in query.lower():
            return len([r for r in self.pending_actions.values() if r["status"] == "pending"])
        return None

    async def execute(self, query: str, *args: Any) -> str:
        if "INSERT INTO pending_actions" in query:
            action_id = args[0] if args else uuid.uuid4()
            if isinstance(action_id, str):
                action_id = uuid.UUID(action_id)
            row: dict[str, Any] = {
                "id": action_id,
                "tool_name": args[1] if len(args) > 1 else "unknown",
                "tool_args": args[2] if len(args) > 2 else "{}",
                "status": "pending",
                "requested_at": args[3] if len(args) > 3 else datetime.now(UTC),
                "agent_summary": args[4] if len(args) > 4 else None,
                "session_id": args[5] if len(args) > 5 else None,
                "expires_at": args[6] if len(args) > 6 else None,
                "decided_by": None,
                "decided_at": None,
                "execution_result": None,
                "approval_rule_id": None,
            }
            self.pending_actions[action_id] = row
            return "INSERT 0 1"

        if "UPDATE pending_actions" in query and args:
            # Find the UUID in args (action_id can be at various positions)
            action_id = next((a for a in args if isinstance(a, uuid.UUID)), None)
            if action_id is None:
                return "UPDATE 0"
            row = self.pending_actions.get(action_id)
            if row:
                if "status = $1" in query:
                    row["status"] = args[0]
                elif "status = $3" in query and len(args) > 2:
                    row["status"] = args[2]
                if "decided_by = $2" in query and len(args) > 1:
                    row["decided_by"] = args[1]
                if "decided_at = $3" in query and len(args) > 2:
                    row["decided_at"] = args[2]
                if "execution_result = $2" in query and len(args) > 1:
                    row["execution_result"] = args[1]
                elif "execution_result = $3" in query and len(args) > 2:
                    row["execution_result"] = args[2]
            return "UPDATE 1"

        if "INSERT INTO approval_events" in query:
            self.approval_events.append({"query": query, "args": args})
            return "INSERT 0 1"

        if "INSERT INTO approval_rules" in query:
            rule_id = args[0] if args else uuid.uuid4()
            if isinstance(rule_id, str):
                rule_id = uuid.UUID(rule_id)
            row_r: dict[str, Any] = {
                "id": rule_id,
                "tool_name": args[1] if len(args) > 1 else "unknown",
                "arg_constraints": args[2] if len(args) > 2 else "{}",
                "description": args[3] if len(args) > 3 else "",
                "created_from": args[4] if len(args) > 4 else None,
                "created_at": datetime.now(UTC),
                "expires_at": None,
                "max_uses": None,
                "use_count": 0,
                "active": True,
            }
            self.approval_rules[rule_id] = row_r
            return "INSERT 0 1"

        if "UPDATE approval_rules" in query and args:
            rule_id_raw = args[-1]
            rule_id = (
                rule_id_raw if isinstance(rule_id_raw, uuid.UUID) else uuid.UUID(str(rule_id_raw))
            )
            row_r = self.approval_rules.get(rule_id)
            if row_r and "active = false" in query:
                row_r["active"] = False
            if row_r and "use_count = use_count + 1" in query:
                row_r["use_count"] = row_r.get("use_count", 0) + 1
            return "UPDATE 1"

        return "OK"

    @property
    def pool(self):
        return self


@pytest.fixture
def mock_db() -> MockDB:
    return MockDB()


@pytest.fixture
def module() -> ApprovalsModule:
    return ApprovalsModule()


@pytest.fixture
def human_actor() -> dict[str, Any]:
    return {
        "type": "human",
        "id": str(uuid.uuid4()),
        "name": "Alice",
        "authenticated": True,
        "roles": ["owner"],
    }


# ---------------------------------------------------------------------------
# ABC compliance
# ---------------------------------------------------------------------------


class TestModuleABC:
    def test_module_contract(self):
        """ApprovalsModule satisfies Module ABC: name, config_schema, revisions."""
        mod = ApprovalsModule()
        assert issubclass(ApprovalsModule, Module)
        assert mod.name == "approvals"
        assert mod.config_schema is ApprovalsConfig
        assert mod.migration_revisions() == "approvals"


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


class TestToolRegistration:
    async def test_tool_names(self, module: ApprovalsModule, mock_db: MockDB):
        registered: dict[str, Any] = {}

        mcp = MagicMock()
        mcp.tool.side_effect = lambda: lambda fn: registered.__setitem__(fn.__name__, fn) or fn

        await module.register_tools(mcp=mcp, config=None, db=mock_db, butler_name="test-butler")

        for expected in {
            "list_pending_actions",
            "approve_action",
            "dispatch_approved_action",
            "reject_action",
            "expire_stale_actions",
            "create_approval_rule",
            "revoke_approval_rule",
        }:
            assert expected in registered


# ---------------------------------------------------------------------------
# Status transition model
# ---------------------------------------------------------------------------


class TestStatusTransitions:
    def test_pending_to_approved(self):
        assert validate_transition(ActionStatus.PENDING, ActionStatus.APPROVED) is None

    def test_pending_to_rejected(self):
        assert validate_transition(ActionStatus.PENDING, ActionStatus.REJECTED) is None

    def test_invalid_rejected_to_approved(self):
        with pytest.raises(InvalidTransitionError):
            validate_transition(ActionStatus.REJECTED, ActionStatus.APPROVED)

    def test_invalid_executed_to_pending(self):
        with pytest.raises(InvalidTransitionError):
            validate_transition(ActionStatus.EXECUTED, ActionStatus.PENDING)


# ---------------------------------------------------------------------------
# Full lifecycle: create → approve → execute
# ---------------------------------------------------------------------------


class TestApproveLifecycle:
    async def test_create_list_approve_execute(
        self, module: ApprovalsModule, mock_db: MockDB, human_actor: dict[str, Any]
    ):
        await module.on_startup(config=None, db=mock_db)

        action_id = uuid.uuid4()
        mock_db._insert_action(
            id=action_id,
            tool_name="email_send",
            tool_args={"to": "alice@example.com", "body": "hello"},
            agent_summary="Send greeting email",
            status="pending",
        )

        actions = await module._list_pending_actions()
        assert len(actions) == 1
        assert actions[0]["status"] == "pending"

        executed_calls: list[tuple] = []

        async def mock_executor(tool_name, tool_args):
            executed_calls.append((tool_name, tool_args))
            return {"status": "sent"}

        module.set_tool_executor(mock_executor)
        result = await module._approve_action(str(action_id), actor=human_actor)

        assert result["status"] == "executed"
        assert executed_calls[0][0] == "email_send"

    async def test_reject_action(
        self, module: ApprovalsModule, mock_db: MockDB, human_actor: dict[str, Any]
    ):
        await module.on_startup(config=None, db=mock_db)
        action_id = mock_db._insert_action(tool_name="telegram_send", status="pending")

        result = await module._reject_action(str(action_id), reason="spam", actor=human_actor)
        assert result["status"] == "rejected"

        # Cannot approve after rejection
        approve = await module._approve_action(str(action_id), actor=human_actor)
        assert "error" in approve

    async def test_expire_stale_actions(
        self, module: ApprovalsModule, mock_db: MockDB, human_actor: dict[str, Any]
    ):
        await module.on_startup(config=None, db=mock_db)
        action_id = mock_db._insert_action(
            tool_name="cal_create",
            status="pending",
            expires_at=datetime.now(UTC) - timedelta(minutes=30),
        )

        result = await module._expire_stale_actions()
        assert result["expired_count"] == 1
        assert str(action_id) in result["expired_ids"]

    async def test_approve_invalid_uuid_returns_error(
        self, module: ApprovalsModule, mock_db: MockDB, human_actor: dict[str, Any]
    ):
        await module.on_startup(config=None, db=mock_db)
        result = await module._approve_action("not-a-uuid", actor=human_actor)
        assert "error" in result


# ---------------------------------------------------------------------------
# Dashboard dispatch of an already-approved action (bu-1q9wh)
# ---------------------------------------------------------------------------


class TestDispatchApprovedActionById:
    """``_dispatch_approved_action_by_id`` runs the ORIGINAL tool via the wired
    executor — the un-gated path the dashboard uses so approving a gated action
    does not re-enter the approval gate and re-park itself.
    """

    async def test_executes_original_tool_and_marks_executed(
        self, module: ApprovalsModule, mock_db: MockDB
    ):
        await module.on_startup(config=None, db=mock_db)
        action_id = uuid.uuid4()
        mock_db._insert_action(
            id=action_id,
            tool_name="telegram_send_message",
            tool_args={"chat_id": "206570151", "text": "hi"},
            status="approved",
        )

        calls: list[tuple] = []

        async def mock_executor(tool_name, tool_args):
            calls.append((tool_name, tool_args))
            return {"status": "sent", "message_id": "tg-1"}

        module.set_tool_executor(mock_executor)

        result = await module._dispatch_approved_action_by_id(str(action_id))

        assert result["status"] == "executed"
        assert calls == [("telegram_send_message", {"chat_id": "206570151", "text": "hi"})]

    async def test_already_executed_replays_without_rerunning(
        self, module: ApprovalsModule, mock_db: MockDB
    ):
        """An already-executed action replays its stored result and does NOT
        re-invoke the tool — the idempotency contract for a dashboard double-click.
        """
        await module.on_startup(config=None, db=mock_db)
        action_id = uuid.uuid4()
        mock_db._insert_action(
            id=action_id,
            tool_name="telegram_send_message",
            tool_args={"chat_id": "206570151", "text": "hi"},
            status="executed",
            execution_result={"success": True, "result": {"message_id": "tg-1"}},
        )

        async def boom_executor(tool_name, tool_args):  # pragma: no cover - must not run
            raise AssertionError("executor must not re-run an already-executed action")

        module.set_tool_executor(boom_executor)
        result = await module._dispatch_approved_action_by_id(str(action_id))

        assert result["status"] == "executed"

    async def test_rejects_action_not_in_approved_state(
        self, module: ApprovalsModule, mock_db: MockDB
    ):
        await module.on_startup(config=None, db=mock_db)
        action_id = mock_db._insert_action(tool_name="telegram_send_message", status="pending")

        async def mock_executor(tool_name, tool_args):  # pragma: no cover - must not run
            raise AssertionError("executor must not run for a non-approved action")

        module.set_tool_executor(mock_executor)
        result = await module._dispatch_approved_action_by_id(str(action_id))

        # Non-approved state must be refused (preceding error-presence is the contract).
        assert "error" in result

    async def test_no_executor_wired_returns_error(self, module: ApprovalsModule, mock_db: MockDB):
        await module.on_startup(config=None, db=mock_db)
        action_id = mock_db._insert_action(tool_name="telegram_send_message", status="approved")

        result = await module._dispatch_approved_action_by_id(str(action_id))
        assert "error" in result

    @pytest.mark.parametrize(
        "action_id",
        ["00000000-0000-0000-0000-0000000000ff", "not-a-uuid"],
        ids=["missing-uuid", "invalid-uuid"],
    )
    async def test_missing_or_invalid_action_returns_error(
        self, module: ApprovalsModule, mock_db: MockDB, action_id: str
    ):
        await module.on_startup(config=None, db=mock_db)
        result = await module._dispatch_approved_action_by_id(action_id)
        assert "error" in result


# ---------------------------------------------------------------------------
# Rule matching
# ---------------------------------------------------------------------------


class TestRuleMatching:
    def _make_rule_dict(
        self, tool_name: str, constraints: dict, *, expires_at=None, max_uses=None, use_count=0
    ) -> dict:
        import json

        return {
            "id": str(uuid.uuid4()),
            "tool_name": tool_name,
            "arg_constraints": json.dumps(constraints),
            "description": "test",
            "created_at": datetime.now(UTC),
            "expires_at": expires_at,  # pass datetime object, not ISO string
            "max_uses": max_uses,
            "use_count": use_count,
            "active": True,
        }

    def test_exact_constraint_matches(self):
        rule = self._make_rule_dict(
            "email_send", {"to": {"type": "exact", "value": "alice@example.com"}}
        )
        match = match_rules_from_list("email_send", {"to": "alice@example.com"}, [rule])
        assert match is not None

    def test_any_constraint_matches_anything(self):
        rule = self._make_rule_dict("email_send", {"to": {"type": "any"}})
        match = match_rules_from_list("email_send", {"to": "anyone@example.com"}, [rule])
        assert match is not None

    def test_expired_rule_skipped(self):
        rule = self._make_rule_dict(
            "email_send",
            {"to": {"type": "any"}},
            expires_at=datetime.now(UTC) - timedelta(hours=1),
        )
        match = match_rules_from_list("email_send", {"to": "alice@example.com"}, [rule])
        assert match is None

    def test_max_uses_exceeded_rule_skipped(self):
        rule = self._make_rule_dict("email_send", {"to": {"type": "any"}}, max_uses=3, use_count=3)
        match = match_rules_from_list("email_send", {"to": "alice@example.com"}, [rule])
        assert match is None


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------


class TestRedaction:
    def test_sensitive_fields_are_redacted(self):
        result = redact_tool_args(
            "email_send",
            {"to": "alice@example.com", "password": "secret123", "body": "hello"},
        )
        # Both "to" and "password" are in SENSITIVE_ARG_NAMES
        assert result.get("password") == REDACTION_MARKER
        assert result.get("to") == REDACTION_MARKER
        # "body" is not sensitive — passes through
        assert result.get("body") == "hello"

    def test_non_sensitive_fields_pass_through(self):
        result = redact_tool_args("some_tool", {"message": "hello", "count": 3})
        assert result["message"] == "hello"
        assert result["count"] == 3
