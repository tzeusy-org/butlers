"""A Stop-cancelled goal effect fences its owner prompt as ``action_cancelled`` (bu-m1u7zy.10).

approvals_022 admits the code in the delivery reason CHECKs; the canonical
transition and the recovery sweep write it for ``cancelled`` actions, and
``action_rejected`` keeps meaning an owner rejection.
"""

from __future__ import annotations

import importlib
import shutil
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import asyncpg
import pytest

from butlers.core.approval_delivery_worker import SAFE_REASON_CODES
from butlers.modules.approvals import delivery_lifecycle
from butlers.modules.approvals.delivery_lifecycle import transition_pending_action
from butlers.modules.approvals.delivery_recovery import ApprovalDeliveryRepository
from butlers.modules.approvals.goal_park import cancel_goal_effects
from butlers.modules.approvals.models import ActionStatus
from butlers.modules.approvals.park import park_pending_action

_pg = pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available")


_MIGRATION = "butlers.modules.approvals.migrations.022_action_cancelled_reason"


@pytest.mark.unit
def test_cancelled_actions_have_their_own_fence_reason():
    reasons = delivery_lifecycle._REASON_FOR_STATUS
    assert reasons[ActionStatus.CANCELLED] == "action_cancelled"
    assert reasons[ActionStatus.REJECTED] == "action_rejected"
    assert "action_cancelled" in SAFE_REASON_CODES  # observed, not relabelled "invalid"


@pytest.mark.unit
def test_approvals_022_follows_the_goal_lineage_chain():
    migration = importlib.import_module(_MIGRATION)
    assert (migration.revision, migration.down_revision) == ("approvals_022", "approvals_021")


def _downgrade_sql(monkeypatch) -> str:
    migration = importlib.import_module(_MIGRATION)
    statements: list[str] = []
    monkeypatch.setattr(migration, "op", SimpleNamespace(execute=statements.append))
    migration.downgrade()
    [sql] = statements
    return sql


@pytest.fixture
async def approvals_pool(db):
    pool = await db("core", "approvals")
    await pool.execute(
        "UPDATE approval_delivery_rollout SET admission_enabled = true WHERE singleton"
    )
    return pool


async def _park(pool, goal_id: uuid.UUID | None = None) -> uuid.UUID:
    now = datetime.now(UTC)
    action_id = uuid.uuid4()
    await park_pending_action(
        pool,
        action_id=action_id,
        tool_name="email_send_message",
        tool_args={"to": [f"{action_id.hex}@example.com"], "subject": "x", "body": "y"},
        agent_summary="goal effect",
        requested_at=now,
        expires_at=now + timedelta(hours=48),
        origin_butler="messenger",
    )
    if goal_id is not None:
        await pool.execute(
            "UPDATE pending_actions SET goal_id = $2, goal_invocation_id = $3, goal_depth = 0, "
            "goal_exposed = false, goal_origin = 'owner' WHERE id = $1",
            action_id,
            goal_id,
            uuid.uuid4(),
        )
    return action_id


async def _fence_reasons(pool, action_id: uuid.UUID) -> set[str | None]:
    rows = await pool.fetch(
        "SELECT p.last_reason_code FROM approval_delivery_presentations p "
        "JOIN approval_delivery_intents i ON i.id = p.intent_id WHERE i.action_id = $1",
        action_id,
    )
    return {row["last_reason_code"] for row in rows}


@_pg
@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_stop_fences_the_prompt_as_cancelled_and_rejection_is_unchanged(approvals_pool):
    goal_id = uuid.uuid4()
    cancelled = await _park(approvals_pool, goal_id)
    rejected = await _park(approvals_pool)
    assert await _fence_reasons(approvals_pool, cancelled) == {None}  # an open prompt

    assert await cancel_goal_effects(approvals_pool, goal_id, [cancelled]) == [cancelled]
    await transition_pending_action(
        approvals_pool,
        action_id=rejected,
        target_status=ActionStatus.REJECTED,
        decided_by="owner",
        event_actor="owner",
        event_reason="owner rejected",
    )

    assert await _fence_reasons(approvals_pool, cancelled) == {"action_cancelled"}
    assert await _fence_reasons(approvals_pool, rejected) == {"action_rejected"}
    events = await approvals_pool.fetch(
        "SELECT event_type FROM approval_events WHERE action_id = $1", cancelled
    )
    assert "action_cancelled" in {row["event_type"] for row in events}


