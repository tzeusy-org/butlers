"""Real-Postgres concurrency coverage for day-close cache containment."""

from __future__ import annotations

import asyncio
import shutil
from datetime import UTC, date, datetime
from types import SimpleNamespace

import asyncpg
import pytest

from butlers.chronicler.day_close_cache import day_close_cache_key, lock_day_close_cache_tuple
from butlers.chronicler.day_close_writer import DAY_CLOSE_TASK_NAME, write_day_close_cache
from butlers.db import register_jsonb_codec
from butlers.testing.migration import create_migrated_test_db, migration_db_name

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["chronicler"],
    )


@pytest.fixture
async def pool(migrated_db_url: str) -> asyncpg.Pool:
    # Three connections are needed for the blocker and the two overlapping
    # writers; a larger pool proves serialization comes from the DB lock.
    p = await asyncpg.create_pool(
        migrated_db_url,
        min_size=3,
        max_size=6,
        init=register_jsonb_codec,
    )
    await p.execute("TRUNCATE TABLE tier2_cache")
    await p.execute("TRUNCATE TABLE covered_local_days")
    yield p
    await p.close()


def _result(*, prose: str, date_label: str, timezone: str = "UTC") -> SimpleNamespace:
    return SimpleNamespace(
        success=True,
        output=prose,
        tool_calls=[
            {
                "name": "chronicler_day_close_bundle",
                "input": {"date_label": date_label, "timezone": timezone},
                "outcome": "success",
                "result": {
                    "date": date_label,
                    "citations": [],
                    "episodes": [],
                    "events": [],
                },
            }
        ],
    )


async def test_concurrent_invalid_candidate_cannot_overwrite_valid_cache_row(
    pool: asyncpg.Pool,
) -> None:
    """Both cache writes serialize on the same real PostgreSQL lock.

    While an external transaction holds the cache-key lock, the invalid and
    valid writers must both wait. Once released, either order leaves the valid
    prose renderable: a later invalid candidate observes and preserves it; a
    later valid candidate replaces an earlier audit-only invalid row.
    """
    run_at = datetime(2026, 4, 25, 1, 5, tzinfo=UTC)
    target_date = date(2026, 4, 24)
    cache_key = day_close_cache_key(target_date, "UTC")
    invalid_result = _result(
        prose="This candidate has an unbound day label.",
        date_label="2026-04-23",
    )
    valid_result = _result(
        prose="The day centered on a focused build and an evening walk.",
        date_label="2026-04-24",
    )
    invalid_task: asyncio.Task | None = None
    valid_task: asyncio.Task | None = None

    try:
        async with pool.acquire() as blocker:
            async with blocker.transaction():
                await lock_day_close_cache_tuple(blocker, target_date, "UTC")
                invalid_task = asyncio.create_task(
                    write_day_close_cache(
                        pool,
                        task_name=DAY_CLOSE_TASK_NAME,
                        result=invalid_result,
                        run_at=run_at,
                    )
                )
                valid_task = asyncio.create_task(
                    write_day_close_cache(
                        pool,
                        task_name=DAY_CLOSE_TASK_NAME,
                        result=valid_result,
                        run_at=run_at,
                    )
                )

                async with asyncio.timeout(5):
                    while (
                        await pool.fetchval(
                            """
                            SELECT count(*)
                            FROM pg_locks
                            WHERE locktype = 'transactionid'
                              AND NOT granted
                            """
                        )
                        < 2
                    ):
                        await asyncio.sleep(0.01)

                assert not invalid_task.done()
                assert not valid_task.done()

        assert invalid_task is not None
        assert valid_task is not None
        await asyncio.gather(invalid_task, valid_task)

        row = await pool.fetchrow(
            "SELECT prose, date_label, invalid_reason FROM tier2_cache WHERE cache_key = $1",
            cache_key,
        )
        assert row is not None
        assert row["prose"] == valid_result.output
        assert row["date_label"] == "2026-04-24"
        assert row["invalid_reason"] is None
    finally:
        pending = [
            task for task in (invalid_task, valid_task) if task is not None and not task.done()
        ]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)


async def test_different_timezone_tuples_do_not_block_each_other(pool: asyncpg.Pool) -> None:
    """A held UTC tuple lock cannot delay an otherwise independent SGT writer."""
    run_at = datetime(2026, 4, 25, 1, 5, tzinfo=UTC)
    target_date = date(2026, 4, 24)
    result = _result(
        prose="The Singapore local day stayed independent of the UTC cache row.",
        date_label="2026-04-24",
        timezone="Asia/Singapore",
    )

    async with pool.acquire() as blocker:
        async with blocker.transaction():
            await lock_day_close_cache_tuple(blocker, target_date, "UTC")
            async with asyncio.timeout(2):
                await write_day_close_cache(
                    pool,
                    task_name=DAY_CLOSE_TASK_NAME,
                    result=result,
                    run_at=run_at,
                    tz="Asia/Singapore",
                    target_date=target_date,
                )

    row = await pool.fetchrow(
        "SELECT cache_key FROM tier2_cache WHERE cache_key = $1",
        day_close_cache_key(target_date, "Asia/Singapore"),
    )
    assert row is not None
