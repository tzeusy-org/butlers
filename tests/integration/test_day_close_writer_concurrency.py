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
from tests.polling import poll_until

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]


@pytest.fixture
async def chronicler_pool(db, fresh_db) -> asyncpg.Pool:
    # The blocker and two overlapping writers need three connections; a larger
    # pool proves serialization comes from the DB lock.
    from tests.chronicler._private_memory_setup import open_prepared_chronicler_pool

    return await open_prepared_chronicler_pool(db, fresh_db, "chronicler", min_size=3, max_size=6)


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
    chronicler_pool: asyncpg.Pool,
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
        async with chronicler_pool.acquire() as blocker:
            async with blocker.transaction():
                await lock_day_close_cache_tuple(blocker, target_date, "UTC")
                invalid_task = asyncio.create_task(
                    write_day_close_cache(
                        chronicler_pool,
                        task_name=DAY_CLOSE_TASK_NAME,
                        result=invalid_result,
                        run_at=run_at,
                    )
                )
                valid_task = asyncio.create_task(
                    write_day_close_cache(
                        chronicler_pool,
                        task_name=DAY_CLOSE_TASK_NAME,
                        result=valid_result,
                        run_at=run_at,
                    )
                )

                async with asyncio.timeout(5):

                    async def _condition_met() -> bool:
                        return (
                            await chronicler_pool.fetchval(
                                """
                                SELECT count(*)
                                FROM pg_locks
                                WHERE locktype = 'transactionid'
                                  AND NOT granted
                                """
                            )
                        ) >= 2

                    await poll_until(_condition_met, interval_s=0.01)

                assert not invalid_task.done()
                assert not valid_task.done()

        assert invalid_task is not None
        assert valid_task is not None
        await asyncio.gather(invalid_task, valid_task)

        row = await chronicler_pool.fetchrow(
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


async def test_different_timezone_tuples_do_not_block_each_other(
    chronicler_pool: asyncpg.Pool,
) -> None:
    """A held UTC tuple lock cannot delay an otherwise independent SGT writer."""
    run_at = datetime(2026, 4, 25, 1, 5, tzinfo=UTC)
    target_date = date(2026, 4, 24)
    result = _result(
        prose="The Singapore local day stayed independent of the UTC cache row.",
        date_label="2026-04-24",
        timezone="Asia/Singapore",
    )

    async with chronicler_pool.acquire() as blocker:
        async with blocker.transaction():
            await lock_day_close_cache_tuple(blocker, target_date, "UTC")
            async with asyncio.timeout(2):
                await write_day_close_cache(
                    chronicler_pool,
                    task_name=DAY_CLOSE_TASK_NAME,
                    result=result,
                    run_at=run_at,
                    tz="Asia/Singapore",
                    target_date=target_date,
                )

    row = await chronicler_pool.fetchrow(
        "SELECT cache_key FROM tier2_cache WHERE cache_key = $1",
        day_close_cache_key(target_date, "Asia/Singapore"),
    )
    assert row is not None
