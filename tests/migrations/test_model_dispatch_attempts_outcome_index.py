"""Real-Postgres proof that core_173's ``(outcome, ts DESC)`` index serves the
fleet outcome-mode dispatch-attempts query (bu-ij9xl).

PR #3161's ``GET /api/dispatch/attempts`` outcome mode
(``model_settings.py`` list_dispatch_attempts, the ``outcome is not None``
branch) runs ``WHERE outcome = $1 [AND ts >= $n] ORDER BY ts <dir> LIMIT $n``
plus a paired ``COUNT(*) WHERE outcome = $1``. core_104 gave
``model_dispatch_attempts`` no index on ``outcome``, so both full-scanned.

This provisions the core chain (which now includes core_173), seeds a realistic
row volume, ``ANALYZE``s, and, with ``enable_seqscan`` disabled so the assertion
is about index *usability* rather than tiny-table cost estimation, asserts the
planner serves both the ordered LIMIT query and the COUNT from
``idx_model_dispatch_attempts_outcome_ts``. Drop the index (or the migration)
and these EXPLAINs fall back to a Seq Scan and the assertions fail.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from butlers.testing.migration import create_migrated_test_db, migration_db_name

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]

_INDEX = "idx_model_dispatch_attempts_outcome_ts"
_OUTCOMES = ["quota_skip", "runtime_failure", "success"]


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
    catalog_id = await pool.fetchval(
        """
        INSERT INTO public.model_catalog (alias, runtime_type, model_id)
        VALUES ('bu-ij9xl-test', 'codex', 'test-model')
        RETURNING id
        """
    )
    # Seed a realistic volume spread across the three outcomes and a time window
    # so the planner has a reason (and an ANALYZE'd histogram) to prefer an index.
    base = datetime(2026, 7, 1, tzinfo=UTC)
    rows = [
        (catalog_id, base + timedelta(minutes=i), "butler", _OUTCOMES[i % len(_OUTCOMES)])
        for i in range(2000)
    ]
    await pool.executemany(
        """
        INSERT INTO public.model_dispatch_attempts (catalog_entry_id, ts, butler, outcome)
        VALUES ($1, $2, $3, $4)
        """,
        rows,
    )
    await pool.execute("ANALYZE public.model_dispatch_attempts")
    yield pool
    await pool.close()


async def _plan(conn: asyncpg.Connection, sql: str, *args) -> str:
    # enable_seqscan off => the assertion proves the index CAN serve the query,
    # independent of the planner's tiny-table cost estimate.
    await conn.execute("SET enable_seqscan = off")
    lines = await conn.fetch(f"EXPLAIN {sql}", *args)
    return "\n".join(r[0] for r in lines)


@pytest.mark.asyncio(loop_scope="session")
async def test_outcome_ordered_query_uses_the_index(seeded_pool: asyncpg.Pool) -> None:
    async with seeded_pool.acquire() as conn:
        plan = await _plan(
            conn,
            """
            SELECT ts, butler, outcome, attempt_index
            FROM public.model_dispatch_attempts
            WHERE outcome = $1
            ORDER BY ts DESC
            LIMIT 50
            """,
            "runtime_failure",
        )
    assert _INDEX in plan, f"outcome ORDER BY query did not use {_INDEX}:\n{plan}"
    # The composite (outcome, ts DESC) satisfies the ORDER BY, so no explicit Sort.
    assert "Sort" not in plan, f"unexpected Sort node (index should provide order):\n{plan}"


@pytest.mark.asyncio(loop_scope="session")
async def test_outcome_windowed_query_uses_the_index(seeded_pool: asyncpg.Pool) -> None:
    async with seeded_pool.acquire() as conn:
        plan = await _plan(
            conn,
            """
            SELECT ts FROM public.model_dispatch_attempts
            WHERE outcome = $1 AND ts >= $2
            ORDER BY ts DESC
            LIMIT 50
            """,
            "success",
            datetime(2026, 7, 1, 12, tzinfo=UTC),
        )
    assert _INDEX in plan, f"outcome+ts window query did not use {_INDEX}:\n{plan}"


@pytest.mark.asyncio(loop_scope="session")
async def test_outcome_count_uses_the_index(seeded_pool: asyncpg.Pool) -> None:
    async with seeded_pool.acquire() as conn:
        plan = await _plan(
            conn,
            "SELECT count(*) FROM public.model_dispatch_attempts WHERE outcome = $1",
            "quota_skip",
        )
    assert _INDEX in plan, f"outcome COUNT(*) did not use {_INDEX}:\n{plan}"
