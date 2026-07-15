"""Real-Postgres proof for OwnTracks timestamp-plus-UUID cursor paging."""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from uuid import UUID

import asyncpg
import pytest

from butlers.testing.migration import create_migrated_test_db, migration_db_name

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]

_INDEX = "idx_owntracks_points_ts_id"
_BOUNDARY_TS = datetime(2026, 7, 15, 1, 0, tzinfo=UTC)
_BOUNDARY_ID = UUID("00000000-0000-0000-0000-0000000001f4")


@pytest.fixture(scope="module")
def core_db_url(postgres_container) -> str:
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core"],
    )


@pytest.fixture(scope="module")
async def seeded_pool(core_db_url: str) -> asyncpg.Pool:
    pool = await asyncpg.create_pool(core_db_url, min_size=1, max_size=3)
    await pool.execute(
        """
        INSERT INTO connectors.owntracks_points (
            id, idempotency_key, ts, lat, lon, endpoint_identity, recorded_at
        )
        SELECT
            lpad(to_hex(i), 32, '0')::uuid,
            'cursor-index:' || i,
            $1::timestamptz + ((i / 1000) * interval '1 second'),
            1.3,
            103.8,
            'owntracks:index-test',
            $1::timestamptz + ((i / 1000) * interval '1 second')
        FROM generate_series(1, 20000) AS rows(i)
        """,
        _BOUNDARY_TS,
    )
    await pool.execute("ANALYZE connectors.owntracks_points")
    yield pool
    await pool.close()


async def test_tuple_cursor_query_uses_composite_index_without_sort(
    seeded_pool: asyncpg.Pool,
) -> None:
    async with seeded_pool.acquire() as conn:
        await conn.execute("SET enable_seqscan = off")
        rows = await conn.fetch(
            """
            EXPLAIN (COSTS OFF)
            SELECT id, ts
            FROM connectors.owntracks_points
            WHERE (ts, id) > ($1, $2)
            ORDER BY ts ASC, id ASC
            LIMIT 1000
            """,
            _BOUNDARY_TS,
            _BOUNDARY_ID,
        )

    plan = "\n".join(row[0] for row in rows)
    assert _INDEX in plan, f"tuple cursor query did not use {_INDEX}:\n{plan}"
    assert "Sort" not in plan, f"tuple cursor index should provide total order:\n{plan}"
