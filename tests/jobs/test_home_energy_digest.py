"""Tests for butlers.jobs.home — run_energy_digest and helpers.

Covers _is_energy_entity, _compute_device_totals,
_build_digest_message, run_energy_digest, and daemon registry.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.connectors.home_assistant_statistics import HAStatisticsError
from butlers.core.baselines import compute_baselines, score
from butlers.jobs.home import (
    _build_digest_message,
    _compute_device_totals,
    _fetch_weekly_statistics,
    _is_energy_entity,
    run_energy_digest,
)
from butlers.jobs.home_baselines import (
    EnergyFinding,
    anomalies_from,
    energy_metric,
    scored_week_anchor,
)

pytestmark = pytest.mark.unit


def _make_pool(
    *,
    snapshot_count=5,
    snapshot_rows=None,
    state_rows=None,
    ha_status="healthy",
) -> Any:
    pool = MagicMock()

    async def _fetchval(query, *args, **kwargs):
        count_q = "count(*)" in query.lower() and "ha_entity_snapshot" in query.lower()
        return snapshot_count if count_q else None

    async def _fetch(query, *args, **kwargs):
        q = query.lower()
        if "ha_entity_snapshot" in q:
            return [r for r in (snapshot_rows or [])]
        return []

    async def _fetchrow(query, *args, **kwargs):
        q = query.lower()
        if "ha_source_health" in q:
            if ha_status is None:
                return None
            return {
                "status": ha_status,
                "last_success_at": datetime.now(UTC) if ha_status == "healthy" else None,
                "lease_current": ha_status == "healthy",
            }
        if "state" in q and "key" in q and args:
            for row in state_rows or []:
                if row.get("key") == args[0]:
                    return row
        return None

    pool.fetchval = AsyncMock(side_effect=_fetchval)
    pool.fetch = AsyncMock(side_effect=_fetch)
    pool.fetchrow = AsyncMock(side_effect=_fetchrow)
    pool.execute = AsyncMock()
    return pool


def _make_energy_row(entity_id, state="10.5", friendly_name=None) -> dict:
    attrs = {"friendly_name": friendly_name} if friendly_name else {}
    return {"entity_id": entity_id, "state": state, "attributes": attrs}


def _finding(key, entity_id, name, kwh, history):
    """A scored finding from a real baseline over ``history`` weekly values."""
    from datetime import date, timedelta

    from butlers.core.baselines import compute_baselines, score
    from butlers.jobs.home_baselines import EnergyFinding, energy_metric

    metric = energy_metric(key)
    as_of = date(2026, 10, 12)
    samples = {as_of - timedelta(days=7 * (i + 1)): v for i, v in enumerate(history)}
    now = datetime(2026, 10, 12, 6, tzinfo=UTC)
    (baseline,) = compute_baselines(metric, samples, as_of=as_of, now=now)
    return EnergyFinding(key, entity_id, name, kwh, score(metric, kwh, baseline, now=now), baseline)


# ---------------------------------------------------------------------------
# _is_energy_entity
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "entity_id, friendly_name, expected",
    [
        ("sensor.home_energy_kwh", None, True),
        ("sensor.solar_watt", None, True),
        ("sensor.abc_xyz", "Energy Usage Living Room", True),
        ("sensor.temperature", "Room Temperature", False),
    ],
)
def test_is_energy_entity(entity_id, friendly_name, expected):
    """_is_energy_entity identifies energy sensors by entity_id/friendly_name patterns."""
    assert _is_energy_entity(entity_id, friendly_name) is expected


# ---------------------------------------------------------------------------
# _compute_device_totals
# ---------------------------------------------------------------------------


def test_compute_device_totals():
    """Empty → []; sorted by kWh desc; zeros excluded; shares sum to 100."""
    assert _compute_device_totals({}, []) == []

    stats = {
        "sensor.a": {"weekly_sum": 40.0},
        "sensor.b": {"weekly_sum": 60.0},
        "sensor.z": {"weekly_sum": 0.0},
    }
    sensors = [{"entity_id": k, "state": "0", "attributes": {}, "friendly_name": k} for k in stats]
    result = _compute_device_totals(stats, sensors)
    assert len(result) == 2
    assert result[0]["entity_id"] == "sensor.b"
    assert sum(d["share_pct"] for d in result) == pytest.approx(100.0, abs=0.5)


# ---------------------------------------------------------------------------
# _fetch_weekly_statistics
# ---------------------------------------------------------------------------


async def test_fetch_weekly_statistics_uses_ha_websocket_recorder_command():
    """The digest reuses the shared client for aligned hourly and daily data."""
    hourly = {
        "sensor.energy": [
            {"start": 1, "end": 2, "sum": 105.0, "change": 5.0},
            {"start": 2, "end": 3, "sum": 112.5, "change": 7.5},
        ]
    }
    daily = {
        "sensor.energy": [
            {"start": 1, "end": 2, "sum": 105.0, "change": 5.0},
            {"start": 2, "end": 3, "sum": 112.5, "change": 7.5},
        ]
    }
    client = MagicMock()
    client.get_statistics = AsyncMock(side_effect=[hourly, daily])

    with patch(
        "butlers.jobs.home.HAStatisticsClient",
        return_value=client,
    ) as client_factory:
        result = await _fetch_weekly_statistics(
            ["sensor.energy"],
            ha_url="http://ha.local:8123/",
            ha_token="token",
        )

    client_factory.assert_called_once_with(
        ha_url="http://ha.local:8123/",
        ha_token="token",
        verify_ssl=False,
    )
    calls = client.get_statistics.await_args_list
    assert [call.kwargs["period"] for call in calls] == ["hour", "day"]
    assert all(call.kwargs["types"] == ("change",) for call in calls)
    assert calls[0].kwargs["start"] == calls[1].kwargs["start"]
    assert calls[0].kwargs["end"] == calls[1].kwargs["end"]
    assert result == {
        "available": True,
        "statistics": {
            "sensor.energy": {
                "weekly_sum": 12.5,
                "daily": daily["sensor.energy"],
            }
        },
        "unsupported_entity_ids": [],
    }


async def test_fetch_weekly_statistics_returns_empty_when_authentication_is_rejected():
    """Authentication rejection preserves the digest's graceful fallback."""
    client = MagicMock()
    client.get_statistics = AsyncMock(
        side_effect=HAStatisticsError("unauthorized", scope="connection")
    )

    with patch("butlers.jobs.home.HAStatisticsClient", return_value=client):
        result = await _fetch_weekly_statistics(
            ["sensor.energy"],
            ha_url="http://ha.local:8123/",
            ha_token="token",
        )

    assert result == {
        "available": False,
        "statistics": {},
        "unsupported_entity_ids": [],
    }


