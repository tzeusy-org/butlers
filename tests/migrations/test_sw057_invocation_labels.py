"""sw_057: legacy ledger rows gain S1 labels that keep their old restriction.

Real PostgreSQL. A ledger populated at sw_056 is upgraded; every row's label
reproduces its Boolean exposure and assumes all six categories, an exposed
invocation's cause is ``legacy_unknown``, and the downgrade refuses while any
goal exists. An empty ledger downgrades and upgrades again.
"""

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy.exc import DBAPIError

from alembic import command
from butlers.core.goal_admission import Evidence
from butlers.core.goal_labels import CATEGORIES
from butlers.migrations import _build_alembic_config
from butlers.testing.migration import create_migrated_test_db, migration_db_name
from tests.switchboard.goal_admission_fixtures import limits
from tests.switchboard.goal_ledger_fixtures import admit, configure, effect, pools

pytestmark = pytest.mark.integration

OWNER = Evidence("message", "owner", owner=True)


def _ledger(container, revision=None):
    return create_migrated_test_db(
        container,
        migration_db_name(),
        ["core", "switchboard"],
        schemas={"core": "switchboard", "switchboard": "switchboard"},
        revisions={"switchboard": revision} if revision else None,
    )


async def _populate(url):
    async with pools(url) as (owner, runtime):
        await configure(owner, limits())
        clean = await admit(runtime, OWNER)
        entity, delivery = uuid4(), f"legacy-{uuid4()}"
        expiry = await owner.fetchval("SELECT now() + interval '1 hour'")
        clean_lead, clean_effect = await effect(
            owner,
            clean.goal_id,
            delivery=delivery,
            entity=entity,
            channel="telegram",
            accepts=True,
            expiry=expiry,
        )
        reply = Evidence(
            "message",
            "owner",
            owner=True,
            reply_delivery=delivery,
            owner_entity=entity,
            owner_channel="telegram",
        )
        joined = await admit(runtime, reply)
        assert joined.result == "joined"
        exposed = await admit(runtime)  # third party
        exposed_lead, exposed_effect = await effect(owner, exposed.goal_id)
        return {
            "clean": clean.goal_id,
            "clean_lead": clean_lead,
            "clean_effect": clean_effect,
            "joined": joined.decision_id,
            "exposed": exposed.goal_id,
            "exposed_lead": exposed_lead,
            "exposed_effect": exposed_effect,
        }


async def _labels(url, ids):
    async with pools(url) as (owner, _):

        async def row(table, column, value):
            return dict(
                await owner.fetchrow(f"SELECT * FROM switchboard.{table} WHERE {column}=$1", value)
            )

        return {
            "clean": await row("goals", "id", ids["clean"]),
            "clean_lead": await row("invocations", "id", ids["clean_lead"]),
            "clean_effect": await row("effects", "id", ids["clean_effect"]),
            "inbox": await row("goal_inbox", "decision_id", ids["joined"]),
            "exposed": await row("goals", "id", ids["exposed"]),
            "exposed_lead": await row("invocations", "id", ids["exposed_lead"]),
            "exposed_effect": await row("effects", "id", ids["exposed_effect"]),
        }


def _label(row):
    return row["label_instr"], row["label_data"], set(row["label_sens"])


def test_sw057_backfill_keeps_each_legacy_restriction(postgres_container):
    url = _ledger(postgres_container, "sw_056")  # pinned-revision: sw_057 predecessor
    ids = asyncio.run(_populate(url))
    config = _build_alembic_config(url, ["switchboard"], target_schema="switchboard")
    command.upgrade(config, "switchboard@sw_057")  # pinned-revision: owns the backfill
    rows = asyncio.run(_labels(url, ids))

    owner_label = (["owner"], ["owner"], CATEGORIES)
    world_label = (["world"], ["world"], CATEGORIES)
    for name in ("clean", "clean_lead", "clean_effect", "inbox"):
        assert _label(rows[name]) == owner_label, name
    for name in ("exposed", "exposed_lead", "exposed_effect"):
        assert _label(rows[name]) == world_label, name
    clean, exposed = rows["clean"], rows["exposed"]
    assert (clean["exposed"], exposed["exposed"]) == (False, True)
    assert (clean["admitted_label_data"], exposed["admitted_label_data"]) == (["owner"], ["world"])
    assert set(clean["admitted_label_sens"]) == CATEGORIES
    assert rows["clean_lead"]["exposure_cause"] is None
    assert rows["exposed_lead"]["exposure_cause"] == "legacy_unknown"

    # A legacy goal's later invocations keep its all-category restriction.
    async def later():
        async with pools(url) as (owner, _):
            replacement = uuid4()
            await owner.execute(
                "INSERT INTO switchboard.invocations (id,goal_id,depth,role,lead_epoch,status,"
                "grant_snapshot,grant_digest,max_turns,max_tokens) "
                "VALUES ($1,$2,0,'lead',1,'running','[]','digest',1,1)",
                replacement,
                ids["clean"],
            )
            return await owner.fetchrow(
                "SELECT * FROM switchboard.invocations WHERE id=$1", replacement
            )

    assert _label(asyncio.run(later())) == owner_label

    with pytest.raises(DBAPIError, match="Cannot discard durable invocation labels"):
        command.downgrade(config, "switchboard@sw_056")  # pinned-revision: sw_057 fence
    assert asyncio.run(_labels(url, ids))["exposed_lead"]["exposure_cause"] == "legacy_unknown"


def test_sw057_round_trips_an_empty_ledger(postgres_container):
    url = _ledger(postgres_container)
    config = _build_alembic_config(url, ["switchboard"], target_schema="switchboard")
    command.downgrade(config, "switchboard@sw_056")  # pinned-revision: sw_057 predecessor
    command.upgrade(config, "switchboard@sw_057")  # pinned-revision: replays its installer

    async def admitted():
        async with pools(url) as (owner, runtime):
            await configure(owner, limits())
            goal = await admit(runtime, OWNER)
            return await owner.fetchrow("SELECT * FROM switchboard.goals WHERE id=$1", goal.goal_id)

    goal = asyncio.run(admitted())
    assert _label(goal) == (["owner"], ["owner"], set())
