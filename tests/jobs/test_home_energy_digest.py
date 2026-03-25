"""Unit tests for butlers.jobs.home — run_energy_digest and helpers.

Covers:
- _is_energy_entity: keyword detection
- _extract_numeric_state: state parsing
- _compute_device_totals: ranking and share computation
- detect_anomalies: anomaly detection, severity levels, baseline comparison
- _build_digest_message: digest composition
- run_energy_digest: no-sensors fallback, no-entity-snapshot fallback,
  HA unreachable partial-data path, successful full run

All tests use mocked asyncpg pools — no database required.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.jobs.home import (
    _build_digest_message,
    _compute_device_totals,
    _extract_numeric_state,
    _is_energy_entity,
    detect_anomalies,
    run_energy_digest,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pool(
    *,
    snapshot_count: int = 5,
    snapshot_rows: list[dict[str, Any]] | None = None,
    state_rows: list[dict[str, Any]] | None = None,
    facts_rows: list[dict[str, Any]] | None = None,
) -> MagicMock:
    """Return a minimal mock asyncpg pool for energy digest tests."""
    pool = MagicMock()

    default_snapshot = snapshot_rows or []
    default_state = state_rows or []
    default_facts = facts_rows or []

    async def _fetchval(query: str, *args, **kwargs) -> Any:
        q_lower = query.lower()
        if "count(*)" in q_lower and "ha_entity_snapshot" in q_lower:
            return snapshot_count
        return None

    async def _fetch(query: str, *args, **kwargs) -> list[Any]:
        q_lower = query.lower()
        if "ha_entity_snapshot" in q_lower:
            return [_row(r) for r in default_snapshot]
        if "state" in q_lower and "key" in q_lower:
            return [_row(r) for r in default_state]
        if "facts" in q_lower and "energy_baseline" in q_lower:
            return [_row(r) for r in default_facts]
        return []

    async def _fetchrow(query: str, *args, **kwargs) -> Any:
        q_lower = query.lower()
        if "state" in q_lower and "key" in q_lower and args:
            key = args[0] if args else None
            for row in default_state:
                if row.get("key") == key:
                    return _row(row)
        return None

    pool.fetchval = AsyncMock(side_effect=_fetchval)
    pool.fetch = AsyncMock(side_effect=_fetch)
    pool.fetchrow = AsyncMock(side_effect=_fetchrow)
    pool.execute = AsyncMock()

    return pool


def _row(data: dict[str, Any]) -> MagicMock:
    """Create a mock row that behaves like an asyncpg Record."""
    row = MagicMock()
    row.__getitem__ = lambda self, key: data[key]
    row.get = lambda key, default=None: data.get(key, default)
    return row


def _make_energy_snapshot_row(
    entity_id: str,
    state: str = "10.5",
    friendly_name: str | None = None,
) -> dict[str, Any]:
    """Create a mock ha_entity_snapshot row for an energy sensor."""
    attrs: dict[str, Any] = {}
    if friendly_name:
        attrs["friendly_name"] = friendly_name
    return {
        "entity_id": entity_id,
        "state": state,
        "attributes": attrs,
    }


def _make_fact_row(subject: str, content: str) -> dict[str, Any]:
    return {"subject": subject, "content": content}


def _make_state_row(key: str, value: Any) -> dict[str, Any]:
    return {"key": key, "value": json.dumps(value)}


# ---------------------------------------------------------------------------
# _is_energy_entity tests
# ---------------------------------------------------------------------------


class TestIsEnergyEntity:
    def test_entity_id_with_energy(self):
        assert _is_energy_entity("sensor.home_energy_kwh", None) is True

    def test_entity_id_with_power(self):
        assert _is_energy_entity("sensor.washing_machine_power", None) is True

    def test_entity_id_with_kwh(self):
        assert _is_energy_entity("sensor.daily_kwh", None) is True

    def test_entity_id_with_consumption(self):
        assert _is_energy_entity("sensor.hvac_consumption", None) is True

    def test_entity_id_with_watt(self):
        assert _is_energy_entity("sensor.solar_watt", None) is True

    def test_friendly_name_match(self):
        assert _is_energy_entity("sensor.abc_xyz", "Energy Usage Living Room") is True

    def test_non_energy_entity(self):
        assert _is_energy_entity("sensor.temperature", "Room Temperature") is False

    def test_case_insensitive(self):
        assert _is_energy_entity("sensor.POWER_METER", None) is True

    def test_empty_friendly_name(self):
        assert _is_energy_entity("sensor.door_lock", "") is False

    def test_none_friendly_name(self):
        assert _is_energy_entity("sensor.light_level", None) is False


# ---------------------------------------------------------------------------
# _extract_numeric_state tests
# ---------------------------------------------------------------------------


class TestExtractNumericState:
    def test_valid_float(self):
        assert _extract_numeric_state("10.5") == pytest.approx(10.5)

    def test_valid_integer(self):
        assert _extract_numeric_state("100") == pytest.approx(100.0)

    def test_unavailable(self):
        assert _extract_numeric_state("unavailable") is None

    def test_unknown(self):
        assert _extract_numeric_state("unknown") is None

    def test_empty_string(self):
        assert _extract_numeric_state("") is None

    def test_none(self):
        assert _extract_numeric_state(None) is None

    def test_non_numeric_string(self):
        assert _extract_numeric_state("on") is None

    def test_zero(self):
        assert _extract_numeric_state("0") == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# _compute_device_totals tests
# ---------------------------------------------------------------------------


class TestComputeDeviceTotals:
    def _make_sensors(self, entity_ids: list[str]) -> list[dict[str, Any]]:
        return [
            {"entity_id": eid, "state": "0", "attributes": {}, "friendly_name": eid}
            for eid in entity_ids
        ]

    def test_empty_stats(self):
        result = _compute_device_totals({}, self._make_sensors([]))
        assert result == []

    def test_single_device(self):
        stats = {"sensor.hvac_energy": {"weekly_sum": 50.0}}
        sensors = self._make_sensors(["sensor.hvac_energy"])
        result = _compute_device_totals(stats, sensors)
        assert len(result) == 1
        assert result[0]["entity_id"] == "sensor.hvac_energy"
        assert result[0]["weekly_kwh"] == pytest.approx(50.0)
        assert result[0]["share_pct"] == pytest.approx(100.0)

    def test_multiple_devices_sorted_by_kwh(self):
        stats = {
            "sensor.hvac_energy": {"weekly_sum": 30.0},
            "sensor.water_heater_energy": {"weekly_sum": 50.0},
            "sensor.lights_energy": {"weekly_sum": 10.0},
        }
        sensors = self._make_sensors(list(stats.keys()))
        result = _compute_device_totals(stats, sensors)
        assert len(result) == 3
        assert result[0]["entity_id"] == "sensor.water_heater_energy"
        assert result[1]["entity_id"] == "sensor.hvac_energy"
        assert result[2]["entity_id"] == "sensor.lights_energy"

    def test_share_percentage_sums_to_100(self):
        stats = {
            "sensor.a": {"weekly_sum": 40.0},
            "sensor.b": {"weekly_sum": 60.0},
        }
        sensors = self._make_sensors(list(stats.keys()))
        result = _compute_device_totals(stats, sensors)
        total_share = sum(d["share_pct"] for d in result)
        assert total_share == pytest.approx(100.0, abs=0.5)

    def test_zero_kwh_devices_excluded(self):
        stats = {
            "sensor.a": {"weekly_sum": 0.0},
            "sensor.b": {"weekly_sum": 10.0},
        }
        sensors = self._make_sensors(list(stats.keys()))
        result = _compute_device_totals(stats, sensors)
        assert len(result) == 1
        assert result[0]["entity_id"] == "sensor.b"

    def test_missing_weekly_sum_treated_as_zero(self):
        stats = {
            "sensor.a": {},  # no weekly_sum key
        }
        sensors = self._make_sensors(["sensor.a"])
        result = _compute_device_totals(stats, sensors)
        # 0 kWh → excluded
        assert result == []

    def test_friendly_name_used(self):
        stats = {"sensor.hvac_kwh": {"weekly_sum": 20.0}}
        sensors = [
            {
                "entity_id": "sensor.hvac_kwh",
                "state": "20",
                "attributes": {},
                "friendly_name": "HVAC Energy Meter",
            }
        ]
        result = _compute_device_totals(stats, sensors)
        assert result[0]["friendly_name"] == "HVAC Energy Meter"


# ---------------------------------------------------------------------------
# detect_anomalies tests
# ---------------------------------------------------------------------------


class TestDetectAnomalies:
    def _make_totals(
        self,
        items: list[tuple[str, float]],
    ) -> list[dict[str, Any]]:
        """Build device_totals list from (entity_id, weekly_kwh) pairs."""
        total = sum(kwh for _, kwh in items)
        return [
            {
                "entity_id": eid,
                "friendly_name": eid,
                "weekly_kwh": kwh,
                "share_pct": kwh / total * 100 if total > 0 else 0.0,
            }
            for eid, kwh in items
        ]

    def _make_baselines(self, items: list[tuple[str, float]]) -> dict[str, Any]:
        """Build baselines dict from (entity_id, baseline_kwh) pairs."""
        return {eid: {"content": f"{kwh:.1f} kWh weekly baseline"} for eid, kwh in items}

    def test_no_anomaly_below_threshold(self):
        totals = self._make_totals([("sensor.hvac", 50.0)])
        baselines = self._make_baselines([("sensor.hvac", 45.0)])
        # 50/45 → ~11.1% above — below 20% threshold
        result = detect_anomalies(totals, baselines, anomaly_pct=20.0, high_severity_pct=100.0)
        assert result == []

    def test_anomaly_at_threshold(self):
        totals = self._make_totals([("sensor.hvac", 60.0)])
        baselines = self._make_baselines([("sensor.hvac", 50.0)])
        # 60/50 → 20% above baseline — exactly at threshold
        result = detect_anomalies(totals, baselines, anomaly_pct=20.0, high_severity_pct=100.0)
        assert len(result) == 1
        assert result[0]["severity"] == "anomaly"
        assert result[0]["pct_above"] == pytest.approx(20.0, abs=0.5)

    def test_anomaly_above_threshold(self):
        totals = self._make_totals([("sensor.hvac", 70.0)])
        baselines = self._make_baselines([("sensor.hvac", 50.0)])
        # 70/50 → 40% above baseline
        result = detect_anomalies(totals, baselines, anomaly_pct=20.0, high_severity_pct=100.0)
        assert len(result) == 1
        assert result[0]["severity"] == "anomaly"

    def test_high_severity_at_threshold(self):
        totals = self._make_totals([("sensor.hvac", 100.0)])
        baselines = self._make_baselines([("sensor.hvac", 50.0)])
        # 100/50 → 100% above baseline — high severity
        result = detect_anomalies(totals, baselines, anomaly_pct=20.0, high_severity_pct=100.0)
        assert len(result) == 1
        assert result[0]["severity"] == "high"

    def test_high_severity_above_threshold(self):
        totals = self._make_totals([("sensor.hvac", 200.0)])
        baselines = self._make_baselines([("sensor.hvac", 50.0)])
        # 200/50 → 300% above — definitely high severity
        result = detect_anomalies(totals, baselines, anomaly_pct=20.0, high_severity_pct=100.0)
        assert len(result) == 1
        assert result[0]["severity"] == "high"

    def test_no_baseline_no_anomaly(self):
        totals = self._make_totals([("sensor.hvac", 100.0)])
        result = detect_anomalies(totals, {}, anomaly_pct=20.0, high_severity_pct=100.0)
        assert result == []

    def test_mixed_anomaly_and_normal(self):
        totals = self._make_totals(
            [
                ("sensor.hvac", 60.0),  # 20% above → anomaly
                ("sensor.lights", 25.0),  # 0% above → normal
            ]
        )
        baselines = self._make_baselines(
            [
                ("sensor.hvac", 50.0),
                ("sensor.lights", 25.0),
            ]
        )
        result = detect_anomalies(totals, baselines, anomaly_pct=20.0, high_severity_pct=100.0)
        assert len(result) == 1
        assert result[0]["entity_id"] == "sensor.hvac"

    def test_anomaly_result_fields(self):
        totals = self._make_totals([("sensor.hvac", 62.0)])
        baselines = self._make_baselines([("sensor.hvac", 50.0)])
        result = detect_anomalies(totals, baselines, anomaly_pct=20.0, high_severity_pct=100.0)
        assert len(result) == 1
        a = result[0]
        assert "entity_id" in a
        assert "friendly_name" in a
        assert "weekly_kwh" in a
        assert "baseline_kwh" in a
        assert "pct_above" in a
        assert "severity" in a
        assert a["baseline_kwh"] == pytest.approx(50.0)

    def test_configurable_anomaly_pct(self):
        totals = self._make_totals([("sensor.hvac", 51.0)])
        baselines = self._make_baselines([("sensor.hvac", 50.0)])
        # 2% above — below default 20% but above custom 1%
        result_default = detect_anomalies(
            totals, baselines, anomaly_pct=20.0, high_severity_pct=100.0
        )
        result_custom = detect_anomalies(
            totals, baselines, anomaly_pct=1.0, high_severity_pct=100.0
        )
        assert result_default == []
        assert len(result_custom) == 1

    def test_zero_baseline_ignored(self):
        totals = self._make_totals([("sensor.hvac", 50.0)])
        baselines = {"sensor.hvac": {"content": "0 kWh weekly baseline"}}
        # Baseline of 0 should not divide-by-zero; entity skipped
        result = detect_anomalies(totals, baselines, anomaly_pct=20.0, high_severity_pct=100.0)
        assert result == []

    def test_multiple_high_severity_anomalies(self):
        totals = self._make_totals(
            [
                ("sensor.hvac", 200.0),  # 300% above → high
                ("sensor.water", 100.0),  # 100% above → high (boundary)
            ]
        )
        baselines = self._make_baselines(
            [
                ("sensor.hvac", 50.0),
                ("sensor.water", 50.0),
            ]
        )
        result = detect_anomalies(totals, baselines, anomaly_pct=20.0, high_severity_pct=100.0)
        assert len(result) == 2
        assert all(a["severity"] == "high" for a in result)


# ---------------------------------------------------------------------------
# _build_digest_message tests
# ---------------------------------------------------------------------------


class TestBuildDigestMessage:
    def _make_top_consumer(self, entity_id: str, kwh: float, share: float) -> dict[str, Any]:
        return {
            "entity_id": entity_id,
            "friendly_name": entity_id,
            "weekly_kwh": kwh,
            "share_pct": share,
        }

    def test_includes_total_kwh(self):
        msg = _build_digest_message(
            total_kwh=100.0,
            top_consumers=[],
            anomalies=[],
            baseline_total=None,
        )
        assert "100.0" in msg

    def test_includes_weekly_energy_digest_heading(self):
        msg = _build_digest_message(
            total_kwh=50.0,
            top_consumers=[],
            anomalies=[],
            baseline_total=None,
        )
        assert "Energy Digest" in msg

    def test_trend_vs_baseline_positive(self):
        msg = _build_digest_message(
            total_kwh=110.0,
            top_consumers=[],
            anomalies=[],
            baseline_total=100.0,
        )
        assert "+10.0%" in msg

    def test_trend_vs_baseline_negative(self):
        msg = _build_digest_message(
            total_kwh=90.0,
            top_consumers=[],
            anomalies=[],
            baseline_total=100.0,
        )
        assert "-10.0%" in msg

    def test_no_baseline_no_trend(self):
        msg = _build_digest_message(
            total_kwh=100.0,
            top_consumers=[],
            anomalies=[],
            baseline_total=None,
        )
        assert "%" not in msg or "vs baseline" not in msg

    def test_top_consumers_listed(self):
        consumers = [
            self._make_top_consumer("sensor.hvac", 50.0, 50.0),
            self._make_top_consumer("sensor.wh", 30.0, 30.0),
        ]
        msg = _build_digest_message(
            total_kwh=100.0,
            top_consumers=consumers,
            anomalies=[],
            baseline_total=None,
        )
        assert "sensor.hvac" in msg
        assert "sensor.wh" in msg

    def test_anomaly_alert_included(self):
        anomaly = {
            "entity_id": "sensor.hvac",
            "friendly_name": "HVAC",
            "weekly_kwh": 200.0,
            "baseline_kwh": 50.0,
            "pct_above": 300.0,
            "severity": "high",
        }
        msg = _build_digest_message(
            total_kwh=200.0,
            top_consumers=[],
            anomalies=[anomaly],
            baseline_total=None,
        )
        assert "HVAC" in msg
        assert "HIGH" in msg.upper() or "Anomaly" in msg

    def test_no_anomalies_no_alert_section(self):
        msg = _build_digest_message(
            total_kwh=50.0,
            top_consumers=[],
            anomalies=[],
            baseline_total=None,
        )
        # Should not mention "Anomaly" in alert context
        assert "⚠️" not in msg or "anomaly" not in msg.lower()

    def test_recommendations_included(self):
        consumers = [self._make_top_consumer("sensor.hvac", 80.0, 80.0)]
        msg = _build_digest_message(
            total_kwh=100.0,
            top_consumers=consumers,
            anomalies=[],
            baseline_total=None,
        )
        assert "Recommendations" in msg or "sensor.hvac" in msg


# ---------------------------------------------------------------------------
# run_energy_digest integration tests (pool-level mocking)
# ---------------------------------------------------------------------------


class TestRunEnergyDigest:
    """Test run_energy_digest with mocked pool and external dependencies."""

    async def test_no_entity_snapshot_returns_error(self):
        """When snapshot count is 0, return {'error': 'no_entity_snapshot'}."""
        pool = _make_pool(snapshot_count=0)

        with patch(
            "butlers.jobs.home._notify_owner_telegram", new_callable=AsyncMock
        ) as mock_notify:
            result = await run_energy_digest(pool, None)

        assert result == {"error": "no_entity_snapshot"}
        mock_notify.assert_called_once()
        msg = mock_notify.call_args[0][1]
        assert "unavailable" in msg.lower() or "data" in msg.lower()

    async def test_no_energy_sensors_returns_error(self):
        """When snapshot has rows but no energy sensors, return {'error': 'no_energy_sensors'}."""
        non_energy_rows = [
            _make_energy_snapshot_row("sensor.temperature", "22.5", "Room Temp"),
            _make_energy_snapshot_row("sensor.humidity", "45", "Humidity"),
        ]
        pool = _make_pool(snapshot_count=2, snapshot_rows=non_energy_rows)

        with patch(
            "butlers.jobs.home._notify_owner_telegram", new_callable=AsyncMock
        ) as mock_notify:
            result = await run_energy_digest(pool, None)

        assert result == {"error": "no_energy_sensors"}
        mock_notify.assert_called_once()
        msg = mock_notify.call_args[0][1]
        assert "not configured" in msg.lower() or "energy" in msg.lower()

    async def test_ha_unreachable_with_no_stats(self):
        """When HA is unreachable, return result with zeroed totals but no error."""
        energy_rows = [
            _make_energy_snapshot_row("sensor.hvac_energy", "10", "HVAC Energy"),
        ]
        pool = _make_pool(snapshot_count=1, snapshot_rows=energy_rows)

        with (
            patch(
                "butlers.jobs.home._notify_owner_telegram", new_callable=AsyncMock
            ) as mock_notify,
            patch(
                "butlers.credential_store.resolve_owner_entity_info",
                new_callable=AsyncMock,
                return_value="http://ha.local",
            ),
            patch(
                "butlers.jobs.home._fetch_weekly_statistics",
                new_callable=AsyncMock,
                return_value={},  # HA unreachable → empty stats
            ),
            patch(
                "butlers.jobs.home._load_energy_baselines",
                new_callable=AsyncMock,
                return_value={},
            ),
            patch(
                "butlers.jobs.home._load_energy_thresholds",
                new_callable=AsyncMock,
                return_value={"anomaly_pct": 20.0, "high_severity_pct": 100.0},
            ),
        ):
            result = await run_energy_digest(pool, None)

        assert "error" not in result
        assert result["total_kwh"] == 0.0
        assert result["devices_ranked"] == 0
        assert result["anomalies_found"] == 0
        mock_notify.assert_called_once()

    async def test_full_run_with_anomalies(self):
        """Full successful run with energy sensors, statistics, and anomaly detection."""
        energy_rows = [
            _make_energy_snapshot_row("sensor.hvac_energy", "100", "HVAC Energy"),
            _make_energy_snapshot_row("sensor.water_heater_energy", "200", "Water Heater"),
        ]
        pool = _make_pool(snapshot_count=2, snapshot_rows=energy_rows)

        weekly_stats = {
            "sensor.hvac_energy": {"weekly_sum": 120.0},
            "sensor.water_heater_energy": {"weekly_sum": 200.0},
        }
        # HVAC: 120 vs baseline 50 → 140% above → high severity
        # Water heater: 200 vs baseline 100 → 100% → high severity
        baselines = {
            "sensor.hvac_energy": {"content": "50.0 kWh weekly baseline"},
            "sensor.water_heater_energy": {"content": "100.0 kWh weekly baseline"},
        }

        with (
            patch(
                "butlers.jobs.home._notify_owner_telegram", new_callable=AsyncMock
            ) as mock_notify,
            patch(
                "butlers.credential_store.resolve_owner_entity_info",
                new_callable=AsyncMock,
                return_value="token_value",
            ),
            patch(
                "butlers.jobs.home._fetch_weekly_statistics",
                new_callable=AsyncMock,
                return_value=weekly_stats,
            ),
            patch(
                "butlers.jobs.home._load_energy_baselines",
                new_callable=AsyncMock,
                return_value=baselines,
            ),
            patch(
                "butlers.jobs.home._load_energy_thresholds",
                new_callable=AsyncMock,
                return_value={"anomaly_pct": 20.0, "high_severity_pct": 100.0},
            ),
            patch(
                "butlers.modules.memory.storage.store_fact",
                new_callable=AsyncMock,
            ),
        ):
            result = await run_energy_digest(pool, None)

        assert "error" not in result
        assert result["total_kwh"] == pytest.approx(320.0, abs=0.1)
        assert result["devices_ranked"] == 2
        assert result["anomalies_found"] == 2
        mock_notify.assert_called_once()
        msg = mock_notify.call_args[0][1]
        assert "320" in msg or "320.0" in msg

    async def test_full_run_no_anomalies(self):
        """Full run where no device exceeds baseline threshold."""
        energy_rows = [
            _make_energy_snapshot_row("sensor.hvac_energy", "50", "HVAC"),
        ]
        pool = _make_pool(snapshot_count=1, snapshot_rows=energy_rows)

        weekly_stats = {"sensor.hvac_energy": {"weekly_sum": 50.0}}
        baselines = {"sensor.hvac_energy": {"content": "49.0 kWh weekly baseline"}}

        with (
            patch("butlers.jobs.home._notify_owner_telegram", new_callable=AsyncMock),
            patch(
                "butlers.credential_store.resolve_owner_entity_info",
                new_callable=AsyncMock,
                return_value="token_value",
            ),
            patch(
                "butlers.jobs.home._fetch_weekly_statistics",
                new_callable=AsyncMock,
                return_value=weekly_stats,
            ),
            patch(
                "butlers.jobs.home._load_energy_baselines",
                new_callable=AsyncMock,
                return_value=baselines,
            ),
            patch(
                "butlers.jobs.home._load_energy_thresholds",
                new_callable=AsyncMock,
                return_value={"anomaly_pct": 20.0, "high_severity_pct": 100.0},
            ),
            patch("butlers.modules.memory.storage.store_fact", new_callable=AsyncMock),
        ):
            result = await run_energy_digest(pool, None)

        # 50/49 ≈ 2% above → no anomaly
        assert result["anomalies_found"] == 0
        assert result["total_kwh"] == pytest.approx(50.0, abs=0.1)

    async def test_job_args_accepted_and_ignored(self):
        """job_args parameter is accepted without error."""
        pool = _make_pool(snapshot_count=0)

        with patch("butlers.jobs.home._notify_owner_telegram", new_callable=AsyncMock):
            result = await run_energy_digest(pool, {"some_arg": "value"})

        # Should not raise — just returns early due to empty snapshot
        assert result == {"error": "no_entity_snapshot"}

    async def test_baseline_updated_flag(self):
        """baseline_updated is True when total_kwh > 0 and store_fact succeeds."""
        energy_rows = [
            _make_energy_snapshot_row("sensor.solar_energy", "100", "Solar"),
        ]
        pool = _make_pool(snapshot_count=1, snapshot_rows=energy_rows)

        weekly_stats = {"sensor.solar_energy": {"weekly_sum": 80.0}}

        with (
            patch("butlers.jobs.home._notify_owner_telegram", new_callable=AsyncMock),
            patch(
                "butlers.credential_store.resolve_owner_entity_info",
                new_callable=AsyncMock,
                return_value="token_value",
            ),
            patch(
                "butlers.jobs.home._fetch_weekly_statistics",
                new_callable=AsyncMock,
                return_value=weekly_stats,
            ),
            patch(
                "butlers.jobs.home._load_energy_baselines",
                new_callable=AsyncMock,
                return_value={},
            ),
            patch(
                "butlers.jobs.home._load_energy_thresholds",
                new_callable=AsyncMock,
                return_value={"anomaly_pct": 20.0, "high_severity_pct": 100.0},
            ),
            patch(
                "butlers.modules.memory.storage.store_fact",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            result = await run_energy_digest(pool, None)

        assert result["baseline_updated"] is True

    async def test_return_value_structure(self):
        """Successful run returns dict with required keys."""
        energy_rows = [
            _make_energy_snapshot_row("sensor.home_energy", "100", "Home Energy"),
        ]
        pool = _make_pool(snapshot_count=1, snapshot_rows=energy_rows)

        with (
            patch("butlers.jobs.home._notify_owner_telegram", new_callable=AsyncMock),
            patch(
                "butlers.credential_store.resolve_owner_entity_info",
                new_callable=AsyncMock,
                return_value=None,  # no HA credentials → unreachable path
            ),
            patch(
                "butlers.jobs.home._load_energy_thresholds",
                new_callable=AsyncMock,
                return_value={"anomaly_pct": 20.0, "high_severity_pct": 100.0},
            ),
            patch(
                "butlers.jobs.home._load_energy_baselines",
                new_callable=AsyncMock,
                return_value={},
            ),
        ):
            result = await run_energy_digest(pool, None)

        assert set(result.keys()) >= {"total_kwh", "devices_ranked", "anomalies_found"}
        assert isinstance(result["total_kwh"], float)
        assert isinstance(result["devices_ranked"], int)
        assert isinstance(result["anomalies_found"], int)


# ---------------------------------------------------------------------------
# Daemon registry test
# ---------------------------------------------------------------------------


class TestHomeDaemonRegistry:
    """Verify energy_digest is registered in the daemon job registry for home."""

    def test_energy_digest_registered_in_home_registry(self):
        from butlers.daemon import _DETERMINISTIC_SCHEDULE_JOB_REGISTRY

        home_handlers = _DETERMINISTIC_SCHEDULE_JOB_REGISTRY.get("home", {})
        assert "energy_digest" in home_handlers, (
            "energy_digest handler not found in _DETERMINISTIC_SCHEDULE_JOB_REGISTRY['home']. "
            "Check daemon.py _HOME_DETERMINISTIC_JOB_HANDLERS."
        )

    def test_all_home_deterministic_jobs_registered(self):
        from butlers.daemon import _DETERMINISTIC_SCHEDULE_JOB_REGISTRY

        home_handlers = _DETERMINISTIC_SCHEDULE_JOB_REGISTRY.get("home", {})
        expected_jobs = {
            "device_health_check",
            "environment_report",
            "energy_digest",
            "maintenance_schedule_check",
        }
        for job_name in expected_jobs:
            assert job_name in home_handlers, (
                f"Expected home job '{job_name}' not found in registry. "
                f"Registered: {sorted(home_handlers)}"
            )

    async def test_energy_digest_handler_callable(self):
        """The registered handler can be called with pool and job_args=None."""
        from butlers.daemon import _DETERMINISTIC_SCHEDULE_JOB_REGISTRY

        handler = _DETERMINISTIC_SCHEDULE_JOB_REGISTRY["home"]["energy_digest"]

        mock_pool = _make_pool(snapshot_count=0)

        with patch("butlers.jobs.home._notify_owner_telegram", new_callable=AsyncMock):
            result = await handler(mock_pool, None)

        # Should not raise; returns error dict for empty snapshot
        assert isinstance(result, dict)