async def test_fetch_weekly_statistics_returns_empty_when_aggregate_command_is_rejected(caplog):
    """A rejected aggregate response is unavailable and logs only its bounded code."""
    client = MagicMock()
    client.get_statistics = AsyncMock(
        side_effect=HAStatisticsError("unknown_error", scope="command")
    )

    with patch("butlers.jobs.home.HAStatisticsClient", return_value=client):
        result = await _fetch_weekly_statistics(
            ["sensor.energy"],
            ha_url="http://ha.local:8123/",
            ha_token="token",
        )

    assert result == {
        "available": False,
        "statistics": {},
        "unsupported_entity_ids": [],
    }
    assert "unknown_error" in caplog.text


@pytest.mark.parametrize(
    "invalid_change",
    [
        pytest.param(None, id="missing"),
        pytest.param("not-a-number", id="nonnumeric"),
        pytest.param(float("inf"), id="infinite"),
        pytest.param(float("nan"), id="nan"),
    ],
)
async def test_fetch_weekly_statistics_marks_incomplete_change_series_unsupported(invalid_change):
    """A series without finite hourly changes must never be coerced to zero consumption."""
    invalid_row = {"start": 1, "end": 2, "mean": 750.0}
    if invalid_change is not None:
        invalid_row["change"] = invalid_change

    client = MagicMock()
    client.get_statistics = AsyncMock(
        side_effect=[
            {"sensor.instantaneous_power": [invalid_row]},
            {"sensor.instantaneous_power": [{"start": 1, "end": 2, "mean": 750.0}]},
        ]
    )

    with patch("butlers.jobs.home.HAStatisticsClient", return_value=client):
        result = await _fetch_weekly_statistics(
            ["sensor.instantaneous_power"],
            ha_url="http://ha.local:8123/",
            ha_token="token",
        )

    assert result == {
        "available": True,
        "statistics": {},
        "unsupported_entity_ids": ["sensor.instantaneous_power"],
    }


