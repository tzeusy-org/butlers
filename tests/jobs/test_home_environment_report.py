"""Unit tests for butlers.jobs.home — environment report job.

Covers:
- classify_deviation: all sensor types, all severity levels, default fallback
- _load_comfort_defaults / _load_comfort_deviation: state store hit, miss, malformed
- run_environment_report: empty snapshot, no sensors, report composition, deviation storage
- _build_environment_report_message: message content and structure

All tests use mocked asyncpg pools — no database or network required.
"""

from __future__ import annotations

from typing import Any
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pool(
    *,
    snapshot_count: int = 3,
    snapshot_rows: list[dict[str, Any]] | None = None,
    facts_row: dict[str, Any] | None = None,
) -> MagicMock:
    """Return a minimal mock asyncpg pool for environment report tests."""
    pool = MagicMock()

    if snapshot_rows is None:
        snapshot_rows = []

    # fetchval used for snapshot_count and store_fact upsert version
    def _fetchval_side_effect(query: str, *args: Any) -> Any:
        if "count(*)" in query.lower():
            return snapshot_count
        # store_fact RETURNING version
        return 1

    pool.fetchval = AsyncMock(side_effect=_fetchval_side_effect)

    # fetch used for ha_entity_snapshot and facts queries
    def _fetch_side_effect(query: str, *args: Any) -> list[Any]:
        if "ha_entity_snapshot" in query.lower():
            return snapshot_rows
        return []

    pool.fetch = AsyncMock(side_effect=_fetch_side_effect)

    # fetchrow used for area comfort preference lookup
    pool.fetchrow = AsyncMock(return_value=facts_row)

    pool.execute = AsyncMock()

    return pool


def _make_snapshot_row(
    entity_id: str,
    state: str = "72.0",
    attributes: dict[str, Any] | None = None,
) -> MagicMock:
    """Return a mock asyncpg record simulating ha_entity_snapshot row."""
    row = MagicMock()
    attrs = attributes or {"friendly_name": entity_id.split(".")[-1].replace("_", " ").title()}
    row.__getitem__ = lambda self, key: {
        "entity_id": entity_id,
        "state": state,
        "attributes": attrs,
    }[key]
    return row


# ---------------------------------------------------------------------------
# _classify_sensor_type
# ---------------------------------------------------------------------------


class TestClassifySensorType:
    def test_temperature_by_entity_id(self):
        assert _classify_sensor_type("sensor.living_room_temperature", None) == "temperature"

    def test_temperature_by_friendly_name(self):
        assert _classify_sensor_type("sensor.sensor_001", "Bedroom Temp") == "temperature"

    def test_humidity_by_entity_id(self):
        assert _classify_sensor_type("sensor.bathroom_humidity", None) == "humidity"

    def test_co2_by_entity_id(self):
        assert _classify_sensor_type("sensor.office_co2", None) == "co2"

    def test_illuminance_by_entity_id(self):
        assert _classify_sensor_type("sensor.kitchen_lux", None) == "illuminance"

    def test_unknown_returns_none(self):
        assert _classify_sensor_type("light.living_room", "Living Room Light") is None

    def test_air_quality_classified_as_co2(self):
        assert _classify_sensor_type("sensor.air_quality", None) == "co2"


# ---------------------------------------------------------------------------
# _extract_numeric_state
# ---------------------------------------------------------------------------


class TestExtractNumericState:
    def test_valid_float(self):
        assert _extract_numeric_state("72.5") == 72.5

    def test_valid_int(self):
        assert _extract_numeric_state("45") == 45.0

    def test_unavailable_returns_none(self):
        assert _extract_numeric_state("unavailable") is None

    def test_unknown_returns_none(self):
        assert _extract_numeric_state("unknown") is None

    def test_empty_returns_none(self):
        assert _extract_numeric_state("") is None

    def test_none_returns_none(self):
        assert _extract_numeric_state(None) is None

    def test_non_numeric_string_returns_none(self):
        assert _extract_numeric_state("on") is None


# ---------------------------------------------------------------------------
# _extract_area
# ---------------------------------------------------------------------------


class TestExtractArea:
    def test_area_id_field(self):
        assert _extract_area({"area_id": "bedroom"}) == "bedroom"

    def test_area_field_fallback(self):
        assert _extract_area({"area": "kitchen"}) == "kitchen"

    def test_room_field_fallback(self):
        assert _extract_area({"room": "living_room"}) == "living_room"

    def test_no_area_returns_none(self):
        assert _extract_area({"friendly_name": "Temp Sensor"}) is None

    def test_empty_dict(self):
        assert _extract_area({}) is None


