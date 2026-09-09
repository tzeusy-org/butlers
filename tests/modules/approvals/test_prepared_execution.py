"""Real-Postgres coverage for approving an origin='prepared' pending action.

execute_approved_action is the universal executor seam every approval path
replays through (module.py's manual approve/dispatch, gate.py's auto-approve).
This exercises it directly against a real origin='prepared' row: approval must
replay the exact stored tool_args and land execution_result -- no different
from an ordinary gated action's approval, which is the point (prepared actions
need no bespoke execution path, only a bespoke, push-free park path).
"""

from __future__ import annotations

import shutil
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import asyncpg
import pytest

from butlers.core.attention_ledger import record_attention_event
from butlers.db import register_jsonb_codec
from butlers.modules.approvals.executor import execute_approved_action
from butlers.modules.approvals.models import ActionStatus
from butlers.modules.approvals.park import park_prepared_action
from butlers.testing.migration import create_migrated_test_db, migration_db_name

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "approvals"],
    )


@pytest.fixture
async def pool(migrated_db_url: str):
    db_pool = await asyncpg.create_pool(
        migrated_db_url,
        min_size=1,
        max_size=3,
        init=register_jsonb_codec,
    )
    await db_pool.execute("TRUNCATE approval_events, pending_actions CASCADE")
    yield db_pool
    await db_pool.close()


async def _park_and_approve(pool, *, action_id: uuid.UUID, tool_args: dict) -> None:
    now = datetime.now(UTC)
    await park_prepared_action(
        pool,
        action_id=action_id,
        tool_name="notify",
        tool_args=tool_args,
        agent_summary="Prepared reach-out draft",
        requested_at=now,
        expires_at=now + timedelta(days=3),
        deduplication_key=f"relationship:prepared-reach-out:{action_id}",
    )
    await pool.execute(
        "UPDATE pending_actions SET status = $1, decided_by = $2, decided_at = $3 "
        "WHERE id = $4",
        ActionStatus.APPROVED.value,
        "human:owner",
        now,
        action_id,
    )


async def test_approve_replays_exact_stored_args_and_writes_execution_result(pool) -> None:
    action_id = uuid.uuid4()
    entity_id = str(uuid.uuid4())
    tool_args = {"entity_id": entity_id, "message": "It's been a while!", "intent": "send"}
    await _park_and_approve(pool, action_id=action_id, tool_args=tool_args)

    tool_fn = AsyncMock(return_value={"status": "sent"})

    result = await execute_approved_action(
        pool=pool,
        action_id=action_id,
        tool_name="notify",
        tool_args=tool_args,
        tool_fn=tool_fn,
        origin_butler="relationship",
    )

    assert result.success is True
    tool_fn.assert_awaited_once_with(**tool_args)

    row = await pool.fetchrow(
        "SELECT status, execution_result FROM pending_actions WHERE id = $1", action_id
    )
    assert row["status"] == ActionStatus.EXECUTED.value
    assert row["execution_result"]["success"] is True
    assert row["execution_result"]["result"] == {"status": "sent"}


async def test_execution_failure_on_a_prepared_action_records_attention_ledger_row(
    pool,
) -> None:
    """A prepared action has no approval_rule_id, so demotion never fires --
    the attention-ledger row is the analogous failure hook (bu-2jtfw.11)."""
    action_id = uuid.uuid4()
    tool_args = {"entity_id": str(uuid.uuid4()), "message": "hi", "intent": "send"}
    await _park_and_approve(pool, action_id=action_id, tool_args=tool_args)

    async def _failing_tool(**_kwargs: object) -> dict:
        raise RuntimeError("delivery channel unavailable")

    result = await execute_approved_action(
        pool=pool,
        action_id=action_id,
        tool_name="notify",
        tool_args=tool_args,
        tool_fn=_failing_tool,
        origin_butler="relationship",
    )

    assert result.success is False

    ledger_row = await pool.fetchrow(
        "SELECT source, outcome, origin_butler, notification_ref, reason "
        "FROM public.attention_ledger WHERE notification_ref = $1",
        str(action_id),
    )
    assert ledger_row is not None
    assert ledger_row["source"] == "approvals"
    assert ledger_row["outcome"] == "failed"
    assert ledger_row["origin_butler"] == "relationship"
    assert "delivery channel unavailable" in ledger_row["reason"]

    # Sanity: record_attention_event itself accepts the new source value
    # (guards against a future VALID_SOURCES regression silently dropping rows).
    written_id = await record_attention_event(
        pool, origin_butler="relationship", source="approvals", outcome="failed"
    )
    assert written_id is not None
