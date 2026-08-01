"""Real-Postgres regression: the last three approvals JSONB column families
must not double-encode (bu-c8b8e — sibling sweep to bu-cymc4/PR #2924 and
bu-bstqu/PR #2930).

Every asyncpg pool in this codebase registers a JSONB codec
(``register_jsonb_codec``, ``src/butlers/db.py``) whose encoder already calls
``json.dumps()`` once. Pre-serializing a dict with ``json.dumps()`` before
binding it double-encodes the value into a jsonb-typed STRING instead of an
OBJECT. This bead fixes the remaining writer sites for:

1. ``approval_rules.arg_constraints`` — module.py's ``_approve_action``
   (create_rule branch), ``_create_approval_rule``, ``_create_rule_from_action``;
   operations.py's ``create_approval_rule``, ``create_rule_from_action``;
   autonomy_suggestions.py's ``confirm_suggestion`` (promotion path).
2. ``pending_actions.execution_result`` — executor.py's
   ``execute_approved_action``; operations.py's ``mark_executed``.
3. ``autonomy_suggestions.representative_args`` — autonomy_suggestions.py's
   ``create_promotion_suggestion``/``create_demotion_suggestion``.
   ``autonomy_approval_history.tool_args`` — autonomy_tracker.py's
   ``record_approval``.

The mocked-pool unit tests in test_module_approvals.py cannot catch this:
they only assert on the Python value handed to the mock, never round-trip
through real Postgres. These tests write via the real production code paths
against a migrated-shape Postgres table and read the rows back directly.

Live-data audit (read-only, butlers-dev, 2026-07-05):
- ``approval_rules`` (arg_constraints): 0 rows in any schema (home, messenger,
  relationship — the only schemas with this table). No corruption possible;
  the corresponding read-side isinstance(str) workarounds in rules.py and
  autonomy_tracker.py were removed rather than kept.
- ``autonomy_suggestions`` (representative_args): 0 rows in any schema.
  Same conclusion — the isinstance(str) workarounds in autonomy_suggestions.py
  and api/routers/approvals.py were removed.
- ``pending_actions`` (execution_result): ACTIVELY corrupted — messenger 25
  string-typed / 1 object-typed rows, relationship 6 string / 4 object. The
  isinstance(str) workaround in executor.py's ``_parse_execution_result`` is
  kept.
- ``autonomy_approval_history`` (tool_args): messenger 13 string / 1 object,
  relationship 62 string / 0 object — corrupted at rest, but this column is
  never read back as a dict anywhere in the codebase (write-only audit trail),
  so there is no read-side workaround to evaluate for this column.
"""

from __future__ import annotations

import asyncio
import shutil
import uuid
from datetime import UTC, datetime

import pytest

from butlers.modules.approvals import executor as executor_mod
from butlers.modules.approvals import operations as operations_mod
from butlers.modules.approvals.autonomy_suggestions import (
    confirm_suggestion,
    create_demotion_suggestion,
    create_promotion_suggestion,
)
from butlers.modules.approvals.autonomy_tracker import record_approval
from butlers.modules.approvals.models import ActionStatus, PendingAction
from butlers.modules.approvals.module import ApprovalsModule

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


