"""sw_072: decision sets are append-only, a version changes only its state, and none is decided.

Real PostgreSQL. The runtime role may read and insert sets, versions and items, and may move an
open version to superseded, cancelled or expired. It can neither edit a version's membership
facts or an item, nor reach ``decided`` (that path arrives with the resolver), nor delete
anything. One goal holds at most one open version. The downgrade refuses while a set exists.
"""

import asyncio
from uuid import uuid4

import asyncpg
import pytest

from alembic import command
from butlers.core.goal_admission import Evidence
from butlers.core.goal_waiting import WaitLedger
from butlers.migrations import _build_alembic_config
from butlers.testing.migration import create_migrated_test_db, migration_db_name
from tests.switchboard.goal_admission_fixtures import limits
from tests.switchboard.goal_ledger_fixtures import admit, configure, effect, pools

pytestmark = pytest.mark.integration

OWNER = Evidence("message", "owner", owner=True)
DIGEST = "a" * 64


def _ledger(container):
    return create_migrated_test_db(
        container,
        migration_db_name(),
        ["core", "switchboard"],
        schemas={"core": "switchboard", "switchboard": "switchboard"},
        revisions={"switchboard": "sw_072"},  # pinned-revision: owns the decision set tables
    )


async def _goal(url):
    """A goal with a lead at epoch 0, a plan and two effects: (goal, lead, plan, effects)."""
    async with pools(url) as (owner, runtime):
        await configure(owner, limits())
        goal = (await admit(runtime, OWNER)).goal_id
        lead, first = await effect(owner, goal)
        _, second = await effect(owner, goal)
        async with owner.acquire() as connection, connection.transaction():
            plan = await WaitLedger(connection).append_plan(goal, 0, [], [], [])
        return goal, lead, plan, (first, second)


async def _insert_set(runtime, goal, lead, plan, effects, *, version=1, set_id=None):
    set_id = set_id or uuid4()
    async with runtime.acquire() as connection, connection.transaction():
        if version == 1:
            await connection.execute(
                "INSERT INTO switchboard.decision_sets (id, goal_id) VALUES ($1, $2)", set_id, goal
            )
        await connection.execute(
            "INSERT INTO switchboard.decision_set_versions (set_id, version, goal_id, epoch, "
            "plan_version, created_by, item_count, digest, expires_at) "
            "VALUES ($1, $2, $3, 0, $4, $5, $6, $7, now() + interval '1 hour')",
            set_id,
            version,
            goal,
            plan,
            lead,
            len(effects),
            DIGEST,
        )
        for position, effect_id in enumerate(effects):
            await connection.execute(
                "INSERT INTO switchboard.decision_set_items (set_id, set_version, position, "
                "goal_id, effect_id, capability, args_digest, explicit_only, item_digest) "
                "VALUES ($1, $2, $3, $4, $5, 'general__notify', 'args', false, $6)",
                set_id,
                version,
                position,
                goal,
                effect_id,
                DIGEST,
            )
    return set_id


async def _refused(pool, query, *args) -> bool:
    try:
        await pool.execute(query, *args)
    except asyncpg.PostgresError:
        return True
    return False


async def _exercise(url):
    goal, lead, plan, effects = await _goal(url)
    async with pools(url) as (owner, runtime):
        set_id = await _insert_set(runtime, goal, lead, plan, effects)
        assert await runtime.fetchval(
            "SELECT count(*) FROM switchboard.decision_set_items WHERE set_id = $1", set_id
        ) == len(effects)

        # A second open version for the same goal is refused until the first leaves open.
        with pytest.raises(asyncpg.UniqueViolationError):
            await _insert_set(runtime, goal, lead, plan, effects, version=2, set_id=set_id)

        for pool in (runtime, owner):
            for query in (
                "UPDATE switchboard.decision_sets SET created_at = now()",
                "UPDATE switchboard.decision_set_items SET args_digest = 'other'",
                "UPDATE switchboard.decision_set_versions SET expires_at = now() + interval '1 day'",
                "UPDATE switchboard.decision_set_versions SET digest = repeat('b', 64)",
                # No decision path exists yet: decided needs the resolver's receipt.
                "UPDATE switchboard.decision_set_versions SET state = 'decided'",
                "DELETE FROM switchboard.decision_set_items",
                "DELETE FROM switchboard.decision_set_versions",
                "DELETE FROM switchboard.decision_sets",
            ):
                assert await _refused(pool, query), query

        # A version may only leave open, once.
        await runtime.execute(
            "UPDATE switchboard.decision_set_versions SET state = 'superseded' WHERE set_id = $1",
            set_id,
        )
        assert await _refused(
            runtime, "UPDATE switchboard.decision_set_versions SET state = 'open'"
        )
        # With the first superseded, the next version of the same set may open.
        await _insert_set(runtime, goal, lead, plan, effects, version=2, set_id=set_id)

        # A one-item set is refused: grouping needs at least two calls.
        with pytest.raises(asyncpg.CheckViolationError):
            await _insert_set(runtime, goal, lead, plan, effects[:1])


def test_decision_sets_are_append_only_and_cannot_be_decided_yet(postgres_container):
    url = _ledger(postgres_container)
    asyncio.run(_exercise(url))

    config = _build_alembic_config(url, ["switchboard"], target_schema="switchboard")
    with pytest.raises(Exception, match="Cannot discard decision sets"):
        command.downgrade(config, "switchboard@sw_071")  # pinned-revision: sw_072 fence


def test_the_empty_tables_round_trip(postgres_container):
    url = _ledger(postgres_container)
    config = _build_alembic_config(url, ["switchboard"], target_schema="switchboard")
    command.downgrade(config, "switchboard@sw_071")  # pinned-revision: sw_072 removal
    command.upgrade(config, "switchboard@sw_072")  # pinned-revision: sw_072 reapply
