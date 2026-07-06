"""Real-Postgres integration tests for the daily-rollup narrative columns
and the once-daily labeling pass (bu-v9y18, telemetry-distillation bead 6,
migration chronicler_020).

Mocked-pool tests cannot prove the central design guarantee this bead
depends on: that a label written to ``daily_rollup_flags.narrative`` (or
``daily_rollups.narrative``) survives a *later* re-run of the deterministic
jobs that own those tables (``rollups.materialize_daily_rollups``,
``flags.evaluate_and_write_daily_flags`` — both re-upsert on every run,
hourly, over a trailing window per ``chronicler_rollup_daily``). If either
upsert's SQL ever grows a reference to ``narrative`` in its ``SET`` clause,
a label would be silently wiped within the hour; only a real re-run against
a real Postgres row proves it is not. These tests run the real migration
chain (through chronicler_020) against a migrated Postgres container.
"""

from __future__ import annotations

import shutil
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import asyncpg
import pytest

from butlers.chronicler.contracts import INITIAL_SOURCES, seed_source_registry
from butlers.chronicler.flags import (
    FLAG_SLEEP_MISSING,
    SOURCE_CRON_MINUTES,
    evaluate_and_write_daily_flags,
)
from butlers.chronicler.models import Episode, Layer
from butlers.chronicler.narration import narrate_daily_rollup
from butlers.chronicler.rollups import materialize_daily_rollups
from butlers.chronicler.storage import (
    list_daily_rollup_flags,
    list_daily_rollups,
    mark_source_active,
    set_daily_rollup_day_narrative,
    set_daily_rollup_flag_narrative,
    upsert_checkpoint,
    upsert_daily_rollup,
    upsert_daily_rollup_flag,
    upsert_episode,
)
from butlers.db import register_jsonb_codec
from butlers.testing.migration import create_migrated_test_db, migration_db_name

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]

_TZ = ZoneInfo("Asia/Singapore")
_LOCAL_DATE = date(2026, 7, 1)
_OTHER_DATE = date(2026, 7, 2)


def _local(d: date, hour: int, minute: int = 0) -> datetime:
    return datetime(d.year, d.month, d.day, hour, minute, tzinfo=_TZ).astimezone(UTC)


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
    await p.execute("TRUNCATE TABLE daily_rollup_flags, daily_rollups CASCADE")
    await p.execute("TRUNCATE TABLE episodes, point_events CASCADE")
    await p.execute("TRUNCATE TABLE source_adapter_state, projection_checkpoints CASCADE")
    await seed_source_registry(p, sources=INITIAL_SOURCES)
    yield p
    await p.close()


async def _mark_all_healthy(pool, *, now: datetime) -> None:
    """Mark every source the flag rules gate as active with a fresh
    checkpoint relative to *now*, so `evaluate_and_write_daily_flags` never
    suppresses a behavioral flag as feeder-dark in these tests. Mirrors
    ``test_daily_rollup_flags_integration.py``'s helper of the same name."""
    fresh_at = now - timedelta(minutes=1)
    for source_name in SOURCE_CRON_MINUTES:
        await mark_source_active(pool, source_name, active=True)
        await upsert_checkpoint(pool, source_name, watermark=fresh_at, success=True)
        await pool.execute(
            "UPDATE projection_checkpoints SET last_success_at = $2, last_run_at = $2 "
            "WHERE source_name = $1",
            source_name,
            fresh_at,
        )


async def test_narrative_columns_default_to_null(pool) -> None:
    await upsert_daily_rollup(
        pool, local_date=_LOCAL_DATE, lane="sleep", seconds=0, episode_count=0
    )
    await upsert_daily_rollup_flag(
        pool, local_date=_LOCAL_DATE, flag_type=FLAG_SLEEP_MISSING, severity="warning", detail={}
    )

    rollups = await list_daily_rollups(pool, local_date=_LOCAL_DATE)
    flags = await list_daily_rollup_flags(pool, local_date=_LOCAL_DATE)

    assert rollups[0].narrative is None
    assert flags[0].narrative is None


async def test_set_day_narrative_writes_every_lane_row_for_the_date_only(pool) -> None:
    await upsert_daily_rollup(
        pool, local_date=_LOCAL_DATE, lane="sleep", seconds=0, episode_count=0
    )
    await upsert_daily_rollup(
        pool, local_date=_LOCAL_DATE, lane="work", seconds=100, episode_count=1
    )
    await upsert_daily_rollup(
        pool, local_date=_OTHER_DATE, lane="sleep", seconds=0, episode_count=0
    )

    rows_updated = await set_daily_rollup_day_narrative(
        pool, local_date=_LOCAL_DATE, narrative="A quiet day."
    )
    assert rows_updated == 2

    same_day_rows = await list_daily_rollups(pool, local_date=_LOCAL_DATE)
    assert {r.narrative for r in same_day_rows} == {"A quiet day."}

    other_day_rows = await list_daily_rollups(pool, local_date=_OTHER_DATE)
    assert other_day_rows[0].narrative is None


