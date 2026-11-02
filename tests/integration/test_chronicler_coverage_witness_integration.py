"""Real-Postgres integration tests for the covered-local-day witness and the
no_data/unavailable/quiet precedence it drives (bu-ep4ks.1,
clarify-chronicles-narrative-truth).

Mocked-pool unit tests (``tests/chronicler/test_editorial.py``) cover the
precedence logic with monkeypatched coverage helpers. These tests prove the
real read/write path against the migrated ``covered_local_days`` table and
``tier2_cache`` admission columns added by ``chronicler_023`` — the exact
gap that shipped zero runtime code in bu-27dxl.1.1: an outage or an
unproven historical day must never render as "Quiet day."
"""

from __future__ import annotations

import shutil
from datetime import UTC, date, datetime
from types import SimpleNamespace

import asyncpg
import pytest
from fastmcp import FastMCP

from butlers.chronicler.day_close_writer import DAY_CLOSE_TASK_NAME, write_day_close_cache
from butlers.chronicler.editorial import (
    _fetch_earliest_covered_date,
    _fetch_recent_days,
    _is_local_day_covered,
    compose_briefing_payload,
    record_coverage_witness,
)
from butlers.db import register_jsonb_codec
from butlers.testing.mcp import registered_tools
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
async def pool(migrated_db_url: str):
    p = await asyncpg.create_pool(
        migrated_db_url, min_size=1, max_size=5, init=register_jsonb_codec
    )
    await p.execute("TRUNCATE TABLE covered_local_days")
    await p.execute("TRUNCATE TABLE tier2_cache")
    await p.execute("TRUNCATE TABLE episodes CASCADE")
    yield p
    await p.close()


# ---------------------------------------------------------------------------
# covered_local_days table shape + witness round-trip
# ---------------------------------------------------------------------------


async def test_migration_creates_covered_local_days_table(pool) -> None:
    row = await pool.fetchrow(
        "SELECT to_regclass('covered_local_days') AS reg",
    )
    assert row["reg"] is not None


async def test_migration_marks_coverage_witness_provenance(pool) -> None:
    columns = await pool.fetch(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'covered_local_days' AND column_name = 'origin'
        """
    )
    assert [row["column_name"] for row in columns] == ["origin"]


async def test_migration_adds_tier2_cache_admission_columns(pool) -> None:
    rows = await pool.fetch(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'tier2_cache' AND column_name IN ('date_label', 'invalid_reason')
        """
    )
    columns = {r["column_name"] for r in rows}
    assert columns == {"date_label", "invalid_reason"}


async def test_record_coverage_witness_is_idempotent(pool) -> None:
    await record_coverage_witness(pool, date(2026, 5, 1), "UTC")
    await record_coverage_witness(pool, date(2026, 5, 1), "UTC")

    count = await pool.fetchval(
        "SELECT count(*) FROM covered_local_days WHERE local_date = $1 AND timezone = $2",
        date(2026, 5, 1),
        "UTC",
    )
    assert count == 1
    assert (
        await pool.fetchval(
            "SELECT origin FROM covered_local_days WHERE local_date = $1 AND timezone = $2",
            date(2026, 5, 1),
            "UTC",
        )
        == "day_close_success"
    )


async def test_record_coverage_witness_promotes_legacy_row(pool) -> None:
    await pool.execute(
        """
        INSERT INTO covered_local_days (local_date, timezone, origin)
        VALUES ($1, $2, 'legacy_unverified')
        """,
        date(2026, 5, 1),
        "UTC",
    )

    await record_coverage_witness(pool, date(2026, 5, 1), "UTC")

    assert (
        await pool.fetchval(
            "SELECT origin FROM covered_local_days WHERE local_date = $1 AND timezone = $2",
            date(2026, 5, 1),
            "UTC",
        )
        == "day_close_success"
    )


async def test_is_local_day_covered_reflects_recorded_witness(pool) -> None:
    assert await _is_local_day_covered(pool, date(2026, 5, 2), "UTC") is False
    await record_coverage_witness(pool, date(2026, 5, 2), "UTC")
    assert await _is_local_day_covered(pool, date(2026, 5, 2), "UTC") is True


async def test_legacy_witness_is_not_authoritative_coverage(pool) -> None:
    await pool.execute(
        """
        INSERT INTO covered_local_days (local_date, timezone, origin)
        VALUES ($1, $2, 'legacy_unverified')
        """,
        date(2026, 5, 2),
        "UTC",
    )

    assert await _is_local_day_covered(pool, date(2026, 5, 2), "UTC") is False
    assert await _fetch_earliest_covered_date(pool, "UTC") is None


