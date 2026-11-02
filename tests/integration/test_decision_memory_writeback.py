"""Real-Postgres coverage for deterministic approval decision-memory writeback."""

from __future__ import annotations

import asyncio
import shutil
import uuid
from datetime import UTC, datetime

import asyncpg
import pytest

from butlers.identity import ResolvedContact
from butlers.modules.approvals import operations
from butlers.modules.approvals.autonomy_tracker import FINGERPRINT_VERSION
from butlers.modules.approvals.decision_memory import DecisionMemoryWriter, memory_pool_for_schema
from butlers.modules.approvals.executor import execute_approved_action
from butlers.modules.approvals.models import ActionStatus, ApprovalRule, PendingAction
from butlers.modules.base import ToolMeta
from butlers.modules.memory.tools.context import memory_context

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


@pytest.fixture(autouse=True)
def admitted_standing_rule_boundary(monkeypatch):
    """Retain this owner's non-auth invariants after explicit D13 admission.

    No factor/nonce/role evidence is claimed here; the dedicated migrated
    dashboard-stepup owner executes that authority with real signed assertions.
    """
    from tests.approval_stepup_helpers import admit_rule_boundary_for_non_auth_owner

    admit_rule_boundary_for_non_auth_owner(monkeypatch)


@pytest.fixture
async def home_pool(db) -> asyncpg.Pool:
    """The exact core, memory (as home_mem), and approvals schemas."""
    return await db("core", "memory", "approvals", schemas={"memory": "home_mem"})