# ---------------------------------------------------------------------------
# classify_deviation — temperature
# ---------------------------------------------------------------------------


class TestClassifyDeviationTemperature:
    def _defaults(self) -> dict[str, float]:
        return dict(_DEFAULT_COMFORT_DEFAULTS)

    def _deviations(self) -> dict[str, float]:
        return dict(_DEFAULT_COMFORT_DEVIATION)

    def test_within_range_is_ok(self):
        # Default range: 68-76°F
        result = classify_deviation(
            "temperature",
            72.0,
            comfort_defaults=self._defaults(),
            deviation_thresholds=self._deviations(),
        )
        assert result == "ok"

    def test_at_lower_boundary_is_ok(self):
        result = classify_deviation(
            "temperature",
            68.0,
            comfort_defaults=self._defaults(),
            deviation_thresholds=self._deviations(),
        )
        assert result == "ok"

    def test_at_upper_boundary_is_ok(self):
        result = classify_deviation(
            "temperature",
            76.0,
            comfort_defaults=self._defaults(),
            deviation_thresholds=self._deviations(),
        )
        assert result == "ok"

    def test_minor_below_range(self):
        # 68 - 1 = 67°F, minor threshold = 2°F → within minor band
        result = classify_deviation(
            "temperature",
            67.0,
            comfort_defaults=self._defaults(),
            deviation_thresholds=self._deviations(),
        )
        assert result == "minor"

    def test_moderate_below_range(self):
        # 68 - 4 = 64°F, moderate threshold = 5°F → within moderate band
        result = classify_deviation(
            "temperature",
            64.0,
            comfort_defaults=self._defaults(),
            deviation_thresholds=self._deviations(),
        )
        assert result == "moderate"

    def test_critical_below_range(self):
        # Below critical_temp_low_f = 60°F
        result = classify_deviation(
            "temperature",
            55.0,
            comfort_defaults=self._defaults(),
            deviation_thresholds=self._deviations(),
        )
        assert result == "critical"

    def test_critical_above_range(self):
        # Above critical_temp_high_f = 85°F
        result = classify_deviation(
            "temperature",
            90.0,
            comfort_defaults=self._defaults(),
            deviation_thresholds=self._deviations(),
        )
        assert result == "critical"

    def test_area_preference_overrides_range(self):
        # Area prefers 65-70°F; reading of 72°F should be out of range
        area_pref = {"temp_min_f": 65.0, "temp_max_f": 70.0}
        result = classify_deviation(
            "temperature",
            72.0,
            comfort_defaults=self._defaults(),
            deviation_thresholds=self._deviations(),
            area_preference=area_pref,
        )
        assert result in ("minor", "moderate", "critical")

    def test_no_area_preference_uses_defaults(self):
        # No area preference → defaults apply
        result = classify_deviation(
            "temperature",
            72.0,
            comfort_defaults=self._defaults(),
            deviation_thresholds=self._deviations(),
            area_preference=None,
        )
        assert result == "ok"


# ---------------------------------------------------------------------------
# classify_deviation — humidity
# ---------------------------------------------------------------------------


class TestClassifyDeviationHumidity:
    def _defaults(self) -> dict[str, float]:
        return dict(_DEFAULT_COMFORT_DEFAULTS)

    def _deviations(self) -> dict[str, float]:
        return dict(_DEFAULT_COMFORT_DEVIATION)

    def test_within_range_is_ok(self):
        result = classify_deviation(
            "humidity",
            45.0,
            comfort_defaults=self._defaults(),
            deviation_thresholds=self._deviations(),
        )
        assert result == "ok"

    def test_critical_low_humidity(self):
        # Below critical_humidity_low = 15%
        result = classify_deviation(
            "humidity",
            10.0,
            comfort_defaults=self._defaults(),
            deviation_thresholds=self._deviations(),
        )
        assert result == "critical"

    def test_critical_high_humidity(self):
        # Above critical_humidity_high = 80%
        result = classify_deviation(
            "humidity",
            85.0,
            comfort_defaults=self._defaults(),
            deviation_thresholds=self._deviations(),
        )
        assert result == "critical"

    def test_minor_below_range(self):
        # 30 - 5 = 25%, minor_humidity = 10 → within minor band
        result = classify_deviation(
            "humidity",
            25.0,
            comfort_defaults=self._defaults(),
            deviation_thresholds=self._deviations(),
        )
        assert result == "minor"

    def test_moderate_below_range(self):
        # 30 - 15 = 15%; moderate_humidity = 20 → within moderate band
        result = classify_deviation(
            "humidity",
            15.0,
            comfort_defaults=self._defaults(),
            deviation_thresholds=self._deviations(),
        )
        # 15 is at critical_humidity_low, not below it → moderate or critical
        # critical_humidity_low=15 → 15% is exactly at the critical boundary
        assert result in ("moderate", "critical")