async def test_recent_days_index_excludes_legacy_witnesses(pool) -> None:
    await record_coverage_witness(pool, date(2026, 5, 9), "UTC")
    await pool.execute(
        """
        INSERT INTO covered_local_days (local_date, timezone, origin)
        VALUES ($1, $2, 'legacy_unverified')
        """,
        date(2026, 5, 8),
        "UTC",
    )

    recent_days = await _fetch_recent_days(
        pool,
        datetime(2026, 5, 10, 12, 0, tzinfo=UTC),
        days=3,
        tz_name="UTC",
    )

    assert [recent_day.date for recent_day in recent_days] == ["2026-05-09"]


async def test_earliest_covered_date_is_min_across_witnesses(pool) -> None:
    await record_coverage_witness(pool, date(2026, 5, 5), "UTC")
    await record_coverage_witness(pool, date(2026, 5, 2), "UTC")
    await record_coverage_witness(pool, date(2026, 5, 9), "UTC")

    earliest = await _fetch_earliest_covered_date(pool, "UTC")
    assert earliest == date(2026, 5, 2)


async def test_earliest_covered_date_scoped_by_timezone(pool) -> None:
    await record_coverage_witness(pool, date(2026, 5, 1), "UTC")
    await record_coverage_witness(pool, date(2026, 4, 1), "Asia/Singapore")

    assert await _fetch_earliest_covered_date(pool, "UTC") == date(2026, 5, 1)
    assert await _fetch_earliest_covered_date(pool, "Asia/Singapore") == date(2026, 4, 1)


# ---------------------------------------------------------------------------
# compose_briefing_payload precedence, end to end against a real pool
# ---------------------------------------------------------------------------


async def test_compose_unavailable_before_any_witness_recorded(pool) -> None:
    """No coverage floor exists yet: every settled day is unavailable, never
    a fabricated quiet day."""
    now = datetime(2026, 5, 10, 12, 0, tzinfo=UTC)

    payload = await compose_briefing_payload(pool, date(2026, 5, 5), "UTC", now=now)

    assert payload.state_class == "unavailable"
    assert payload.covered_and_available is False
    assert payload.earliest_date is None


async def test_compose_no_data_before_recorded_floor(pool) -> None:
    await record_coverage_witness(pool, date(2026, 5, 5), "UTC")
    now = datetime(2026, 5, 10, 12, 0, tzinfo=UTC)

    payload = await compose_briefing_payload(pool, date(2026, 5, 1), "UTC", now=now)

    assert payload.state_class == "no_data"
    assert payload.covered_and_available is False
    assert payload.earliest_date == "2026-05-05"


async def test_compose_unavailable_for_coverage_gap_after_floor(pool) -> None:
    """A day on/after the floor with no witness of its own is a gap, not proof
    of absence — stays unavailable, never no_data or quiet."""
    await record_coverage_witness(pool, date(2026, 5, 1), "UTC")
    await record_coverage_witness(pool, date(2026, 5, 9), "UTC")
    now = datetime(2026, 5, 10, 12, 0, tzinfo=UTC)

    payload = await compose_briefing_payload(pool, date(2026, 5, 5), "UTC", now=now)

    assert payload.state_class == "unavailable"
    assert payload.covered_and_available is False


async def test_compose_quiet_for_covered_empty_day(pool) -> None:
    await record_coverage_witness(pool, date(2026, 5, 5), "UTC")
    now = datetime(2026, 5, 10, 12, 0, tzinfo=UTC)

    payload = await compose_briefing_payload(pool, date(2026, 5, 5), "UTC", now=now)

    assert payload.state_class == "quiet"
    assert payload.covered_and_available is True
    assert payload.earliest_date == "2026-05-05"


