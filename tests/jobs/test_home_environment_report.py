"""Tests for butlers.jobs.home — environment report job.

Covers _classify_sensor_type, _extract_numeric_state, _extract_area,
classify_deviation, _build_environment_report_message, run_environment_report,
_NullEmbeddingEngine, and daemon registry.
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


def test_classify_sensor_type():
    """_classify_sensor_type identifies types by entity_id or friendly_name keywords."""
    assert _classify_sensor_type("sensor.living_room_temperature", None) == "temperature"
    assert _classify_sensor_type("sensor.bathroom_humidity", None) == "humidity"
    assert _classify_sensor_type("sensor.office_co2", None) == "co2"
    assert _classify_sensor_type("sensor.sensor_001", "Bedroom Temp") == "temperature"
    assert _classify_sensor_type("light.living_room", "Living Room Light") is None


def test_extract_numeric_state():
    """_extract_numeric_state parses floats; returns None for non-numeric/special states."""
    assert _extract_numeric_state("72.5") == 72.5
    assert _extract_numeric_state("unavailable") is None
    assert _extract_numeric_state("on") is None


def test_extract_area():
    """_extract_area resolves area_id > area > room; returns None when absent."""
    assert _extract_area({"area_id": "bedroom"}) == "bedroom"
    assert _extract_area({"area": "kitchen"}) == "kitchen"
    assert _extract_area({"room": "living_room"}) == "living_room"
    assert _extract_area({}) is None


# ---------------------------------------------------------------------------
# classify_deviation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sensor_type, value, expected",
    [
        ("temperature", 72.0, "ok"),
        ("temperature", 55.0, "critical"),
        ("humidity", 10.0, "critical"),
        ("co2", 800.0, "ok"),
    ],
)
def test_classify_deviation(sensor_type, value, expected):
    """classify_deviation returns correct severity for ok and critical values."""
    result = classify_deviation(
        sensor_type, value, comfort_defaults=_DEFAULTS, deviation_thresholds=_DEVIATIONS
    )
    assert result == expected


def test_classify_deviation_area_preference_overrides():
    """Area preference overrides defaults for range calculation."""
    area_pref = {"temp_min_f": 65.0, "temp_max_f": 70.0}
    result = classify_deviation(
        "temperature",
        72.0,
        comfort_defaults=_DEFAULTS,
        deviation_thresholds=_DEVIATIONS,
        area_preference=area_pref,
    )
    assert result in ("minor", "moderate", "critical")


# ---------------------------------------------------------------------------
# _build_environment_report_message
# ---------------------------------------------------------------------------


def test_build_environment_report_message():
    """Message includes header, area readings, icons, and caps recommendations at 3."""
    msg = _build_environment_report_message([], _DEFAULTS)
    assert "Environment Report" in msg

    area_results = [
        {
            "area": "bedroom",
            "readings": {"temperature": 72.0, "humidity": 45.0},
            "deviations": {"temperature": "ok", "humidity": "ok"},
        }
    ]
    msg2 = _build_environment_report_message(area_results, _DEFAULTS)
    assert "Bedroom" in msg2 and "72.0°F" in msg2 and "✅" in msg2

    area_crit = [
        {
            "area": "garage",
            "readings": {"temperature": 55.0},
            "deviations": {"temperature": "critical"},
        }
    ]
    msg3 = _build_environment_report_message(area_crit, _DEFAULTS)
    assert "🔴" in msg3

    areas_4 = [
        {"area": f"room{i}", "readings": {"humidity": 10.0}, "deviations": {"humidity": "critical"}}
        for i in range(4)
    ]
    msg4 = _build_environment_report_message(areas_4, _DEFAULTS)
    recs_section = msg4.split("Recommendations:")[-1] if "Recommendations:" in msg4 else ""
    assert recs_section.count("  •") <= 3


# ---------------------------------------------------------------------------
# run_environment_report
# ---------------------------------------------------------------------------


async def test_run_environment_report_empty_snapshot():
    """Empty snapshot returns error."""
    pool = _make_pool(snapshot_count=0)
    with patch("butlers.jobs.home._notify_owner_telegram", new_callable=AsyncMock):
        result = await run_environment_report(pool, None)
    assert result == {"error": "no_entity_snapshot"}


async def test_run_environment_report_no_sensors_and_normal_run():
    """Non-sensor rows → zero counts; normal run returns correct counts; critical triggers store."""
    rows = [_make_snapshot_row("light.living_room", "on")]
    pool = _make_pool(snapshot_count=1, snapshot_rows=rows)
    with patch("butlers.jobs.home._notify_owner_telegram", new_callable=AsyncMock):
        result = await run_environment_report(pool, None)
    assert result == {"areas_checked": 0, "sensors_read": 0, "deviations_found": 0}

    rows2 = [
        _make_snapshot_row(
            "sensor.bedroom_temperature",
            "72.0",
            {"friendly_name": "Bedroom Temp", "area_id": "bedroom"},
        ),
        _make_snapshot_row(
            "sensor.office_co2", "800.0", {"friendly_name": "Office CO2", "area_id": "office"}
        ),
    ]
    pool2 = _make_pool(snapshot_count=2, snapshot_rows=rows2)
    with (
        patch("butlers.jobs.home._notify_owner_telegram", new_callable=AsyncMock),
        patch("butlers.jobs.home.state_get", new_callable=AsyncMock, return_value=None),
        patch("butlers.jobs.home.store_fact", new_callable=AsyncMock, return_value=None),
    ):
        result2 = await run_environment_report(pool2, None)
    assert result2["areas_checked"] == 2 and result2["sensors_read"] == 2
    assert result2["deviations_found"] == 0

    # Critical deviation triggers store_fact
    rows_crit = [
        _make_snapshot_row(
            "sensor.garage_temperature",
            "50.0",
            {"friendly_name": "Garage Temp", "area_id": "garage"},
        )
    ]
    pool_crit = _make_pool(snapshot_count=1, snapshot_rows=rows_crit)
    with (
        patch("butlers.jobs.home._notify_owner_telegram", new_callable=AsyncMock),
        patch("butlers.jobs.home.state_get", new_callable=AsyncMock, return_value=None),
        patch(
            "butlers.jobs.home.store_fact", new_callable=AsyncMock, return_value=None
        ) as mock_store,
    ):
        result3 = await run_environment_report(pool_crit, None)
    assert result3["deviations_found"] >= 1
    mock_store.assert_awaited()
    assert mock_store.await_args.kwargs["source_butler"] == "home"


# ---------------------------------------------------------------------------
# _NullEmbeddingEngine + Registry
# ---------------------------------------------------------------------------


def test_null_embedding_engine():
    """_NullEmbeddingEngine exposes the fields store_fact reads from real engines."""
    import inspect

    eng = _NullEmbeddingEngine()
    assert eng.model_name == "deterministic-null"
    assert eng.embed("hello") == [] and eng.embed("") == []
    assert not inspect.iscoroutinefunction(eng.embed)


# environment_report registration is asserted by the canonical
# test_all_home_deterministic_jobs_registered in test_home_energy_digest.py.