# ---------------------------------------------------------------------------
# classify_deviation — CO2
# ---------------------------------------------------------------------------


class TestClassifyDeviationCO2:
    def _defaults(self) -> dict[str, float]:
        return dict(_DEFAULT_COMFORT_DEFAULTS)

    def _deviations(self) -> dict[str, float]:
        return dict(_DEFAULT_COMFORT_DEVIATION)

    def test_within_range_is_ok(self):
        result = classify_deviation(
            "co2",
            800.0,
            comfort_defaults=self._defaults(),
            deviation_thresholds=self._deviations(),
        )
        assert result == "ok"

    def test_above_max_is_moderate(self):
        # > co2_max_ppm (1000) but <= critical (1500)
        result = classify_deviation(
            "co2",
            1200.0,
            comfort_defaults=self._defaults(),
            deviation_thresholds=self._deviations(),
        )
        assert result == "moderate"

    def test_above_critical_is_critical(self):
        result = classify_deviation(
            "co2",
            1600.0,
            comfort_defaults=self._defaults(),
            deviation_thresholds=self._deviations(),
        )
        assert result == "critical"


# ---------------------------------------------------------------------------
# classify_deviation — illuminance (no classification)
# ---------------------------------------------------------------------------


class TestClassifyDeviationIlluminance:
    def test_illuminance_always_ok(self):
        result = classify_deviation(
            "illuminance",
            500.0,
            comfort_defaults=dict(_DEFAULT_COMFORT_DEFAULTS),
            deviation_thresholds=dict(_DEFAULT_COMFORT_DEVIATION),
        )
        assert result == "ok"


# ---------------------------------------------------------------------------
# classify_deviation — default fallback (no state store)
# ---------------------------------------------------------------------------


class TestClassifyDeviationDefaultFallback:
    """Verifies that hardcoded defaults produce correct classification when no
    stored thresholds exist (matching the spec's fallback scenario)."""

    def test_default_temp_comfortable(self):
        """72°F is within the default 68-76°F range."""
        result = classify_deviation(
            "temperature",
            72.0,
            comfort_defaults=dict(_DEFAULT_COMFORT_DEFAULTS),
            deviation_thresholds=dict(_DEFAULT_COMFORT_DEVIATION),
        )
        assert result == "ok"

    def test_default_temp_critical_cold(self):
        """Below 60°F is critical per default thresholds."""
        result = classify_deviation(
            "temperature",
            58.0,
            comfort_defaults=dict(_DEFAULT_COMFORT_DEFAULTS),
            deviation_thresholds=dict(_DEFAULT_COMFORT_DEVIATION),
        )
        assert result == "critical"

    def test_default_temp_critical_hot(self):
        """Above 85°F is critical per default thresholds."""
        result = classify_deviation(
            "temperature",
            86.0,
            comfort_defaults=dict(_DEFAULT_COMFORT_DEFAULTS),
            deviation_thresholds=dict(_DEFAULT_COMFORT_DEVIATION),
        )
        assert result == "critical"

    def test_default_humidity_normal(self):
        """45% RH is within 30-60% default range."""
        result = classify_deviation(
            "humidity",
            45.0,
            comfort_defaults=dict(_DEFAULT_COMFORT_DEFAULTS),
            deviation_thresholds=dict(_DEFAULT_COMFORT_DEVIATION),
        )
        assert result == "ok"

    def test_default_co2_critical(self):
        """CO2 above 1500 ppm is critical per default."""
        result = classify_deviation(
            "co2",
            1600.0,
            comfort_defaults=dict(_DEFAULT_COMFORT_DEFAULTS),
            deviation_thresholds=dict(_DEFAULT_COMFORT_DEVIATION),
        )
        assert result == "critical"

    def test_default_co2_moderate(self):
        """CO2 between 1000-1500 ppm is moderate per default."""
        result = classify_deviation(
            "co2",
            1200.0,
            comfort_defaults=dict(_DEFAULT_COMFORT_DEFAULTS),
            deviation_thresholds=dict(_DEFAULT_COMFORT_DEVIATION),
        )
        assert result == "moderate"


