"""Tests for butlers.jobs.health_ha_reader — HaEnvironmentReader factory.

Verifies that:
1. ``build_ha_environment_reader`` returns ``None`` when HA credentials are absent.
2. ``build_ha_environment_reader`` returns an async callable when credentials are set.
3. The reader callable fetches history from the HA REST API and classifies readings.
4. Internal helpers (``_classify_entity``, ``_is_adverse``) behave correctly.
5. ``_run_health_insight_scan_job`` in scheduled_jobs.py injects the reader into
   ``run_insight_scan``, wiring the environment-correlation path in production.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.jobs.health_ha_reader import (
    _classify_entity,
    _is_adverse,
    build_ha_environment_reader,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pool(*, snapshot_rows: list[dict] | None = None) -> MagicMock:
    """Build a mock pool that returns snapshot_rows for ha_entity_snapshot queries."""
    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value=None)
    pool.fetchval = AsyncMock(return_value=0)
    pool.execute = AsyncMock()

    def _fetch_side_effect(sql: str, *args: Any, **kwargs: Any) -> list[Any]:
        if "ha_entity_snapshot" in sql:
            if snapshot_rows is not None:
                return [_make_row(r) for r in snapshot_rows]
            return []
        return []

    pool.fetch = AsyncMock(side_effect=_fetch_side_effect)
    return pool


def _make_row(data: dict) -> MagicMock:
    row = MagicMock()
    row.__getitem__ = lambda self, key: data[key]
    return row


def _make_ha_ctx(ha_url: str | None, ha_token: str | None) -> MagicMock:
    """Build a mock HomeJobContext returned by HomeJobContext.create()."""
    ctx = MagicMock()
    ctx.ha_url = ha_url
    ctx.ha_token = ha_token
    return ctx


def _mock_hjc_class(
    ha_url: str | None,
    ha_token: str | None,
    mock_client: MagicMock | None = None,
) -> MagicMock:
    """Return a mock HomeJobContext *class* covering both usage sites in the module.

    ``build_ha_environment_reader`` calls ``HomeJobContext.create(pool)`` (classmethod)
    to get credentials.  The reader closure later calls ``HomeJobContext(url, token)``
    as an async context manager.  Patching the class itself (not just ``create``)
    keeps both usages consistent on the same mock object.
    """
    creds_instance = _make_ha_ctx(ha_url, ha_token)

    cm_instance = MagicMock()
    cm_instance.client = mock_client
    cm_instance.__aenter__ = AsyncMock(return_value=cm_instance)
    cm_instance.__aexit__ = AsyncMock(return_value=None)

    mock_cls = MagicMock()
    mock_cls.create = AsyncMock(return_value=creds_instance)
    mock_cls.return_value = cm_instance  # HomeJobContext(url, token) call
    return mock_cls


# ---------------------------------------------------------------------------
# build_ha_environment_reader — credential guard
# ---------------------------------------------------------------------------


async def test_build_ha_environment_reader_no_credentials_returns_none():
    """Returns None when HA URL is absent — no credentials configured."""
    pool = _make_pool()

    with patch(
        "butlers.jobs.health_ha_reader.HomeJobContext",
        _mock_hjc_class(ha_url=None, ha_token=None),
    ):
        result = await build_ha_environment_reader(pool)

    assert result is None


async def test_build_ha_environment_reader_no_token_returns_none():
    """Returns None when token is absent even if URL is set."""
    pool = _make_pool()

    with patch(
        "butlers.jobs.health_ha_reader.HomeJobContext",
        _mock_hjc_class(ha_url="http://ha.local:8123", ha_token=None),
    ):
        result = await build_ha_environment_reader(pool)

    assert result is None


async def test_build_ha_environment_reader_with_credentials_returns_callable():
    """Returns an async callable when both URL and token are present."""
    pool = _make_pool()

    with patch(
        "butlers.jobs.health_ha_reader.HomeJobContext",
        _mock_hjc_class(ha_url="http://ha.local:8123", ha_token="tok_abc"),
    ):
        reader = await build_ha_environment_reader(pool)

    assert reader is not None
    assert callable(reader)


# ---------------------------------------------------------------------------
# build_ha_environment_reader — reader behaviour (via _fetch_environment_readings)
# ---------------------------------------------------------------------------


async def test_reader_returns_empty_when_no_env_entities():
    """Reader returns [] when ha_entity_snapshot has no environmental sensors."""
    pool = _make_pool(snapshot_rows=[{"entity_id": "switch.living_room_light"}])

    with patch(
        "butlers.jobs.health_ha_reader.HomeJobContext",
        _mock_hjc_class(ha_url="http://ha.local:8123", ha_token="tok_abc"),
    ):
        reader = await build_ha_environment_reader(pool)

    assert reader is not None
    readings = await reader()
    assert readings == []


async def test_reader_returns_empty_when_snapshot_table_missing():
    """Reader returns [] when ha_entity_snapshot query raises (table not yet created)."""
    pool = MagicMock()
    pool.fetch = AsyncMock(side_effect=Exception("relation 'ha_entity_snapshot' does not exist"))

    with patch(
        "butlers.jobs.health_ha_reader.HomeJobContext",
        _mock_hjc_class(ha_url="http://ha.local:8123", ha_token="tok_abc"),
    ):
        reader = await build_ha_environment_reader(pool)

    assert reader is not None
    readings = await reader()
    assert readings == []


async def test_reader_classifies_temperature_readings():
    """Reader fetches HA history and classifies temperature readings as adverse/ok."""
    now = datetime.now(UTC)
    day1 = now - timedelta(days=2)
    day2 = now - timedelta(days=3)

    pool = _make_pool(snapshot_rows=[{"entity_id": "sensor.bedroom_temperature"}])

    # HA history API response: two readings — one adverse (too cold), one ok, one null state.
    history_response = [
        [
            {
                "entity_id": "sensor.bedroom_temperature",
                "state": "60.0",  # 60°F — below 68°F threshold → adverse
                "last_changed": day1.isoformat(),
            },
            {
                "entity_id": "sensor.bedroom_temperature",
                "state": "72.0",  # 72°F — in range → not adverse
                "last_changed": day2.isoformat(),
            },
            {
                "entity_id": "sensor.bedroom_temperature",
                "state": None,  # null state — should degrade gracefully (not adverse)
                "last_changed": day2.isoformat(),
            },
        ]
    ]

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = history_response

    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=mock_response)

    with patch(
        "butlers.jobs.health_ha_reader.HomeJobContext",
        _mock_hjc_class(ha_url="http://ha.local:8123", ha_token="tok_abc", mock_client=mock_client),
    ):
        reader = await build_ha_environment_reader(pool)
        assert reader is not None
        readings = await reader()

    assert len(readings) == 3
    adverse = [r for r in readings if r["adverse"]]
    ok = [r for r in readings if not r["adverse"]]
    assert len(adverse) == 1
    assert adverse[0]["metric"] == "temperature"
    assert len(ok) == 2
    assert all(r["metric"] == "temperature" for r in readings)
    assert all("captured_at" in r for r in readings)


async def test_reader_returns_empty_on_ha_api_error():
    """Reader returns [] when HA REST API call fails — no exception raised."""
    pool = _make_pool(snapshot_rows=[{"entity_id": "sensor.bedroom_temperature"}])

    # cm_instance.__aenter__ raising simulates a connection error
    mock_cls = _mock_hjc_class(ha_url="http://ha.local:8123", ha_token="tok_abc")
    mock_cls.return_value.__aenter__ = AsyncMock(side_effect=Exception("Connection refused"))

    with patch("butlers.jobs.health_ha_reader.HomeJobContext", mock_cls):
        reader = await build_ha_environment_reader(pool)
        assert reader is not None
        readings = await reader()

    assert readings == []


async def test_reader_returns_empty_on_ha_non_200():
    """Reader returns [] when HA history API returns a non-200 status."""
    pool = _make_pool(snapshot_rows=[{"entity_id": "sensor.bedroom_temperature"}])

    mock_response = MagicMock()
    mock_response.status_code = 503

    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=mock_response)

    with patch(
        "butlers.jobs.health_ha_reader.HomeJobContext",
        _mock_hjc_class(ha_url="http://ha.local:8123", ha_token="tok_abc", mock_client=mock_client),
    ):
        reader = await build_ha_environment_reader(pool)
        assert reader is not None
        readings = await reader()

    assert readings == []


# ---------------------------------------------------------------------------
# _classify_entity — keyword matching
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("entity_id", "expected"),
    [
        ("sensor.bedroom_temperature", "temperature"),
        ("sensor.living_room_temp", "temperature"),
        ("sensor.bathroom_humidity", "humidity"),
        ("sensor.bedroom_humid", "humidity"),
        ("sensor.office_co2", "co2"),
        ("sensor.carbon_dioxide_level", "co2"),
        ("sensor.indoor_air_quality", "air_quality"),
        ("sensor.aqi_sensor", "air_quality"),
        ("sensor.pm25_outdoor", "air_quality"),
        # Non-environmental entities must return None
        ("light.living_room", None),
        ("switch.bedroom_fan", None),
        ("binary_sensor.motion_detected", None),
    ],
)
def test_classify_entity(entity_id: str, expected: str | None):
    """_classify_entity maps entity IDs to metric buckets or None."""
    assert _classify_entity(entity_id) == expected


# ---------------------------------------------------------------------------
# _is_adverse — comfort thresholds
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("metric", "state", "expect_adverse"),
    [
        # Temperature (Fahrenheit — below 50 treated as Celsius)
        ("temperature", "60.0", True),  # too cold (60°F < 68°F threshold)
        ("temperature", "80.0", True),  # too hot (80°F > 76°F threshold)
        ("temperature", "72.0", False),  # comfortable range
        ("temperature", "20.0", False),  # 20°C → 68°F exactly = lower boundary → not adverse
        ("temperature", "19.0", True),  # 19°C → 66.2°F → below 68°F → adverse
        ("temperature", "25.0", True),  # 25°C → 77°F → above 76°F → adverse
        ("temperature", "unavailable", False),  # non-numeric → not adverse
        # Humidity
        ("humidity", "20.0", True),  # too dry
        ("humidity", "70.0", True),  # too humid
        ("humidity", "45.0", False),  # comfortable
        ("humidity", "off", False),  # non-numeric
        # CO2
        ("co2", "1500.0", True),  # too high
        ("co2", "800.0", False),  # acceptable
        ("co2", "", False),  # empty → not adverse
        # Air quality — named states
        ("air_quality", "poor", True),
        ("air_quality", "very_poor", True),
        ("air_quality", "hazardous", True),
        ("air_quality", "good", False),
        # Air quality — numeric AQI
        ("air_quality", "60.0", True),  # > 50 threshold
        ("air_quality", "40.0", False),
        # Unknown metric
        ("pressure", "1013.0", False),
    ],
)
def test_is_adverse(metric: str, state: str, expect_adverse: bool):
    """_is_adverse classifies sensor readings against comfort thresholds."""
    assert _is_adverse(metric, state) == expect_adverse


# ---------------------------------------------------------------------------
# _parse_ha_datetime — datetime parsing
# ---------------------------------------------------------------------------


def test_parse_ha_datetime_branches():
    """_parse_ha_datetime: ISO-Z → UTC-aware datetime; None/invalid → None;
    existing datetime passes through unchanged."""
    from butlers.jobs.health_ha_reader import _parse_ha_datetime

    iso = _parse_ha_datetime("2024-01-15T10:30:00Z")
    assert iso is not None
    assert iso.tzinfo is not None
    assert iso.year == 2024 and iso.month == 1

    assert _parse_ha_datetime(None) is None
    assert _parse_ha_datetime("not-a-date") is None

    dt = datetime(2024, 6, 1, 12, 0, tzinfo=UTC)
    assert _parse_ha_datetime(dt) == dt


# ---------------------------------------------------------------------------
# _run_health_insight_scan_job dispatch wiring
# Proves (a) reader is constructed/injected at dispatch and (b) the
# environment-correlation path executes when the reader is present.
# ---------------------------------------------------------------------------


async def test_dispatch_injects_reader_when_ha_configured():
    """_run_health_insight_scan_job passes the reader to run_insight_scan.

    Proves requirement (a): reader is constructed and injected at dispatch.
    """
    from butlers.scheduled_jobs import _run_health_insight_scan_job

    pool = MagicMock()

    async def fake_reader():
        return []

    mock_mod = MagicMock()
    mock_mod.run_insight_scan = AsyncMock(return_value={"scanned": 0})

    with (
        patch(
            "butlers.jobs.health_ha_reader.build_ha_environment_reader",
            new=AsyncMock(return_value=fake_reader),
        ),
        patch(
            "butlers.jobs._roster_loader.load_roster_jobs",
            return_value=mock_mod,
        ),
    ):
        await _run_health_insight_scan_job(pool, None)

    mock_mod.run_insight_scan.assert_awaited_once()
    call_kwargs = mock_mod.run_insight_scan.call_args
    # The reader must be passed as ha_environment_reader keyword argument.
    assert call_kwargs.kwargs.get("ha_environment_reader") is fake_reader


async def test_dispatch_passes_none_reader_when_ha_absent():
    """_run_health_insight_scan_job passes None reader when HA is not configured.

    Environment correlation is skipped cleanly — same as before this fix.
    """
    from butlers.scheduled_jobs import _run_health_insight_scan_job

    pool = MagicMock()

    mock_mod = MagicMock()
    mock_mod.run_insight_scan = AsyncMock(return_value={"scanned": 0})

    with (
        patch(
            "butlers.jobs.health_ha_reader.build_ha_environment_reader",
            new=AsyncMock(return_value=None),  # no credentials
        ),
        patch(
            "butlers.jobs._roster_loader.load_roster_jobs",
            return_value=mock_mod,
        ),
    ):
        await _run_health_insight_scan_job(pool, None)

    mock_mod.run_insight_scan.assert_awaited_once()
    call_kwargs = mock_mod.run_insight_scan.call_args
    assert call_kwargs.kwargs.get("ha_environment_reader") is None


# The end-to-end environment-correlation scan path (reader present → candidate
# fires) is covered by test_environment_correlation_submits_via_mcp_tool in
# test_health_jobs.py; the dispatch wiring above only needs to prove the reader
# is constructed/injected (and skipped when HA is absent).
