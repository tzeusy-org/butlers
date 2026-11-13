"""sw_073: owner decision receipts are durable, one per effect, and only they decide a set.

Real PostgreSQL. The runtime role may read and insert receipts and their items, never edit or
delete one. An effect carries at most one approving or rejecting owner decision; an item left
unticked (``left``) decides nothing, so it may appear in later receipts. A set version reaches
``decided`` only when a receipt names that exact version. A goal that ends cancels its open set.
The downgrade refuses while a receipt exists.
"""

import asyncio
from uuid import uuid4

import asyncpg
import pytest

from alembic import command
from butlers.migrations import _build_alembic_config
from butlers.testing.migration import create_migrated_test_db, migration_db_name
from tests.migrations.test_sw072_decision_sets import _goal, _insert_set, _refused
from tests.switchboard.goal_ledger_fixtures import pools

pytestmark = pytest.mark.integration

DIGEST = "a" * 64


def _ledger(container):
    return create_migrated_test_db(
        container,
        migration_db_name(),
        ["core", "switchboard"],
        schemas={"core": "switchboard", "switchboard": "switchboard"},
        revisions={"switchboard": "sw_073"},  # pinned-revision: owns the owner decision tables
    )


async def _receipt(runtime, goal, items, *, set_id=None, version=None, decision="approve"):
    receipt = uuid4()
    async with runtime.acquire() as connection, connection.transaction():
        await connection.execute(
            "INSERT INTO switchboard.owner_decisions (id, goal_id, set_id, set_version, "
            "decision, decided_by, via) VALUES ($1, $2, $3, $4, $5, 'owner', 'test')",
            receipt,
            goal,
            set_id,
            version,
            decision,
        )
        for effect_id, outcome in items:
            await connection.execute(
                "INSERT INTO switchboard.owner_decision_items (decision_id, goal_id, effect_id, "
                "approval_ref, args_digest, outcome) VALUES ($1, $2, $3, $4, $5, $6)",
                receipt,
                goal,
                effect_id,
                str(effect_id),
                DIGEST,
                outcome,
            )
    return receipt


async def _exercise(url):
    goal, lead, plan, effects = await _goal(url)
    first, second = effects
    async with pools(url) as (owner, runtime):
        set_id = await _insert_set(runtime, goal, lead, plan, effects)
        decide = "UPDATE switchboard.decision_set_versions SET state = 'decided' WHERE set_id = $1"
        # Without a receipt naming this version, nothing decides it.
        assert await _refused(runtime, decide, set_id)
        # A single-item receipt (no set) is not a decision of the set either.
        await _receipt(runtime, goal, [(second, "left")])
        assert await _refused(runtime, decide, set_id)

        await _receipt(
            runtime, goal, [(first, "approved"), (second, "left")], set_id=set_id, version=1
        )
        await runtime.execute(decide, set_id)
        assert await _refused(
            runtime, "UPDATE switchboard.decision_set_versions SET state = 'open'"
        )

        # One approving or rejecting owner decision per effect, ever.
        with pytest.raises(asyncpg.UniqueViolationError):
            await _receipt(runtime, goal, [(first, "rejected")])
        # The unticked item was left for its own later decision.
        await _receipt(runtime, goal, [(second, "rejected")], decision="reject")
        # A set version carries at most one receipt.
        with pytest.raises(asyncpg.UniqueViolationError):
            await _receipt(runtime, goal, [], set_id=set_id, version=1)
        # A half-named set and an unknown decision or outcome are refused.
        with pytest.raises(asyncpg.CheckViolationError):
            await _receipt(runtime, goal, [], set_id=set_id)
        with pytest.raises(asyncpg.CheckViolationError):
            await _receipt(runtime, goal, [], decision="maybe")
        with pytest.raises(asyncpg.CheckViolationError):
            await _receipt(runtime, goal, [(uuid4(), "edited")])

        # A goal that ends cancels its open set in the same transaction.
        other = await _insert_set(runtime, goal, lead, plan, effects)
        await runtime.execute(
            "UPDATE switchboard.goals SET status = 'failed', updated_at = now() WHERE id = $1",
            goal,
        )
        state = await runtime.fetchval(
            "SELECT state FROM switchboard.decision_set_versions WHERE set_id = $1", other
        )
        assert state == "cancelled"

        for pool in (runtime, owner):
            for query in (
                "UPDATE switchboard.owner_decisions SET decided_by = 'someone'",
                "UPDATE switchboard.owner_decision_items SET outcome = 'left'",
                "DELETE FROM switchboard.owner_decision_items",
                "DELETE FROM switchboard.owner_decisions",
                "TRUNCATE switchboard.owner_decisions CASCADE",
            ):
                assert await _refused(pool, query), query


def test_owner_decisions_are_durable_and_only_they_decide_a_set(postgres_container):
    url = _ledger(postgres_container)
    asyncio.run(_exercise(url))

    config = _build_alembic_config(url, ["switchboard"], target_schema="switchboard")
    with pytest.raises(Exception, match="Cannot discard owner decisions"):
        command.downgrade(config, "switchboard@sw_072")  # pinned-revision: sw_073 fence


def test_the_empty_tables_round_trip(postgres_container):
    url = _ledger(postgres_container)
    config = _build_alembic_config(url, ["switchboard"], target_schema="switchboard")
    command.downgrade(config, "switchboard@sw_072")  # pinned-revision: sw_073 removal
    command.upgrade(config, "switchboard@sw_073")  # pinned-revision: sw_073 reapply