def _writer(pool: asyncpg.Pool) -> DecisionMemoryWriter:
    return DecisionMemoryWriter(
        butler_name="home",
        memory_pool_provider=lambda: memory_pool_for_schema(pool, "home_mem"),
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


async def test_terminal_decisions_upsert_entity_linked_tally_and_recall_it(home_pool) -> None:
    """A decision tally is own-store, entity-linked, versioned, and recallable."""
    entity_id = await home_pool.fetchval(
        "INSERT INTO public.entities (canonical_name, entity_type) VALUES ($1, $2) RETURNING id",
        "Avery Example",
        "person",
    )
    writer = _writer(home_pool)

    approved = _action(entity_id)
    rejected = _action(entity_id)
    await writer.record_terminal_decision(approved, "approved")
    await writer.record_terminal_decision(rejected, "rejected")

    fact = await home_pool.fetchrow(
        "SELECT subject, predicate, content, scope, metadata, entity_id "
        "FROM home_mem.facts WHERE predicate = $1 AND validity = 'active'",
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
        memory_pool_for_schema(home_pool, "home_mem"),
        _EmbeddingEngine(),
        "Should I notify Avery again?",
        butler="home",
    )
    assert "decision:approval_tally" in context


async def test_concurrent_terminal_decisions_do_not_lose_tally_updates(home_pool) -> None:
    """The per-pattern tally lock preserves both simultaneous terminal outcomes."""
    entity_id = await home_pool.fetchval(
        "INSERT INTO public.entities (canonical_name, entity_type) VALUES ($1, $2) RETURNING id",
        "Avery Example",
        "person",
    )
    writer = _writer(home_pool)

    await asyncio.gather(
        writer.record_terminal_decision(_action(entity_id), "approved"),
        writer.record_terminal_decision(_action(entity_id), "approved"),
    )

    tally = await home_pool.fetchrow(
        "SELECT metadata FROM home_mem.facts WHERE predicate = $1 AND validity = 'active'",
        "decision:approval_tally",
    )
    assert tally is not None
    assert tally["metadata"]["approve_count"] == 2
    assert tally["metadata"]["reject_count"] == 0


async def test_owner_definer_resolution_keeps_tally_entity_linked(home_pool, monkeypatch) -> None:
    """Writeback shares the gate's owner-only fallback under schema isolation."""
    entity_id = await home_pool.fetchval(
        "INSERT INTO public.entities (canonical_name, entity_type) VALUES ($1, $2) RETURNING id",
        "Owner Example",
        "person",
    )

    async def _owner_fallback(_pool, channel_type: str, channel_value: str):
        assert (channel_type, channel_value) == ("telegram", "123456")
        return (
            ResolvedContact(
                name="Owner Example",
                roles=["owner"],
                entity_id=entity_id,
            ),
            True,
        )

    monkeypatch.setattr(
        "butlers.identity.resolve_owner_channel_via_definer",
        _owner_fallback,
    )
    action = PendingAction(
        id=uuid.uuid4(),
        tool_name="notify",
        tool_args={"channel": "telegram", "recipient": "123456", "text": "Hello"},
        status=ActionStatus.REJECTED,
        requested_at=datetime.now(UTC),
    )

    await _writer(home_pool).record_terminal_decision(action, "rejected")

    linked_entity_id = await home_pool.fetchval(
        "SELECT entity_id FROM home_mem.facts WHERE predicate = $1 AND validity = 'active'",
        "decision:approval_tally",
    )
    assert linked_entity_id == entity_id


async def test_standing_rule_fact_is_updated_on_revocation(home_pool) -> None:
    """Rule grants are descriptive memory facts, not executable policy."""
    writer = _writer(home_pool)
    rule = ApprovalRule(
        id=uuid.uuid4(),
        tool_name="notify",
        arg_constraints={"recipient": {"type": "exact", "value": "123456"}},
        description="Notify Avery over Telegram",
        created_at=datetime.now(UTC),
    )

    await writer.record_standing_rule(rule, active=True)
    await writer.record_standing_rule(rule, active=False)

    fact = await home_pool.fetchrow(
        "SELECT content, metadata FROM home_mem.facts WHERE predicate = $1 AND validity = 'active'",
        "decision:standing_rule",
    )

    assert fact is not None
    assert fact["metadata"] == {
        "rule_id": str(rule.id),
        "tool_name": "notify",
        "arg_constraints": rule.arg_constraints,
        "state": "revoked",
    }


async def test_executor_writes_tally_only_after_an_execution_outcome(home_pool) -> None:
    """The universal executor seam writes the approved terminal outcome."""
    entity_id = await home_pool.fetchval(
        "INSERT INTO public.entities (canonical_name, entity_type) VALUES ($1, $2) RETURNING id",
        "Avery Example",
        "person",
    )
    action = _action(entity_id)
    await home_pool.execute(
        "INSERT INTO pending_actions (id, tool_name, tool_args, status, requested_at) "
        "VALUES ($1, $2, $3, $4, $5)",
        action.id,
        action.tool_name,
        action.tool_args,
        ActionStatus.APPROVED.value,
        action.requested_at,
    )

    result = await execute_approved_action(
        home_pool=home_pool,
        action_id=action.id,
        tool_name=action.tool_name,
        tool_args=action.tool_args,
        tool_fn=lambda **_kwargs: {"ok": True},
        decision_memory_writer=_writer(home_pool),
    )

    assert result.success is True
    tally = await home_pool.fetchrow(
        "SELECT metadata FROM home_mem.facts WHERE predicate = $1 AND validity = 'active'",
        "decision:approval_tally",
    )
    assert tally is not None
    assert tally["metadata"]["approve_count"] == 1
    assert tally["metadata"]["last_decision"] == "approved"


async def test_operations_rejection_and_rule_changes_write_memory_facts(home_pool) -> None:
    """REST/shared operations preserve the same writeback contract as MCP tools."""
    entity_id = await home_pool.fetchval(
        "INSERT INTO public.entities (canonical_name, entity_type) VALUES ($1, $2) RETURNING id",
        "Avery Example",
        "person",
    )
    action = _action(entity_id)
    await home_pool.execute(
        "INSERT INTO pending_actions (id, tool_name, tool_args, status, requested_at) "
        "VALUES ($1, $2, $3, $4, $5)",
        action.id,
        action.tool_name,
        action.tool_args,
        ActionStatus.PENDING.value,
        action.requested_at,
    )
    writer = _writer(home_pool)

    rejected = await operations.reject_action(
        home_pool,
        action_id=str(action.id),
        decision_memory_writer=writer,
    )
    approved_action = _action(entity_id)
    await home_pool.execute(
        "INSERT INTO pending_actions (id, tool_name, tool_args, status, requested_at) "
        "VALUES ($1, $2, $3, $4, $5)",
        approved_action.id,
        approved_action.tool_name,
        approved_action.tool_args,
        ActionStatus.APPROVED.value,
        approved_action.requested_at,
    )
    executed = await operations.mark_executed(
        home_pool,
        action_id=str(approved_action.id),
        execution_result={"success": True},
        decision_memory_writer=writer,
    )
    created = await operations.create_approval_rule(
        home_pool,
        tool_name="notify",
        arg_constraints={"recipient": {"type": "exact", "value": "123456"}},
        description="Notify Avery over Telegram",
        decision_memory_writer=writer,
    )
    revoked = await operations.revoke_approval_rule(
        home_pool,
        rule_id=created["id"],
        decision_memory_writer=writer,
    )

    assert rejected["status"] == "rejected"
    assert executed["status"] == "executed"
    assert revoked["active"] is False
    tally = await home_pool.fetchrow(
        "SELECT metadata FROM home_mem.facts WHERE predicate = $1 AND validity = 'active'",
        "decision:approval_tally",
    )
    standing_rule = await home_pool.fetchrow(
        "SELECT metadata FROM home_mem.facts WHERE predicate = $1 AND validity = 'active'",
        "decision:standing_rule",
    )
    assert tally is not None
    assert tally["metadata"]["reject_count"] == 1
    assert tally["metadata"]["approve_count"] == 1
    assert standing_rule is not None
    assert standing_rule["metadata"]["state"] == "revoked"
