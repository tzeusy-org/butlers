"""Unit tests for GET /api/chronicler/balance and GET /api/chronicler/trends
(IEA, tasks.md S9b, bu-jc6htw.2).

Mocked-pool tests: storage calls (list_daily_rollups_range /
list_daily_rollup_flags_range) are monkeypatched directly on the
dynamically-loaded router module, mirroring test_rollups_api.py. Exercises
parameter validation, the vs-baseline math end-to-end through the HTTP
surface, the feeder_dark-marks-lane-unavailable cross-reference, and the
degraded-envelope (*_source_error) flags without a real database.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.chronicler.models import DailyRollup, DailyRollupFlag

pytestmark = pytest.mark.unit

_BALANCE_ENDPOINT = "/api/chronicler/balance"
_TRENDS_ENDPOINT = "/api/chronicler/trends"
_ALL_LANES = ("eat", "exercise", "play", "rest", "sleep", "social", "travel", "work")


def _find_chronicler_router_module(app: Any) -> Any:
    for butler_name, router_module in app.state.butler_routers:
        if butler_name == "chronicler":
            return router_module
    raise AssertionError("chronicler router module not registered")


def _build_app(
    *,
    rollups: list[DailyRollup] | None = None,
    flags: list[DailyRollupFlag] | None = None,
    raise_error: bool = False,
):
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = MagicMock()

    app = create_app()
    router_module = _find_chronicler_router_module(app)
    app.dependency_overrides[router_module._get_db_manager] = lambda: mock_db

    async def fake_rollups_range(pool, *, start_date, end_date):
        if raise_error:
            raise RuntimeError("simulated query failure")
        return [r for r in (rollups or []) if start_date <= r.local_date <= end_date]

    async def fake_flags_range(pool, *, start_date, end_date):
        if raise_error:
            raise RuntimeError("simulated query failure")
        return [f for f in (flags or []) if start_date <= f.local_date <= end_date]

    router_module.list_daily_rollups_range = fake_rollups_range
    router_module.list_daily_rollup_flags_range = fake_flags_range

    return app


async def _get(app: Any, endpoint: str, params: dict[str, Any]) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.get(endpoint, params=params)


def _rollup(local_date: date, lane: str, seconds: int, episode_count: int = 1) -> DailyRollup:
    return DailyRollup(
        local_date=local_date,
        lane=lane,
        seconds=seconds,
        episode_count=episode_count,
        timezone="Asia/Singapore",
        distinct_place_count=None,
        computed_at=datetime(2026, 7, 6, 0, 5),
    )


def _flag(
    local_date: date, flag_type: str, *, severity: str = "warning", detail: dict | None = None
) -> DailyRollupFlag:
    return DailyRollupFlag(
        local_date=local_date,
        flag_type=flag_type,
        severity=severity,
        detail=detail or {},
        created_at=datetime(2026, 7, 6, 0, 5),
    )


# ---------------------------------------------------------------------------
# GET /balance
# ---------------------------------------------------------------------------


async def test_balance_missing_date_returns_422():
    app = _build_app()
    resp = await _get(app, _BALANCE_ENDPOINT, {})
    assert resp.status_code == 422  # FastAPI required-query-param validation


async def test_balance_not_yet_materialized_day_returns_empty_lanes():
    app = _build_app(rollups=[], flags=[])
    resp = await _get(app, _BALANCE_ENDPOINT, {"date": "2026-07-05"})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["status"] == "not_yet_materialized"
    assert data["lanes"] == []
    assert data["balance_source_error"] is False


async def test_balance_materialized_day_zero_fills_lane_with_no_activity_but_keeps_baseline():
    rollups = [
        _rollup(date(2026, 7, 5), "sleep", 25200),
        _rollup(date(2026, 6, 20), "sleep", 28800),
        _rollup(date(2026, 6, 21), "sleep", 30600),
    ]
    app = _build_app(rollups=rollups, flags=[])
    resp = await _get(app, _BALANCE_ENDPOINT, {"date": "2026-07-05"})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["status"] == "materialized"
    lanes_by_name = {row["lane"]: row for row in data["lanes"]}
    assert set(lanes_by_name) == set(_ALL_LANES)

    sleep = lanes_by_name["sleep"]
    assert sleep["seconds"] == 25200
    assert sleep["baseline_seconds"] == pytest.approx(29700.0)
    assert sleep["delta_seconds"] == pytest.approx(25200 - 29700.0)
    assert sleep["baseline_sample_days"] == 2

    # No activity today, but baseline history exists -> zero with context.
    work = lanes_by_name["work"]
    assert work["seconds"] == 0
    assert work["baseline_seconds"] is None
    assert work["baseline_sample_days"] == 0


async def test_balance_feeder_dark_marks_lane_unavailable():
    rollups = [_rollup(date(2026, 7, 5), "travel", 0)]
    flags = [
        _flag(date(2026, 7, 5), "feeder_dark", detail={"dark_sources": ["owntracks.points"]}),
    ]
    app = _build_app(rollups=rollups, flags=flags)
    resp = await _get(app, _BALANCE_ENDPOINT, {"date": "2026-07-05"})
    data = resp.json()["data"]
    lanes_by_name = {row["lane"]: row for row in data["lanes"]}
    assert lanes_by_name["travel"]["unavailable"] is True
    assert lanes_by_name["sleep"]["unavailable"] is False


async def test_balance_source_error_sets_unknown_status_and_flag():
    app = _build_app(raise_error=True)
    resp = await _get(app, _BALANCE_ENDPOINT, {"date": "2026-07-05"})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["status"] == "unknown"
    assert data["lanes"] == []
    assert data["balance_source_error"] is True


async def test_balance_lookback_days_param_is_honoured():
    app = _build_app(
        rollups=[
            _rollup(date(2026, 7, 5), "work", 3600),
            _rollup(date(2026, 7, 4), "work", 7200),
        ],
        flags=[],
    )
    resp = await _get(app, _BALANCE_ENDPOINT, {"date": "2026-07-05", "lookback_days": 1})
    data = resp.json()["data"]
    assert data["baseline_lookback_days"] == 1
    work = {row["lane"]: row for row in data["lanes"]}["work"]
    assert work["baseline_seconds"] == pytest.approx(7200.0)
    assert work["baseline_sample_days"] == 1


# ---------------------------------------------------------------------------
# GET /trends
# ---------------------------------------------------------------------------


async def test_trends_invalid_window_returns_400():
    app = _build_app()
    resp = await _get(app, _TRENDS_ENDPOINT, {"window": "year", "end_date": "2026-07-05"})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_parameter"


async def test_trends_week_window_spans_seven_days():
    app = _build_app(rollups=[], flags=[])
    resp = await _get(app, _TRENDS_ENDPOINT, {"window": "week", "end_date": "2026-07-05"})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["start_date"] == "2026-06-29"
    assert data["end_date"] == "2026-07-05"
    work_series = next(s for s in data["lanes"] if s["lane"] == "work")
    assert len(work_series["days"]) == 7
    assert all(d["status"] == "not_yet_materialized" for d in work_series["days"])


async def test_trends_month_window_spans_thirty_days():
    app = _build_app(rollups=[], flags=[])
    resp = await _get(app, _TRENDS_ENDPOINT, {"window": "month", "end_date": "2026-07-05"})
    data = resp.json()["data"]
    work_series = next(s for s in data["lanes"] if s["lane"] == "work")
    assert len(work_series["days"]) == 30


async def test_trends_streak_counts_trailing_nonzero_days():
    rollups = [
        _rollup(date(2026, 6, 29), "work", 0),
        _rollup(date(2026, 6, 30), "work", 3600),
        _rollup(date(2026, 7, 1), "work", 3600),
        _rollup(date(2026, 7, 2), "work", 3600),
        _rollup(date(2026, 7, 3), "work", 3600),
        _rollup(date(2026, 7, 4), "work", 3600),
        _rollup(date(2026, 7, 5), "work", 3600),
    ]
    app = _build_app(rollups=rollups, flags=[])
    resp = await _get(app, _TRENDS_ENDPOINT, {"window": "week", "end_date": "2026-07-05"})
    data = resp.json()["data"]
    work_series = next(s for s in data["lanes"] if s["lane"] == "work")
    assert work_series["streak_days"] == 6


async def test_trends_flags_anomaly_when_delta_clears_thresholds():
    rollups = [_rollup(date(2026, 7, 5), "work", 36000)]
    # 8 baseline days at 7200s each -> anomaly-eligible sample size.
    for i in range(1, 9):
        rollups.append(_rollup(date(2026, 7, 5) - timedelta(days=i), "work", 7200))
    app = _build_app(rollups=rollups, flags=[])
    resp = await _get(app, _TRENDS_ENDPOINT, {"window": "week", "end_date": "2026-07-05"})
    data = resp.json()["data"]
    anomalies = [
        a for a in data["anomalies"] if a["lane"] == "work" and a["local_date"] == "2026-07-05"
    ]
    assert len(anomalies) == 1
    assert anomalies[0]["direction"] == "spike"


async def test_trends_source_error_sets_unknown_status_for_every_day():
    app = _build_app(raise_error=True)
    resp = await _get(app, _TRENDS_ENDPOINT, {"window": "week", "end_date": "2026-07-05"})
    data = resp.json()["data"]
    assert data["trends_source_error"] is True
    work_series = next(s for s in data["lanes"] if s["lane"] == "work")
    assert all(d["status"] == "unknown" for d in work_series["days"])
    assert data["anomalies"] == []
