"""sw_063: a populated ledger keeps every row and gains the waiting records losslessly.

Real PostgreSQL. A ledger populated at sw_062 (an owner goal with a replacement lead, a joined
reply that was already consumed, a pending reply, and a legacy thread id) is upgraded: the thread
becomes a real registry row, each lead's epoch is recorded with its reason and state, the
consumed reply is marked legacy rather than given an invented epoch, and replies keep their
kind. The downgrade refuses while any waiting record exists; an empty ledger round-trips.
"""

import asyncio
from uuid import uuid4

import asyncpg
import pytest
from sqlalchemy.exc import DBAPIError

from alembic import command
from butlers.core.goal_admission import Evidence
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


def _reply(delivery, entity):
    return Evidence(
        "message",
        "owner",
        owner=True,
        reply_delivery=delivery,
        owner_entity=entity,
        owner_channel="telegram",
    )


async def _populate(url):
    async with pools(url) as (owner, runtime):
        await configure(owner, limits())
        goal = await admit(runtime, OWNER)
        entity, delivery = uuid4(), f"legacy-{uuid4()}"
        expiry = await owner.fetchval("SELECT now() + interval '1 hour'")
        first_lead, prompt = await effect(
            owner,
            goal.goal_id,
            delivery=delivery,
            entity=entity,
            channel="telegram",
            accepts=True,
            expiry=expiry,
        )
        consumed = await admit(runtime, _reply(delivery, entity))
        pending = await admit(runtime, _reply(delivery, entity))
        assert (consumed.result, pending.result) == ("joined", "joined")
        await owner.execute(
            "UPDATE switchboard.goal_inbox SET consumed_at = now() WHERE decision_id = $1",
            consumed.decision_id,
        )
        # An escalated first lead, then a recovered second lead still running.
        await owner.execute(
            "UPDATE switchboard.invocations SET status = 'escalated' WHERE id = $1", first_lead
        )
        second = uuid4()
        await owner.execute(
            "INSERT INTO switchboard.invocations (id,goal_id,depth,role,lead_epoch,status,"
            "grant_snapshot,grant_digest,max_turns,max_tokens) "
            "VALUES ($1,$2,0,'lead',1,'running','[]','digest',1,1)",
            second,
            goal.goal_id,
        )
        thread = uuid4()
        await owner.execute(
            "UPDATE switchboard.goals SET thread_id = $1 WHERE id = $2", thread, goal.goal_id
        )
        return {
            "goal": goal.goal_id,
            "first": first_lead,
            "second": second,
            "thread": thread,
            "consumed": consumed.decision_id,
            "pending": pending.decision_id,
            "prompt": prompt,
        }


async def _read(url, ids):
    async with pools(url) as (owner, _):
        return {
            "threads": [
                dict(r) for r in await owner.fetch("SELECT * FROM switchboard.owner_threads")
            ],
            "epochs": [
                dict(r)
                for r in await owner.fetch(
                    "SELECT * FROM switchboard.goal_epochs WHERE goal_id = $1 ORDER BY epoch",
                    ids["goal"],
                )
            ],
            "inbox": {
                r["decision_id"]: dict(r)
                for r in await owner.fetch("SELECT * FROM switchboard.goal_inbox")
            },
            "decisions": {
                r["id"]: dict(r)
                for r in await owner.fetch("SELECT * FROM switchboard.admission_decisions")
            },
            "goal": dict(
                await owner.fetchrow("SELECT * FROM switchboard.goals WHERE id = $1", ids["goal"])
            ),
        }


def test_sw063_backfill_keeps_every_row_and_records_epochs(postgres_container):
    url = _ledger(postgres_container, "sw_062")  # pinned-revision: sw_063 predecessor
    ids = asyncio.run(_populate(url))
    config = _build_alembic_config(url, ["switchboard"], target_schema="switchboard")
    command.upgrade(config, "switchboard@sw_063")  # pinned-revision: owns the backfill
    rows = asyncio.run(_read(url, ids))

    # The legacy thread id is now a real registry row the goal references.
    (thread,) = rows["threads"]
    assert (thread["id"], thread["legacy"], thread["channel"]) == (ids["thread"], True, "legacy")
    assert rows["goal"]["thread_id"] == ids["thread"]
    # Statuses are untouched here; the legacy marker and plan binding start empty.
    assert rows["goal"]["status"] == "admitted"
    assert (rows["goal"]["legacy_status"], rows["goal"]["plan_version"]) == (None, None)
    assert rows["goal"]["wait_generation"] == 0

    first, second = rows["epochs"]
    assert (first["epoch"], first["reason"], first["state"]) == (0, "initial", "finished")
    assert first["predecessor_invocation_id"] is None
    # The escalated predecessor makes the next lead an escalation; its state follows the lead.
    assert (second["epoch"], second["reason"], second["state"]) == (1, "escalation", "running")
    assert second["predecessor_invocation_id"] == ids["first"]

    consumed, pending = rows["inbox"][ids["consumed"]], rows["inbox"][ids["pending"]]
    assert consumed["input_kind"] == pending["input_kind"] == "owner_reply"
    assert (consumed["legacy_consumed"], consumed["consumed_epoch"]) == (True, None)
    assert consumed["consumed_at"] is not None
    assert (pending["legacy_consumed"], pending["consumed_at"]) == (False, None)
    assert rows["decisions"][ids["consumed"]]["input_kind"] == "owner_reply"
    assert rows["decisions"][ids["consumed"]]["reason"] == "reply_joined"

    async def refused():
        async with pools(url) as (owner, _):
            async with owner.acquire() as conn:
                # A legacy-consumed row is never consumed again, and its tuple cannot be edited.
                for query in (
                    "UPDATE switchboard.goal_inbox SET consumed_at = now() WHERE decision_id = $1",
                    "UPDATE switchboard.goal_inbox SET consumed_epoch = 1 WHERE decision_id = $1",
                ):
                    with pytest.raises(asyncpg.PostgresError):
                        async with conn.transaction():
                            await conn.execute(query, ids["consumed"])
                # A pending row consumes once, as the (time, epoch) tuple.
                async with conn.transaction():
                    await conn.execute(
                        "UPDATE switchboard.goal_inbox SET consumed_at = now(), consumed_epoch = 1 "
                        "WHERE decision_id = $1",
                        ids["pending"],
                    )
                with pytest.raises(asyncpg.PostgresError):
                    async with conn.transaction():
                        await conn.execute(
                            "UPDATE switchboard.goal_inbox SET consumed_epoch = 0 "
                            "WHERE decision_id = $1",
                            ids["pending"],
                        )

    asyncio.run(refused())

    with pytest.raises(DBAPIError, match="Cannot discard"):
        command.downgrade(config, "switchboard@sw_062")  # pinned-revision: sw_063 fence


def test_sw063_round_trips_an_empty_ledger(postgres_container):
    url = _ledger(postgres_container)
    config = _build_alembic_config(url, ["switchboard"], target_schema="switchboard")
    command.downgrade(config, "switchboard@sw_062")  # pinned-revision: sw_063 predecessor
    command.upgrade(config, "switchboard@sw_063")  # pinned-revision: replays its installer

    async def admitted():
        async with pools(url) as (owner, runtime):
            await configure(owner, limits())
            goal = await admit(runtime, OWNER)
            await effect(owner, goal.goal_id)
            return await owner.fetchval(
                "SELECT count(*) FROM switchboard.goal_epochs WHERE goal_id = $1", goal.goal_id
            )

    # A lead invocation derives its epoch record even before the runner writes epochs itself.
    assert asyncio.run(admitted()) == 1