async def test_set_flag_narrative_targets_only_the_named_flag(pool) -> None:
    await upsert_daily_rollup_flag(
        pool, local_date=_LOCAL_DATE, flag_type=FLAG_SLEEP_MISSING, severity="warning", detail={}
    )
    await upsert_daily_rollup_flag(
        pool, local_date=_LOCAL_DATE, flag_type="routine_break", severity="info", detail={}
    )

    updated = await set_daily_rollup_flag_narrative(
        pool, local_date=_LOCAL_DATE, flag_type=FLAG_SLEEP_MISSING, narrative="No sleep logged."
    )
    assert updated is not None
    assert updated.narrative == "No sleep logged."

    flags_by_type = {
        f.flag_type: f for f in await list_daily_rollup_flags(pool, local_date=_LOCAL_DATE)
    }
    assert flags_by_type[FLAG_SLEEP_MISSING].narrative == "No sleep logged."
    assert flags_by_type["routine_break"].narrative is None


async def test_set_flag_narrative_is_noop_for_nonexistent_flag(pool) -> None:
    result = await set_daily_rollup_flag_narrative(
        pool, local_date=_LOCAL_DATE, flag_type="lane_share_outlier", narrative="whatever"
    )
    assert result is None


async def test_flag_narrative_survives_flag_reevaluation(pool) -> None:
    """The central design guarantee: `flags.py`'s hourly reconciliation must
    never wipe a label written to the dedicated `narrative` column, even
    though it fully overwrites `detail` on every run for a flag that still
    holds."""
    now = _local(_LOCAL_DATE, 23, 0)
    await _mark_all_healthy(pool, now=now)

    # Zero sleep, healthy sources -> sleep_missing holds.
    await upsert_daily_rollup(
        pool, local_date=_LOCAL_DATE, lane="sleep", seconds=0, episode_count=0
    )
    result = await evaluate_and_write_daily_flags(
        pool, local_date=_LOCAL_DATE, timezone="Asia/Singapore", now=now
    )
    assert result["flags"][FLAG_SLEEP_MISSING] is True

    await set_daily_rollup_flag_narrative(
        pool, local_date=_LOCAL_DATE, flag_type=FLAG_SLEEP_MISSING, narrative="No sleep logged."
    )

    # Re-run the deterministic evaluator again (same conditions still hold,
    # mirroring the hourly chronicler_rollup_daily re-evaluation).
    result_again = await evaluate_and_write_daily_flags(
        pool, local_date=_LOCAL_DATE, timezone="Asia/Singapore", now=now
    )
    assert result_again["flags"][FLAG_SLEEP_MISSING] is True

    flags_by_type = {
        f.flag_type: f for f in await list_daily_rollup_flags(pool, local_date=_LOCAL_DATE)
    }
    assert flags_by_type[FLAG_SLEEP_MISSING].narrative == "No sleep logged."


async def test_day_narrative_survives_rollup_rematerialization(pool) -> None:
    """Same guarantee, for `rollups.py`'s upsert against `daily_rollups`."""
    d = _LOCAL_DATE
    await upsert_episode(
        pool,
        Episode(
            source_name="steam.play_history",
            source_ref="narration-it-gaming-1",
            episode_type="play_episode",
            start_at=_local(d, 20, 0),
            end_at=_local(d, 22, 0),
            layer=Layer.ACTIVITY,
        ),
    )
    now = _local(d + timedelta(days=1), 1, 0)

    await materialize_daily_rollups(pool, timezone="Asia/Singapore", lookback_days=3, now=now)
    await set_daily_rollup_day_narrative(
        pool, local_date=d, narrative="Played games in the evening."
    )

    # Re-run the materializer (idempotent re-run over the same trailing window).
    await materialize_daily_rollups(pool, timezone="Asia/Singapore", lookback_days=3, now=now)

    rows = await list_daily_rollups(pool, local_date=d)
    assert {r.narrative for r in rows} == {"Played games in the evening."}


async def test_narrate_daily_rollup_end_to_end_against_real_rows(pool, monkeypatch) -> None:
    """Exercises the real DB reads/writes of the orchestrator end-to-end;
    the LLM call itself is stubbed (no real network/model dependency in
    integration tests)."""
    d = _LOCAL_DATE
    now = _local(d + timedelta(days=1), 1, 0)

    await _mark_all_healthy(pool, now=now)
    await materialize_daily_rollups(pool, timezone="Asia/Singapore", lookback_days=3, now=now)
    await evaluate_and_write_daily_flags(pool, local_date=d, timezone="Asia/Singapore", now=now)

    class _StubDispatcher:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def call(self, prompt: str, system_prompt: str = "") -> str:
            return '{"day_summary": "An ordinary day.", "flag_labels": {}}'

    monkeypatch.setattr("butlers.chronicler.narration.DiscretionDispatcher", _StubDispatcher)

    result = await narrate_daily_rollup(pool, local_date=d, timezone="Asia/Singapore")

    assert result["status"] == "labeled"
    rows = await list_daily_rollups(pool, local_date=d)
    assert {r.narrative for r in rows} == {"An ordinary day."}
