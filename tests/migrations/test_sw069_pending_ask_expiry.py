"""sw_069: ``pending_asks`` expiry backfill, the original-ask rule and a guarded downgrade.

Real PostgreSQL. Rows that predate the migration get ``created_at + 24h``; a new row defaults
to a day from now; ``original_ask`` may exist only on an open ask; the downgrade refuses while a
refused owner request is still remembered.
"""

import asyncio

import asyncpg
import pytest

from alembic import command
from butlers.migrations import _build_alembic_config
from butlers.testing.migration import create_migrated_test_db, migration_db_name
from tests.switchboard.goal_ledger_fixtures import pools

pytestmark = pytest.mark.integration


async def _seed_old(url):
    async with pools(url) as (owner, _):
        await owner.execute(
            "INSERT INTO switchboard.pending_asks (source_key, cause, created_at) "
            "VALUES ('old', 'failed', '2026-01-01T00:00:00Z')"
        )


async def _inspect(url):
    async with pools(url) as (owner, _):
        old = await owner.fetchval(
            "SELECT expires_at - created_at FROM switchboard.pending_asks WHERE source_key='old'"
        )
        await owner.execute(
            "INSERT INTO switchboard.pending_asks (source_key, cause, original_ask) "
            "VALUES ('new', 'admission_refused', '{\"v\":1}'::jsonb)"
        )
        fresh = await owner.fetchval(
            "SELECT expires_at - clock_timestamp() FROM switchboard.pending_asks "
            "WHERE source_key='new'"
        )
        rejected = []
        for statement in (
            "UPDATE switchboard.pending_asks SET state='dropped' WHERE source_key='new'",
            "INSERT INTO switchboard.pending_asks (source_key, cause, original_ask) "
            "VALUES ('scalar', 'failed', '1'::jsonb)",
        ):
            try:
                await owner.execute(statement)
            except asyncpg.CheckViolationError:
                rejected.append(statement)
        return old, fresh, len(rejected)


def test_expiry_backfill_default_check_and_guarded_downgrade(postgres_container):
    url = create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        ["core", "switchboard"],
        schemas={"core": "switchboard", "switchboard": "switchboard"},
        revisions={"switchboard": "sw_068"},  # pinned-revision: sw_069 predecessor
    )
    asyncio.run(_seed_old(url))
    config = _build_alembic_config(url, ["switchboard"], target_schema="switchboard")
    command.upgrade(config, "switchboard@sw_069")  # pinned-revision: owns the columns
    old, fresh, rejected = asyncio.run(_inspect(url))
    assert old.total_seconds() == 24 * 3600
    assert 23.9 * 3600 < fresh.total_seconds() <= 24 * 3600
    assert rejected == 2
    with pytest.raises(Exception, match="original asks exist"):
        command.downgrade(config, "switchboard@sw_068")  # pinned-revision: sw_069 fence