async def test_fetch_weekly_statistics_accepts_explicit_zero_change():
    """An explicit finite zero is a supported zero-consumption series, not missing data."""
    client = MagicMock()
    client.get_statistics = AsyncMock(
        side_effect=[
            {"sensor.cumulative_energy": [{"start": 1, "end": 2, "change": 0}]},
            {"sensor.cumulative_energy": [{"start": 1, "end": 2, "change": 0}]},
        ]
    )

    with patch("butlers.jobs.home.HAStatisticsClient", return_value=client):
        result = await _fetch_weekly_statistics(
            ["sensor.cumulative_energy"],
            ha_url="http://ha.local:8123/",
            ha_token="token",
        )

    assert result == {
        "available": True,
        "statistics": {
            "sensor.cumulative_energy": {
                "weekly_sum": 0.0,
                "daily": [{"start": 1, "end": 2, "change": 0}],
            }
        },
        "unsupported_entity_ids": [],
    }


async def test_fetch_weekly_statistics_preserves_hourly_data_when_daily_fetch_fails():
    """Daily enrichment failure does not discard a valid cumulative-energy total."""
    client = MagicMock()
    client.get_statistics = AsyncMock(
        side_effect=[
            {"sensor.energy": [{"start": 1, "end": 2, "change": 4.5}]},
            HAStatisticsError("protocol_error", scope="command"),
        ]
    )

    with patch("butlers.jobs.home.HAStatisticsClient", return_value=client):
        result = await _fetch_weekly_statistics(
            ["sensor.energy"],
            ha_url="http://ha.local:8123/",
            ha_token="token",
        )

    assert result == {
        "available": True,
        "statistics": {"sensor.energy": {"weekly_sum": 4.5}},
        "unsupported_entity_ids": [],
    }


# ---------------------------------------------------------------------------
# detect_anomalies (now anomalies_from: a device above its own usual range)
# ---------------------------------------------------------------------------

_ANCHOR = scored_week_anchor(datetime(2026, 10, 19, 6, tzinfo=UTC))


def _device_finding(kwh: float, history: list[float]) -> EnergyFinding:
    metric = energy_metric("energy.device:sensor.hvac")
    samples = {_ANCHOR - timedelta(days=7 * (i + 1)): v for i, v in enumerate(history)}
    now = datetime(2026, 10, 12, 6, tzinfo=UTC)
    (baseline,) = compute_baselines(metric, samples, as_of=_ANCHOR, now=now)
    return EnergyFinding(
        "energy.device:sensor.hvac",
        "sensor.hvac",
        "HVAC",
        kwh,
        score(metric, kwh, baseline, now=now),
        baseline,
    )


@pytest.mark.parametrize(
    "weekly_kwh, baseline_kwh, expected_count, expected_severity",
    [
        (50.0, 45.0, 0, None),  # inside the usual range -> no anomaly
        (60.0, 50.0, 1, "anomaly"),  # above the usual range -> anomaly
        (100.0, 50.0, 1, "high"),  # twice the usual centre -> high
    ],
)
def test_detect_anomalies(weekly_kwh, baseline_kwh, expected_count, expected_severity):
    """Thresholds still grade a device: inside its usual range none, above it anomaly, 2x high."""
    history = [baseline_kwh * share for share in (0.94, 1.06, 1.0, 0.96, 1.04, 1.0)]
    result = anomalies_from([_device_finding(weekly_kwh, history)])
    assert len(result) == expected_count
    if expected_severity:
        assert result[0]["severity"] == expected_severity


def test_detect_anomalies_edge_cases():
    """No ready band -> no anomaly; a zero centre is skipped."""
    assert anomalies_from([_device_finding(100.0, [])]) == []
    assert anomalies_from([_device_finding(100.0, [10.0, 11.0])]) == []
    assert anomalies_from([_device_finding(100.0, [0.0, 0.0, 0.0, 0.0, 0.0])]) == []


# ---------------------------------------------------------------------------
# _build_digest_message
# ---------------------------------------------------------------------------