@pytest.fixture
async def approvals_full_pool(provisioned_postgres_pool):
    """Provision a fresh database with the full approvals + autonomy schema.

    WARNING: kept in sync with tests/modules/conftest.py's ``approvals_pool``
    fixture and src/butlers/modules/approvals/migrations/001_approvals_tables.py
    (same underlying migrations) — update all three if the schema drifts.
    """
    async with provisioned_postgres_pool() as pool:
        await pool.execute("""
            CREATE TABLE IF NOT EXISTS pending_actions (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tool_name TEXT NOT NULL,
                tool_args JSONB NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                agent_summary TEXT,
                session_id UUID,
                requested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                expires_at TIMESTAMPTZ,
                decided_by TEXT,
                decided_at TIMESTAMPTZ,
                execution_result JSONB,
                why TEXT,
                evidence JSONB NOT NULL DEFAULT '[]'::jsonb,
                approval_rule_id UUID,
                CONSTRAINT pending_actions_status_check
                    CHECK (status IN (
                        'pending', 'approved', 'rejected', 'expired', 'executed', 'abandoned'
                    ))
            )
        """)
        await pool.execute("""
            CREATE TABLE IF NOT EXISTS approval_rules (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tool_name TEXT NOT NULL,
                arg_constraints JSONB NOT NULL DEFAULT '{}'::jsonb,
                description TEXT NOT NULL,
                created_from UUID,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                expires_at TIMESTAMPTZ,
                max_uses INTEGER,
                use_count INTEGER NOT NULL DEFAULT 0,
                active BOOLEAN NOT NULL DEFAULT true
            )
        """)
        await pool.execute("""
            CREATE TABLE IF NOT EXISTS approval_events (
                event_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                action_id UUID REFERENCES pending_actions(id),
                rule_id UUID REFERENCES approval_rules(id),
                event_type TEXT NOT NULL,
                actor TEXT NOT NULL,
                reason TEXT,
                event_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                CONSTRAINT approval_events_type_check
                    CHECK (event_type IN (
                        'action_queued',
                        'action_auto_approved',
                        'action_approved',
                        'action_rejected',
                        'action_expired',
                        'action_abandoned',
                        'action_execution_succeeded',
                        'action_execution_failed',
                        'rule_created',
                        'rule_revoked',
                        'promotion_suggested',
                        'promotion_confirmed',
                        'promotion_dismissed',
                        'promotion_superseded',
                        'demotion_suggested',
                        'demotion_confirmed',
                        'demotion_dismissed'
                    )),
                CONSTRAINT approval_events_link_check
                    CHECK (
                        action_id IS NOT NULL
                        OR rule_id IS NOT NULL
                        OR event_type IN (
                            'promotion_suggested',
                            'promotion_confirmed',
                            'promotion_dismissed',
                            'promotion_superseded',
                            'demotion_suggested',
                            'demotion_confirmed',
                            'demotion_dismissed'
                        )
                    )
            )
        """)
        await pool.execute("""
            CREATE TABLE IF NOT EXISTS autonomy_approval_history (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                pattern_fingerprint VARCHAR(64) NOT NULL,
                tool_name TEXT NOT NULL,
                tool_args JSONB NOT NULL,
                action_id UUID REFERENCES pending_actions(id) ON DELETE SET NULL,
                approved_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                time_to_decision_seconds DOUBLE PRECISION,
                fingerprint_version SMALLINT NOT NULL DEFAULT 1
            )
        """)
        await pool.execute("""
            CREATE TABLE IF NOT EXISTS autonomy_suggestions (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                action_id UUID REFERENCES pending_actions(id) ON DELETE SET NULL,
                suggestion_type VARCHAR NOT NULL DEFAULT 'promotion',
                pattern_fingerprint VARCHAR(64) NOT NULL,
                tool_name TEXT NOT NULL,
                representative_args JSONB NOT NULL,
                status VARCHAR NOT NULL DEFAULT 'pending',
                approval_count_at_creation INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                decided_at TIMESTAMPTZ,
                decided_by TEXT,
                resulting_rule_id UUID REFERENCES approval_rules(id) ON DELETE SET NULL,
                cooldown_until TIMESTAMPTZ,
                dismissal_reason TEXT,
                fingerprint_version SMALLINT NOT NULL DEFAULT 1,
                CONSTRAINT autonomy_suggestions_type_check
                    CHECK (suggestion_type IN ('promotion', 'demotion')),
                CONSTRAINT autonomy_suggestions_status_check
                    CHECK (status IN ('pending', 'confirmed', 'dismissed', 'superseded'))
            )
        """)
        yield pool


async def _insert_pending_action(
    pool,
    *,
    tool_name: str = "test_tool",
    tool_args: dict | None = None,
    status: str = "approved",
) -> uuid.UUID:
    action_id = uuid.uuid4()
    await pool.execute(
        "INSERT INTO pending_actions (id, tool_name, tool_args, status) VALUES ($1, $2, $3, $4)",
        action_id,
        tool_name,
        tool_args or {"to": "alice@example.com"},
        status,
    )
    return action_id


def _human_actor() -> dict:
    return {
        "type": "human",
        "id": str(uuid.uuid4()),
        "name": "Alice",
        "authenticated": True,
        "roles": ["owner"],
    }


# ---------------------------------------------------------------------------
# 1. approval_rules.arg_constraints
# ---------------------------------------------------------------------------