@_pg
@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_delivery_reason_checks_admit_the_new_code_and_nothing_else(approvals_pool):
    action_id = await _park(approvals_pool)
    for table, column in (
        ("approval_delivery_presentations", "last_reason_code"),
        ("approval_delivery_attempts", "reason_code"),
    ):
        check = await approvals_pool.fetchval(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conrelid = $1::regclass AND conname = $2",
            table,
            f"{table}_{column}_check",
        )
        assert "action_cancelled" in check and "action_rejected" in check
    await approvals_pool.execute(
        "UPDATE approval_delivery_presentations SET last_reason_code = 'action_cancelled'"
    )
    with pytest.raises(asyncpg.CheckViolationError):
        await approvals_pool.execute(
            "UPDATE approval_delivery_presentations SET last_reason_code = 'action_withdrawn'"
        )
    assert await _fence_reasons(approvals_pool, action_id) == {"action_cancelled"}


@_pg
@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_the_recovery_sweep_fences_a_cancelled_action_as_cancelled(approvals_pool):
    """An action that left ``pending`` without fencing its prompt is fenced by the sweep."""
    cancelled, rejected = await _park(approvals_pool), await _park(approvals_pool)
    for action_id, status in ((cancelled, "cancelled"), (rejected, "rejected")):
        # The domain status moved but the presentation stayed open (e.g. a crash
        # between the two writes); recovery must give it the right reason.
        await approvals_pool.execute(
            "UPDATE pending_actions SET status = $2 WHERE id = $1", action_id, status
        )
        assert await _fence_reasons(approvals_pool, action_id) == {None}

    assert await ApprovalDeliveryRepository(approvals_pool).cancel_ineligible_presentations() >= 2
    assert await _fence_reasons(approvals_pool, cancelled) == {"action_cancelled"}
    assert await _fence_reasons(approvals_pool, rejected) == {"action_rejected"}


@_pg
@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_approvals_022_downgrade_refuses_while_a_cancelled_fence_exists(
    approvals_pool, monkeypatch
):
    downgrade = _downgrade_sql(monkeypatch)
    action_id = await _park(approvals_pool)
    async with approvals_pool.acquire() as connection:
        refused = connection.transaction()
        await refused.start()
        try:
            await connection.execute(
                "UPDATE approval_delivery_presentations p SET last_reason_code = 'action_cancelled' "
                "FROM approval_delivery_intents i WHERE i.id = p.intent_id AND i.action_id = $1",
                action_id,
            )
            with pytest.raises(asyncpg.RaiseError, match="cancelled fences exist"):
                await connection.execute(downgrade)
        finally:
            await refused.rollback()

        # Positive control: with no cancelled fence the downgrade narrows the CHECK.
        narrowed = connection.transaction()
        await narrowed.start()
        try:
            await connection.execute(
                "UPDATE approval_delivery_presentations SET last_reason_code = 'action_rejected' "
                "WHERE last_reason_code = 'action_cancelled'"
            )
            assert not await connection.fetchval(
                "SELECT count(*) FROM approval_delivery_attempts "
                "WHERE reason_code = 'action_cancelled'"
            )
            await connection.execute(downgrade)
            with pytest.raises(asyncpg.CheckViolationError):
                await connection.execute(
                    "UPDATE approval_delivery_presentations SET last_reason_code = "
                    "'action_cancelled'"
                )
        finally:
            await narrowed.rollback()
