"""sw_074: a decision-set wait binds one exact version, and a moved goal supersedes its open set.

Real PostgreSQL. A ``decision_set`` envelope must name a set version of its own goal, and only that
kind may name one. A changed plan version or a later lead epoch supersedes the goal's open set in
the same transaction, whichever path moved the goal. The downgrade refuses while a decision-set
wait exists.
"""

import asyncio
from uuid import uuid4

import asyncpg
import pytest

from alembic import command
from butlers.core.goal_admission import digest
from butlers.core.goal_waiting import WaitLedger
from butlers.migrations import _build_alembic_config
from butlers.testing.migration import create_migrated_test_db, migration_db_name
from tests.migrations.test_sw072_decision_sets import _goal, _insert_set
from tests.switchboard.goal_ledger_fixtures import pools

pytestmark = pytest.mark.integration


def _ledger(container):
    return create_migrated_test_db(
        container,
        migration_db_name(),
        ["core", "switchboard"],
        schemas={"core": "switchboard", "switchboard": "switchboard"},
        revisions={"switchboard": "sw_074"},  # pinned-revision: owns the decision-set wait
    )


async def _state(pool, set_id, version=1):
    return await pool.fetchval(
        "SELECT state FROM switchboard.decision_set_versions WHERE set_id = $1 AND version = $2",
        set_id,
        version,
    )


async def _lead_at(owner, goal, epoch):
    """A lead invocation at ``epoch``: sw_063 derives the goal's epoch record from it."""
    ceiling = await owner.fetchrow(
        "SELECT ceiling, exposed FROM switchboard.goals WHERE id = $1", goal
    )
    await owner.execute(
        "INSERT INTO switchboard.invocations (id, goal_id, depth, role, lead_epoch, status, "
        "grant_snapshot, grant_digest, exposed, max_turns, max_tokens) "
        "VALUES ($1, $2, 0, 'lead', $3, 'returned', $4::jsonb, $5, $6, 5, 500)",
        uuid4(),
        goal,
        epoch,
        ceiling["ceiling"],
        digest(ceiling["ceiling"]),
        ceiling["exposed"],
    )


async def _envelope(runtime, goal, kind, set_id=None, version=None):
    async with runtime.acquire() as connection, connection.transaction():
        await connection.execute(
            "INSERT INTO switchboard.wait_envelopes (id, goal_id, kind, original_epoch, "
            "plan_version, plan_digest, predicate, predicate_digest, source_set, "
            "permitted_sources, grant_snapshot, grant_digest, label_version, label_instr, "
            "label_data, label_sens, deadline, max_wakes, decision_set_id, decision_set_version) "
            "SELECT $1, g.id, $2, 0, g.plan_version, p.digest, '{}'::jsonb, p.digest, "
            "ARRAY['effects'], ARRAY['effects'], '[]'::jsonb, p.digest, g.label_version, "
            "g.label_instr, g.label_data, g.label_sens, now() + interval '1 hour', 1, $4, $5 "
            "FROM switchboard.goals g JOIN switchboard.goal_plans p "
            "ON (p.goal_id, p.version) = (g.id, g.plan_version) WHERE g.id = $3",
            uuid4(),
            kind,
            goal,
            set_id,
            version,
        )


async def _exercise(url):
    goal, lead, plan, effects = await _goal(url)
    other, other_lead, other_plan, other_effects = await _goal(url)
    async with pools(url) as (owner, runtime):
        waited = await _insert_set(runtime, goal, lead, plan, effects)
        foreign = await _insert_set(runtime, other, other_lead, other_plan, other_effects)

        # Only a decision_set envelope names a set, and it names one version of its own goal.
        for kind, set_id, version, error in (
            ("decision_set", None, None, asyncpg.CheckViolationError),
            ("decision_set", waited, None, asyncpg.CheckViolationError),
            ("approval", waited, 1, asyncpg.CheckViolationError),
            ("conditions", waited, 1, asyncpg.CheckViolationError),
            ("decision_set", waited, 2, asyncpg.ForeignKeyViolationError),
            ("decision_set", foreign, 1, asyncpg.ForeignKeyViolationError),
        ):
            with pytest.raises(error):
                await _envelope(runtime, goal, kind, set_id, version)
        await _envelope(runtime, goal, "decision_set", waited, 1)
        # The binding is as immutable as the rest of the envelope.
        with pytest.raises(asyncpg.PostgresError, match="immutable"):
            await owner.execute(
                "UPDATE switchboard.wait_envelopes SET decision_set_version = 1, "
                "decision_set_id = $2 WHERE goal_id = $1",
                goal,
                foreign,
            )
        await owner.execute(
            "UPDATE switchboard.wait_envelopes SET state = 'cancelled' WHERE goal_id = $1", goal
        )

        # A changed plan supersedes the open set in the same transaction.
        async with runtime.acquire() as connection, connection.transaction():
            await WaitLedger(connection).append_plan(goal, 0, ["moved"], [], [])
            assert (
                await connection.fetchval(
                    "SELECT state FROM switchboard.decision_set_versions WHERE set_id = $1", waited
                )
                == "superseded"
            )
        # Re-recording the same plan moves nothing: a set under the current plan stays open.
        current = await runtime.fetchval(
            "SELECT plan_version FROM switchboard.goals WHERE id = $1", goal
        )
        kept = await _insert_set(runtime, goal, lead, current, effects)
        async with runtime.acquire() as connection, connection.transaction():
            assert await WaitLedger(connection).append_plan(goal, 0, ["moved"], [], []) == current
        assert await _state(runtime, kept) == "open"

        # A later lead epoch supersedes it too; the other goal's open set is untouched.
        await _lead_at(owner, goal, 1)
        assert await _state(runtime, kept) == "superseded"
        assert await _state(runtime, foreign) == "open"


def test_a_decision_set_wait_binds_one_version_and_a_moved_goal_supersedes_it(
    postgres_container,
):
    url = _ledger(postgres_container)
    asyncio.run(_exercise(url))

    config = _build_alembic_config(url, ["switchboard"], target_schema="switchboard")
    with pytest.raises(Exception, match="Cannot discard decision set waits"):
        command.downgrade(config, "switchboard@sw_073")  # pinned-revision: sw_074 fence


def test_the_empty_wait_round_trips(postgres_container):
    url = _ledger(postgres_container)
    config = _build_alembic_config(url, ["switchboard"], target_schema="switchboard")
    command.downgrade(config, "switchboard@sw_073")  # pinned-revision: sw_074 removal
    command.upgrade(config, "switchboard@sw_074")  # pinned-revision: sw_074 reapply
