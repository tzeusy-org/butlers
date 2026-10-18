"""Real migrated PostgreSQL fixtures for the additive dark ledger."""

import json
from contextlib import asynccontextmanager
from uuid import uuid4

import asyncpg
import pytest

from butlers.core.goal_admission import GoalAdmission, SourceRef, digest
from butlers.testing.migration import create_migrated_test_db, migration_db_name
from tests.switchboard.goal_admission_fixtures import Catalogs, Sources, limits

TABLES = ("goals", "invocations", "effects", "budgets", "admission_decisions")


@pytest.fixture(scope="module")
def ledger_url(postgres_container):
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        ["core", "switchboard"],
        schemas={"core": "switchboard", "switchboard": "switchboard"},
    )


async def codec(connection):
    await connection.set_type_codec(
        "jsonb", schema="pg_catalog", encoder=json.dumps, decoder=json.loads, format="text"
    )


@asynccontextmanager
async def pools(url):
    async def role(connection):
        await connection.execute("SET ROLE butler_switchboard_rw")

    async with asyncpg.create_pool(url, init=codec, min_size=1, max_size=4) as owner:
        async with asyncpg.create_pool(
            url, init=codec, setup=role, min_size=1, max_size=4
        ) as runtime:
            yield owner, runtime


@pytest.fixture
async def ledger(ledger_url):
    async with pools(ledger_url) as (owner, runtime):
        await owner.execute("TRUNCATE " + ",".join("switchboard." + t for t in TABLES) + " CASCADE")
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
    owner, goal_id, *, delivery=None, entity=None, channel=None, accepts=False, expiry=None
):
    invocation, effect_id = uuid4(), uuid4()
    grants = await owner.fetchval("SELECT ceiling FROM switchboard.goals WHERE id=$1", goal_id)
    await owner.execute(
        "INSERT INTO switchboard.invocations (id,goal_id,depth,role,lead_epoch,status,grant_snapshot,grant_digest,"
        "max_turns,max_tokens) VALUES ($1,$2,0,'lead',0,'returned',$3::jsonb,$4,5,500)",
        invocation,
        goal_id,
        grants,
        digest(grants),
    )
    await owner.execute(
        "INSERT INTO switchboard.effects (id,goal_id,invocation_id,capability,args_digest,status,delivery_ref,"
        "recipient_entity,recipient_channel,prompt_accepts_input,prompt_expires_at) "
        "VALUES ($1,$2,$3,'messenger.notify','args-digest','committed',$4,$5,$6,$7,$8)",
        effect_id,
        goal_id,
        invocation,
        delivery,
        entity,
        channel,
        accepts,
        expiry,
    )
    return invocation, effect_id
