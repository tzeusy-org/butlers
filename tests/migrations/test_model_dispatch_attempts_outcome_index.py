"""Real-Postgres proof that the deterministic outcome-order index serves the
fleet outcome-mode dispatch-attempts query (bu-ij9xl).

PR #3161's ``GET /api/dispatch/attempts`` outcome mode
(``model_settings.py`` list_dispatch_attempts, the ``outcome is not None``
branch) runs ``WHERE outcome = $1 [AND ts >= $n] ORDER BY ts <dir>, id <dir>
LIMIT $n`` plus a paired ``COUNT(*) WHERE outcome = $1``. core_104 gave
``model_dispatch_attempts`` no index on ``outcome``, so both full-scanned.

core_173 supplied ``(outcome, ts DESC)``. core_198 extends it to
``(outcome, ts DESC, id DESC)`` so equal timestamps are deterministic for the
user-visible list. This provisions the core chain, seeds a realistic row
volume, ``ANALYZE``s, and, with ``enable_seqscan`` disabled so the assertion is
about index *usability* rather than tiny-table cost estimation, asserts the
planner serves the ordered LIMIT query from the exact tie-break index. The
paired count may use either outcome index because it has no ordering contract.
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

_ORDER_INDEX = "idx_model_dispatch_attempts_outcome_ts_id"
_COUNT_INDEX = "idx_model_dispatch_attempts_outcome_ts"
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
            SELECT id, ts, butler, outcome, attempt_index
            FROM public.model_dispatch_attempts
            WHERE outcome = $1
            ORDER BY ts DESC, id DESC
            LIMIT 50
            """,
            "runtime_failure",
        )
    assert _ORDER_INDEX in plan, f"outcome ORDER BY query did not use {_ORDER_INDEX}:\n{plan}"
    # The composite (outcome, ts DESC, id DESC) satisfies the ORDER BY, so no Sort.
    assert "Sort" not in plan, f"unexpected Sort node (index should provide order):\n{plan}"


@pytest.mark.asyncio(loop_scope="session")
async def test_outcome_windowed_query_uses_the_index(seeded_pool: asyncpg.Pool) -> None:
    async with seeded_pool.acquire() as conn:
        plan = await _plan(
            conn,
            """
            SELECT id, ts FROM public.model_dispatch_attempts
            WHERE outcome = $1 AND ts >= $2
            ORDER BY ts DESC, id DESC
            LIMIT 50
            """,
            "success",
            datetime(2026, 7, 1, 12, tzinfo=UTC),
        )
    assert _ORDER_INDEX in plan, f"outcome+ts window query did not use {_ORDER_INDEX}:\n{plan}"


@pytest.mark.asyncio(loop_scope="session")
async def test_outcome_list_orders_equal_timestamps_by_descending_attempt_id(
    seeded_pool: asyncpg.Pool,
) -> None:
    """The public outcome list has a stable newest-attempt tie-breaker."""
    catalog_id = await seeded_pool.fetchval(
        """
        INSERT INTO public.model_catalog (alias, runtime_type, model_id)
        VALUES ('bu-ij9xl-equal-ts', 'codex', 'equal-ts-model')
        RETURNING id
        """
    )
    tied_ts = datetime(2026, 7, 2, tzinfo=UTC)
    first_id = await seeded_pool.fetchval(
        """
        INSERT INTO public.model_dispatch_attempts (catalog_entry_id, ts, butler, outcome)
        VALUES ($1, $2, 'first-tied-attempt', 'quota_skip')
        RETURNING id
        """,
        catalog_id,
        tied_ts,
    )
    second_id = await seeded_pool.fetchval(
        """
        INSERT INTO public.model_dispatch_attempts (catalog_entry_id, ts, butler, outcome)
        VALUES ($1, $2, 'second-tied-attempt', 'quota_skip')
        RETURNING id
        """,
        catalog_id,
        tied_ts,
    )

    rows = await seeded_pool.fetch(
        """
        SELECT id, butler
        FROM public.model_dispatch_attempts
        WHERE outcome = 'quota_skip' AND id = ANY($1::bigint[])
        ORDER BY ts DESC, id DESC
        """,
        [first_id, second_id],
    )
    assert [row["id"] for row in rows] == [second_id, first_id]
    assert [row["butler"] for row in rows] == [
        "second-tied-attempt",
        "first-tied-attempt",
    ]


@pytest.mark.asyncio(loop_scope="session")
async def test_outcome_count_uses_the_index(seeded_pool: asyncpg.Pool) -> None:
    async with seeded_pool.acquire() as conn:
        plan = await _plan(
            conn,
            "SELECT count(*) FROM public.model_dispatch_attempts WHERE outcome = $1",
            "quota_skip",
        )
    assert _COUNT_INDEX in plan, f"outcome COUNT(*) did not use {_COUNT_INDEX}:\n{plan}"