@pytest.mark.parametrize(
    "tool_calls",
    [
        [],
        [
            {
                "name": "chronicler_day_close_bundle",
                "input": {"date_label": "2026-05-05", "timezone": "UTC"},
                "outcome": "success",
                "result": {
                    "date": "2026-05-04",
                    "citations": [],
                    "episodes": [],
                    "events": [],
                },
            }
        ],
        [
            {
                "name": "chronicler_day_close_bundle",
                "input": {"date_label": "2026-05-01", "timezone": "UTC"},
                "outcome": "success",
                "result": {
                    "date": "2026-05-01",
                    "citations": [],
                    "episodes": [],
                    "events": [],
                },
            }
        ],
        [
            {
                "name": "chronicler_day_close_bundle",
                "input": {"date_label": "2026-05-05", "timezone": "UTC"},
                "outcome": "success",
                "result": {"date": "2026-05-05", "citations": [], "events": []},
            }
        ],
        [
            {
                "name": "chronicler_day_close_bundle",
                "input": {"date_label": "2026-05-05", "timezone": "UTC"},
                "outcome": "success",
                "result": {"date": "2026-05-05", "citations": [], "episodes": []},
            }
        ],
        [
            {
                "name": "chronicler_day_close_bundle",
                "input": {"date_label": "2026-05-05", "timezone": "UTC"},
                "outcome": "success",
                "result": {
                    "date": "2026-05-05",
                    "citations": [],
                    "episodes": {"unexpected": "shape"},
                    "events": [],
                },
            }
        ],
        [
            {
                "name": "chronicler_day_close_bundle",
                "input": {"date_label": "2026-05-05", "timezone": "UTC"},
                "outcome": "success",
                "result": {
                    "date": "2026-05-05",
                    "citations": [],
                    "episodes": [],
                    "events": {"unexpected": "shape"},
                },
            }
        ],
    ],
    ids=[
        "no-bundle",
        "wrong-date",
        "historical-mismatch",
        "missing-episodes",
        "missing-events",
        "nonlist-episodes",
        "nonlist-events",
    ],
)
async def test_unproven_day_close_capture_leaves_historical_briefing_unavailable(
    pool, tool_calls
) -> None:
    """A successful dispatch without a matching target capture never proves history."""
    target = date(2026, 5, 5)
    result = SimpleNamespace(success=True, output="", tool_calls=tool_calls)

    await write_day_close_cache(
        pool,
        task_name=DAY_CLOSE_TASK_NAME,
        result=result,
        run_at=datetime(2026, 5, 6, 1, 5, tzinfo=UTC),
        tz="UTC",
    )

    assert await _is_local_day_covered(pool, target, "UTC") is False
    payload = await compose_briefing_payload(
        pool,
        target,
        "UTC",
        now=datetime(2026, 5, 10, 12, 0, tzinfo=UTC),
    )
    assert payload.state_class == "unavailable"
    assert payload.covered_and_available is False


async def test_valid_empty_canonical_day_close_capture_makes_historical_briefing_quiet(
    pool,
) -> None:
    """A matching canonical empty bundle is durable proof of a quiet closed day."""
    target = date(2026, 5, 5)
    result = SimpleNamespace(
        success=True,
        output="",
        tool_calls=[
            {
                "name": "chronicler_day_close_bundle",
                "input": {"date_label": "2026-05-05", "timezone": "UTC"},
                "outcome": "success",
                "result": {
                    "date": "2026-05-05",
                    "citations": [],
                    "episodes": [],
                    "events": [],
                },
            }
        ],
    )

    await write_day_close_cache(
        pool,
        task_name=DAY_CLOSE_TASK_NAME,
        result=result,
        run_at=datetime(2026, 5, 6, 1, 5, tzinfo=UTC),
        tz="UTC",
    )

    assert await _is_local_day_covered(pool, target, "UTC") is True
    payload = await compose_briefing_payload(
        pool,
        target,
        "UTC",
        now=datetime(2026, 5, 10, 12, 0, tzinfo=UTC),
    )
    assert payload.state_class == "quiet"
    assert payload.covered_and_available is True


async def test_compose_today_bypasses_coverage_gate_even_when_uncovered(pool) -> None:
    """Today has not closed yet, so it never renders unavailable purely for
    lacking a witness -- unlike a settled day."""
    now = datetime(2026, 5, 10, 12, 0, tzinfo=UTC)

    payload = await compose_briefing_payload(pool, date(2026, 5, 10), "UTC", now=now)

    assert payload.state_class == "quiet"
    assert payload.covered_and_available is True


@pytest.fixture(scope="module")
def continuation_migrated_url(postgres_container):
    """Synchronous full production bootstrap and chains before any async pool."""
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "chronicler"],
        schemas={"core": "chronicler", "chronicler": "chronicler"},
    )


