"""sw_068: ``switchboard.pending_asks`` identity, closed words and a guarded downgrade.

Real PostgreSQL. One failure has one row (``source_key`` is unique); the cause and state
columns refuse anything outside their closed lists; the downgrade refuses while a row
exists, because a pending ask is the only record that the owner was never told.
"""

import asyncio

import asyncpg
import pytest

from alembic import command
from butlers.migrations import _build_alembic_config
from butlers.testing.migration import create_migrated_test_db, migration_db_name
from tests.switchboard.goal_ledger_fixtures import pools

pytestmark = pytest.mark.integration

INSERT = "INSERT INTO switchboard.pending_asks (source_key, cause, state) VALUES ($1, $2, $3)"


async def _insert(url, key, cause="failed", state="open"):
    async with pools(url) as (owner, runtime):
        for pool in (owner, runtime):
            try:
                await pool.execute(INSERT, f"{key}-{id(pool)}", cause, state)
            except asyncpg.PostgresError:
                return False
        return True


async def _duplicate(url):
    async with pools(url) as (owner, _):
        await owner.execute(INSERT, "same", "failed", "open")
        try:
            await owner.execute(INSERT, "same", "budget_exhausted", "open")
        except asyncpg.UniqueViolationError:
            return True
        return False


def test_the_table_arrives_in_sw_068_and_pins_the_downgrade(postgres_container):
    url = create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        ["core", "switchboard"],
        schemas={"core": "switchboard", "switchboard": "switchboard"},
        revisions={"switchboard": "sw_067"},  # pinned-revision: sw_068 predecessor
    )
    assert asyncio.run(_insert(url, "before")) is False  # the table does not exist yet

    config = _build_alembic_config(url, ["switchboard"], target_schema="switchboard")
    command.upgrade(config, "switchboard@sw_068")  # pinned-revision: owns the table
    for cause in ("failed", "budget_exhausted", "capability_unavailable", "admission_refused"):
        assert asyncio.run(_insert(url, cause, cause)) is True, cause
    for state in ("run", "dropped", "dropped_stale", "acked"):
        assert asyncio.run(_insert(url, state, state=state)) is True, state
    assert asyncio.run(_insert(url, "bad-cause", cause="invented")) is False
    assert asyncio.run(_insert(url, "bad-state", state="invented")) is False
    assert asyncio.run(_duplicate(url)) is True

    with pytest.raises(Exception, match="pending asks exist"):
        command.downgrade(config, "switchboard@sw_067")  # pinned-revision: sw_068 fence
