"""Unit tests for GET /api/chronicler/rollups (bu-333dq, telemetry-
distillation bead 5, design doc §6.5).

Mocked-pool tests: the endpoint's storage calls
(``list_daily_rollups_range``/``list_daily_rollup_flags_range``) are
monkeypatched directly on the dynamically-loaded router module, so these
tests exercise parameter validation, per-day status derivation
(``materialized``/``not_yet_materialized``/``unknown``), the
``feeder_dark``-marks-lane-unavailable cross-reference, and the
``rollups_source_error`` degraded-envelope flag without a real database.

Real-Postgres end-to-end coverage (seeding actual daily_rollups/
daily_rollup_flags rows and reading them back through the live HTTP surface)
lives in tests/integration/test_chronicler_rollups_api_integration.py.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.chronicler.models import DailyRollup, DailyRollupFlag

pytestmark = pytest.mark.unit

_ENDPOINT = "/api/chronicler/rollups"


# ---------------------------------------------------------------------------
# Test harness
# ---------------------------------------------------------------------------


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
    """Wire a FastAPI test app with the range-query storage calls stubbed.

    Patches the names directly on the router module's namespace (it imports
    ``list_daily_rollups_range``/``list_daily_rollup_flags_range`` by value
    at module load time), rather than mocking ``pool.fetch`` — simpler than
    replicating asyncpg Record mocking for two distinct queries.
    """
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = MagicMock()

    app = create_app()
    router_module = _find_chronicler_router_module(app)
    app.dependency_overrides[router_module._get_db_manager] = lambda: mock_db

    async def fake_rollups_range(pool, *, start_date, end_date):
        if raise_error:
            raise RuntimeError("simulated query failure")
        return rollups or []

    async def fake_flags_range(pool, *, start_date, end_date):
        if raise_error:
            raise RuntimeError("simulated query failure")
        return flags or []

    router_module.list_daily_rollups_range = fake_rollups_range
    router_module.list_daily_rollup_flags_range = fake_flags_range

    return app


async def _get(app: Any, params: dict[str, Any]) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.get(_ENDPOINT, params=params)


def _rollup(
    local_date: date,
    lane: str,
    seconds: int,
    episode_count: int = 1,
    *,
    narrative: str | None = None,
) -> DailyRollup:
    return DailyRollup(
        local_date=local_date,
        lane=lane,
        seconds=seconds,
        episode_count=episode_count,
        timezone="Asia/Singapore",
        distinct_place_count=None,
        narrative=narrative,
        computed_at=datetime(2026, 7, 6, 0, 5),
    )


def _flag(
    local_date: date,
    flag_type: str,
    *,
    severity: str = "warning",
    detail: dict | None = None,
    narrative: str | None = None,
) -> DailyRollupFlag:
    return DailyRollupFlag(
        local_date=local_date,
        flag_type=flag_type,
        severity=severity,
        detail=detail or {},
        narrative=narrative,
        created_at=datetime(2026, 7, 6, 0, 5),
    )


_DAY = date(2026, 7, 5)
_ALL_LANES = ("butler_ops", "eat", "exercise", "play", "rest", "sleep", "social", "travel", "work")


# ---------------------------------------------------------------------------
# Parameter validation
# ---------------------------------------------------------------------------


async def test_no_params_returns_400():
    app = _build_app()
    resp = await _get(app, {})
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"]["code"] == "missing_parameter"
    assert body["error"]["butler"] == "chronicler"


async def test_date_and_range_conflict_returns_400():
    app = _build_app()
    resp = await _get(
        app, {"date": "2026-07-05", "start_date": "2026-07-01", "end_date": "2026-07-05"}
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "conflicting_parameters"


async def test_start_date_without_end_date_returns_400():
    app = _build_app()
    resp = await _get(app, {"start_date": "2026-07-01"})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "missing_parameter"


async def test_end_date_without_start_date_returns_400():
    app = _build_app()
    resp = await _get(app, {"end_date": "2026-07-05"})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "missing_parameter"


async def test_end_before_start_returns_400():
    app = _build_app()
    resp = await _get(app, {"start_date": "2026-07-05", "end_date": "2026-07-01"})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_time_range"


async def test_range_too_large_returns_400():
    app = _build_app()
    resp = await _get(app, {"start_date": "2026-01-01", "end_date": "2026-12-31"})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "range_too_large"


async def test_range_at_cap_is_accepted():
    app = _build_app()
    resp = await _get(app, {"start_date": "2026-01-01", "end_date": "2026-04-02"})  # 92 days
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Absent day (not_yet_materialized) — legitimate absence, not degraded
# ---------------------------------------------------------------------------


async def test_absent_day_is_not_yet_materialized_not_degraded():
    app = _build_app(rollups=[], flags=[])
    resp = await _get(app, {"date": "2026-07-05"})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["rollups_source_error"] is False
    assert len(data["days"]) == 1
    day = data["days"][0]
    assert day["local_date"] == "2026-07-05"
    assert day["status"] == "not_yet_materialized"
    # Absent day carries no lanes/flags — not a fabricated zero-filled set.
    assert day["lanes"] == []
    assert day["flags"] == []


# ---------------------------------------------------------------------------
# Materialized day — zero-filled lanes, real values
# ---------------------------------------------------------------------------


async def test_materialized_day_zero_fills_every_lane():
    rollups = [_rollup(_DAY, "work", 3600, episode_count=2)]
    app = _build_app(rollups=rollups, flags=[])
    resp = await _get(app, {"date": "2026-07-05"})
    assert resp.status_code == 200
    day = resp.json()["data"]["days"][0]
    assert day["status"] == "materialized"
    lanes_by_name = {lane_row["lane"]: lane_row for lane_row in day["lanes"]}
    assert set(lanes_by_name) == set(_ALL_LANES)
    assert lanes_by_name["work"]["seconds"] == 3600
    assert lanes_by_name["work"]["episode_count"] == 2
    assert lanes_by_name["work"]["unavailable"] is False
    # Every other lane is zero-filled, not omitted.
    assert lanes_by_name["sleep"]["seconds"] == 0
    assert lanes_by_name["sleep"]["episode_count"] == 0
    assert lanes_by_name["sleep"]["unavailable"] is False


# ---------------------------------------------------------------------------
# feeder_dark marks the affected lane unavailable, never a false all-clear
# ---------------------------------------------------------------------------


async def test_feeder_dark_marks_only_the_affected_lane_unavailable():
    rollups = [_rollup(_DAY, "sleep", 0, episode_count=0), _rollup(_DAY, "work", 3600)]
    flags = [
        _flag(
            _DAY,
            "feeder_dark",
            severity="warning",
            detail={"dark_sources": ["google_health.measurements"]},
        )
    ]
    app = _build_app(rollups=rollups, flags=flags)
    resp = await _get(app, {"date": "2026-07-05"})
    assert resp.status_code == 200
    day = resp.json()["data"]["days"][0]
    lanes_by_name = {lane_row["lane"]: lane_row for lane_row in day["lanes"]}
    # google_health.measurements only contributes to 'sleep' (sources_for_lane).
    assert lanes_by_name["sleep"]["unavailable"] is True
    assert lanes_by_name["sleep"]["seconds"] == 0
    assert lanes_by_name["work"]["unavailable"] is False
    flag_types = {f["flag_type"] for f in day["flags"]}
    assert "feeder_dark" in flag_types


async def test_no_feeder_dark_flag_leaves_every_lane_available():
    rollups = [_rollup(_DAY, "sleep", 0, episode_count=0)]
    app = _build_app(rollups=rollups, flags=[])
    resp = await _get(app, {"date": "2026-07-05"})
    day = resp.json()["data"]["days"][0]
    lanes_by_name = {lane_row["lane"]: lane_row for lane_row in day["lanes"]}
    # A genuine zero (no feeder_dark flag) is a truthful zero, not unavailable.
    assert lanes_by_name["sleep"]["unavailable"] is False


async def test_non_feeder_dark_flags_pass_through():
    rollups = [_rollup(_DAY, "work", 3600)]
    flags = [_flag(_DAY, "routine_break", severity="info", detail={"routines": [{"label": "gym"}]})]
    app = _build_app(rollups=rollups, flags=flags)
    resp = await _get(app, {"date": "2026-07-05"})
    day = resp.json()["data"]["days"][0]
    assert len(day["flags"]) == 1
    assert day["flags"][0]["flag_type"] == "routine_break"
    assert day["flags"][0]["severity"] == "info"
    assert day["flags"][0]["detail"] == {"routines": [{"label": "gym"}]}
    # routine_break gates nothing lane-level by itself (only feeder_dark does).
    lanes_by_name = {lane_row["lane"]: lane_row for lane_row in day["lanes"]}
    assert lanes_by_name["work"]["unavailable"] is False


# ---------------------------------------------------------------------------
# Optional narrative (chronicler_020) — surfaced when present, None otherwise,
# never rendered as an error (bu-4qymf)
# ---------------------------------------------------------------------------


async def test_day_narrative_surfaced_when_present():
    # The narration job writes the same day summary onto every lane row for the
    # date; the endpoint reads it off the first rollup row.
    rollups = [
        _rollup(_DAY, "work", 3600, narrative="A busy work day with a long focus block."),
        _rollup(_DAY, "sleep", 25200, narrative="A busy work day with a long focus block."),
    ]
    app = _build_app(rollups=rollups, flags=[])
    resp = await _get(app, {"date": "2026-07-05"})
    assert resp.status_code == 200
    day = resp.json()["data"]["days"][0]
    assert day["narrative"] == "A busy work day with a long focus block."


async def test_flag_narrative_surfaced_when_present():
    rollups = [_rollup(_DAY, "work", 3600)]
    flags = [
        _flag(
            _DAY,
            "routine_break",
            severity="info",
            detail={"routines": [{"label": "gym"}]},
            narrative="Skipped the usual morning gym session.",
        )
    ]
    app = _build_app(rollups=rollups, flags=flags)
    resp = await _get(app, {"date": "2026-07-05"})
    day = resp.json()["data"]["days"][0]
    assert day["flags"][0]["narrative"] == "Skipped the usual morning gym session."


async def test_absent_narrative_is_null_not_error():
    # Pre-feature / labeling-skipped day: rows exist and are materialized, but no
    # narration ran. Absent narrative is a legitimate None, never a degraded state.
    rollups = [_rollup(_DAY, "work", 3600)]
    flags = [_flag(_DAY, "routine_break", severity="info")]
    app = _build_app(rollups=rollups, flags=flags)
    resp = await _get(app, {"date": "2026-07-05"})
    data = resp.json()["data"]
    assert data["rollups_source_error"] is False
    day = data["days"][0]
    assert day["status"] == "materialized"
    assert day["narrative"] is None
    assert day["flags"][0]["narrative"] is None


async def test_not_yet_materialized_day_has_null_narrative():
    # No rows for the day → no lane row to carry a day summary.
    app = _build_app(rollups=[], flags=[])
    resp = await _get(app, {"date": "2026-07-05"})
    day = resp.json()["data"]["days"][0]
    assert day["status"] == "not_yet_materialized"
    assert day["narrative"] is None


# ---------------------------------------------------------------------------
# Genuine query failure — rollups_source_error, status=unknown, never a
# truthful empty/zero result
# ---------------------------------------------------------------------------


async def test_query_failure_sets_source_error_and_unknown_status():
    app = _build_app(raise_error=True)
    resp = await _get(app, {"date": "2026-07-05"})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["rollups_source_error"] is True
    day = data["days"][0]
    assert day["status"] == "unknown"
    assert day["lanes"] == []
    assert day["flags"] == []


# ---------------------------------------------------------------------------
# Multi-day range
# ---------------------------------------------------------------------------


async def test_range_returns_one_entry_per_day_ascending():
    rollups = [
        _rollup(date(2026, 7, 1), "work", 1000),
        _rollup(date(2026, 7, 3), "work", 2000),
    ]
    app = _build_app(rollups=rollups, flags=[])
    resp = await _get(app, {"start_date": "2026-07-01", "end_date": "2026-07-03"})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["start_date"] == "2026-07-01"
    assert data["end_date"] == "2026-07-03"
    days = data["days"]
    assert [d["local_date"] for d in days] == ["2026-07-01", "2026-07-02", "2026-07-03"]
    assert days[0]["status"] == "materialized"
    assert days[1]["status"] == "not_yet_materialized"
    assert days[2]["status"] == "materialized"


async def test_single_day_range_via_start_end_matches_date_param():
    rollups = [_rollup(_DAY, "work", 500)]
    app = _build_app(rollups=rollups, flags=[])
    resp_date = await _get(app, {"date": "2026-07-05"})
    resp_range = await _get(app, {"start_date": "2026-07-05", "end_date": "2026-07-05"})
    assert resp_date.status_code == resp_range.status_code == 200
    assert resp_date.json()["data"]["days"] == resp_range.json()["data"]["days"]


async def test_response_echoes_timezone():
    app = _build_app(rollups=[], flags=[])
    resp = await _get(app, {"date": "2026-07-05"})
    assert resp.json()["data"]["tz"] == "Asia/Singapore"
