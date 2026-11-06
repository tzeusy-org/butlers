"""The ineligible-presentation sweep never waits on a row, so it is in no lock cycle (bu-dhloi1.75).

It runs outside the action or cohort lock that serializes every other multi-row writer of these
rows, and it updates several rows in one statement. Skipping locked rows is what keeps it out of a cycle.
"""

from __future__ import annotations

import asyncio
import shutil
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg
import pytest

from butlers.modules.approvals.delivery_lifecycle import defer_pending_action
from butlers.modules.approvals.delivery_recovery import ApprovalDeliveryRepository
from tests.integration.test_owner_delivery_ledger import _park

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]

_NEVER_WAITS_S = 5


@pytest.fixture
async def sweep_pool(db) -> asyncpg.Pool:
    pool = await db("core", "approvals", max_size=8)
    await pool.execute(
        "UPDATE public.approvals_policy SET quiet_start_hour = NULL, "
        "quiet_end_hour = NULL, timezone = 'UTC' WHERE id = 1"
    )
    await pool.execute(
        "UPDATE approval_delivery_rollout SET admission_enabled = true, worker_enabled = false"
    )
    return pool


@asynccontextmanager
async def _holding(pool: asyncpg.Pool, presentation_id: uuid.UUID) -> AsyncIterator[None]:
    async with pool.acquire() as gate, gate.transaction():
        await gate.execute(
            "SELECT 1 FROM approval_delivery_presentations WHERE id = $1 FOR UPDATE",
            presentation_id,
        )
        yield


async def _expire(pool: asyncpg.Pool, action_id: uuid.UUID) -> None:
    await pool.execute(
        "UPDATE pending_actions SET expires_at = clock_timestamp() + interval '50 milliseconds' "
        "WHERE id = $1",
        action_id,
    )
    await asyncio.sleep(0.1)


async def _state(pool: asyncpg.Pool, presentation_id: uuid.UUID) -> str:
    return await pool.fetchval(
        "SELECT state FROM approval_delivery_presentations WHERE id = $1", presentation_id
    )


async def _sweep(repository: ApprovalDeliveryRepository) -> int:
    return await asyncio.wait_for(repository.cancel_ineligible_presentations(), _NEVER_WAITS_S)


async def test_sweep_skips_a_held_row_of_an_intent_and_cancels_the_other(sweep_pool) -> None:
    """An in-flight card plus its deferred successor: two live rows the sweep must take."""
    action_id = await _park(sweep_pool)
    repository = ApprovalDeliveryRepository(sweep_pool)
    claim = await repository.claim_next()
    assert claim is not None and await repository.mark_handoff_started(claim) is True
    deferred = await defer_pending_action(sweep_pool, action_id=action_id, hours=1, actor="owner")
    assert deferred.changed
    rows = await sweep_pool.fetch(
        "SELECT id, state FROM approval_delivery_presentations ORDER BY presentation_generation"
    )
    assert [r["state"] for r in rows] == ["handoff_started", "ready"]
    await _expire(sweep_pool, action_id)

    free, held = rows[0]["id"], rows[1]["id"]
    async with _holding(sweep_pool, held):
        assert await _sweep(repository) == 1
        assert await _state(sweep_pool, free) == "cancelled"
    assert await _state(sweep_pool, held) == "ready"

    # The next tick picks up what was skipped, with the reason computed as before.
    assert await _sweep(repository) == 1
    row = await sweep_pool.fetchrow(
        "SELECT state, last_reason_code FROM approval_delivery_presentations WHERE id = $1",
        held,
    )
    assert dict(row) == {"state": "cancelled", "last_reason_code": "action_expired"}


async def test_sweep_skips_a_held_cohort_card_until_the_next_tick(sweep_pool) -> None:
    for ordinal in range(3):
        await _park(sweep_pool, ordinal=ordinal)
    anchor = await _park(sweep_pool, ordinal=3)
    cohort_card = await sweep_pool.fetchval(
        "SELECT id FROM approval_delivery_presentations WHERE cohort_id IS NOT NULL"
    )
    assert cohort_card is not None
    await _expire(sweep_pool, anchor)
    repository = ApprovalDeliveryRepository(sweep_pool)

    async with _holding(sweep_pool, cohort_card):
        assert await _sweep(repository) == 0
    assert await _state(sweep_pool, cohort_card) == "ready"

    assert await _sweep(repository) == 1
    row = await sweep_pool.fetchrow(
        "SELECT state, last_reason_code FROM approval_delivery_presentations WHERE id = $1",
        cohort_card,
    )
    assert dict(row) == {"state": "cancelled", "last_reason_code": "cohort_empty"}


@pytest.mark.parametrize(
    "status, reason",
    [
        ("approved", "action_approved"),
        ("executed", "action_approved"),
        ("rejected", "action_rejected"),
        ("cancelled", "action_cancelled"),
        ("expired", "action_expired"),
        ("abandoned", "action_abandoned"),
    ],
)
async def test_sweep_still_gives_each_terminal_status_its_reason(
    sweep_pool, status: str, reason: str
) -> None:
    """Moving the selection into a locking read must not change what is cancelled, or why."""
    action_id = await _park(sweep_pool)
    await sweep_pool.execute(
        "UPDATE pending_actions SET status = $2 WHERE id = $1", action_id, status
    )
    repository = ApprovalDeliveryRepository(sweep_pool)

    assert await _sweep(repository) == 1

    row = await sweep_pool.fetchrow(
        "SELECT state, last_reason_code, claim_token, next_attempt_at "
        "FROM approval_delivery_presentations"
    )
    assert dict(row) == {
        "state": "cancelled",
        "last_reason_code": reason,
        "claim_token": None,
        "next_attempt_at": None,
    }
