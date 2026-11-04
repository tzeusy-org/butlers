"""Real-Postgres integration tests for the chronicler write-back DB path
(bu-93y4rt, tasks.md §8).

Mocked collaborators are fine for the orchestrator contract
(``roster/chronicler/tests/test_writeback.py``), but the DB-touching code —
``list_daily_rollups`` / ``list_daily_rollup_flags`` reads — must be proven
against the real migrated chronicler schema (mocked-pool green ≠ integration
green). These tests seed real rollup / flag rows and run
``run_day_close_writeback`` end to end with a fake (recording) ``store_fact``
collaborator, so no memory-module tables are required.
"""

from __future__ import annotations

import shutil
from datetime import date

import asyncpg
import pytest

from butlers.chronicler.contracts import INITIAL_SOURCES, seed_source_registry
from butlers.chronicler.storage import (
    upsert_daily_rollup,
    upsert_daily_rollup_flag,
)
from butlers.chronicler.writeback import (
    SOURCE_BUTLER,
    run_day_close_writeback,
)

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]


@pytest.fixture
async def chronicler_pool(db, fresh_db) -> asyncpg.Pool:
    from tests.chronicler._private_memory_setup import open_prepared_chronicler_pool

    pool = await open_prepared_chronicler_pool(db, fresh_db, "chronicler", max_size=5)
    await seed_source_registry(pool, sources=INITIAL_SOURCES)
    return pool


_DAY = date(2026, 7, 9)


class _StoreSpy:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return "fact-id"


async def test_run_day_close_writeback_end_to_end(chronicler_pool):
    # Baseline: 10 days of an 8h work usual → an anomaly bar the closed day can clear.
    for d in range(29, 31):  # 2026-06-29, 06-30
        await upsert_daily_rollup(
            chronicler_pool,
            local_date=date(2026, 6, d),
            lane="work",
            seconds=8 * 3600,
            episode_count=1,
            timezone="UTC",
        )
    for d in range(1, 9):  # 2026-07-01 .. 07-08
        await upsert_daily_rollup(
            chronicler_pool,
            local_date=date(2026, 7, d),
            lane="work",
            seconds=8 * 3600,
            episode_count=1,
            timezone="UTC",
        )
    # Closed day 07-09: supported explicit zero work, not absent material.
    await upsert_daily_rollup(
        chronicler_pool, local_date=_DAY, lane="work", seconds=0, episode_count=1, timezone="UTC"
    )
    await upsert_daily_rollup(
        chronicler_pool,
        local_date=_DAY,
        lane="sleep",
        seconds=7 * 3600,
        episode_count=1,
        timezone="UTC",
    )
    # A pending-backfill flag → one self-reminder.
    await upsert_daily_rollup_flag(
        chronicler_pool,
        local_date=_DAY,
        flag_type="feeder_dark",
        severity="warning",
        detail={"src": ["x"]},
    )
    store = _StoreSpy()
    result = await run_day_close_writeback(
        chronicler_pool,
        day_date=_DAY,
        timezone="UTC",
        store_fact_fn=store,
    )

    # Own-schema insight + self-reminder were written; every write is chronicler-owned.
    assert result.insights_written >= 1
    assert result.self_reminders_written == 1
    assert store.calls
    assert all(c["metadata"]["source"] == SOURCE_BUTLER for c in store.calls)


async def test_real_absent_and_zero_filled_target_do_not_persist_skew(chronicler_pool):
    for prior in range(1, 9):
        await upsert_daily_rollup(
            chronicler_pool,
            local_date=date(2026, 7, prior),
            lane="work",
            seconds=8 * 3600,
            episode_count=1,
            timezone="UTC",
        )
    store = _StoreSpy()
    await run_day_close_writeback(
        chronicler_pool, day_date=_DAY, timezone="UTC", store_fact_fn=store
    )
    assert store.calls == []
    await upsert_daily_rollup(
        chronicler_pool, local_date=_DAY, lane="work", seconds=0, episode_count=0, timezone="UTC"
    )
    await run_day_close_writeback(
        chronicler_pool, day_date=_DAY, timezone="UTC", store_fact_fn=store
    )
    assert store.calls == []
    await upsert_daily_rollup(
        chronicler_pool, local_date=_DAY, lane="work", seconds=0, episode_count=1, timezone="UTC"
    )
    await run_day_close_writeback(
        chronicler_pool, day_date=_DAY, timezone="UTC", store_fact_fn=store
    )
    assert store.calls
