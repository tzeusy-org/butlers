"""Real-Postgres coverage for deterministic approval decision-memory writeback."""

from __future__ import annotations

import shutil
import uuid
from datetime import UTC, datetime

import asyncpg
import pytest

from butlers.db import register_jsonb_codec
from butlers.modules.approvals import operations
from butlers.modules.approvals.autonomy_tracker import FINGERPRINT_VERSION
from butlers.modules.approvals.decision_memory import DecisionMemoryWriter
from butlers.modules.approvals.executor import execute_approved_action
from butlers.modules.approvals.models import ActionStatus, ApprovalRule, PendingAction
from butlers.modules.base import ToolMeta
from butlers.modules.memory.tools.context import memory_context
from butlers.testing.migration import create_migrated_test_db, migration_db_name

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


class _EmbeddingEngine:
    """Small deterministic embedding double suitable for pgvector storage."""

    model_name = "decision-memory-test"

    def embed(self, _text: str) -> list[float]:
        return [0.1] * 384


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    """Provision the exact core, memory, and approvals schemas once."""
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "memory", "approvals"],
    )


@pytest.fixture
async def pool(migrated_db_url: str):
    """Return a clean JSONB-aware pool against the migrated database."""
    db_pool = await asyncpg.create_pool(
        migrated_db_url,
        min_size=1,
        max_size=3,
        init=register_jsonb_codec,
    )
    await db_pool.execute(
        "TRUNCATE TABLE memory_links, facts, approval_events, pending_actions, "
        "approval_rules, autonomy_approval_history, autonomy_suggestions, public.entities "
        "CASCADE"
    )
    yield db_pool
    await db_pool.close()


def _writer(pool: asyncpg.Pool) -> DecisionMemoryWriter:
    return DecisionMemoryWriter(
        butler_name="home",
        memory_pool_provider=lambda: pool,
        resolution_pool_provider=lambda: pool,
        embedding_engine_provider=_EmbeddingEngine,
        tool_meta_provider=lambda _tool_name: ToolMeta(
            arg_sensitivities={"entity_id": True, "recipient": True}
        ),
    )


def _action(entity_id: uuid.UUID, *, action_id: uuid.UUID | None = None) -> PendingAction:
    return PendingAction(
        id=action_id or uuid.uuid4(),
        tool_name="notify",
        tool_args={
            "entity_id": str(entity_id),
            "channel": "telegram",
            "recipient": "123456",
            "text": "The content can vary without changing the decision pattern.",
        },
        status=ActionStatus.EXECUTED,
        requested_at=datetime.now(UTC),
    )


async def test_terminal_decisions_upsert_entity_linked_tally_and_recall_it(pool) -> None:
    """A decision tally is own-store, entity-linked, versioned, and recallable."""
    entity_id = await pool.fetchval(
        "INSERT INTO public.entities (canonical_name, entity_type) VALUES ($1, $2) RETURNING id",
        "Avery Example",
        "person",
    )
    writer = _writer(pool)

    approved = _action(entity_id)
    rejected = _action(entity_id)
    await writer.record_terminal_decision(approved, "approved")
    await writer.record_terminal_decision(rejected, "rejected")

    fact = await pool.fetchrow(
        "SELECT subject, predicate, content, scope, metadata, entity_id "
        "FROM facts WHERE predicate = $1 AND validity = 'active'",
        "decision:approval_tally",
    )

    assert fact is not None
    assert fact["entity_id"] == entity_id
    assert fact["predicate"] == "decision:approval_tally"
    assert fact["scope"].startswith("home:decision:")
    assert fact["metadata"] == {
        "approve_count": 1,
        "reject_count": 1,
        "last_decision": "rejected",
        "last_action_id": str(rejected.id),
        "fingerprint": fact["metadata"]["fingerprint"],
        "fingerprint_version": FINGERPRINT_VERSION,
    }
    assert "notify" in fact["subject"]

    context = await memory_context(
        pool,
        _EmbeddingEngine(),
        "Should I notify Avery again?",
        butler="home",
    )
    assert "decision:approval_tally" in context