@pytest.fixture
async def continuation_pools(continuation_migrated_url):
    async def initialize(conn):
        await register_jsonb_codec(conn)
        await conn.execute("SET ROLE butler_chronicler_rw")

    runtime = await asyncpg.create_pool(
        continuation_migrated_url,
        min_size=1,
        max_size=3,
        init=initialize,
        server_settings={"search_path": "chronicler,public"},
    )
    observer = await asyncpg.create_pool(
        continuation_migrated_url,
        min_size=1,
        max_size=2,
        init=register_jsonb_codec,
        server_settings={"search_path": "chronicler,public"},
    )
    try:
        from butlers.chronicler.contracts import INITIAL_SOURCES, seed_source_registry

        await seed_source_registry(runtime, sources=INITIAL_SOURCES)
        assert await runtime.fetchval("SELECT current_user") == "butler_chronicler_rw"
        yield runtime, observer
    finally:
        await runtime.close()
        await observer.close()


async def test_continuation_real_registered_empty_capture_commit_restart_and_rate(
    continuation_pools,
):
    """Real registered reader/canonical writer, runtime role, independent readback."""
    import asyncio

    from butlers.chronicler.day_close_continuation import DayCloseContinuation
    from butlers.chronicler.day_close_refresh import DayCloseRefreshService
    from roster.chronicler.modules import ChroniclerModule

    runtime, observer = continuation_pools
    target = date(2026, 4, 1)
    await observer.execute(
        "INSERT INTO scheduled_tasks(name,cron,prompt,max_token_budget) VALUES($1,'5 1 * * *',$2,1200) "
        "ON CONFLICT(name) DO UPDATE SET enabled=true,prompt=EXCLUDED.prompt",
        DAY_CLOSE_TASK_NAME,
        "Close the captured day",
    )
    await observer.execute(
        "INSERT INTO daily_rollups(local_date,timezone,lane,seconds,episode_count) "
        "VALUES($1,'UTC','work',0,0) ON CONFLICT(local_date,lane) DO NOTHING",
        target,
    )
    mcp = FastMCP("chronicler-continuation")

    module = ChroniclerModule()
    await module.register_tools(mcp, None, SimpleNamespace(pool=runtime), "chronicler")
    registered = await registered_tools(mcp)
    calls = []

    async def dispatch(**kwargs):
        calls.append(kwargs)
        bundle = await registered["chronicler_day_close_bundle"](target.isoformat(), "UTC")
        return SimpleNamespace(
            success=True,
            output="",
            tool_calls=[
                {
                    "name": "chronicler_day_close_bundle",
                    "outcome": "success",
                    "input": {"date_label": target.isoformat(), "timezone": "UTC"},
                    "result": bundle,
                }
            ],
        )

    # Seed an actually captured prior quiet day; zero-fill alone is not
    # automatic refresh authority. Backdate only this synthetic historical
    # witness so the real 24-hour production rate window has elapsed.
    initial = await dispatch()
    await write_day_close_cache(
        runtime,
        task_name=DAY_CLOSE_TASK_NAME,
        result=initial,
        run_at=datetime(2026, 4, 2, 12, tzinfo=UTC),
        tz="UTC",
        target_date=target,
    )
    await observer.execute(
        "UPDATE covered_local_days SET covered_at=$2 WHERE local_date=$1 AND timezone='UTC'",
        target,
        datetime(2026, 4, 2, 12, tzinfo=UTC),
    )
    calls.clear()
    service = DayCloseRefreshService(runtime, SimpleNamespace(_dispatch_scheduled_task=dispatch))
    continuation = DayCloseContinuation(service, "UTC")
    try:
        results = await asyncio.gather(continuation.resume_once(), service.refresh(target, "UTC"))
        assert any(result.get("quiet") is True for result in results)
        assert len(calls) == 1
        assert calls[0]["max_token_budget"] == 1200
        assert (
            await observer.fetchval(
                "SELECT origin FROM covered_local_days WHERE local_date=$1 AND timezone='UTC'",
                target,
            )
            == "day_close_success"
        )
        pending = await observer.fetchval(
            "SELECT value FROM state WHERE key=$1", "day_close:pending:day_close:2026-04-01:tz:UTC"
        )
        # If the dashboard wins, continuation remains pending/rate-deferred,
        # never claiming its own revision processed from another call's status.
        assert pending["revision"]
        restarted = DayCloseContinuation(service, "UTC")
        assert (await restarted.resume_once())["status"] in {"unchanged", "error"}
        assert len(calls) == 1
    finally:
        await service.close(1)
        await service.settle()


