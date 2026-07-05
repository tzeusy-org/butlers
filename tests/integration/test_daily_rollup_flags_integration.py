"""Real-Postgres integration tests for chronicler.daily_rollup_flags
(bu-v76a7, telemetry-distillation bead 4).

Mirrors ``tests/integration/test_daily_rollups_integration.py``'s fixture
shape. Mocked-pool unit tests (``tests/chronicler/test_flags.py``) cover
every rule's pure logic; these tests prove the real write path against a
migrated Postgres container: the flag-write/delete SQL actually works, the
``(local_date, flag_type)`` unique constraint holds under a real re-run, and
the feeder_dark-suppresses-derived-flags behavior survives an end-to-end run
through ``evaluate_and_write_daily_flags`` reading real
``source_adapter_state``/``projection_checkpoints``/``daily_rollups`` rows.
"""

from __future__ import annotations

import shutil
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import asyncpg
import pytest

from butlers.chronicler.contracts import INITIAL_SOURCES, seed_source_registry
from butlers.chronicler.flags import (
    FLAG_FEEDER_DARK,
    FLAG_LANE_SHARE_OUTLIER,
    FLAG_ROUTINE_BREAK,
    FLAG_SLEEP_MISSING,
    SOURCE_CRON_MINUTES,
    evaluate_and_write_daily_flags,
)
from butlers.chronicler.storage import (
    list_daily_rollup_flags,
    mark_source_active,
    upsert_checkpoint,
    upsert_daily_rollup,
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
_NOW = datetime(2026, 7, 2, 8, 0, 0, tzinfo=UTC)


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
    await p.execute("TRUNCATE TABLE routines CASCADE")
    await seed_source_registry(p, sources=INITIAL_SOURCES)
    yield p
    await p.close()


async def _mark_all_healthy(pool, *, now: datetime) -> None:
    """Mark every source the flag rules gate as active with a fresh
    checkpoint relative to `now` (the fictional test clock later passed to
    `evaluate_and_write_daily_flags`), so a test only needs to override the
    specific source(s) it wants dark.

    `upsert_checkpoint(success=True)` always stamps `last_success_at` with
    the *real* wall-clock time (`_utcnow()`), not a caller-supplied value —
    so a follow-up direct UPDATE is required to pin the checkpoint's
    freshness to the test's fictional `now` instead of actual test-run time.
    """
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


async def _seed_zero_sleep_rollup(pool, local_date: date) -> None:
    """One rollup row per lane, sleep at zero — every other lane nonzero
    enough to clear the lane_share_outlier evidence floor without itself
    triggering it (values are stable/uniform, no trailing history to compare
    against in these tests, so lane_share_outlier never fires here either
    way — it needs 5+ trailing days of history first)."""
    from butlers.chronicler.aggregations import LANES

    for lane in LANES:
        await upsert_daily_rollup(
            pool,
            local_date=local_date,
            lane=lane,
            seconds=0 if lane == "sleep" else 1800,
            episode_count=0 if lane == "sleep" else 1,
        )


async def test_healthy_day_writes_sleep_missing_only(pool) -> None:
    await _mark_all_healthy(pool, now=_NOW)
    await _seed_zero_sleep_rollup(pool, _LOCAL_DATE)

    await evaluate_and_write_daily_flags(
        pool, local_date=_LOCAL_DATE, timezone="Asia/Singapore", now=_NOW
    )

    flags = await list_daily_rollup_flags(pool, local_date=_LOCAL_DATE)
    flag_types = {f.flag_type for f in flags}
    assert flag_types == {FLAG_SLEEP_MISSING}


async def test_feeder_dark_suppresses_sleep_missing_end_to_end(pool) -> None:
    """The required feeder_dark-suppresses-derived-flags case, exercised
    against real source_adapter_state/projection_checkpoints/daily_rollups
    rows: a dark google_health.measurements feeder must write feeder_dark
    and must NOT write sleep_missing despite the day's sleep lane being 0."""
    await _mark_all_healthy(pool, now=_NOW)
    await mark_source_active(pool, "google_health.measurements", active=False)
    await _seed_zero_sleep_rollup(pool, _LOCAL_DATE)

    result = await evaluate_and_write_daily_flags(
        pool, local_date=_LOCAL_DATE, timezone="Asia/Singapore", now=_NOW
    )
    assert result["dark_sources"] == ["google_health.measurements"]

    flags = await list_daily_rollup_flags(pool, local_date=_LOCAL_DATE)
    flags_by_type = {f.flag_type: f for f in flags}
    assert set(flags_by_type) == {FLAG_FEEDER_DARK}
    assert flags_by_type[FLAG_FEEDER_DARK].detail == {
        "dark_sources": ["google_health.measurements"]
    }
    assert flags_by_type[FLAG_FEEDER_DARK].severity == "warning"


async def test_stale_checkpoint_also_produces_feeder_dark(pool) -> None:
    """active=True but a checkpoint far older than 2x its cron interval must
    still count as dark — the silent-failure case, not just an explicit
    active=False toggle."""
    await _mark_all_healthy(pool, now=_NOW)
    await mark_source_active(pool, "chronicler.occupation_inferred", active=True)
    await upsert_checkpoint(
        pool,
        "chronicler.occupation_inferred",
        watermark=_NOW - timedelta(hours=5),
        success=True,
    )
    # cron interval for chronicler.occupation_inferred is 60 min (hourly);
    # backdate its last_success_at well beyond the 2x threshold.
    await pool.execute(
        "UPDATE projection_checkpoints SET last_success_at = $2 WHERE source_name = $1",
        "chronicler.occupation_inferred",
        _NOW - timedelta(hours=5),
    )
    await _seed_zero_sleep_rollup(pool, _LOCAL_DATE)

    result = await evaluate_and_write_daily_flags(
        pool, local_date=_LOCAL_DATE, timezone="Asia/Singapore", now=_NOW
    )
    assert "chronicler.occupation_inferred" in result["dark_sources"]

    flags = await list_daily_rollup_flags(pool, local_date=_LOCAL_DATE)
    feeder_dark = next(f for f in flags if f.flag_type == FLAG_FEEDER_DARK)
    assert "chronicler.occupation_inferred" in feeder_dark.detail["dark_sources"]
    # routine_break also depends on this source; with no routines seeded it
    # cannot fire regardless, but the suppression path must not raise.
    assert FLAG_ROUTINE_BREAK not in {f.flag_type for f in flags}


async def test_idempotent_rerun_reconciles_flags_not_duplicates(pool) -> None:
    """Re-running for an already-evaluated day must not create duplicate
    rows (the (local_date, flag_type) unique constraint), and must delete a
    previously-written flag once its underlying condition clears — the
    "idempotent re-runs" requirement covers removal, not just
    non-duplication."""
    await _mark_all_healthy(pool, now=_NOW)
    await mark_source_active(pool, "google_health.measurements", active=False)
    await _seed_zero_sleep_rollup(pool, _LOCAL_DATE)

    await evaluate_and_write_daily_flags(
        pool, local_date=_LOCAL_DATE, timezone="Asia/Singapore", now=_NOW
    )
    first_flags = await list_daily_rollup_flags(pool, local_date=_LOCAL_DATE)
    assert {f.flag_type for f in first_flags} == {FLAG_FEEDER_DARK}

    # Re-run unchanged: must not duplicate.
    await evaluate_and_write_daily_flags(
        pool, local_date=_LOCAL_DATE, timezone="Asia/Singapore", now=_NOW
    )
    second_flags = await list_daily_rollup_flags(pool, local_date=_LOCAL_DATE)
    assert len(second_flags) == 1
    assert second_flags[0].flag_type == FLAG_FEEDER_DARK

    # The feeder recovers and the day's sleep lane gets corrected upward —
    # re-running must clear feeder_dark (and never have written
    # sleep_missing, since sleep is no longer zero).
    await mark_source_active(pool, "google_health.measurements", active=True)
    await upsert_checkpoint(
        pool,
        "google_health.measurements",
        watermark=_NOW - timedelta(minutes=1),
        success=True,
    )
    await pool.execute(
        "UPDATE projection_checkpoints SET last_success_at = $2, last_run_at = $2 "
        "WHERE source_name = $1",
        "google_health.measurements",
        _NOW - timedelta(minutes=1),
    )
    await upsert_daily_rollup(
        pool, local_date=_LOCAL_DATE, lane="sleep", seconds=21600, episode_count=1
    )

    await evaluate_and_write_daily_flags(
        pool, local_date=_LOCAL_DATE, timezone="Asia/Singapore", now=_NOW
    )
    third_flags = await list_daily_rollup_flags(pool, local_date=_LOCAL_DATE)
    assert third_flags == []


async def test_routine_break_writes_and_clears_end_to_end(pool) -> None:
    """An enabled Monday routine with no occupation_block on a healthy day
    must fire routine_break; once an occupation_block covers the window, a
    re-run must clear it."""
    from datetime import time as dtime

    from butlers.chronicler.models import Episode, Layer, Precision, Privacy
    from butlers.chronicler.storage import upsert_episode, upsert_mined_routine

    monday = date(2026, 7, 6)
    assert monday.weekday() == 0
    later_now = datetime(2026, 7, 7, 8, 0, 0, tzinfo=UTC)

    # Checkpoints must be fresh relative to `later_now` (the clock this test
    # evaluates against), not `_NOW` — `_mark_all_healthy` pins freshness to
    # whatever `now` it is given.
    await _mark_all_healthy(pool, now=later_now)
    await upsert_daily_rollup(pool, local_date=monday, lane="work", seconds=0, episode_count=0)

    routine = await upsert_mined_routine(
        pool,
        dow_mask=1 << 0,
        window_start_local=dtime(9, 0),
        window_end_local=dtime(17, 0),
        label="weekday desk block",
        support_count=5,
        confidence=0.8,
        evidence_summary={},
        timezone="Asia/Singapore",
    )

    result = await evaluate_and_write_daily_flags(
        pool, local_date=monday, timezone="Asia/Singapore", now=later_now
    )
    assert result["flags"][FLAG_ROUTINE_BREAK] is True

    flags = await list_daily_rollup_flags(pool, local_date=monday)
    routine_break = next(f for f in flags if f.flag_type == FLAG_ROUTINE_BREAK)
    assert routine_break.detail["routines"][0]["label"] == "weekday desk block"
    assert routine_break.detail["routines"][0]["routine_id"] == str(routine.id)

    window_start = datetime(2026, 7, 6, 9, 0, tzinfo=_TZ).astimezone(UTC)
    window_end = datetime(2026, 7, 6, 17, 0, tzinfo=_TZ).astimezone(UTC)
    await upsert_episode(
        pool,
        Episode(
            source_name="chronicler.occupation_inferred",
            source_ref=f"chronicler.routines:{routine.id}:{monday.isoformat()}",
            episode_type="occupation_block",
            start_at=window_start,
            end_at=window_end,
            precision=Precision.HOUR,
            privacy=Privacy.NORMAL,
            layer=Layer.ACTIVITY,
        ),
    )

    await evaluate_and_write_daily_flags(
        pool, local_date=monday, timezone="Asia/Singapore", now=later_now
    )
    flags_after = await list_daily_rollup_flags(pool, local_date=monday)
    assert FLAG_ROUTINE_BREAK not in {f.flag_type for f in flags_after}


async def test_lane_share_outlier_fires_with_sufficient_history_and_spike(pool) -> None:
    await _mark_all_healthy(pool, now=_NOW)

    from butlers.chronicler.aggregations import LANES

    history_dates = [_LOCAL_DATE - timedelta(days=i) for i in range(1, 8)]
    for d in history_dates:
        for lane in LANES:
            await upsert_daily_rollup(
                pool,
                local_date=d,
                lane=lane,
                seconds=1000 if lane == "work" else 200,
                episode_count=1,
            )

    # Today: 'work' share spikes far beyond 2x its historical median share.
    for lane in LANES:
        await upsert_daily_rollup(
            pool,
            local_date=_LOCAL_DATE,
            lane=lane,
            seconds=8000 if lane == "work" else 200,
            episode_count=1,
        )

    result = await evaluate_and_write_daily_flags(
        pool, local_date=_LOCAL_DATE, timezone="Asia/Singapore", now=_NOW
    )
    assert result["flags"][FLAG_LANE_SHARE_OUTLIER] is True

    flags = await list_daily_rollup_flags(pool, local_date=_LOCAL_DATE)
    outlier_flag = next(f for f in flags if f.flag_type == FLAG_LANE_SHARE_OUTLIER)
    assert "work" in outlier_flag.detail["lanes"]