def test_build_digest_message():
    """Message includes heading, total kWh, trend, and anomaly sections."""
    msg = _build_digest_message(
        total_kwh=100.0, top_consumers=[], anomalies=[], baseline_total=None
    )
    assert "Energy Digest" in msg and "100.0" in msg

    assert "+10.0%" in _build_digest_message(110.0, [], [], 100.0)
    assert "-10.0%" in _build_digest_message(90.0, [], [], 100.0)

    anomaly = {
        "entity_id": "s.hvac",
        "friendly_name": "HVAC",
        "weekly_kwh": 200.0,
        "baseline_kwh": 50.0,
        "pct_above": 300.0,
        "severity": "high",
    }
    msg2 = _build_digest_message(200.0, [], [anomaly], None)
    assert "HVAC" in msg2


# ---------------------------------------------------------------------------
# run_energy_digest
# ---------------------------------------------------------------------------


async def test_run_energy_digest_unmeasurable_on_ha_outage():
    """A simulated HA outage (unhealthy source) short-circuits before the snapshot check."""
    pool = _make_pool(snapshot_count=5, ha_status="error")
    with patch("butlers.jobs.home._send_notify", new_callable=AsyncMock) as notify:
        result = await run_energy_digest(pool, None)
    assert result == {"error": "ha_source_unmeasurable", "last_good_at": None}
    notify.assert_awaited_once()
    assert "unmeasurable" in notify.await_args.args[1].lower()
    pool.fetchval.assert_not_called()


async def test_run_energy_digest_early_exits():
    """Empty snapshot → error; no energy sensors → error."""
    with patch("butlers.jobs.home._send_notify", new_callable=AsyncMock):
        result = await run_energy_digest(_make_pool(snapshot_count=0), None)
    assert result == {"error": "no_entity_snapshot"}

    pool = _make_pool(
        snapshot_count=2,
        snapshot_rows=[
            _make_energy_row("sensor.temperature", "22.5", "Room Temp"),
        ],
    )
    with patch("butlers.jobs.home._send_notify", new_callable=AsyncMock):
        result2 = await run_energy_digest(pool, None)
    assert result2 == {"error": "no_energy_sensors"}


async def test_run_energy_digest_full_run_with_anomalies():
    """Full successful run returns correct totals and anomaly count."""
    energy_rows = [
        _make_energy_row("sensor.hvac_energy", "100", "HVAC Energy"),
        _make_energy_row("sensor.water_heater_energy", "200", "Water Heater"),
    ]
    pool = _make_pool(snapshot_count=2, snapshot_rows=energy_rows)
    weekly_stats = {
        "available": True,
        "statistics": {
            "sensor.hvac_energy": {"weekly_sum": 120.0},
            "sensor.water_heater_energy": {"weekly_sum": 200.0},
        },
        "unsupported_entity_ids": [],
    }
    findings = [
        _finding("energy.total", None, "House total", 320.0, [150.0] * 6),
        _finding(
            "energy.device:sensor.hvac_energy",
            "sensor.hvac_energy",
            "HVAC Energy",
            120.0,
            [50.0] * 6,
        ),
        _finding(
            "energy.device:sensor.water_heater_energy",
            "sensor.water_heater_energy",
            "Water Heater",
            200.0,
            [100.0] * 6,
        ),
    ]

    with (
        patch("butlers.jobs.home._send_notify", new_callable=AsyncMock),
        patch(
            "butlers.jobs.home.resolve_owner_entity_info",
            new_callable=AsyncMock,
            return_value="token_value",
        ),
        patch(
            "butlers.jobs.home._fetch_weekly_statistics",
            new_callable=AsyncMock,
            return_value=weekly_stats,
        ),
        patch(
            "butlers.jobs.home.fetch_weekly_history",
            new_callable=AsyncMock,
            return_value={},
        ),
        patch(
            "butlers.jobs.home.score_energy_week",
            new_callable=AsyncMock,
            return_value=(findings, True),
        ),
        patch("butlers.jobs.home.store_fact", new_callable=AsyncMock) as mock_store,
    ):
        result = await run_energy_digest(pool, None)

    assert "error" not in result
    assert result["total_kwh"] == pytest.approx(320.0, abs=0.1)
    assert result["devices_ranked"] == 2 and result["anomalies_found"] == 2
    assert result["baseline_updated"] is True
    # Only the two spike facts: the prose energy_baseline facts are no longer written.
    assert mock_store.await_count == 2
    assert {call.kwargs["predicate"] for call in mock_store.await_args_list} == {"energy_spike"}
    assert all(call.kwargs["source_butler"] == "home" for call in mock_store.await_args_list)