# ---------------------------------------------------------------------------
# _build_environment_report_message
# ---------------------------------------------------------------------------


class TestBuildEnvironmentReportMessage:
    def test_message_contains_header(self):
        msg = _build_environment_report_message([], dict(_DEFAULT_COMFORT_DEFAULTS))
        assert "Environment Report" in msg

    def test_message_includes_area_readings(self):
        area_results = [
            {
                "area": "bedroom",
                "readings": {"temperature": 72.0, "humidity": 45.0},
                "deviations": {"temperature": "ok", "humidity": "ok"},
            }
        ]
        msg = _build_environment_report_message(area_results, dict(_DEFAULT_COMFORT_DEFAULTS))
        assert "Bedroom" in msg
        assert "72.0°F" in msg
        assert "45% RH" in msg

    def test_critical_deviation_shows_red_icon(self):
        area_results = [
            {
                "area": "garage",
                "readings": {"temperature": 55.0},
                "deviations": {"temperature": "critical"},
            }
        ]
        msg = _build_environment_report_message(area_results, dict(_DEFAULT_COMFORT_DEFAULTS))
        assert "🔴" in msg

    def test_ok_shows_checkmark(self):
        area_results = [
            {
                "area": "living_room",
                "readings": {"temperature": 72.0},
                "deviations": {"temperature": "ok"},
            }
        ]
        msg = _build_environment_report_message(area_results, dict(_DEFAULT_COMFORT_DEFAULTS))
        assert "✅" in msg

    def test_recommendations_included_for_deviations(self):
        area_results = [
            {
                "area": "bedroom",
                "readings": {"humidity": 10.0},
                "deviations": {"humidity": "critical"},
            }
        ]
        msg = _build_environment_report_message(area_results, dict(_DEFAULT_COMFORT_DEFAULTS))
        assert "Recommendations" in msg
        assert "humidifier" in msg.lower()

    def test_at_most_3_recommendations(self):
        # 4 areas with critical deviations — should cap at 3 recs
        area_results = [
            {
                "area": f"room{i}",
                "readings": {"humidity": 10.0},
                "deviations": {"humidity": "critical"},
            }
            for i in range(4)
        ]
        msg = _build_environment_report_message(area_results, dict(_DEFAULT_COMFORT_DEFAULTS))
        # Count bullet points in Recommendations section
        recs_section = msg.split("Recommendations:")[-1] if "Recommendations:" in msg else ""
        bullet_count = recs_section.count("  •")
        assert bullet_count <= 3

    def test_co2_reading_shown(self):
        area_results = [
            {
                "area": "office",
                "readings": {"co2": 1200.0},
                "deviations": {"co2": "moderate"},
            }
        ]
        msg = _build_environment_report_message(area_results, dict(_DEFAULT_COMFORT_DEFAULTS))
        assert "1200" in msg
        assert "CO" in msg


# ---------------------------------------------------------------------------
# run_environment_report — empty snapshot
# ---------------------------------------------------------------------------


class TestRunEnvironmentReportEmptySnapshot:
    async def test_empty_snapshot_returns_error(self):
        pool = _make_pool(snapshot_count=0)
        with patch(
            "butlers.jobs.home._notify_owner_telegram", new_callable=AsyncMock
        ) as mock_notify:
            result = await run_environment_report(pool, None)

        assert result == {"error": "no_entity_snapshot"}
        mock_notify.assert_awaited_once()
        msg = mock_notify.call_args[0][1]
        assert "unavailable" in msg.lower() or "entity data" in msg.lower()

    async def test_snapshot_query_error_treated_as_empty(self):
        pool = _make_pool()
        pool.fetchval = AsyncMock(side_effect=Exception("DB error"))
        with patch(
            "butlers.jobs.home._notify_owner_telegram", new_callable=AsyncMock
        ) as mock_notify:
            result = await run_environment_report(pool, None)

        assert result == {"error": "no_entity_snapshot"}
        mock_notify.assert_awaited_once()


# ---------------------------------------------------------------------------
# run_environment_report — no environment sensors
# ---------------------------------------------------------------------------


