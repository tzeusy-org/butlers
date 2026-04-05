"""Unit tests for butlers.jobs.home — environment report job.

Covers _classify_sensor_type, _extract_numeric_state, _extract_area,
classify_deviation (all sensor types + severity levels), _build_environment_report_message,
run_environment_report, and daemon registry.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.jobs.home import (
    _DEFAULT_COMFORT_DEFAULTS,
    _DEFAULT_COMFORT_DEVIATION,
    _build_environment_report_message,
    _classify_sensor_type,
    _extract_area,
    _extract_numeric_state,
    _NullEmbeddingEngine,
    classify_deviation,
    run_environment_report,
)

pytestmark = pytest.mark.unit

_DEFAULTS = dict(_DEFAULT_COMFORT_DEFAULTS)
_DEVIATIONS = dict(_DEFAULT_COMFORT_DEVIATION)


def _make_pool(*, snapshot_count=3, snapshot_rows=None, facts_row=None) -> MagicMock:
    pool = MagicMock()

    def _fetchval_side_effect(query, *args):
        return snapshot_count if "count(*)" in query.lower() else 1

    pool.fetchval = AsyncMock(side_effect=_fetchval_side_effect)

    def _fetch_side_effect(query, *args):
        return snapshot_rows or [] if "ha_entity_snapshot" in query.lower() else []

    pool.fetch = AsyncMock(side_effect=_fetch_side_effect)
    pool.fetchrow = AsyncMock(return_value=facts_row)
    pool.execute = AsyncMock()
    return pool


def _make_snapshot_row(entity_id, state="72.0", attributes=None) -> MagicMock:
    row = MagicMock()
    attrs = attributes or {"friendly_name": entity_id.split(".")[-1].replace("_", " ").title()}
    data = {"entity_id": entity_id, "state": state, "attributes": attrs}
    row.__getitem__ = lambda self, key: data[key]
    return row


# ---------------------------------------------------------------------------
# Helper function contracts
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "entity_id, friendly_name, expected",
    [
        ("sensor.living_room_temperature", None, "temperature"),
        ("sensor.sensor_001", "Bedroom Temp", "temperature"),
        ("sensor.bathroom_humidity", None, "humidity"),
        ("sensor.office_co2", None, "co2"),
        ("sensor.air_quality", None, "co2"),
        ("sensor.kitchen_lux", None, "illuminance"),
        ("light.living_room", "Living Room Light", None),
    ],
)
def test_classify_sensor_type(entity_id, friendly_name, expected):
    """_classify_sensor_type identifies sensor types by entity_id/friendly_name patterns."""
    assert _classify_sensor_type(entity_id, friendly_name) == expected


@pytest.mark.parametrize(
    "state, expected",
    [
        ("72.5", 72.5), ("45", 45.0), ("0", 0.0),
        ("unavailable", None), ("unknown", None), ("", None), (None, None), ("on", None),
    ],
)
def test_extract_numeric_state(state, expected):
    """_extract_numeric_state parses floats and returns None for non-numeric/special states."""
    assert _extract_numeric_state(state) == expected


def test_extract_area():
    """_extract_area resolves area_id > area > room; returns None when absent."""
    assert _extract_area({"area_id": "bedroom"}) == "bedroom"
    assert _extract_area({"area": "kitchen"}) == "kitchen"
    assert _extract_area({"room": "living_room"}) == "living_room"
    assert _extract_area({"friendly_name": "Temp"}) is None
    assert _extract_area({}) is None


# ---------------------------------------------------------------------------
# classify_deviation — all sensor types and severity levels
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sensor_type, value, expected",
    [
        # temperature: ok/minor/moderate/critical boundaries
        ("temperature", 72.0, "ok"),      # within range
        ("temperature", 67.0, "minor"),   # 1°F below lower bound
        ("temperature", 64.0, "moderate"), # moderate deviation
        ("temperature", 55.0, "critical"), # below critical threshold
        ("temperature", 90.0, "critical"), # above critical threshold
        # humidity
        ("humidity", 45.0, "ok"),
        ("humidity", 10.0, "critical"),    # below critical threshold
        ("humidity", 85.0, "critical"),    # above critical threshold
        ("humidity", 25.0, "minor"),
        # CO2
        ("co2", 800.0, "ok"),
        ("co2", 1200.0, "moderate"),
        ("co2", 1600.0, "critical"),
        # illuminance: always ok
        ("illuminance", 500.0, "ok"),
    ],
)
def test_classify_deviation(sensor_type, value, expected):
    """classify_deviation returns correct severity for all sensor types and boundaries."""
    result = classify_deviation(sensor_type, value,
                                 comfort_defaults=_DEFAULTS, deviation_thresholds=_DEVIATIONS)
    assert result == expected


def test_classify_deviation_area_preference_overrides():
    """Area preference overrides defaults for range calculation."""
    area_pref = {"temp_min_f": 65.0, "temp_max_f": 70.0}
    result = classify_deviation("temperature", 72.0, comfort_defaults=_DEFAULTS,
                                 deviation_thresholds=_DEVIATIONS, area_preference=area_pref)
    assert result in ("minor", "moderate", "critical")


# ---------------------------------------------------------------------------
# _build_environment_report_message
# ---------------------------------------------------------------------------


def test_build_environment_report_message():
    """Message includes header, area readings, correct icons, and capped recommendations."""
    # Header only
    msg = _build_environment_report_message([], _DEFAULTS)
    assert "Environment Report" in msg

    # Area with temperature and humidity
    area_results = [{"area": "bedroom", "readings": {"temperature": 72.0, "humidity": 45.0},
                     "deviations": {"temperature": "ok", "humidity": "ok"}}]
    msg2 = _build_environment_report_message(area_results, _DEFAULTS)
    assert "Bedroom" in msg2 and "72.0°F" in msg2 and "✅" in msg2

    # Critical shows red icon
    area_crit = [
        {
            "area": "garage",
            "readings": {"temperature": 55.0},
            "deviations": {"temperature": "critical"},
        }
    ]
    msg3 = _build_environment_report_message(area_crit, _DEFAULTS)
    assert "🔴" in msg3

    # Recommendations capped at 3
    areas_4 = [{"area": f"room{i}", "readings": {"humidity": 10.0},
                 "deviations": {"humidity": "critical"}} for i in range(4)]
    msg4 = _build_environment_report_message(areas_4, _DEFAULTS)
    recs_section = msg4.split("Recommendations:")[-1] if "Recommendations:" in msg4 else ""
    assert recs_section.count("  •") <= 3


# ---------------------------------------------------------------------------
# run_environment_report
# ---------------------------------------------------------------------------


async def test_run_environment_report_empty_snapshot():
    """Empty snapshot returns error and notifies owner."""
    pool = _make_pool(snapshot_count=0)
    with patch("butlers.jobs.home._notify_owner_telegram", new_callable=AsyncMock) as mock_notify:
        result = await run_environment_report(pool, None)
    assert result == {"error": "no_entity_snapshot"}
    mock_notify.assert_awaited_once()


async def test_run_environment_report_no_sensors():
    """Rows with no env sensors → zero counts + notification."""
    rows = [_make_snapshot_row("light.living_room", "on")]
    pool = _make_pool(snapshot_count=1, snapshot_rows=rows)
    with patch("butlers.jobs.home._notify_owner_telegram", new_callable=AsyncMock):
        result = await run_environment_report(pool, None)
    assert result == {"areas_checked": 0, "sensors_read": 0, "deviations_found": 0}


async def test_run_environment_report_counts_and_deviations():
    """Normal run returns correct area/sensor counts; critical temp triggers store_fact."""
    rows = [
        _make_snapshot_row("sensor.bedroom_temperature", "72.0",
                           {"friendly_name": "Bedroom Temp", "area_id": "bedroom"}),
        _make_snapshot_row("sensor.office_co2", "800.0",
                           {"friendly_name": "Office CO2", "area_id": "office"}),
    ]
    pool = _make_pool(snapshot_count=2, snapshot_rows=rows)

    with (
        patch("butlers.jobs.home._notify_owner_telegram", new_callable=AsyncMock),
        patch("butlers.jobs.home.state_get", new_callable=AsyncMock, return_value=None),
        patch("butlers.jobs.home.store_fact", new_callable=AsyncMock, return_value=None),
    ):
        result = await run_environment_report(pool, None)

    assert result["areas_checked"] == 2 and result["sensors_read"] == 2
    assert result["deviations_found"] == 0

    # Critical deviation triggers store_fact
    rows_crit = [_make_snapshot_row("sensor.garage_temperature", "50.0",
                                    {"friendly_name": "Garage Temp", "area_id": "garage"})]
    pool_crit = _make_pool(snapshot_count=1, snapshot_rows=rows_crit)
    with (
        patch("butlers.jobs.home._notify_owner_telegram", new_callable=AsyncMock),
        patch("butlers.jobs.home.state_get", new_callable=AsyncMock, return_value=None),
        patch("butlers.jobs.home.store_fact", new_callable=AsyncMock, return_value=None) as mock_store,  # noqa: E501
    ):
        result2 = await run_environment_report(pool_crit, None)
    assert result2["deviations_found"] >= 1
    mock_store.assert_awaited()


# ---------------------------------------------------------------------------
# _NullEmbeddingEngine + Registry
# ---------------------------------------------------------------------------


def test_null_embedding_engine():
    """_NullEmbeddingEngine.embed() returns [] synchronously for any input."""
    import inspect
    eng = _NullEmbeddingEngine()
    assert eng.embed("hello") == [] and eng.embed("") == []
    assert not inspect.iscoroutinefunction(eng.embed)


def test_environment_report_registered():
    """environment_report is registered in _DETERMINISTIC_SCHEDULE_JOB_REGISTRY for home."""
    from butlers.daemon import _DETERMINISTIC_SCHEDULE_JOB_REGISTRY
    home_jobs = _DETERMINISTIC_SCHEDULE_JOB_REGISTRY.get("home", {})
    assert "environment_report" in home_jobs and callable(home_jobs["environment_report"])