async def test_continuation_pending_state_rollback_and_revision_removal(continuation_pools):
    from butlers.chronicler.day_close_continuation import DayCloseContinuation
    from butlers.chronicler.day_close_refresh import DayCloseRefreshService
    from butlers.chronicler.models import Episode, Layer, Precision, Privacy
    from butlers.chronicler.storage import upsert_episode

    runtime, observer = continuation_pools
    target = date(2026, 4, 2)
    service = DayCloseRefreshService(runtime, SimpleNamespace())
    continuation = DayCloseContinuation(service, "UTC")
    before = await continuation.revision(target)
    episode = Episode(
        source_name="core.sessions",
        source_ref="synthetic:continuation:rollback",
        episode_type="session_episode",
        start_at=datetime(2026, 4, 2, 12, tzinfo=UTC),
        end_at=datetime(2026, 4, 2, 13, tzinfo=UTC),
        precision=Precision.EXACT,
        title="Synthetic work",
        payload={},
        privacy=Privacy.NORMAL,
        layer=Layer.ACTIVITY,
    )
    async with runtime.acquire() as conn:
        tx = conn.transaction()
        await tx.start()
        await upsert_episode(conn, episode)
        await tx.rollback()
    assert await continuation.revision(target) == before
    await upsert_episode(runtime, episode)
    changed = await continuation.revision(target)
    assert changed != before
    await continuation._save(
        "day_close:pending:day_close:2026-04-02:tz:UTC",
        {"date": target.isoformat(), "timezone": "UTC", "revision": changed},
    )
    assert await observer.fetchval(
        "SELECT value FROM state WHERE key=$1", "day_close:pending:day_close:2026-04-02:tz:UTC"
    ) == {"date": target.isoformat(), "timezone": "UTC", "revision": changed}
    await observer.execute(
        "UPDATE episodes SET tombstone_at=now() WHERE source_ref=$1", episode.source_ref
    )
    assert await continuation.revision(target) == before
    assert target in await continuation._discover()
    assert not service.has_pending


async def test_continuation_full_migrated_runtime_writeback_preserves_unknown_vs_zero(
    continuation_pools,
):
    from datetime import timedelta

    from butlers.chronicler.aggregations import sources_for_lane
    from butlers.chronicler.storage import upsert_daily_rollup, upsert_daily_rollup_flag
    from butlers.chronicler.writeback import run_day_close_writeback

    runtime, observer = continuation_pools
    target = date(2026, 4, 20)
    for offset in range(1, 9):
        await upsert_daily_rollup(
            runtime,
            local_date=target - timedelta(days=offset),
            lane="work",
            seconds=28800,
            episode_count=1,
            timezone="UTC",
        )
    calls = []

    async def store(**kwargs):
        calls.append(kwargs)

    await run_day_close_writeback(runtime, day_date=target, timezone="UTC", store_fact_fn=store)
    assert calls == []
    await upsert_daily_rollup(
        runtime, local_date=target, lane="work", seconds=0, episode_count=0, timezone="UTC"
    )
    await run_day_close_writeback(runtime, day_date=target, timezone="UTC", store_fact_fn=store)
    assert calls == []
    await upsert_daily_rollup(
        runtime, local_date=target, lane="work", seconds=0, episode_count=1, timezone="UTC"
    )
    assert (
        await observer.fetchval(
            "SELECT episode_count FROM daily_rollups WHERE local_date=$1 AND lane='work'", target
        )
        == 1
    )
    await run_day_close_writeback(runtime, day_date=target, timezone="UTC", store_fact_fn=store)
    assert calls

    # Positive historical counts are not availability when the real per-day
    # producer persisted feeder-dark flags for the same lane.
    calls.clear()
    for offset in range(1, 9):
        await upsert_daily_rollup_flag(
            runtime,
            local_date=target - timedelta(days=offset),
            flag_type="feeder_dark",
            detail={"dark_sources": sorted(sources_for_lane("work"))},
        )
    assert (
        await observer.fetchval(
            "SELECT count(*) FROM daily_rollup_flags WHERE local_date BETWEEN $1 AND $2 "
            "AND flag_type='feeder_dark'",
            target - timedelta(days=8),
            target - timedelta(days=1),
        )
        == 8
    )
    await run_day_close_writeback(runtime, day_date=target, timezone="UTC", store_fact_fn=store)
    assert calls == []