class TestRunEnvironmentReportNoSensors:
    async def test_no_env_sensors_returns_zero_counts(self):
        # Snapshot has rows but none are env sensors
        rows = [_make_snapshot_row("light.living_room", "on")]
        pool = _make_pool(snapshot_count=1, snapshot_rows=rows)
        with patch(
            "butlers.jobs.home._notify_owner_telegram", new_callable=AsyncMock
        ) as mock_notify:
            result = await run_environment_report(pool, None)

        assert result == {"areas_checked": 0, "sensors_read": 0, "deviations_found": 0}
        mock_notify.assert_awaited_once()


# ---------------------------------------------------------------------------
# run_environment_report — normal report composition
# ---------------------------------------------------------------------------


class TestRunEnvironmentReportComposition:
    async def test_returns_correct_counts(self):
        rows = [
            _make_snapshot_row(
                "sensor.bedroom_temperature",
                "72.0",
                {"friendly_name": "Bedroom Temperature", "area_id": "bedroom"},
            ),
            _make_snapshot_row(
                "sensor.bedroom_humidity",
                "45.0",
                {"friendly_name": "Bedroom Humidity", "area_id": "bedroom"},
            ),
            _make_snapshot_row(
                "sensor.office_co2",
                "800.0",
                {"friendly_name": "Office CO2", "area_id": "office"},
            ),
        ]
        pool = _make_pool(snapshot_count=3, snapshot_rows=rows)

        with (
            patch(
                "butlers.jobs.home._notify_owner_telegram", new_callable=AsyncMock
            ) as mock_notify,
            patch(
                "butlers.jobs.home.state_get",
                new_callable=AsyncMock,
                return_value=None,  # use defaults
            ),
            patch(
                "butlers.jobs.home.store_fact",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            result = await run_environment_report(pool, None)

        assert result["areas_checked"] == 2  # bedroom + office
        assert result["sensors_read"] == 3
        assert result["deviations_found"] == 0  # all within defaults
        mock_notify.assert_awaited_once()

    async def test_deviations_stored_as_facts(self):
        """Moderate and critical deviations should trigger store_fact calls."""
        rows = [
            _make_snapshot_row(
                "sensor.garage_temperature",
                "50.0",  # critically cold
                {"friendly_name": "Garage Temp", "area_id": "garage"},
            ),
        ]
        pool = _make_pool(snapshot_count=1, snapshot_rows=rows)

        with (
            patch("butlers.jobs.home._notify_owner_telegram", new_callable=AsyncMock),
            patch(
                "butlers.jobs.home.state_get",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "butlers.jobs.home.store_fact",
                new_callable=AsyncMock,
                return_value=None,
            ) as mock_store_fact,
        ):
            result = await run_environment_report(pool, None)

        assert result["deviations_found"] >= 1
        mock_store_fact.assert_awaited()

        # Verify the fact call used correct predicate and permanence
        call_kwargs = mock_store_fact.call_args_list[0]
        assert call_kwargs.kwargs.get("predicate") == "comfort_deviation" or (
            len(call_kwargs.args) > 2 and call_kwargs.args[2] == "comfort_deviation"
        )

    async def test_job_args_ignored(self):
        """job_args is accepted but unused — should not raise."""
        rows = [
            _make_snapshot_row(
                "sensor.bedroom_temperature",
                "72.0",
                {"friendly_name": "Bedroom Temperature", "area_id": "bedroom"},
            ),
        ]
        pool = _make_pool(snapshot_count=1, snapshot_rows=rows)

        with (
            patch("butlers.jobs.home._notify_owner_telegram", new_callable=AsyncMock),
            patch("butlers.jobs.home.state_get", new_callable=AsyncMock, return_value=None),
            patch("butlers.jobs.home.store_fact", new_callable=AsyncMock, return_value=None),
        ):
            result = await run_environment_report(pool, {"unused_arg": "value"})

        assert "areas_checked" in result

    async def test_notification_contains_area_readings(self):
        """The Telegram notification should include the area name and sensor values."""
        rows = [
            _make_snapshot_row(
                "sensor.kitchen_temperature",
                "78.0",
                {"friendly_name": "Kitchen Temperature", "area_id": "kitchen"},
            ),
        ]
        pool = _make_pool(snapshot_count=1, snapshot_rows=rows)

        with (
            patch(
                "butlers.jobs.home._notify_owner_telegram", new_callable=AsyncMock
            ) as mock_notify,
            patch("butlers.jobs.home.state_get", new_callable=AsyncMock, return_value=None),
            patch("butlers.jobs.home.store_fact", new_callable=AsyncMock, return_value=None),
        ):
            await run_environment_report(pool, None)

        mock_notify.assert_awaited_once()
        msg = mock_notify.call_args[0][1]
        assert "Kitchen" in msg
        assert "78.0" in msg


# ---------------------------------------------------------------------------
# run_environment_report — state store fallback
# ---------------------------------------------------------------------------


class TestRunEnvironmentReportStateStoreFallback:
    async def test_uses_defaults_when_state_store_empty(self):
        """When state_get returns None, job should log warning and use hardcoded defaults."""
        rows = [
            _make_snapshot_row(
                "sensor.bedroom_temperature",
                "72.0",
                {"friendly_name": "Bedroom Temp", "area_id": "bedroom"},
            ),
        ]
        pool = _make_pool(snapshot_count=1, snapshot_rows=rows)

        with (
            patch("butlers.jobs.home._notify_owner_telegram", new_callable=AsyncMock),
            patch(
                "butlers.jobs.home.state_get",
                new_callable=AsyncMock,
                return_value=None,  # no stored thresholds
            ),
            patch("butlers.jobs.home.store_fact", new_callable=AsyncMock, return_value=None),
        ):
            result = await run_environment_report(pool, None)

        # Job should complete normally using defaults
        assert "areas_checked" in result
        assert result["areas_checked"] >= 1

    async def test_custom_thresholds_applied_when_stored(self):
        """Custom thresholds from state store are applied over defaults."""
        rows = [
            _make_snapshot_row(
                "sensor.living_room_temperature",
                "80.0",  # above default 76°F but within custom 78-82°F
                {"friendly_name": "Living Room Temp", "area_id": "living_room"},
            ),
        ]
        pool = _make_pool(snapshot_count=1, snapshot_rows=rows)

        custom_defaults = dict(_DEFAULT_COMFORT_DEFAULTS)
        custom_defaults["temp_max_f"] = 82.0  # raise the ceiling

        custom_deviation = dict(_DEFAULT_COMFORT_DEVIATION)

        def _mock_state_get(pool_arg: Any, key: str) -> Any:
            if key == "home:thresholds:comfort_defaults":
                return custom_defaults
            if key == "home:thresholds:comfort_deviation":
                return custom_deviation
            return None

        with (
            patch("butlers.jobs.home._notify_owner_telegram", new_callable=AsyncMock),
            patch(
                "butlers.jobs.home.state_get",
                new_callable=AsyncMock,
                side_effect=_mock_state_get,
            ),
            patch("butlers.jobs.home.store_fact", new_callable=AsyncMock, return_value=None),
        ):
            result = await run_environment_report(pool, None)

        # With custom threshold (max 82°F), 80°F should be ok → no deviations
        assert result["deviations_found"] == 0


# ---------------------------------------------------------------------------
# _NullEmbeddingEngine
# ---------------------------------------------------------------------------


class TestNullEmbeddingEngine:
    def test_returns_empty_vector_for_text(self):
        eng = _NullEmbeddingEngine()
        result = eng.embed("hello")
        assert result == []

    def test_returns_empty_vector_for_empty_string(self):
        eng = _NullEmbeddingEngine()
        result = eng.embed("")
        assert result == []

    def test_embed_is_synchronous(self):
        """embed() must be synchronous to match EmbeddingEngine interface."""
        import inspect

        eng = _NullEmbeddingEngine()
        # Should NOT be a coroutine function
        assert not inspect.iscoroutinefunction(eng.embed)


# ---------------------------------------------------------------------------
# Daemon registry — environment_report is registered for "home"
# ---------------------------------------------------------------------------


class TestDaemonRegistryEnvironmentReport:
    def test_environment_report_registered_for_home(self):
        from butlers.daemon import _DETERMINISTIC_SCHEDULE_JOB_REGISTRY

        home_jobs = _DETERMINISTIC_SCHEDULE_JOB_REGISTRY.get("home", {})
        assert "environment_report" in home_jobs, (
            "environment_report must be registered in _DETERMINISTIC_SCHEDULE_JOB_REGISTRY['home']"
        )

    def test_environment_report_handler_is_callable(self):
        from butlers.daemon import _DETERMINISTIC_SCHEDULE_JOB_REGISTRY

        handler = _DETERMINISTIC_SCHEDULE_JOB_REGISTRY["home"]["environment_report"]
        assert callable(handler)
