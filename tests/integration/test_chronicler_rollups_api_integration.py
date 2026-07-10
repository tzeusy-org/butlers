"""Real-Postgres integration tests for GET /api/chronicler/rollups
(bu-333dq, telemetry-distillation bead 5, design doc §6.5).

Mocked-storage unit tests (``tests/chronicler/test_rollups_api.py``) cover
parameter validation and the absent/degraded/feeder_dark distinction against
stubbed range-query functions. These tests prove the real read path: the new
``list_daily_rollups_range``/``list_daily_rollup_flags_range`` SQL actually
reads seeded ``daily_rollups``/``daily_rollup_flags`` rows back correctly
through the live HTTP surface, against a migrated Postgres container.
"""

from __future__ import annotations

import shutil
from datetime import date
from unittest.mock import MagicMock

import asyncpg
import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.chronicler.contracts import INITIAL_SOURCES, seed_source_registry
from butlers.chronicler.storage import (
    set_daily_rollup_day_narrative,
    set_daily_rollup_flag_narrative,
    upsert_daily_rollup,
    upsert_daily_rollup_flag,
)
from butlers.db import register_jsonb_codec
from butlers.testing.migration import create_migrated_test_db, migration_db_name

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]

_DAY_1 = date(2026, 7, 1)
_DAY_2 = date(2026, 7, 3)


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
    await p.execute("TRUNCATE TABLE source_adapter_state, projection_checkpoints CASCADE")
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


async def _get(pool, params: dict) -> httpx.Response:
    transport = _build_chronicler_api(pool)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get("/api/chronicler/rollups", params=params)


# ---------------------------------------------------------------------------
# Materialized day — real rows round-trip through the HTTP surface
# ---------------------------------------------------------------------------


async def test_materialized_day_reads_real_rows(pool) -> None:
    await upsert_daily_rollup(pool, local_date=_DAY_1, lane="work", seconds=7200, episode_count=3)
    await upsert_daily_rollup(pool, local_date=_DAY_1, lane="sleep", seconds=21600, episode_count=1)

    resp = await _get(pool, {"date": _DAY_1.isoformat()})
    assert resp.status_code == 200, resp.text
    day = resp.json()["data"]["days"][0]
    assert day["status"] == "materialized"
    lanes_by_name = {row["lane"]: row for row in day["lanes"]}
    assert lanes_by_name["work"]["seconds"] == 7200
    assert lanes_by_name["work"]["episode_count"] == 3
    assert lanes_by_name["sleep"]["seconds"] == 21600
    # Zero-filled for every lane in the fixed taxonomy, not just the seeded ones.
    assert lanes_by_name["travel"]["seconds"] == 0
    assert set(lanes_by_name) == {
        "eat",
        "exercise",
        "play",
        "rest",
        "sleep",
        "social",
        "travel",
        "work",
    }


# ---------------------------------------------------------------------------
# Optional narrative (chronicler_020) round-trips through the real read path
# ---------------------------------------------------------------------------


async def test_narrative_round_trips_when_written(pool) -> None:
    await upsert_daily_rollup(pool, local_date=_DAY_1, lane="work", seconds=7200, episode_count=3)
    await upsert_daily_rollup(pool, local_date=_DAY_1, lane="sleep", seconds=21600, episode_count=1)
    await upsert_daily_rollup_flag(
        pool,
        local_date=_DAY_1,
        flag_type="routine_break",
        severity="info",
        detail={"routines": [{"label": "gym"}]},
    )
    # The narration job writes the day summary onto every lane row for the date.
    rows_updated = await set_daily_rollup_day_narrative(
        pool, local_date=_DAY_1, narrative="A focused work day; skipped the gym."
    )
    assert rows_updated == 2
    await set_daily_rollup_flag_narrative(
        pool,
        local_date=_DAY_1,
        flag_type="routine_break",
        narrative="Missed the usual gym session.",
    )

    resp = await _get(pool, {"date": _DAY_1.isoformat()})
    assert resp.status_code == 200, resp.text
    day = resp.json()["data"]["days"][0]
    assert day["narrative"] == "A focused work day; skipped the gym."
    assert day["flags"][0]["narrative"] == "Missed the usual gym session."


async def test_materialized_day_without_narration_returns_null_narrative(pool) -> None:
    await upsert_daily_rollup(pool, local_date=_DAY_1, lane="work", seconds=3600, episode_count=1)
    await upsert_daily_rollup_flag(
        pool, local_date=_DAY_1, flag_type="routine_break", severity="info"
    )

    resp = await _get(pool, {"date": _DAY_1.isoformat()})
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["rollups_source_error"] is False
    day = data["days"][0]
    # Real materialized rows, but the labeling pass never ran — absent narrative
    # is a legitimate None, never a degraded/error state.
    assert day["status"] == "materialized"
    assert day["narrative"] is None
    assert day["flags"][0]["narrative"] is None


# ---------------------------------------------------------------------------
# Absent day — legitimately not yet materialized, never a fabricated zero
# ---------------------------------------------------------------------------


async def test_absent_day_is_not_yet_materialized(pool) -> None:
    resp = await _get(pool, {"date": "2026-08-15"})
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["rollups_source_error"] is False
    day = data["days"][0]
    assert day["status"] == "not_yet_materialized"
    assert day["lanes"] == []
    assert day["flags"] == []


# ---------------------------------------------------------------------------
# feeder_dark cross-references sources_for_lane on the real detail JSONB
# ---------------------------------------------------------------------------


async def test_feeder_dark_flag_marks_sleep_lane_unavailable(pool) -> None:
    await upsert_daily_rollup(pool, local_date=_DAY_1, lane="sleep", seconds=0, episode_count=0)
    await upsert_daily_rollup(pool, local_date=_DAY_1, lane="work", seconds=3600, episode_count=1)
    await upsert_daily_rollup_flag(
        pool,
        local_date=_DAY_1,
        flag_type="feeder_dark",
        severity="warning",
        detail={"dark_sources": ["google_health.measurements"]},
    )

    resp = await _get(pool, {"date": _DAY_1.isoformat()})
    assert resp.status_code == 200, resp.text
    day = resp.json()["data"]["days"][0]
    lanes_by_name = {row["lane"]: row for row in day["lanes"]}
    assert lanes_by_name["sleep"]["unavailable"] is True
    # google_health.measurements does not contribute to 'work'.
    assert lanes_by_name["work"]["unavailable"] is False
    flag_types = {f["flag_type"] for f in day["flags"]}
    assert flag_types == {"feeder_dark"}


# ---------------------------------------------------------------------------
# Multi-day range spanning a materialized day, a gap, and another materialized day
# ---------------------------------------------------------------------------


async def test_range_query_spans_materialized_and_absent_days(pool) -> None:
    await upsert_daily_rollup(pool, local_date=_DAY_1, lane="work", seconds=1800, episode_count=1)
    await upsert_daily_rollup(pool, local_date=_DAY_2, lane="work", seconds=3600, episode_count=2)

    resp = await _get(pool, {"start_date": _DAY_1.isoformat(), "end_date": _DAY_2.isoformat()})
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    days = data["days"]
    assert [d["local_date"] for d in days] == ["2026-07-01", "2026-07-02", "2026-07-03"]
    assert days[0]["status"] == "materialized"
    assert days[1]["status"] == "not_yet_materialized"
    assert days[2]["status"] == "materialized"
    assert {row["lane"]: row["seconds"] for row in days[0]["lanes"]}["work"] == 1800
    assert {row["lane"]: row["seconds"] for row in days[2]["lanes"]}["work"] == 3600
