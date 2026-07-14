"""Real-Postgres integration tests for chronicler.daily_rollups (bu-u30as,
telemetry-distillation bead 3).

Mocked-pool tests cannot validate the ``(local_date, lane)`` upsert
idempotency this feature depends on, nor prove the rollup's per-lane totals
actually match the live ``aggregate/by-category`` endpoint end-to-end (both
run their own real SQL against ``v_episodes_corrected``) — see "Mocked-pool
vs integration test gap" (PR #2598 class, ~8h main-red from SQL that passed
mocked-pool tests only). These tests run the real migration chain, the real
rollup materializer, and the real HTTP surface against a migrated Postgres
container.

The bit-for-bit regression test is the design-mandated guard against the
bu-whhll.1-class KPI-divergence bug: the rollup and the live endpoint must
never disagree on a lane's tracked seconds for the same window (design doc
§3.3, spec.md "Rollup output matches the live endpoint").
"""

from __future__ import annotations

import shutil
from datetime import UTC, date, datetime, timedelta
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import asyncpg
import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.chronicler.contracts import INITIAL_SOURCES, seed_source_registry
from butlers.chronicler.models import Episode, Layer
from butlers.chronicler.rollups import materialize_daily_rollups
from butlers.chronicler.storage import list_daily_rollups, upsert_episode
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


def _local(d: date, hour: int, minute: int = 0) -> datetime:
    return datetime(d.year, d.month, d.day, hour, minute, tzinfo=_TZ).astimezone(UTC)


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    """Provision the chronicler migration chain (public schema, unscoped)."""
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
    await seed_source_registry(p, sources=INITIAL_SOURCES)
    yield p
    await p.close()


def _build_chronicler_api(pool) -> httpx.ASGITransport:
    db = MagicMock(spec=DatabaseManager)
    db.pool.return_value = pool
    app = create_app(api_key="")
    for butler_name, router_module in app.state.butler_routers:
        if butler_name == "chronicler" and hasattr(router_module, "_get_db_manager"):
            app.dependency_overrides[router_module._get_db_manager] = lambda: db
            break
    else:  # pragma: no cover — defensive
        raise AssertionError("chronicler router not registered on the app")
    return httpx.ASGITransport(app=app)


async def _seed_representative_day(pool) -> None:
    """Insert a spread of activity-layer episodes across several lanes, plus
    an intent-layer calendar block and an uncorroborated evidence-layer row
    that must both contribute zero seconds to every lane."""
    d = _LOCAL_DATE
    await upsert_episode(
        pool,
        Episode(
            source_name="spotify.session_summary",
            source_ref="rollup-it-play-1",
            episode_type="listening_episode",
            start_at=_local(d, 9, 0),
            end_at=_local(d, 10, 0),
            layer=Layer.ACTIVITY,
        ),
    )
    # Overlapping second play episode — must union, not sum, with the first.
    await upsert_episode(
        pool,
        Episode(
            source_name="spotify.session_summary",
            source_ref="rollup-it-play-2",
            episode_type="listening_episode",
            start_at=_local(d, 9, 30),
            end_at=_local(d, 11, 0),
            layer=Layer.ACTIVITY,
        ),
    )
    await upsert_episode(
        pool,
        Episode(
            source_name="google_health.measurements",
            source_ref="rollup-it-sleep-1",
            episode_type="sleep_episode",
            start_at=_local(d, 0, 0),
            end_at=_local(d, 6, 0),
            layer=Layer.ACTIVITY,
        ),
    )
    await upsert_episode(
        pool,
        Episode(
            source_name="steam.play_history",
            source_ref="rollup-it-gaming-1",
            episode_type="play_episode",
            start_at=_local(d, 20, 0),
            end_at=_local(d, 22, 0),
            layer=Layer.ACTIVITY,
        ),
    )
    # Intent-layer calendar block — never counted (the "calendar = 5h" fix).
    await upsert_episode(
        pool,
        Episode(
            source_name="google_calendar.completed",
            source_ref="rollup-it-intent-1",
            episode_type="scheduled_block",
            start_at=_local(d, 13, 0),
            end_at=_local(d, 17, 0),
            layer=Layer.INTENT,
        ),
    )
    # Uncorroborated evidence-layer ambient sensor row — never counted until
    # reconciliation promotes it (out of scope for this bead).
    await upsert_episode(
        pool,
        Episode(
            source_name="home_assistant.sensor_activity",
            source_ref="rollup-it-evidence-1",
            episode_type="room_activity_episode",
            start_at=_local(d, 14, 0),
            end_at=_local(d, 14, 30),
            layer=Layer.EVIDENCE,
        ),
    )


async def _fetch_live_endpoint_buckets(pool, *, day_start_utc, day_end_utc) -> dict[str, dict]:
    transport = _build_chronicler_api(pool)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/chronicler/aggregate/by-category",
            params={
                "start_at": day_start_utc.isoformat(),
                "end_at": day_end_utc.isoformat(),
                "tz": "Asia/Singapore",
            },
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()["data"]
    return {bucket["category"]: bucket for bucket in body["buckets"]}


