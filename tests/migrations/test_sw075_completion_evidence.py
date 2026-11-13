"""sw_075: plan requirements keep their owner anchors; completion evidence is durable.

Real PostgreSQL. A plan version's requirements are closed ``{ref, evidence, anchor}`` shapes, and
every later version keeps each owner-anchored requirement exactly; a lead requirement may be
dropped. A version recorded without requirements keeps its pre-sw_075 digest. The runtime role may
read and insert completion evidence, never edit or delete it, and a ``completed`` verdict carries
no missing proof. The downgrade refuses while evidence or a requirement exists.
"""

import asyncio
import json
from uuid import uuid4

import asyncpg
import pytest

from alembic import command
from butlers.core.goal_waiting import PlanRequirementDropped, WaitLedger, plan_digest
from butlers.migrations import _build_alembic_config
from butlers.testing.migration import create_migrated_test_db, migration_db_name
from tests.migrations.test_sw072_decision_sets import _goal, _refused
from tests.switchboard.goal_ledger_fixtures import pools

pytestmark = pytest.mark.integration

DIGEST = "b" * 64


def _ledger(container):
    return create_migrated_test_db(
        container,
        migration_db_name(),
        ["core", "switchboard"],
        schemas={"core": "switchboard", "switchboard": "switchboard"},
        revisions={"switchboard": "sw_075"},  # pinned-revision: owns the completion evidence
    )


def _req(ref, evidence, anchor):
    return {"ref": ref, "evidence": evidence, "anchor": anchor}


async def _append(runtime, goal, premise, requirements):
    async with runtime.acquire() as connection, connection.transaction():
        return await WaitLedger(connection).append_plan(goal, 0, premise, [], [], requirements)


async def _insert_plan(runtime, goal, version, requirements):
    await runtime.execute(
        "INSERT INTO switchboard.goal_plans (goal_id, version, predecessor_version, "
        "created_epoch, digest, requirements) VALUES ($1, $2, $3, 0, $4, $5::text::jsonb)",
        goal,
        version,
        version - 1,
        DIGEST,
        json.dumps(requirements),
    )


async def _exercise(url):
    goal, lead, plan, (first, second) = await _goal(url)
    async with pools(url) as (owner, runtime):
        # A version recorded without requirements keeps the digest it always had.
        assert await runtime.fetchval(
            "SELECT digest FROM switchboard.goal_plans WHERE goal_id = $1 AND version = $2",
            goal,
            plan,
        ) == plan_digest([], [], [])

        anchored = _req(f"effect:{first}", "committed", "owner")
        led = _req(f"effect:{second}", "committed", "lead")
        v2 = await _append(runtime, goal, ["p2"], [anchored, led])
        stored = await runtime.fetchval(
            "SELECT requirements::text FROM switchboard.goal_plans "
            "WHERE goal_id = $1 AND version = $2",
            goal,
            v2,
        )
        assert json.loads(stored) == [anchored, led]
        assert await runtime.fetchval(
            "SELECT digest FROM switchboard.goal_plans WHERE goal_id = $1 AND version = $2",
            goal,
            v2,
        ) == plan_digest(["p2"], [], [], [anchored, led])

        # The ledger refuses a version that drops or weakens the owner anchor...
        for requirements in ([led], [], [_req(f"effect:{first}", "committed", "lead")]):
            with pytest.raises(PlanRequirementDropped):
                await _append(runtime, goal, ["p3"], requirements)
        # ...and the trigger refuses it again when the ledger is bypassed.
        for requirements in ([led], [_req(f"effect:{first}", "owner_delivered", "owner")]):
            with pytest.raises(asyncpg.PostgresError, match="owner-anchored"):
                await _insert_plan(runtime, goal, v2 + 1, requirements)
        # A lead requirement may go; the owner's stays.
        v3 = await _append(runtime, goal, ["p3"], [anchored])
        assert v3 == v2 + 1

        # Every requirement is a closed shape.
        for bad in (
            _req(f"child:{first}", "committed", "lead"),
            _req(f"effect:{first}", "committed", "model"),
            _req("reply", "committed", "owner"),
            _req("effect:not-a-uuid", "committed", "lead"),
            {**anchored, "extra": "x"},
            "effect",
        ):
            with pytest.raises(asyncpg.PostgresError):
                await _insert_plan(runtime, goal, v3 + 1, [anchored, bad])

        # Completion evidence is written once and never edited or removed.
        await runtime.execute(
            "INSERT INTO switchboard.goal_outcomes (goal_id, invocation_id, outcome) "
            "VALUES ($1, $2, 'completed')",
            goal,
            lead,
        )
        evidence = (
            "INSERT INTO switchboard.goal_outcome_evidence (goal_id, final_status, satisfied, "
            "missing) VALUES ($1, $2, '[]'::jsonb, $3::text::jsonb)"
        )
        with pytest.raises(asyncpg.CheckViolationError):
            await runtime.execute(evidence, goal, "completed", json.dumps([{**led, "found": None}]))
        await runtime.execute(evidence, goal, "partial", json.dumps([{**led, "found": None}]))
        assert (
            await runtime.fetchval(
                "SELECT final_status FROM switchboard.goal_outcome_evidence WHERE goal_id = $1",
                goal,
            )
            == "partial"
        )
        for pool in (runtime, owner):
            for query in (
                "UPDATE switchboard.goal_outcome_evidence SET final_status = 'completed', "
                "missing = '[]'::jsonb",
                "UPDATE switchboard.goal_outcome_evidence SET final_status = 'failed' "
                f"WHERE goal_id = '{uuid4()}'",
                "DELETE FROM switchboard.goal_outcome_evidence",
                "TRUNCATE switchboard.goal_outcome_evidence",
            ):
                assert await _refused(pool, query), query


def test_owner_anchors_are_kept_and_completion_evidence_is_durable(postgres_container):
    url = _ledger(postgres_container)
    asyncio.run(_exercise(url))

    config = _build_alembic_config(url, ["switchboard"], target_schema="switchboard")
    with pytest.raises(Exception, match="Cannot discard completion evidence"):
        command.downgrade(config, "switchboard@sw_074")  # pinned-revision: sw_075 fence


def test_the_empty_structures_round_trip(postgres_container):
    url = _ledger(postgres_container)
    config = _build_alembic_config(url, ["switchboard"], target_schema="switchboard")
    command.downgrade(config, "switchboard@sw_074")  # pinned-revision: sw_075 removal
    command.upgrade(config, "switchboard@sw_075")  # pinned-revision: sw_075 reapply
