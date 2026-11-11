"""sw_071: ``replies_unread`` joins the closed pending-ask causes; the downgrade is guarded."""

import asyncio

import asyncpg
import pytest

from alembic import command
from butlers.migrations import _build_alembic_config
from butlers.testing.migration import create_migrated_test_db, migration_db_name
from tests.switchboard.goal_ledger_fixtures import pools

pytestmark = pytest.mark.integration

INSERT = "INSERT INTO switchboard.pending_asks (source_key, cause) VALUES ($1, $2)"


async def _try(url, key, cause):
    async with pools(url) as (owner, _):
        try:
            await owner.execute(INSERT, key, cause)
        except asyncpg.PostgresError:
            return False
        return True


def test_the_cause_arrives_in_sw_071_and_pins_the_downgrade(postgres_container):
    url = create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        ["core", "switchboard"],
        schemas={"core": "switchboard", "switchboard": "switchboard"},
        revisions={"switchboard": "sw_070"},  # pinned-revision: sw_071 predecessor
    )
    assert asyncio.run(_try(url, "before", "replies_unread")) is False
    config = _build_alembic_config(url, ["switchboard"], target_schema="switchboard")
    command.upgrade(config, "switchboard@sw_071")  # pinned-revision: owns the cause word
    assert asyncio.run(_try(url, "after", "replies_unread")) is True
    assert asyncio.run(_try(url, "other", "failed")) is True
    assert asyncio.run(_try(url, "bad", "invented")) is False
    with pytest.raises(Exception, match="replies_unread asks"):
        command.downgrade(config, "switchboard@sw_070")  # pinned-revision: sw_071 fence