# ── Bit-for-bit regression against the live endpoint ───────────────────────


async def test_rollup_matches_live_aggregate_by_category_bit_for_bit(pool) -> None:
    await _seed_representative_day(pool)

    day_start_utc = _local(_LOCAL_DATE, 0, 0)
    day_end_utc = _local(_LOCAL_DATE + timedelta(days=1), 0, 0)

    # "Now" is the next morning, so 2026-07-01 is fully elapsed.
    now = _local(_LOCAL_DATE + timedelta(days=1), 8, 0)
    result = await materialize_daily_rollups(
        pool, timezone="Asia/Singapore", lookback_days=3, now=now
    )
    assert _LOCAL_DATE.isoformat() in result["days_processed"]

    rollup_rows = await list_daily_rollups(pool, local_date=_LOCAL_DATE)
    rollup_by_lane = {r.lane: r for r in rollup_rows}

    live_buckets = await _fetch_live_endpoint_buckets(
        pool, day_start_utc=day_start_utc, day_end_utc=day_end_utc
    )

    # Every lane the live endpoint reports must match the rollup exactly.
    for lane, bucket in live_buckets.items():
        assert lane in rollup_by_lane, f"rollup missing lane {lane!r} the live endpoint reported"
        assert rollup_by_lane[lane].seconds == round(bucket["total_seconds"]), (
            f"lane={lane!r}: rollup={rollup_by_lane[lane].seconds} "
            f"vs live={bucket['total_seconds']}"
        )
        assert rollup_by_lane[lane].episode_count == bucket["episode_count"]

    # Every lane the rollup wrote but the live endpoint omitted (zero
    # activity) must be zero, not a fabricated nonzero value.
    for lane, row in rollup_by_lane.items():
        if lane not in live_buckets:
            assert row.seconds == 0
            assert row.episode_count == 0

    # Sanity: the play lane folds both "music" (Spotify) and "gaming" (Steam)
    # categories. The two overlapping Spotify episodes ([9:00,10:00) and
    # [9:30,11:00)) union to 2h (not sum to 2.5h), plus the separate,
    # non-overlapping 2h Steam session = 4h total, 3 episodes.
    assert live_buckets["play"]["total_seconds"] == pytest.approx(4 * 3600.0)
    assert rollup_by_lane["play"].seconds == 4 * 3600
    assert rollup_by_lane["play"].episode_count == 3

    # The intent-layer calendar block (13:00-17:00) and the uncorroborated
    # evidence-layer sensor row (14:00-14:30) both fall inside the "work"/
    # "rest" windows respectively, but neither is activity-layer, so neither
    # lane picks up any seconds from them.
    assert rollup_by_lane["work"].seconds == 0
    assert rollup_by_lane["work"].episode_count == 0
    assert rollup_by_lane["rest"].seconds == 0
    assert rollup_by_lane["rest"].episode_count == 0


async def test_rollup_upsert_is_idempotent_across_reruns(pool) -> None:
    """Re-running the materializer for an already-rolled-up day recomputes in
    place — no duplicate rows — proving the (local_date, lane) unique
    constraint actually works against real Postgres."""
    await _seed_representative_day(pool)
    now = _local(_LOCAL_DATE + timedelta(days=1), 8, 0)

    await materialize_daily_rollups(pool, timezone="Asia/Singapore", lookback_days=3, now=now)
    first_rows = await list_daily_rollups(pool, local_date=_LOCAL_DATE)
    assert len(first_rows) == 9  # one row per LANES entry, zero-filled (bu-whhll.14: +butler_ops)

    # Add a late-arriving episode and re-run.
    await upsert_episode(
        pool,
        Episode(
            source_name="google_health.measurements",
            source_ref="rollup-it-workout-late",
            episode_type="workout_episode",
            start_at=_local(_LOCAL_DATE, 18, 0),
            end_at=_local(_LOCAL_DATE, 19, 0),
            layer=Layer.ACTIVITY,
        ),
    )
    await materialize_daily_rollups(pool, timezone="Asia/Singapore", lookback_days=3, now=now)

    second_rows = await list_daily_rollups(pool, local_date=_LOCAL_DATE)
    assert len(second_rows) == 9, "re-run must upsert in place, not duplicate rows"
    exercise_row = next(r for r in second_rows if r.lane == "exercise")
    assert exercise_row.seconds == 3600
    assert exercise_row.episode_count == 1


async def test_materialize_skips_still_partial_local_day(pool) -> None:
    """The current, still-partial local day must never be materialized —
    real-Postgres regression for the fully-elapsed-window guard."""
    await _seed_representative_day(pool)
    # "Now" is still within 2026-07-01 local time.
    now = _local(_LOCAL_DATE, 12, 0)

    result = await materialize_daily_rollups(
        pool, timezone="Asia/Singapore", lookback_days=3, now=now
    )
    assert _LOCAL_DATE.isoformat() not in result["days_processed"]

    rows = await list_daily_rollups(pool, local_date=_LOCAL_DATE)
    assert rows == []
