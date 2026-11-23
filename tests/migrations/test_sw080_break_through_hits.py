"""sw_080: the break-through hit record is durable, and the downgrade refuses to discard it.

Real PostgreSQL. A hit is keyed by (clause, source request); the runtime role inserts and reads
it and nothing updates, deletes or truncates it. The downgrade drops an empty table and refuses
while any hit exists (bu-s11n0s.8).
"""

import asyncio
from uuid import uuid4

import asyncpg
import pytest

from alembic import command
from butlers.migrations import _build_alembic_config
from butlers.testing.migration import create_migrated_test_db, migration_db_name

pytestmark = pytest.mark.integration


def _db(container):
    return create_migrated_test_db(
        container,
        migration_db_name(),
        ["core", "switchboard"],
        schemas={"core": "switchboard", "switchboard": "switchboard"},
        revisions={"switchboard": "sw_080"},  # pinned-revision: owns the hit record
    )


async def _insert(url: str, clause, request) -> None:
    conn = await asyncpg.connect(url)
    try:
        await conn.execute(
            "INSERT INTO switchboard.break_through_hits (clause_id, source_request_id, channel) "
            "VALUES ($1, $2, 'email')",
            clause,
            request,
        )
    finally:
        await conn.close()


def test_the_same_clause_and_request_is_one_hit_and_the_downgrade_fences_it(
    postgres_container,
):
    url = _db(postgres_container)
    clause, request = uuid4(), uuid4()
    asyncio.run(_insert(url, clause, request))
    with pytest.raises(asyncpg.UniqueViolationError):
        asyncio.run(_insert(url, clause, request))

    config = _build_alembic_config(url, ["switchboard"], target_schema="switchboard")
    with pytest.raises(Exception, match="break-through hits"):
        command.downgrade(config, "switchboard@sw_079")  # pinned-revision: sw_080 fence
