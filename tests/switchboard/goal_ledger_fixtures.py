"""Real migrated PostgreSQL fixtures for the additive dark ledger."""

import json
from contextlib import asynccontextmanager
from uuid import uuid4

import asyncpg
import pytest

from butlers.core.goal_admission import GoalAdmission, SourceRef, digest
from butlers.testing.migration import create_migrated_test_db, migration_db_name
from tests.switchboard.goal_admission_fixtures import Catalogs, Sources, limits

TABLES = (
    "goals",
    "invocations",
    "effects",
    "budgets",
    "admission_decisions",
    "goal_inbox",
    "admission_attempt_bindings",
)


@pytest.fixture(scope="module")
def ledger_url(postgres_container):
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        ["core", "switchboard", "relationship"],
        schemas={
            "core": "switchboard",
            "switchboard": "switchboard",
            "relationship": "relationship",
        },
    )


async def codec(connection):
    await connection.set_type_codec(
        "jsonb", schema="pg_catalog", encoder=json.dumps, decoder=json.loads, format="text"
    )


@asynccontextmanager
async def pools(url):
    async def role(connection):
        await connection.execute("SET ROLE butler_switchboard_rw")

    async with asyncpg.create_pool(
        url,
        init=codec,
        min_size=1,
        max_size=4,
        server_settings={"search_path": "switchboard,public"},
    ) as owner:
        async with asyncpg.create_pool(
            url, init=codec, setup=role, min_size=1, max_size=4
        ) as runtime:
            yield owner, runtime


async def reset_ledger(owner):
    """Empty the goal ledger between tests.

    sw_056's trigger refuses TRUNCATE on ``broker_refusals``, which TRUNCATE ...
    CASCADE reaches through its goal foreign key, and sw_057's refuses it on
    ``invocations`` and ``effects``, and sw_059's on the owner-policy terms,
    receipts and holds (which it also empties; the generation row stays), and
    sw_070's on ``goal_inbox_releases``, which the CASCADE reaches through its
    goal and inbox keys (a test that ends a goal leaves release rows), and
    sw_072's on the decision set tables, which it reaches through their goal and
    effect keys, and sw_073's on the owner decision receipts, reached the same way. This
    test-only reset, run as the owner, lifts whichever of those guards the
    pinned revision has, for its own transaction alone.
    """
    async with owner.acquire() as connection, connection.transaction():
        guards = await connection.fetch(
            "SELECT c.relname, t.tgname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'switchboard' "
            "AND t.tgname = ANY($1::text[])",
            [
                "immutable_broker_refusal",
                "durable_goal_lineage",
                "owner_policy_durable",
                "goal_waiting_durable",
                "goal_inbox_releases_durable",
                "decision_set_durable",
                "owner_decision_durable",
            ],
        )
        for guard in guards:
            await connection.execute(
                f"ALTER TABLE switchboard.{guard['relname']} DISABLE TRIGGER {guard['tgname']}"
            )
        policy = [
            f"switchboard.{table}"
            for table in ("owner_policy_holds", "owner_policy_receipts", "owner_policy_terms")
            if any(guard["relname"] == table for guard in guards)
        ]
        await connection.execute(
            "TRUNCATE " + ",".join(["switchboard." + t for t in TABLES] + policy) + " CASCADE"
        )
        for guard in guards:
            await connection.execute(
                f"ALTER TABLE switchboard.{guard['relname']} ENABLE TRIGGER {guard['tgname']}"
            )


@pytest.fixture
async def ledger(ledger_url):
    async with pools(ledger_url) as (owner, runtime):
        await reset_ledger(owner)
        await configure(owner, limits())
        yield owner, runtime


async def configure(owner, value):
    await owner.execute(
        "INSERT INTO switchboard.runtime_config (butler_name,goal_admission_config) VALUES ('switchboard',$1::jsonb) "
        "ON CONFLICT (butler_name) DO UPDATE SET goal_admission_config=EXCLUDED.goal_admission_config",
        value,
    )


async def admit(runtime, facts=None, source=None, attempt=None):
    return await GoalAdmission(runtime, Sources(facts), Catalogs()).admit_submission(
        source or SourceRef("message", str(uuid4())),
        attempt or uuid4(),
    )


async def effect(
    owner,
    goal_id,
    *,
    delivery=None,
    entity=None,
    channel=None,
    accepts=False,
    expiry=None,
    args_digest="args-digest",
    status="committed",
):
    invocation, effect_id = uuid4(), uuid4()
    goal = await owner.fetchrow(
        "SELECT ceiling,exposed FROM switchboard.goals WHERE id=$1", goal_id
    )
    grants = goal["ceiling"]
    await owner.execute(
        "INSERT INTO switchboard.invocations (id,goal_id,depth,role,lead_epoch,status,grant_snapshot,grant_digest,"
        "exposed,max_turns,max_tokens) VALUES ($1,$2,0,'lead',0,'returned',$3::jsonb,$4,$5,5,500)",
        invocation,
        goal_id,
        grants,
        digest(grants),
        goal["exposed"],
    )
    await owner.execute(
        "INSERT INTO switchboard.effects (id,goal_id,invocation_id,capability,args_digest,status,delivery_ref,"
        "recipient_entity,recipient_channel,prompt_accepts_input,prompt_expires_at) "
        "VALUES ($1,$2,$3,'messenger.notify',$9,$10,$4,$5,$6,$7,$8)",
        effect_id,
        goal_id,
        invocation,
        delivery,
        entity,
        channel,
        accepts,
        expiry,
        args_digest,
        status,
    )
    return invocation, effect_id