class TestApprovalRulesArgConstraintsRoundtrip:
    """Every arg_constraints writer must store a native dict, not a string."""

    async def test_module_create_approval_rule_roundtrips_as_dict(
        self, approvals_full_pool
    ) -> None:
        module = ApprovalsModule()
        await module.on_startup(config=None, db=approvals_full_pool)

        result = await module._create_approval_rule(
            tool_name="email_send",
            arg_constraints={"to": {"type": "exact", "value": "alice@example.com"}},
            description="test rule",
            actor=_human_actor(),
        )
        assert "error" not in result, result

        row = await approvals_full_pool.fetchrow(
            "SELECT arg_constraints FROM approval_rules WHERE tool_name = 'email_send'"
        )
        assert row is not None
        stored = row["arg_constraints"]
        assert isinstance(stored, dict), (
            f"arg_constraints arrived as {type(stored).__name__!r}, not a dict — "
            "the jsonb column was double-encoded into a string."
        )
        assert stored == {"to": {"type": "exact", "value": "alice@example.com"}}

    async def test_module_create_rule_from_action_roundtrips_as_dict(
        self, approvals_full_pool
    ) -> None:
        module = ApprovalsModule()
        await module.on_startup(config=None, db=approvals_full_pool)

        action_id = await _insert_pending_action(
            approvals_full_pool,
            tool_name="telegram_send",
            tool_args={"chat_id": "123", "text": "hi"},
        )

        result = await module._create_rule_from_action(
            action_id=str(action_id),
            actor=_human_actor(),
        )
        assert "error" not in result, result

        row = await approvals_full_pool.fetchrow(
            "SELECT arg_constraints FROM approval_rules WHERE tool_name = 'telegram_send'"
        )
        assert row is not None
        stored = row["arg_constraints"]
        assert isinstance(stored, dict), (
            f"arg_constraints arrived as {type(stored).__name__!r}, not a dict — "
            "the jsonb column was double-encoded into a string."
        )

    async def test_module_approve_action_create_rule_branch_roundtrips_as_dict(
        self, approvals_full_pool
    ) -> None:
        """``_approve_action(create_rule=True)`` has its own inline arg_constraints
        INSERT (module.py, separate from create_rule_from_action)."""
        module = ApprovalsModule()
        await module.on_startup(config=None, db=approvals_full_pool)

        action_id = await _insert_pending_action(
            approvals_full_pool,
            tool_name="calendar_create_event",
            tool_args={"title": "Standup", "attendees": ["bob@example.com"]},
            status="pending",
        )

        result = await module._approve_action(
            str(action_id), create_rule=True, actor=_human_actor()
        )
        assert "error" not in result, result
        assert result.get("created_rule_error") is None, result

        row = await approvals_full_pool.fetchrow(
            "SELECT arg_constraints FROM approval_rules WHERE tool_name = 'calendar_create_event'"
        )
        assert row is not None
        stored = row["arg_constraints"]
        assert isinstance(stored, dict), (
            f"arg_constraints arrived as {type(stored).__name__!r}, not a dict — "
            "the jsonb column was double-encoded into a string."
        )
        assert stored == {"title": "Standup", "attendees": ["bob@example.com"]}

    async def test_operations_create_approval_rule_roundtrips_as_dict(
        self, approvals_full_pool
    ) -> None:
        result = await operations_mod.create_approval_rule(
            approvals_full_pool,
            tool_name="rest_email_send",
            arg_constraints={"to": {"type": "exact", "value": "carol@example.com"}},
            description="REST-created rule",
        )
        assert "error" not in result, result

        row = await approvals_full_pool.fetchrow(
            "SELECT arg_constraints FROM approval_rules WHERE tool_name = 'rest_email_send'"
        )
        assert row is not None
        stored = row["arg_constraints"]
        assert isinstance(stored, dict), (
            f"arg_constraints arrived as {type(stored).__name__!r}, not a dict — "
            "the jsonb column was double-encoded into a string."
        )

    async def test_operations_create_rule_from_action_roundtrips_as_dict(
        self, approvals_full_pool
    ) -> None:
        action_id = await _insert_pending_action(
            approvals_full_pool,
            tool_name="rest_telegram_send",
            tool_args={"chat_id": "456"},
        )

        result = await operations_mod.create_rule_from_action(
            approvals_full_pool, action_id=str(action_id)
        )
        assert "error" not in result, result

        row = await approvals_full_pool.fetchrow(
            "SELECT arg_constraints FROM approval_rules WHERE tool_name = 'rest_telegram_send'"
        )
        assert row is not None
        stored = row["arg_constraints"]
        assert isinstance(stored, dict), (
            f"arg_constraints arrived as {type(stored).__name__!r}, not a dict — "
            "the jsonb column was double-encoded into a string."
        )

    async def test_confirm_suggestion_promotion_creates_rule_with_dict_constraints(
        self, approvals_full_pool
    ) -> None:
        """confirm_suggestion()'s promotion path writes a new approval_rules row
        with derived exact-match arg_constraints (autonomy_suggestions.py ~305)."""
        suggestion = await create_promotion_suggestion(
            approvals_full_pool,
            pattern_fingerprint="fp-promo-1",
            tool_name="promo_tool",
            representative_args={"to": "dan@example.com"},
            approval_count=5,
        )

        result = await confirm_suggestion(
            approvals_full_pool, suggestion["id"], actor="dashboard:rest-api"
        )
        assert "error" not in result, result

        row = await approvals_full_pool.fetchrow(
            "SELECT arg_constraints FROM approval_rules WHERE tool_name = 'promo_tool'"
        )
        assert row is not None
        stored = row["arg_constraints"]
        assert isinstance(stored, dict), (
            f"arg_constraints arrived as {type(stored).__name__!r}, not a dict — "
            "the jsonb column was double-encoded into a string."
        )
        assert stored == {"to": {"type": "exact", "value": "dan@example.com"}}


