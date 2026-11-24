"""sw_059: the owner tighten-only law installs, round-trips empty, and keeps its history.

Real PostgreSQL. An empty law downgrades and upgrades again with its generation
row and runtime policies; once a term exists the downgrade refuses, so durable
owner policy and its receipts are never discarded.
"""

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy.exc import DBAPIError

from alembic import command
from butlers.core.owner_policy import OwnerPolicyWriter, read_policy
from butlers.migrations import _build_alembic_config
from butlers.testing.migration import create_migrated_test_db, migration_db_name
from tests.switchboard.goal_ledger_fixtures import pools

pytestmark = pytest.mark.integration


def _ledger(container):
    return create_migrated_test_db(
        container,
        migration_db_name(),
        ["core", "switchboard"],
        schemas={"core": "switchboard", "switchboard": "switchboard"},
    )


async def _policies(url):
    async with pools(url) as (owner, runtime):
        rows = await owner.fetch(
            "SELECT tablename, policyname, cmd FROM pg_policies "
            "WHERE schemaname = 'switchboard' AND tablename LIKE 'owner_policy_%' ORDER BY 1, 2"
        )
        return [tuple(row) for row in rows], (await read_policy(runtime)).generation


async def _create(url):
    async with pools(url) as (owner, _):
        async with owner.acquire() as connection:
            await OwnerPolicyWriter(connection).create(uuid4(), "region", "messenger", "never")


def test_sw059_round_trips_an_empty_law_and_refuses_to_discard_one(postgres_container):
    url = _ledger(postgres_container)
    installed = asyncio.run(_policies(url))
    assert installed[1] == 0
    assert {(table, cmd) for table, _, cmd in installed[0]} == {
        ("owner_policy_holds", "INSERT"),
        ("owner_policy_holds", "SELECT"),
        ("owner_policy_state", "SELECT"),
        ("owner_policy_state", "UPDATE"),
        ("owner_policy_terms", "SELECT"),
    }
    config = _build_alembic_config(url, ["switchboard"], target_schema="switchboard")
    command.downgrade(config, "switchboard@sw_058")  # pinned-revision: sw_059 predecessor
    command.upgrade(config, "switchboard@sw_059")  # pinned-revision: replays its installer
    assert asyncio.run(_policies(url)) == installed

    asyncio.run(_create(url))
    with pytest.raises(DBAPIError, match="Cannot discard durable owner policy"):
        command.downgrade(config, "switchboard@sw_058")  # pinned-revision: sw_059 fence
    assert asyncio.run(_policies(url))[1] == 1
