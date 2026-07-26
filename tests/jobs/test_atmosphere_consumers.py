"""Tests for the atmosphere feed consumer jobs (bu-8bnn9, follow-up bu-ep4ks.16 slice 1).

Covers the three consumers (home pre-conditioning, health advisory, travel
destination outlook): honest skip when the shared feed is not configured / has
no reading, threshold-triggered insight proposals, and the destination-outlook
job's per-trip resilience to geocoding/fetch failures.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from butlers.jobs.atmosphere_consumers import (
    run_health_atmosphere_advisory,
    run_home_atmosphere_preconditioning,
    run_travel_destination_outlook,
)

pytestmark = pytest.mark.unit

_BROKER_PATH = "butlers.tools.switchboard.insight.broker.propose_insight_candidate"


def _make_pool(*, configured: bool, reading: dict | None):
    pool = AsyncMock()
    config_row = {"configured": configured}
    pool.fetchrow = AsyncMock(side_effect=[config_row, reading])
    return pool


def _reading(**overrides):
    row = {
        "apparent_temperature_c": 20.0,
        "aqi_us": 30,
        "pollen_available": False,
        "pollen_tree": None,
        "pollen_grass": None,
        "pollen_weed": None,
    }
    row.update(overrides)
    return row


# ---------------------------------------------------------------------------
# Home pre-conditioning
# ---------------------------------------------------------------------------


async def test_home_preconditioning_skips_when_not_configured():
    pool = _make_pool(configured=False, reading=None)
    result = await run_home_atmosphere_preconditioning(pool)
    assert result == {"skipped": True, "reason": "not_configured"}


async def test_home_preconditioning_skips_when_no_reading():
    pool = _make_pool(configured=True, reading=None)
    result = await run_home_atmosphere_preconditioning(pool)
    assert result == {"skipped": True, "reason": "no_reading"}


async def test_home_preconditioning_hot_condition_proposes_precool():
    pool = _make_pool(configured=True, reading=_reading(apparent_temperature_c=35.0))
    propose_mock = AsyncMock(return_value={"status": "accepted"})
    with patch(_BROKER_PATH, propose_mock):
        result = await run_home_atmosphere_preconditioning(pool)

    assert result["skipped"] is False
    assert result["candidates_proposed"] == 1
    propose_mock.assert_awaited_once()
    _, kwargs = propose_mock.call_args
    assert kwargs["origin_butler"] == "home"
    assert "heat" in kwargs["dedup_key"]


async def test_home_preconditioning_unhealthy_aqi_proposes():
    pool = _make_pool(configured=True, reading=_reading(aqi_us=180))
    propose_mock = AsyncMock(return_value={"status": "accepted"})
    with patch(_BROKER_PATH, propose_mock):
        result = await run_home_atmosphere_preconditioning(pool)

    assert result["candidates_proposed"] == 1
    _, kwargs = propose_mock.call_args
    assert "aqi" in kwargs["dedup_key"]


async def test_home_preconditioning_comfortable_conditions_no_proposal():
    pool = _make_pool(configured=True, reading=_reading())
    propose_mock = AsyncMock(return_value={"status": "accepted"})
    with patch(_BROKER_PATH, propose_mock):
        result = await run_home_atmosphere_preconditioning(pool)

    assert result["candidates_proposed"] == 0
    propose_mock.assert_not_awaited()


# ---------------------------------------------------------------------------
# Health advisory
# ---------------------------------------------------------------------------


async def test_health_advisory_skips_when_not_configured():
    pool = _make_pool(configured=False, reading=None)
    result = await run_health_atmosphere_advisory(pool)
    assert result == {"skipped": True, "reason": "not_configured"}


async def test_health_advisory_unhealthy_aqi_proposes():
    pool = _make_pool(configured=True, reading=_reading(aqi_us=120))
    propose_mock = AsyncMock(return_value={"status": "accepted"})
    with patch(_BROKER_PATH, propose_mock):
        result = await run_health_atmosphere_advisory(pool)

    assert result["candidates_proposed"] == 1
    _, kwargs = propose_mock.call_args
    assert kwargs["origin_butler"] == "health"
    assert "aqi" in kwargs["dedup_key"]


async def test_health_advisory_elevated_pollen_proposes():
    pool = _make_pool(
        configured=True,
        reading=_reading(pollen_available=True, pollen_tree=60.0, pollen_grass=10.0),
    )
    propose_mock = AsyncMock(return_value={"status": "accepted"})
    with patch(_BROKER_PATH, propose_mock):
        result = await run_health_atmosphere_advisory(pool)

    assert result["candidates_proposed"] == 1
    _, kwargs = propose_mock.call_args
    assert "pollen" in kwargs["dedup_key"]


async def test_health_advisory_normal_conditions_no_proposal():
    pool = _make_pool(configured=True, reading=_reading())
    propose_mock = AsyncMock(return_value={"status": "accepted"})
    with patch(_BROKER_PATH, propose_mock):
        result = await run_health_atmosphere_advisory(pool)

    assert result["candidates_proposed"] == 0
    propose_mock.assert_not_awaited()


# ---------------------------------------------------------------------------
# Travel destination outlook
# ---------------------------------------------------------------------------

_GEOCODE_PAYLOAD = {"results": [{"latitude": 35.6, "longitude": 139.7, "name": "Tokyo"}]}
_GEOCODE_EMPTY = {"results": []}
_FORECAST_PAYLOAD = {
    "current": {
        "time": "2026-07-27T12:00",
        "temperature_2m": 24.0,
        "weather_code": 61,
    }
}
_AIR_QUALITY_PAYLOAD = {"current": {"us_aqi": 40}}


def _mock_client(*, geocode_payload=None, forecast_status=200):
    geocode_payload = geocode_payload if geocode_payload is not None else _GEOCODE_PAYLOAD

    def _handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "geocoding-api" in url:
            return httpx.Response(200, json=geocode_payload)
        if forecast_status != 200:
            return httpx.Response(forecast_status, json={"error": "boom"})
        if "air-quality" in url:
            return httpx.Response(200, json=_AIR_QUALITY_PAYLOAD)
        return httpx.Response(200, json=_FORECAST_PAYLOAD)

    return httpx.AsyncClient(transport=httpx.MockTransport(_handler))


def _trip_row(**overrides):
    row = {
        "id": uuid.uuid4(),
        "name": "Tokyo trip",
        "destination": "Tokyo",
        "start_date": date.today() + timedelta(days=1),
    }
    row.update(overrides)
    return row


async def test_destination_outlook_no_trips_skips():
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=[])
    result = await run_travel_destination_outlook(pool)
    assert result == {"skipped": True, "reason": "no_upcoming_trips"}


async def test_destination_outlook_proposes_for_upcoming_trip():
    trip = _trip_row()
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=[trip])
    client = _mock_client()

    propose_mock = AsyncMock(return_value={"status": "accepted"})
    with patch(_BROKER_PATH, propose_mock):
        result = await run_travel_destination_outlook(pool, http_client=client)

    assert result["skipped"] is False
    assert result["trips_checked"] == 1
    assert result["candidates_proposed"] == 1
    propose_mock.assert_awaited_once()
    _, kwargs = propose_mock.call_args
    assert kwargs["origin_butler"] == "travel"
    assert str(trip["id"]) in kwargs["dedup_key"]
    assert "Tokyo" in kwargs["message"]

    await client.aclose()


async def test_destination_outlook_geocode_miss_skips_trip():
    trip = _trip_row()
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=[trip])
    client = _mock_client(geocode_payload=_GEOCODE_EMPTY)

    propose_mock = AsyncMock(return_value={"status": "accepted"})
    with patch(_BROKER_PATH, propose_mock):
        result = await run_travel_destination_outlook(pool, http_client=client)

    assert result["trips_checked"] == 0
    assert result["candidates_proposed"] == 0
    propose_mock.assert_not_awaited()

    await client.aclose()


async def test_destination_outlook_fetch_failure_skips_trip_without_crashing():
    trip = _trip_row()
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=[trip])
    client = _mock_client(forecast_status=503)

    propose_mock = AsyncMock(return_value={"status": "accepted"})
    with patch(_BROKER_PATH, propose_mock):
        result = await run_travel_destination_outlook(pool, http_client=client)

    assert result["skipped"] is False
    assert result["trips_checked"] == 0
    assert result["candidates_proposed"] == 0
    propose_mock.assert_not_awaited()

    await client.aclose()