async def test_run_energy_digest_rejects_all_unsupported_statistics():
    """Power-only sensors require an HA cumulative-energy helper instead of a fake zero."""
    pool = _make_pool(
        snapshot_count=1,
        snapshot_rows=[
            _make_energy_row("sensor.instantaneous_power", "750", "Instantaneous Power"),
        ],
    )

    with (
        patch("butlers.jobs.home._send_notify", new_callable=AsyncMock) as mock_notify,
        patch(
            "butlers.jobs.home.resolve_owner_entity_info",
            new_callable=AsyncMock,
            return_value="configured",
        ),
        patch(
            "butlers.jobs.home._fetch_weekly_statistics",
            new_callable=AsyncMock,
            return_value={
                "available": True,
                "statistics": {},
                "unsupported_entity_ids": ["sensor.instantaneous_power"],
            },
        ),
        patch(
            "butlers.jobs.home.score_energy_week",
            new_callable=AsyncMock,
        ) as mock_score,
        patch("butlers.jobs.home.store_fact", new_callable=AsyncMock) as mock_store,
    ):
        result = await run_energy_digest(pool, None)

    assert result == {
        "error": "no_cumulative_energy_statistics",
        "unsupported_sensors": ["sensor.instantaneous_power"],
    }
    notification = mock_notify.await_args.args[1]
    assert "cumulative-energy statistics" in notification
    assert "energy helper" in notification
    mock_score.assert_not_awaited()
    mock_store.assert_not_awaited()


async def test_run_energy_digest_mixed_statistics_is_visibly_partial():
    """A partial sensor set reports device data without inventing a whole-home total."""
    pool = _make_pool(
        snapshot_count=2,
        snapshot_rows=[
            _make_energy_row("sensor.hvac_energy", "120", "HVAC Energy"),
            _make_energy_row("sensor.instantaneous_power", "750", "Instantaneous Power"),
        ],
    )

    with (
        patch("butlers.jobs.home._send_notify", new_callable=AsyncMock) as mock_notify,
        patch(
            "butlers.jobs.home.resolve_owner_entity_info",
            new_callable=AsyncMock,
            return_value="configured",
        ),
        patch(
            "butlers.jobs.home._fetch_weekly_statistics",
            new_callable=AsyncMock,
            return_value={
                "available": True,
                "statistics": {"sensor.hvac_energy": {"weekly_sum": 12.0}},
                "unsupported_entity_ids": ["sensor.instantaneous_power"],
            },
        ),
        patch(
            "butlers.jobs.home.score_energy_week",
            new_callable=AsyncMock,
        ) as mock_score,
        patch("butlers.jobs.home.store_fact", new_callable=AsyncMock) as mock_store,
    ):
        result = await run_energy_digest(pool, None)

    assert result == {
        "partial": True,
        "omitted_sensors": ["sensor.instantaneous_power"],
        "devices_ranked": 1,
        "anomalies_found": 0,
        "baseline_updated": False,
    }
    notification = mock_notify.await_args.args[1]
    assert "Partial data" in notification
    assert "Instantaneous Power" in notification
    assert "HVAC Energy: 12.0 kWh" in notification
    assert "Total:" not in notification
    assert "vs baseline" not in notification
    assert "%" not in notification
    mock_score.assert_not_awaited()
    assert all(call.kwargs["subject"] != "overall" for call in mock_store.await_args_list)


def test_all_home_deterministic_jobs_registered():
    """All expected home jobs are registered in _DETERMINISTIC_SCHEDULE_JOB_REGISTRY."""
    from butlers.scheduled_jobs import _DETERMINISTIC_SCHEDULE_JOB_REGISTRY

    home_jobs = _DETERMINISTIC_SCHEDULE_JOB_REGISTRY.get("home", {})
    for job in (
        "device_health_check",
        "environment_report",
        "energy_digest",
        "maintenance_schedule_check",
    ):
        assert job in home_jobs and callable(home_jobs[job])
