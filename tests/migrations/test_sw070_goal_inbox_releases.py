"""sw_070: a goal that ends releases the owner replies it joined but never read.

Real PostgreSQL. The goal's transition into a terminal status records each unconsumed
``owner_reply`` inbox row once in ``goal_inbox_releases``, with the reason; a consumed row is not
released. The runtime role may read the record and may not write, change or delete it. The
downgrade refuses while a release exists.
"""

import asyncio
from uuid import uuid4

import asyncpg
import pytest

from alembic import command
from butlers.core.goal_admission import Evidence, SourceRef
from butlers.migrations import _build_alembic_config
from butlers.testing.migration import create_migrated_test_db, migration_db_name
from tests.switchboard.goal_admission_fixtures import limits
from tests.switchboard.goal_ledger_fixtures import admit, configure, effect, pools

pytestmark = pytest.mark.integration

OWNER = Evidence("message", "owner", owner=True)


def _ledger(container):
    return create_migrated_test_db(
        container,
        migration_db_name(),
        ["core", "switchboard"],
        schemas={"core": "switchboard", "switchboard": "switchboard"},
        revisions={"switchboard": "sw_070"},  # pinned-revision: owns the release record
    )


async def _replied(url, delivery: str):
    """A running owner goal with one joined, unread reply to its prompt.

    Returns (goal, inbox row, the lead invocation that sent the prompt).
    """
    async with pools(url) as (owner, runtime):
        await configure(owner, limits())
        goal = await admit(runtime, OWNER)
        entity = uuid4()
        expiry = await owner.fetchval("SELECT now() + interval '1 hour'")
        lead, _ = await effect(
            owner,
            goal.goal_id,
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
        joined = await admit(runtime, reply, SourceRef("message", f"r-{uuid4()}"), uuid4())
        assert (joined.reason, joined.goal_id) == ("reply_joined", goal.goal_id)
        inbox = await owner.fetchval(
            "SELECT id FROM switchboard.goal_inbox WHERE goal_id = $1", goal.goal_id
        )
        return goal.goal_id, inbox, lead


async def _end(url, goal, status):
    async with pools(url) as (owner, _):
        await owner.execute("UPDATE switchboard.goals SET status = $2 WHERE id = $1", goal, status)
        return await owner.fetch(
            "SELECT inbox_id, reason FROM switchboard.goal_inbox_releases WHERE goal_id = $1",
            goal,
        )


async def _runtime_try(url, query, *args):
    async with pools(url) as (_, runtime):
        try:
            await runtime.execute(query, *args)
        except asyncpg.PostgresError:
            return False
        return True


def test_an_ended_goal_releases_its_unread_replies_once(postgres_container):
    url = _ledger(postgres_container)
    goal, inbox, _ = asyncio.run(_replied(url, "prompt-1"))

    released = asyncio.run(_end(url, goal, "stopped"))

    assert [(r["inbox_id"], r["reason"]) for r in released] == [(inbox, "stopped")]
    # The runtime reads the record but can neither forge, change nor remove one.
    assert asyncio.run(_runtime_try(url, "SELECT count(*) FROM switchboard.goal_inbox_releases"))
    for query in (
        "INSERT INTO switchboard.goal_inbox_releases (inbox_id, goal_id, reason) "
        "VALUES (gen_random_uuid(), gen_random_uuid(), 'failed')",
        "UPDATE switchboard.goal_inbox_releases SET reason = 'failed'",
        "DELETE FROM switchboard.goal_inbox_releases",
    ):
        assert asyncio.run(_runtime_try(url, query)) is False, query

    # A goal whose reply was read releases nothing.
    other, other_inbox, other_lead = asyncio.run(_replied(url, "prompt-2"))

    async def consume():
        # The consumption BrokerLedger.consume_inbox writes: the one-time (consumed_at,
        # consumed_epoch) tuple, the epoch being the reading lead's. No guard is disabled.
        async with pools(url) as (owner, _):
            consumed = await owner.execute(
                "UPDATE switchboard.goal_inbox SET consumed_at = clock_timestamp(), "
                "consumed_epoch = (SELECT lead_epoch FROM switchboard.invocations "
                "WHERE goal_id = $1 AND id = $3) "
                "WHERE goal_id = $1 AND id = $2 AND consumed_at IS NULL",
                other,
                other_inbox,
                other_lead,
            )
            assert consumed == "UPDATE 1"

    asyncio.run(consume())
    assert asyncio.run(_end(url, other, "failed")) == []

    config = _build_alembic_config(url, ["switchboard"], target_schema="switchboard")
    with pytest.raises(Exception, match="released owner replies"):
        command.downgrade(config, "switchboard@sw_069")  # pinned-revision: sw_070 fence