# ---------------------------------------------------------------------------
# 2. pending_actions.execution_result
# ---------------------------------------------------------------------------


class TestExecutionResultRoundtrip:
    """Every execution_result writer must store a native dict, not a string."""

    async def test_executor_execute_approved_action_roundtrips_as_dict(
        self, approvals_full_pool
    ) -> None:
        action_id = await _insert_pending_action(
            approvals_full_pool,
            tool_name="notify",
            tool_args={"message": "hello"},
            status="approved",
        )

        async def _tool_fn(**kwargs):
            return {"message_id": "abc-123", "delivered": True}

        outcome = await executor_mod.execute_approved_action(
            approvals_full_pool,
            action_id,
            "notify",
            {"message": "hello"},
            _tool_fn,
        )
        assert outcome.success is True

        row = await approvals_full_pool.fetchrow(
            "SELECT execution_result FROM pending_actions WHERE id = $1", action_id
        )
        assert row is not None
        stored = row["execution_result"]
        assert isinstance(stored, dict), (
            f"execution_result arrived as {type(stored).__name__!r}, not a dict — "
            "the jsonb column was double-encoded into a string."
        )
        assert stored["success"] is True
        assert stored["result"] == {"message_id": "abc-123", "delivered": True}

        event = await approvals_full_pool.fetchrow(
            "SELECT event_type, event_metadata FROM approval_events WHERE action_id = $1",
            action_id,
        )
        assert event is not None
        assert event["event_type"] == "action_execution_succeeded"
        assert event["event_metadata"] == {"tool_name": "notify"}

    async def test_executor_failure_stays_approved_and_writes_failure_audit(
        self, approvals_full_pool
    ) -> None:
        """A failed handler cannot be recorded as completed execution."""
        action_id = await _insert_pending_action(
            approvals_full_pool,
            tool_name="memory_entity_merge",
            tool_args={"source_entity_id": "source", "target_entity_id": "target"},
            status="approved",
        )

        async def _tool_fn(**kwargs):
            raise RuntimeError("source entity is already tombstoned")

        outcome = await executor_mod.execute_approved_action(
            approvals_full_pool,
            action_id,
            "memory_entity_merge",
            {"source_entity_id": "source", "target_entity_id": "target"},
            _tool_fn,
        )

        assert outcome.success is False
        assert outcome.error == "source entity is already tombstoned"
        row = await approvals_full_pool.fetchrow(
            "SELECT status, execution_result FROM pending_actions WHERE id = $1", action_id
        )
        assert row is not None
        assert row["status"] == "approved"
        assert row["execution_result"] is None
        event = await approvals_full_pool.fetchrow(
            "SELECT event_type, reason FROM approval_events WHERE action_id = $1",
            action_id,
        )
        assert event is not None
        assert event["event_type"] == "action_execution_failed"
        assert event["reason"] == "source entity is already tombstoned"

    async def test_executor_does_not_claim_success_until_audit_persists(
        self, approvals_full_pool, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The successful result/status update rolls back with a failed audit append."""
        action_id = await _insert_pending_action(
            approvals_full_pool,
            tool_name="memory_entity_merge",
            status="approved",
        )

        async def _tool_fn(**kwargs):
            return {"target_entity_id": "target"}

        async def _audit_unavailable(*args, **kwargs):
            raise RuntimeError("approval audit unavailable")

        monkeypatch.setattr(executor_mod, "record_approval_event", _audit_unavailable)

        outcome = await executor_mod.execute_approved_action(
            approvals_full_pool,
            action_id,
            "memory_entity_merge",
            {"source_entity_id": "source", "target_entity_id": "target"},
            _tool_fn,
        )

        assert outcome.success is False
        assert "Could not persist execution outcome" in (outcome.error or "")
        row = await approvals_full_pool.fetchrow(
            "SELECT status, execution_result FROM pending_actions WHERE id = $1", action_id
        )
        assert row is not None
        assert row["status"] == "approved"
        assert row["execution_result"] is None

    async def test_abandon_waits_for_locked_execution_and_loses_after_success(
        self, approvals_full_pool
    ) -> None:
        """Abandon never wins after a handler has acquired execution ownership."""
        action_id = await _insert_pending_action(
            approvals_full_pool,
            tool_name="notify",
            tool_args={"message": "hello"},
            status="approved",
        )
        started = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def _tool_fn(**kwargs):
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            return {"delivered": True}

        execution_task = asyncio.create_task(
            executor_mod.execute_approved_action(
                approvals_full_pool,
                action_id,
                "notify",
                {"message": "hello"},
                _tool_fn,
            )
        )
        await asyncio.wait_for(started.wait(), timeout=2)

        abandon_task = asyncio.create_task(
            operations_mod.abandon_approved_action(
                approvals_full_pool,
                str(action_id),
                reason="Owner no longer wants this recovery",
            )
        )
        await asyncio.sleep(0.05)
        assert not abandon_task.done(), "Abandon must wait for the execution row lock"

        release.set()
        execution = await asyncio.wait_for(execution_task, timeout=2)
        abandoned = await asyncio.wait_for(abandon_task, timeout=2)

        assert execution.success is True
        assert calls == 1
        assert "error" in abandoned
        assert "execution result" in abandoned["error"]
        row = await approvals_full_pool.fetchrow(
            "SELECT status, execution_result FROM pending_actions WHERE id = $1", action_id
        )
        assert row is not None
        assert row["status"] == "executed"
        assert row["execution_result"]["success"] is True

    async def test_operations_mark_executed_roundtrips_as_dict(self, approvals_full_pool) -> None:
        action_id = await _insert_pending_action(
            approvals_full_pool,
            tool_name="rest_notify",
            tool_args={"message": "hi"},
            status="approved",
        )

        result = await operations_mod.mark_executed(
            approvals_full_pool,
            str(action_id),
            execution_result={"message_id": "xyz-789"},
            success=True,
        )
        assert "error" not in result, result

        row = await approvals_full_pool.fetchrow(
            "SELECT execution_result FROM pending_actions WHERE id = $1", action_id
        )
        assert row is not None
        stored = row["execution_result"]
        assert isinstance(stored, dict), (
            f"execution_result arrived as {type(stored).__name__!r}, not a dict — "
            "the jsonb column was double-encoded into a string."
        )
        assert stored == {"message_id": "xyz-789"}

    async def test_operations_mark_executed_rolls_back_when_audit_append_fails(
        self, approvals_full_pool, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """REST dispatch must not claim terminal success without its audit event."""
        action_id = await _insert_pending_action(
            approvals_full_pool,
            tool_name="rest_notify",
            status="approved",
        )

        async def _audit_unavailable(*args, **kwargs):
            raise RuntimeError("approval audit unavailable")

        monkeypatch.setattr(operations_mod, "record_approval_event", _audit_unavailable)

        result = await operations_mod.mark_executed(
            approvals_full_pool,
            str(action_id),
            execution_result={"message_id": "xyz-789"},
        )

        assert "Could not persist execution outcome" in result["error"]
        row = await approvals_full_pool.fetchrow(
            "SELECT status, execution_result FROM pending_actions WHERE id = $1", action_id
        )
        assert row is not None
        assert row["status"] == "approved"
        assert row["execution_result"] is None


# ---------------------------------------------------------------------------
# 3. autonomy_suggestions.representative_args + autonomy_approval_history.tool_args
# ---------------------------------------------------------------------------


class TestRepresentativeArgsAndAutonomyHistoryRoundtrip:
    async def test_create_promotion_suggestion_roundtrips_as_dict(
        self, approvals_full_pool
    ) -> None:
        action_id = await _insert_pending_action(
            approvals_full_pool,
            tool_name="email_send",
            tool_args={"to": "alice@example.com", "nested": {"a": 1}},
        )
        result = await create_promotion_suggestion(
            approvals_full_pool,
            pattern_fingerprint="fp-1",
            tool_name="email_send",
            representative_args={"to": "alice@example.com", "nested": {"a": 1}},
            approval_count=3,
            action_id=action_id,
        )
        assert result["id"]
        assert result["action_id"] == str(action_id)

        row = await approvals_full_pool.fetchrow(
            "SELECT representative_args, action_id FROM autonomy_suggestions WHERE tool_name = 'email_send'"
        )
        assert row is not None
        stored = row["representative_args"]
        assert isinstance(stored, dict), (
            f"representative_args arrived as {type(stored).__name__!r}, not a dict — "
            "the jsonb column was double-encoded into a string."
        )
        assert stored == {"to": "alice@example.com", "nested": {"a": 1}}
        assert row["action_id"] == action_id

    async def test_create_demotion_suggestion_roundtrips_as_dict(self, approvals_full_pool) -> None:
        action_id = await _insert_pending_action(
            approvals_full_pool,
            tool_name="telegram_send",
            tool_args={"chat_id": "999", "text": "hi"},
        )
        row = await approvals_full_pool.fetchrow(
            "SELECT * FROM pending_actions WHERE id = $1", action_id
        )
        action = PendingAction.from_row(row)

        rule_id = uuid.uuid4()
        await approvals_full_pool.execute(
            "INSERT INTO approval_rules (id, tool_name, arg_constraints, description) "
            "VALUES ($1, $2, $3, $4)",
            rule_id,
            "telegram_send",
            {},
            "test rule for demotion",
        )

        result = await create_demotion_suggestion(
            approvals_full_pool, action, rule_id, "boom: tool execution failed"
        )
        assert result["id"]

        db_row = await approvals_full_pool.fetchrow(
            "SELECT representative_args, action_id FROM autonomy_suggestions WHERE tool_name = 'telegram_send'"
        )
        assert db_row is not None
        stored = db_row["representative_args"]
        assert isinstance(stored, dict), (
            f"representative_args arrived as {type(stored).__name__!r}, not a dict — "
            "the jsonb column was double-encoded into a string."
        )
        assert stored == {"chat_id": "999", "text": "hi"}
        assert db_row["action_id"] == action_id

    async def test_record_approval_roundtrips_tool_args_as_dict(self, approvals_full_pool) -> None:
        action_id = await _insert_pending_action(
            approvals_full_pool,
            tool_name="email_send",
            tool_args={"to": "eve@example.com"},
        )
        row = await approvals_full_pool.fetchrow(
            "SELECT * FROM pending_actions WHERE id = $1", action_id
        )
        # decided_at/requested_at must both be set for time_to_decision math
        action = PendingAction(
            id=action_id,
            tool_name=row["tool_name"],
            tool_args=dict(row["tool_args"]),
            status=ActionStatus(row["status"]),
            requested_at=row["requested_at"],
            decided_at=datetime.now(UTC),
        )

        await record_approval(approvals_full_pool, action)

        db_row = await approvals_full_pool.fetchrow(
            "SELECT tool_args FROM autonomy_approval_history WHERE tool_name = 'email_send'"
        )
        assert db_row is not None
        stored = db_row["tool_args"]
        assert isinstance(stored, dict), (
            f"tool_args arrived as {type(stored).__name__!r}, not a dict — "
            "the jsonb column was double-encoded into a string. Note: this column is "
            "never read back as a dict in production code today (bu-c8b8e), but it "
            "should still store native JSON types for any future reader/export."
        )
        assert stored == {"to": "eve@example.com"}
