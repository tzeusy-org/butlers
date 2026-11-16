"""sw_081: a break-through hit carries its announcement lease; nothing else about it changes.

Real PostgreSQL. After sw_081 the runtime role may move ``claimed_at`` and set ``published_at``
once; every other column stays immutable, a published stamp cannot be cleared, and deletes and
truncates are still refused. Hits from sw_080 are stamped published (that slice sent nothing),
and the downgrade still refuses to discard a hit (bu-s11n0s.8 S2).
"""

import asyncio
from uuid import uuid4

import asyncpg
import pytest

from alembic import command
from butlers.migrations import _build_alembic_config
from butlers.testing.migration import create_migrated_test_db, migration_db_name

pytestmark = pytest.mark.integration


def _db(container, revision: str):
    return create_migrated_test_db(
        container,
        migration_db_name(),
        ["core", "switchboard"],
        schemas={"core": "switchboard", "switchboard": "switchboard"},
        revisions={"switchboard": revision},  # pinned-revision: owns the announcement lease
    )


async def _run(url: str, *statements: str) -> None:
    conn = await asyncpg.connect(url)
    try:
        for statement in statements:
            await conn.execute(statement)
    finally:
        await conn.close()


async def _fetchval(url: str, query: str):
    conn = await asyncpg.connect(url)
    try:
        return await conn.fetchval(query)
    finally:
        await conn.close()


_INSERT = (
    "INSERT INTO switchboard.break_through_hits (clause_id, source_request_id, channel) "
    f"VALUES ('{uuid4()}', '{uuid4()}', 'email')"
)


def test_a_new_hit_is_unpublished_and_only_the_lease_can_move(postgres_container):
    url = _db(postgres_container, "sw_081")
    asyncio.run(_run(url, _INSERT))
    assert (
        asyncio.run(_fetchval(url, "SELECT published_at FROM switchboard.break_through_hits"))
        is None
    )

    asyncio.run(_run(url, "UPDATE switchboard.break_through_hits SET claimed_at = now()"))
    asyncio.run(_run(url, "UPDATE switchboard.break_through_hits SET published_at = now()"))
    for statement in (
        "UPDATE switchboard.break_through_hits SET channel = 'x'",
        "UPDATE switchboard.break_through_hits SET published_at = NULL",
        "DELETE FROM switchboard.break_through_hits",
        "TRUNCATE switchboard.break_through_hits",
    ):
        with pytest.raises(asyncpg.PostgresError):
            asyncio.run(_run(url, statement))


def test_a_hit_from_sw_080_becomes_published_and_the_downgrade_fences_it(postgres_container):
    url = _db(postgres_container, "sw_080")  # pinned-revision: the predecessor's rows
    asyncio.run(_run(url, _INSERT))

    config = _build_alembic_config(url, ["switchboard"], target_schema="switchboard")
    command.upgrade(config, "switchboard@sw_081")  # pinned-revision: under test
    assert asyncio.run(
        _fetchval(url, "SELECT published_at IS NOT NULL FROM switchboard.break_through_hits")
    )
    with pytest.raises(Exception, match="break-through hits"):
        command.downgrade(config, "switchboard@sw_080")  # pinned-revision: sw_081 fence