async def test_standing_rule_fact_is_updated_on_revocation(pool) -> None:
    """Rule grants are descriptive memory facts, not executable policy."""
    writer = _writer(pool)
    rule = ApprovalRule(
        id=uuid.uuid4(),
        tool_name="notify",
        arg_constraints={"recipient": {"type": "exact", "value": "123456"}},
        description="Notify Avery over Telegram",
        created_at=datetime.now(UTC),
    )

    await writer.record_standing_rule(rule, active=True)
    await writer.record_standing_rule(rule, active=False)

    fact = await pool.fetchrow(
        "SELECT content, metadata FROM facts WHERE predicate = $1 AND validity = 'active'",
        "decision:standing_rule",
    )

    assert fact is not None
    assert fact["metadata"] == {
        "rule_id": str(rule.id),
        "tool_name": "notify",
        "arg_constraints": rule.arg_constraints,
        "state": "revoked",
    }


async def test_executor_writes_tally_only_after_an_execution_outcome(pool) -> None:
    """The universal executor seam writes the approved terminal outcome."""
    entity_id = await pool.fetchval(
        "INSERT INTO public.entities (canonical_name, entity_type) VALUES ($1, $2) RETURNING id",
        "Avery Example",
        "person",
    )
    action = _action(entity_id)
    await pool.execute(
        "INSERT INTO pending_actions (id, tool_name, tool_args, status, requested_at) "
        "VALUES ($1, $2, $3, $4, $5)",
        action.id,
        action.tool_name,
        action.tool_args,
        ActionStatus.APPROVED.value,
        action.requested_at,
    )

    result = await execute_approved_action(
        pool=pool,
        action_id=action.id,
        tool_name=action.tool_name,
        tool_args=action.tool_args,
        tool_fn=lambda **_kwargs: {"ok": True},
        decision_memory_writer=_writer(pool),
    )

    assert result.success is True
    tally = await pool.fetchrow(
        "SELECT metadata FROM facts WHERE predicate = $1 AND validity = 'active'",
        "decision:approval_tally",
    )
    assert tally is not None
    assert tally["metadata"]["approve_count"] == 1
    assert tally["metadata"]["last_decision"] == "approved"


async def test_operations_rejection_and_rule_changes_write_memory_facts(pool) -> None:
    """REST/shared operations preserve the same writeback contract as MCP tools."""
    entity_id = await pool.fetchval(
        "INSERT INTO public.entities (canonical_name, entity_type) VALUES ($1, $2) RETURNING id",
        "Avery Example",
        "person",
    )
    action = _action(entity_id)
    await pool.execute(
        "INSERT INTO pending_actions (id, tool_name, tool_args, status, requested_at) "
        "VALUES ($1, $2, $3, $4, $5)",
        action.id,
        action.tool_name,
        action.tool_args,
        ActionStatus.PENDING.value,
        action.requested_at,
    )
    writer = _writer(pool)

    rejected = await operations.reject_action(
        pool,
        action_id=str(action.id),
        decision_memory_writer=writer,
    )
    approved_action = _action(entity_id)
    await pool.execute(
        "INSERT INTO pending_actions (id, tool_name, tool_args, status, requested_at) "
        "VALUES ($1, $2, $3, $4, $5)",
        approved_action.id,
        approved_action.tool_name,
        approved_action.tool_args,
        ActionStatus.APPROVED.value,
        approved_action.requested_at,
    )
    executed = await operations.mark_executed(
        pool,
        action_id=str(approved_action.id),
        execution_result={"success": True},
        decision_memory_writer=writer,
    )
    created = await operations.create_approval_rule(
        pool,
        tool_name="notify",
        arg_constraints={"recipient": {"type": "exact", "value": "123456"}},
        description="Notify Avery over Telegram",
        decision_memory_writer=writer,
    )
    revoked = await operations.revoke_approval_rule(
        pool,
        rule_id=created["id"],
        decision_memory_writer=writer,
    )

    assert rejected["status"] == "rejected"
    assert executed["status"] == "executed"
    assert revoked["active"] is False
    tally = await pool.fetchrow(
        "SELECT metadata FROM facts WHERE predicate = $1 AND validity = 'active'",
        "decision:approval_tally",
    )
    standing_rule = await pool.fetchrow(
        "SELECT metadata FROM facts WHERE predicate = $1 AND validity = 'active'",
        "decision:standing_rule",
    )
    assert tally is not None
    assert tally["metadata"]["reject_count"] == 1
    assert tally["metadata"]["approve_count"] == 1
    assert standing_rule is not None
    assert standing_rule["metadata"]["state"] == "revoked"
