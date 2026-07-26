"""Tests for the atmosphere (weather/AQI/pollen) context-feed refresh job.

Covers: fetch-parse-store roundtrip, not-configured honest skip, fetch-failure
degradation (no crash, no fabricated reading), and the legitimately-absent
vs genuine-failure distinction for pollen fields (Open-Meteo only forecasts
pollen for European locations).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from butlers.jobs.atmosphere import (
    parse_home_coordinates,
    parse_reading,
    run_atmosphere_feed_refresh,
)

pytestmark = pytest.mark.unit

_FORECAST_PAYLOAD = {
    "current": {
        "time": "2026-07-26T12:00",
        "temperature_2m": 21.5,
        "apparent_temperature": 20.0,
        "relative_humidity_2m": 55.0,
        "precipitation": 0.0,
        "weather_code": 1,
        "wind_speed_10m": 12.0,
    }
}

_AIR_QUALITY_PAYLOAD_NON_EUROPEAN = {
    "current": {
        "us_aqi": 42,
        "european_aqi": 20,
        "pm2_5": 5.1,
        "pm10": 8.2,
        "alder_pollen": None,
        "birch_pollen": None,
        "grass_pollen": None,
        "mugwort_pollen": None,
        "olive_pollen": None,
        "ragweed_pollen": None,
    }
}

_AIR_QUALITY_PAYLOAD_EUROPEAN = {
    "current": {
        "us_aqi": 30,
        "european_aqi": 15,
        "pm2_5": 4.0,
        "pm10": 7.0,
        "alder_pollen": 2.0,
        "birch_pollen": 5.0,
        "grass_pollen": 12.0,
        "mugwort_pollen": 0.0,
        "olive_pollen": 1.0,
        "ragweed_pollen": 3.0,
    }
}


def _make_pool(fetchrow_result=None):
    pool = AsyncMock()
    pool.execute = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=fetchrow_result)
    return pool


def _mock_client(*, air_quality_payload=None, status_code=200):
    air_quality_payload = air_quality_payload or _AIR_QUALITY_PAYLOAD_NON_EUROPEAN

    def _handler(request: httpx.Request) -> httpx.Response:
        if status_code != 200:
            return httpx.Response(status_code, json={"error": "boom"})
        if "air-quality" in str(request.url):
            return httpx.Response(200, json=air_quality_payload)
        return httpx.Response(200, json=_FORECAST_PAYLOAD)

    return httpx.AsyncClient(transport=httpx.MockTransport(_handler))


# ---------------------------------------------------------------------------
# parse_reading / parse_home_coordinates — pure parsing
# ---------------------------------------------------------------------------


def test_parse_reading_non_european_location_has_no_pollen():
    raw = {"forecast": _FORECAST_PAYLOAD, "air_quality": _AIR_QUALITY_PAYLOAD_NON_EUROPEAN}
    reading = parse_reading(raw, latitude=40.0, longitude=-74.0)
    assert reading["temperature_c"] == 21.5
    assert reading["aqi_us"] == 42
    assert reading["pollen_available"] is False
    assert reading["pollen_tree"] is None
    assert reading["pollen_grass"] is None
    assert reading["pollen_weed"] is None


def test_parse_reading_european_location_buckets_pollen():
    raw = {"forecast": _FORECAST_PAYLOAD, "air_quality": _AIR_QUALITY_PAYLOAD_EUROPEAN}
    reading = parse_reading(raw, latitude=51.5, longitude=-0.1)
    assert reading["pollen_available"] is True
    assert reading["pollen_tree"] == 5.0  # max(alder=2.0, birch=5.0)
    assert reading["pollen_grass"] == 12.0
    assert reading["pollen_weed"] == 3.0  # max(mugwort=0.0, olive=1.0, ragweed=3.0)


@pytest.mark.parametrize(
    "value,expected",
    [
        ("40.7128,-74.0060", (40.7128, -74.0060)),
        (" 40.7128 , -74.0060 ", (40.7128, -74.0060)),
        ("not-a-coordinate", None),
        ("40.7128", None),
        ("abc,def", None),
    ],
)
def test_parse_home_coordinates(value, expected):
    assert parse_home_coordinates(value) == expected


# ---------------------------------------------------------------------------
# run_atmosphere_feed_refresh — not configured (honest skip)
# ---------------------------------------------------------------------------


async def test_not_configured_skips_fetch_and_marks_status(monkeypatch):
    monkeypatch.delenv("ATMOSPHERE_HOME_LAT", raising=False)
    monkeypatch.delenv("ATMOSPHERE_HOME_LON", raising=False)
    pool = _make_pool()

    with patch("butlers.jobs.atmosphere.resolve_owner_entity_info", AsyncMock(return_value=None)):
        result = await run_atmosphere_feed_refresh(pool)

    assert result == {"skipped": True, "reason": "not_configured"}
    pool.execute.assert_awaited_once()
    (sql,), _ = pool.execute.call_args
    assert "atmosphere_feed_status" in sql
    assert "configured = false" in sql


# ---------------------------------------------------------------------------
# run_atmosphere_feed_refresh — successful fetch
# ---------------------------------------------------------------------------


async def test_successful_fetch_parses_and_stores_reading(monkeypatch):
    monkeypatch.setenv("ATMOSPHERE_HOME_LAT", "40.0")
    monkeypatch.setenv("ATMOSPHERE_HOME_LON", "-74.0")
    pool = _make_pool()
    client = _mock_client()

    result = await run_atmosphere_feed_refresh(pool, http_client=client)

    assert result["skipped"] is False
    assert result["pollen_available"] is False
    assert pool.execute.await_count == 2  # reading insert + status upsert
    reading_call = pool.execute.call_args_list[0]
    assert "atmosphere_readings" in reading_call.args[0]
    status_call = pool.execute.call_args_list[1]
    assert "atmosphere_feed_status" in status_call.args[0]
    await client.aclose()


# ---------------------------------------------------------------------------
# run_atmosphere_feed_refresh — fetch failure degrades honestly
# ---------------------------------------------------------------------------


async def test_fetch_failure_records_error_without_crashing(monkeypatch):
    monkeypatch.setenv("ATMOSPHERE_HOME_LAT", "40.0")
    monkeypatch.setenv("ATMOSPHERE_HOME_LON", "-74.0")
    pool = _make_pool()
    client = _mock_client(status_code=503)

    result = await run_atmosphere_feed_refresh(pool, http_client=client)

    assert result["skipped"] is False
    assert "error" in result
    pool.execute.assert_awaited_once()
    sql = pool.execute.call_args.args[0]
    assert "atmosphere_feed_status" in sql
    assert "atmosphere_readings" not in sql
    await client.aclose()


async def test_malformed_env_coordinates_falls_back_to_entity_info(monkeypatch):
    monkeypatch.setenv("ATMOSPHERE_HOME_LAT", "not-a-number")
    monkeypatch.setenv("ATMOSPHERE_HOME_LON", "-74.0")
    pool = _make_pool()

    with patch("butlers.jobs.atmosphere.resolve_owner_entity_info", AsyncMock(return_value=None)):
        result = await run_atmosphere_feed_refresh(pool)

    assert result == {"skipped": True, "reason": "not_configured"}
