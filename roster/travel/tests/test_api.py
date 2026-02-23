"""Unit tests for travel butler dashboard API endpoints.

Verifies the API contract (status codes, response shapes, filtering, pagination)
for the travel butler's GET endpoints using mocked database connections.

Issue: butlers-9a3l.7
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Load the travel router for dependency override
# ---------------------------------------------------------------------------

_ROUTER_PATH = Path(__file__).parents[1] / "api" / "router.py"


def _load_travel_router():
    """Dynamically load the travel router module."""
    import importlib.util
    import sys

    module_name = "travel_api_router_test"
    if module_name in sys.modules:
        return sys.modules[module_name]

    spec = importlib.util.spec_from_file_location(module_name, _ROUTER_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


_travel_router_mod = _load_travel_router()

# ---------------------------------------------------------------------------
# Helpers — row factories
# ---------------------------------------------------------------------------

_NOW = datetime.now(tz=UTC)
_TODAY = date.today()
_UUID = str(uuid.uuid4())
_TRIP_UUID = str(uuid.uuid4())


def _trip_row(
    *,
    id: Any = None,
    name: str = "Tokyo Trip",
    destination: str = "Tokyo",
    start_date: Any = None,
    end_date: Any = None,
    status: str = "planned",
    metadata: dict | None = None,
    created_at: Any = None,
    updated_at: Any = None,
) -> dict:
    return {
        "id": uuid.UUID(id) if id else uuid.uuid4(),
        "name": name,
        "destination": destination,
        "start_date": start_date or (_TODAY + timedelta(days=7)),
        "end_date": end_date or (_TODAY + timedelta(days=14)),
        "status": status,
        "metadata": metadata or {},
        "created_at": created_at or _NOW,
        "updated_at": updated_at or _NOW,
    }


def _leg_row(
    *,
    id: Any = None,
    trip_id: Any = None,
    type: str = "flight",
    carrier: str | None = "United Airlines",
    departure_airport_station: str | None = "SFO",
    departure_city: str | None = "San Francisco",
    departure_at: Any = None,
    arrival_airport_station: str | None = "NRT",
    arrival_city: str | None = "Tokyo",
    arrival_at: Any = None,
    confirmation_number: str | None = "ABC123",
    pnr: str | None = "K9X4TZ",
    seat: str | None = "24A",
    metadata: dict | None = None,
    created_at: Any = None,
    updated_at: Any = None,
) -> dict:
    dep = departure_at or (_NOW + timedelta(days=7))
    return {
        "id": uuid.UUID(id) if id else uuid.uuid4(),
        "trip_id": uuid.UUID(trip_id) if trip_id else uuid.uuid4(),
        "type": type,
        "carrier": carrier,
        "departure_airport_station": departure_airport_station,
        "departure_city": departure_city,
        "departure_at": dep,
        "arrival_airport_station": arrival_airport_station,
        "arrival_city": arrival_city,
        "arrival_at": arrival_at or (dep + timedelta(hours=10)),
        "confirmation_number": confirmation_number,
        "pnr": pnr,
        "seat": seat,
        "metadata": metadata or {},
        "created_at": created_at or _NOW,
        "updated_at": updated_at or _NOW,
    }


def _accommodation_row(
    *,
    id: Any = None,
    trip_id: Any = None,
    type: str = "hotel",
    name: str | None = "Shinjuku Granbell Hotel",
    address: str | None = "2-14-5 Kabukicho, Shinjuku",
    check_in: Any = None,
    check_out: Any = None,
    confirmation_number: str | None = "HOTEL9X2",
    metadata: dict | None = None,
    created_at: Any = None,
    updated_at: Any = None,
) -> dict:
    return {
        "id": uuid.UUID(id) if id else uuid.uuid4(),
        "trip_id": uuid.UUID(trip_id) if trip_id else uuid.uuid4(),
        "type": type,
        "name": name,
        "address": address,
        "check_in": check_in or (_NOW + timedelta(days=8)),
        "check_out": check_out or (_NOW + timedelta(days=15)),
        "confirmation_number": confirmation_number,
        "metadata": metadata or {},
        "created_at": created_at or _NOW,
        "updated_at": updated_at or _NOW,
    }


def _reservation_row(
    *,
    id: Any = None,
    trip_id: Any = None,
    type: str = "restaurant",
    provider: str | None = "Sukiyabashi Jiro",
    datetime_val: Any = None,
    confirmation_number: str | None = "RES001",
    metadata: dict | None = None,
    created_at: Any = None,
    updated_at: Any = None,
) -> dict:
    return {
        "id": uuid.UUID(id) if id else uuid.uuid4(),
        "trip_id": uuid.UUID(trip_id) if trip_id else uuid.uuid4(),
        "type": type,
        "provider": provider,
        "datetime": datetime_val or (_NOW + timedelta(days=10)),
        "confirmation_number": confirmation_number,
        "metadata": metadata or {},
        "created_at": created_at or _NOW,
        "updated_at": updated_at or _NOW,
    }


def _document_row(
    *,
    id: Any = None,
    trip_id: Any = None,
    type: str = "boarding_pass",
    blob_ref: str | None = "gs://travel-docs/bp-001.pdf",
    expiry_date: Any = None,
    metadata: dict | None = None,
    created_at: Any = None,
) -> dict:
    return {
        "id": uuid.UUID(id) if id else uuid.uuid4(),
        "trip_id": uuid.UUID(trip_id) if trip_id else uuid.uuid4(),
        "type": type,
        "blob_ref": blob_ref,
        "expiry_date": expiry_date,
        "metadata": metadata or {},
        "created_at": created_at or _NOW,
    }


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def _make_app(
    *,
    fetch_rows: list | None = None,
    fetchval_return: Any = 0,
    fetchrow_return: dict | None = None,
):
    """Build a FastAPI test app with a mocked travel DatabaseManager."""
    from fastapi import FastAPI

    mock_pool = AsyncMock()
    mock_pool.fetch = AsyncMock(return_value=fetch_rows or [])
    mock_pool.fetchval = AsyncMock(return_value=fetchval_return)
    mock_pool.fetchrow = AsyncMock(return_value=fetchrow_return)

    mock_db = MagicMock()
    mock_db.pool.return_value = mock_pool

    app = FastAPI()
    app.include_router(_travel_router_mod.router)
    app.dependency_overrides[_travel_router_mod._get_db_manager] = lambda: mock_db

    return app, mock_pool


def _make_app_multi_fetch(fetch_side_effects: list):
    """Build a FastAPI test app where fetch() returns different values on each call."""
    from fastapi import FastAPI

    mock_pool = AsyncMock()
    mock_pool.fetch = AsyncMock(side_effect=fetch_side_effects)
    mock_pool.fetchval = AsyncMock(return_value=1)
    mock_pool.fetchrow = AsyncMock(return_value=None)

    mock_db = MagicMock()
    mock_db.pool.return_value = mock_pool

    app = FastAPI()
    app.include_router(_travel_router_mod.router)
    app.dependency_overrides[_travel_router_mod._get_db_manager] = lambda: mock_db

    return app, mock_pool


# ---------------------------------------------------------------------------
# Tests: GET /api/travel/trips
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_trips_empty():
    """GET /api/travel/trips returns empty list when no data."""
    app, _ = _make_app(fetch_rows=[], fetchval_return=0)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/travel/trips")

    assert response.status_code == 200
    body = response.json()
    assert body["data"] == []
    assert body["meta"]["total"] == 0
    assert body["meta"]["offset"] == 0
    assert body["meta"]["limit"] == 20


@pytest.mark.asyncio
async def test_list_trips_with_results():
    """GET /api/travel/trips returns trip records."""
    rows = [_trip_row(name="Tokyo Trip"), _trip_row(name="Paris Trip", status="completed")]
    app, _ = _make_app(fetch_rows=rows, fetchval_return=2)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/travel/trips")

    assert response.status_code == 200
    body = response.json()
    assert body["meta"]["total"] == 2
    assert len(body["data"]) == 2

    item = body["data"][0]
    assert "id" in item
    assert "name" in item
    assert "destination" in item
    assert "start_date" in item
    assert "end_date" in item
    assert "status" in item
    assert "metadata" in item
    assert "created_at" in item
    assert "updated_at" in item


@pytest.mark.asyncio
async def test_list_trips_filter_by_status():
    """GET /api/travel/trips filters by status."""
    rows = [_trip_row(status="planned")]
    app, mock_pool = _make_app(fetch_rows=rows, fetchval_return=1)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/travel/trips?status=planned")

    assert response.status_code == 200
    call_args = mock_pool.fetchval.call_args[0][0]
    assert "status" in call_args


@pytest.mark.asyncio
async def test_list_trips_invalid_status():
    """GET /api/travel/trips returns 422 for invalid status value."""
    app, _ = _make_app()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/travel/trips?status=invalid")

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_list_trips_pagination():
    """GET /api/travel/trips respects offset/limit parameters."""
    app, mock_pool = _make_app(fetch_rows=[], fetchval_return=50)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/travel/trips?offset=10&limit=5")

    assert response.status_code == 200
    body = response.json()
    assert body["meta"]["total"] == 50
    assert body["meta"]["offset"] == 10
    assert body["meta"]["limit"] == 5


@pytest.mark.asyncio
async def test_list_trips_schema_prefix():
    """GET /api/travel/trips uses travel schema prefix in queries."""
    app, mock_pool = _make_app(fetch_rows=[], fetchval_return=0)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.get("/api/travel/trips")

    call_args = mock_pool.fetchval.call_args[0][0]
    assert "travel.trips" in call_args


@pytest.mark.asyncio
async def test_list_trips_filter_by_date_range():
    """GET /api/travel/trips filters by from_date and to_date."""
    app, mock_pool = _make_app(fetch_rows=[], fetchval_return=0)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/travel/trips?from_date=2026-03-01&to_date=2026-03-31")

    assert response.status_code == 200
    call_args = mock_pool.fetchval.call_args[0][0]
    assert "start_date" in call_args


# ---------------------------------------------------------------------------
# Tests: GET /api/travel/trips/{trip_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_trip_summary_not_found():
    """GET /api/travel/trips/{trip_id} returns 404 when trip doesn't exist."""
    app, _ = _make_app(fetchrow_return=None)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(f"/api/travel/trips/{_TRIP_UUID}")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_trip_summary_found():
    """GET /api/travel/trips/{trip_id} returns full trip summary."""
    trip = _trip_row(id=_TRIP_UUID)
    leg = _leg_row(trip_id=_TRIP_UUID)
    acc = _accommodation_row(trip_id=_TRIP_UUID)
    res = _reservation_row(trip_id=_TRIP_UUID)
    doc = _document_row(trip_id=_TRIP_UUID)

    from fastapi import FastAPI

    mock_pool = AsyncMock()
    mock_pool.fetchrow = AsyncMock(return_value=trip)
    # fetch is called: legs, accommodations, reservations, documents
    mock_pool.fetch = AsyncMock(side_effect=[[leg], [acc], [res], [doc]])

    mock_db = MagicMock()
    mock_db.pool.return_value = mock_pool

    app = FastAPI()
    app.include_router(_travel_router_mod.router)
    app.dependency_overrides[_travel_router_mod._get_db_manager] = lambda: mock_db

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(f"/api/travel/trips/{_TRIP_UUID}")

    assert response.status_code == 200
    body = response.json()

    assert "trip" in body
    assert "legs" in body
    assert "accommodations" in body
    assert "reservations" in body
    assert "documents" in body
    assert "timeline" in body
    assert "alerts" in body

    assert len(body["legs"]) == 1
    assert len(body["accommodations"]) == 1
    assert len(body["reservations"]) == 1
    assert len(body["documents"]) == 1

    assert body["trip"]["id"] == _TRIP_UUID


@pytest.mark.asyncio
async def test_get_trip_summary_trip_fields():
    """GET /api/travel/trips/{trip_id} returns all required trip fields."""
    trip = _trip_row(id=_TRIP_UUID, name="My Trip", destination="Paris", status="active")

    from fastapi import FastAPI

    mock_pool = AsyncMock()
    mock_pool.fetchrow = AsyncMock(return_value=trip)
    mock_pool.fetch = AsyncMock(side_effect=[[], [], [], []])

    mock_db = MagicMock()
    mock_db.pool.return_value = mock_pool

    app = FastAPI()
    app.include_router(_travel_router_mod.router)
    app.dependency_overrides[_travel_router_mod._get_db_manager] = lambda: mock_db

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(f"/api/travel/trips/{_TRIP_UUID}")

    assert response.status_code == 200
    body = response.json()

    trip_data = body["trip"]
    assert trip_data["name"] == "My Trip"
    assert trip_data["destination"] == "Paris"
    assert trip_data["status"] == "active"


@pytest.mark.asyncio
async def test_get_trip_summary_missing_boarding_pass_alert():
    """GET /api/travel/trips/{trip_id} generates alert when boarding pass is missing."""
    trip = _trip_row(id=_TRIP_UUID)
    # Flight leg but no boarding_pass document
    leg = _leg_row(trip_id=_TRIP_UUID, type="flight")

    from fastapi import FastAPI

    mock_pool = AsyncMock()
    mock_pool.fetchrow = AsyncMock(return_value=trip)
    mock_pool.fetch = AsyncMock(side_effect=[[leg], [], [], []])

    mock_db = MagicMock()
    mock_db.pool.return_value = mock_pool

    app = FastAPI()
    app.include_router(_travel_router_mod.router)
    app.dependency_overrides[_travel_router_mod._get_db_manager] = lambda: mock_db

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(f"/api/travel/trips/{_TRIP_UUID}")

    assert response.status_code == 200
    body = response.json()

    alerts = body["alerts"]
    assert len(alerts) >= 1
    alert_types = [a["type"] for a in alerts]
    assert "missing_boarding_pass" in alert_types


@pytest.mark.asyncio
async def test_get_trip_summary_no_alert_when_boarding_pass_present():
    """GET /api/travel/trips/{trip_id} no missing_boarding_pass alert when doc is present."""
    trip = _trip_row(id=_TRIP_UUID)
    leg = _leg_row(trip_id=_TRIP_UUID, type="flight", seat="12A")
    doc = _document_row(trip_id=_TRIP_UUID, type="boarding_pass")

    from fastapi import FastAPI

    mock_pool = AsyncMock()
    mock_pool.fetchrow = AsyncMock(return_value=trip)
    mock_pool.fetch = AsyncMock(side_effect=[[leg], [], [], [doc]])

    mock_db = MagicMock()
    mock_db.pool.return_value = mock_pool

    app = FastAPI()
    app.include_router(_travel_router_mod.router)
    app.dependency_overrides[_travel_router_mod._get_db_manager] = lambda: mock_db

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(f"/api/travel/trips/{_TRIP_UUID}")

    assert response.status_code == 200
    body = response.json()

    alert_types = [a["type"] for a in body["alerts"]]
    assert "missing_boarding_pass" not in alert_types


@pytest.mark.asyncio
async def test_get_trip_summary_timeline_built():
    """GET /api/travel/trips/{trip_id} builds a timeline with all entity types."""
    trip = _trip_row(id=_TRIP_UUID)
    leg = _leg_row(trip_id=_TRIP_UUID)
    acc = _accommodation_row(trip_id=_TRIP_UUID)
    res = _reservation_row(trip_id=_TRIP_UUID)

    from fastapi import FastAPI

    mock_pool = AsyncMock()
    mock_pool.fetchrow = AsyncMock(return_value=trip)
    mock_pool.fetch = AsyncMock(side_effect=[[leg], [acc], [res], []])

    mock_db = MagicMock()
    mock_db.pool.return_value = mock_pool

    app = FastAPI()
    app.include_router(_travel_router_mod.router)
    app.dependency_overrides[_travel_router_mod._get_db_manager] = lambda: mock_db

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(f"/api/travel/trips/{_TRIP_UUID}")

    assert response.status_code == 200
    body = response.json()

    timeline = body["timeline"]
    assert len(timeline) == 3
    entity_types = {e["entity_type"] for e in timeline}
    assert entity_types == {"leg", "accommodation", "reservation"}


# ---------------------------------------------------------------------------
# Tests: GET /api/travel/trips/{trip_id}/legs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_trip_legs_not_found():
    """GET /api/travel/trips/{trip_id}/legs returns 404 when trip doesn't exist."""
    app, mock_pool = _make_app(fetchval_return=None, fetch_rows=[])
    mock_pool.fetchval = AsyncMock(return_value=None)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(f"/api/travel/trips/{_TRIP_UUID}/legs")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_list_trip_legs_empty():
    """GET /api/travel/trips/{trip_id}/legs returns empty list when no legs."""
    app, mock_pool = _make_app(fetchval_return=1, fetch_rows=[])
    mock_pool.fetchval = AsyncMock(return_value=1)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(f"/api/travel/trips/{_TRIP_UUID}/legs")

    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_list_trip_legs_with_data():
    """GET /api/travel/trips/{trip_id}/legs returns leg records."""
    leg = _leg_row(trip_id=_TRIP_UUID)
    app, mock_pool = _make_app(fetchval_return=1, fetch_rows=[leg])
    mock_pool.fetchval = AsyncMock(return_value=1)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(f"/api/travel/trips/{_TRIP_UUID}/legs")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1

    item = body[0]
    assert "id" in item
    assert "trip_id" in item
    assert "type" in item
    assert "departure_at" in item
    assert "arrival_at" in item
    assert "carrier" in item
    assert "pnr" in item


# ---------------------------------------------------------------------------
# Tests: GET /api/travel/trips/{trip_id}/accommodations
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_trip_accommodations_not_found():
    """GET /api/travel/trips/{trip_id}/accommodations returns 404 when trip doesn't exist."""
    app, mock_pool = _make_app(fetchval_return=None, fetch_rows=[])
    mock_pool.fetchval = AsyncMock(return_value=None)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(f"/api/travel/trips/{_TRIP_UUID}/accommodations")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_list_trip_accommodations_with_data():
    """GET /api/travel/trips/{trip_id}/accommodations returns accommodation records."""
    acc = _accommodation_row(trip_id=_TRIP_UUID)
    app, mock_pool = _make_app(fetchval_return=1, fetch_rows=[acc])
    mock_pool.fetchval = AsyncMock(return_value=1)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(f"/api/travel/trips/{_TRIP_UUID}/accommodations")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1

    item = body[0]
    assert "id" in item
    assert "trip_id" in item
    assert "type" in item
    assert "name" in item
    assert "check_in" in item
    assert "check_out" in item
    assert "confirmation_number" in item


# ---------------------------------------------------------------------------
# Tests: GET /api/travel/trips/{trip_id}/reservations
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_trip_reservations_not_found():
    """GET /api/travel/trips/{trip_id}/reservations returns 404 when trip doesn't exist."""
    app, mock_pool = _make_app(fetchval_return=None, fetch_rows=[])
    mock_pool.fetchval = AsyncMock(return_value=None)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(f"/api/travel/trips/{_TRIP_UUID}/reservations")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_list_trip_reservations_with_data():
    """GET /api/travel/trips/{trip_id}/reservations returns reservation records."""
    res = _reservation_row(trip_id=_TRIP_UUID)
    app, mock_pool = _make_app(fetchval_return=1, fetch_rows=[res])
    mock_pool.fetchval = AsyncMock(return_value=1)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(f"/api/travel/trips/{_TRIP_UUID}/reservations")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1

    item = body[0]
    assert "id" in item
    assert "trip_id" in item
    assert "type" in item
    assert "provider" in item
    assert "datetime" in item
    assert "confirmation_number" in item


# ---------------------------------------------------------------------------
# Tests: GET /api/travel/trips/{trip_id}/documents
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_trip_documents_not_found():
    """GET /api/travel/trips/{trip_id}/documents returns 404 when trip doesn't exist."""
    app, mock_pool = _make_app(fetchval_return=None, fetch_rows=[])
    mock_pool.fetchval = AsyncMock(return_value=None)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(f"/api/travel/trips/{_TRIP_UUID}/documents")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_list_trip_documents_with_data():
    """GET /api/travel/trips/{trip_id}/documents returns document records."""
    doc = _document_row(trip_id=_TRIP_UUID, type="visa")
    app, mock_pool = _make_app(fetchval_return=1, fetch_rows=[doc])
    mock_pool.fetchval = AsyncMock(return_value=1)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(f"/api/travel/trips/{_TRIP_UUID}/documents")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1

    item = body[0]
    assert "id" in item
    assert "trip_id" in item
    assert "type" in item
    assert "blob_ref" in item
    assert "expiry_date" in item
    assert "created_at" in item


# ---------------------------------------------------------------------------
# Tests: GET /api/travel/upcoming
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_upcoming_travel_empty():
    """GET /api/travel/upcoming returns empty result when no upcoming trips."""
    app, _ = _make_app(fetch_rows=[])

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/travel/upcoming")

    assert response.status_code == 200
    body = response.json()
    assert body["upcoming_trips"] == []
    assert body["actions"] == []
    assert "window_start" in body
    assert "window_end" in body


@pytest.mark.asyncio
async def test_get_upcoming_travel_window_dates():
    """GET /api/travel/upcoming window_start and window_end are correct."""
    app, _ = _make_app(fetch_rows=[])

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/travel/upcoming?within_days=7")

    assert response.status_code == 200
    body = response.json()

    today = date.today()
    window_end = today + timedelta(days=7)
    assert body["window_start"] == today.isoformat()
    assert body["window_end"] == window_end.isoformat()


@pytest.mark.asyncio
async def test_get_upcoming_travel_with_trip():
    """GET /api/travel/upcoming returns trips with days_until_departure."""
    trip = _trip_row(id=_TRIP_UUID, start_date=_TODAY + timedelta(days=3))
    leg = _leg_row(trip_id=_TRIP_UUID)
    doc = _document_row(trip_id=_TRIP_UUID, type="boarding_pass")

    from fastapi import FastAPI

    mock_pool = AsyncMock()
    # First fetch: trips list
    # Then for the trip: legs, accommodations, documents (for pretrip actions)
    mock_pool.fetch = AsyncMock(side_effect=[[trip], [leg], [], [doc]])
    mock_pool.fetchval = AsyncMock(return_value=1)
    mock_pool.fetchrow = AsyncMock(return_value=None)

    mock_db = MagicMock()
    mock_db.pool.return_value = mock_pool

    app = FastAPI()
    app.include_router(_travel_router_mod.router)
    app.dependency_overrides[_travel_router_mod._get_db_manager] = lambda: mock_db

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/travel/upcoming")

    assert response.status_code == 200
    body = response.json()

    assert len(body["upcoming_trips"]) == 1
    upcoming = body["upcoming_trips"][0]
    assert "trip" in upcoming
    assert "legs" in upcoming
    assert "accommodations" in upcoming
    assert "days_until_departure" in upcoming
    assert upcoming["days_until_departure"] == 3


@pytest.mark.asyncio
async def test_get_upcoming_travel_actions_with_missing_boarding_pass():
    """GET /api/travel/upcoming surfaces missing boarding pass action."""
    trip = _trip_row(id=_TRIP_UUID)
    leg = _leg_row(trip_id=_TRIP_UUID, type="flight")
    # No boarding pass document

    from fastapi import FastAPI

    mock_pool = AsyncMock()
    # trips, legs, accommodations, documents (empty = no boarding pass)
    mock_pool.fetch = AsyncMock(side_effect=[[trip], [leg], [], []])
    mock_pool.fetchval = AsyncMock(return_value=1)
    mock_pool.fetchrow = AsyncMock(return_value=None)

    mock_db = MagicMock()
    mock_db.pool.return_value = mock_pool

    app = FastAPI()
    app.include_router(_travel_router_mod.router)
    app.dependency_overrides[_travel_router_mod._get_db_manager] = lambda: mock_db

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/travel/upcoming")

    assert response.status_code == 200
    body = response.json()

    actions = body["actions"]
    assert len(actions) >= 1

    action = actions[0]
    assert "trip_id" in action
    assert "trip_name" in action
    assert "type" in action
    assert "message" in action
    assert "severity" in action
    assert "urgency_rank" in action

    action_types = [a["type"] for a in actions]
    assert "missing_boarding_pass" in action_types


@pytest.mark.asyncio
async def test_get_upcoming_travel_urgency_ranking():
    """GET /api/travel/upcoming urgency ranking: high before medium before low."""
    from fastapi import FastAPI

    trip_id1 = str(uuid.uuid4())
    trip_id2 = str(uuid.uuid4())

    trip1 = _trip_row(id=trip_id1, name="Trip 1")
    leg1 = _leg_row(trip_id=trip_id1, type="flight", seat=None)  # no seat → low severity
    trip2 = _trip_row(id=trip_id2, name="Trip 2")
    leg2 = _leg_row(trip_id=trip_id2, type="flight")  # flight → missing bp → high severity

    mock_pool = AsyncMock()
    # trips, then trip1: legs, accommodations, docs; trip2: legs, accommodations, docs
    mock_pool.fetch = AsyncMock(
        side_effect=[
            [trip1, trip2],  # trips query
            [leg1],
            [],
            [],  # trip1: legs, acc, docs (no boarding pass)
            [leg2],
            [],
            [],  # trip2: legs, acc, docs (no boarding pass)
        ]
    )
    mock_pool.fetchval = AsyncMock(return_value=1)
    mock_pool.fetchrow = AsyncMock(return_value=None)

    mock_db = MagicMock()
    mock_db.pool.return_value = mock_pool

    app = FastAPI()
    app.include_router(_travel_router_mod.router)
    app.dependency_overrides[_travel_router_mod._get_db_manager] = lambda: mock_db

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/travel/upcoming")

    assert response.status_code == 200
    body = response.json()

    actions = body["actions"]
    assert len(actions) >= 1

    # Check that urgency_rank is assigned sequentially
    ranks = [a["urgency_rank"] for a in actions]
    assert ranks == list(range(1, len(ranks) + 1))

    # High severity actions should come before low severity
    severity_order = [a["severity"] for a in actions]
    _severity_rank = {"high": 1, "medium": 2, "low": 3}
    for i in range(len(severity_order) - 1):
        assert _severity_rank[severity_order[i]] <= _severity_rank[severity_order[i + 1]]


# ---------------------------------------------------------------------------
# Tests: router auto-discovery
# ---------------------------------------------------------------------------


def test_router_exports_router_variable():
    """The travel router module exports a module-level 'router' variable."""
    from fastapi import APIRouter

    assert hasattr(_travel_router_mod, "router")
    assert isinstance(_travel_router_mod.router, APIRouter)


def test_router_prefix():
    """The travel router uses /api/travel prefix."""
    assert _travel_router_mod.router.prefix == "/api/travel"


def test_router_has_all_endpoints():
    """The travel router defines all 7 required endpoints."""
    routes = {route.path for route in _travel_router_mod.router.routes}  # type: ignore[union-attr]
    assert "/api/travel/trips" in routes
    assert "/api/travel/trips/{trip_id}" in routes
    assert "/api/travel/trips/{trip_id}/legs" in routes
    assert "/api/travel/trips/{trip_id}/accommodations" in routes
    assert "/api/travel/trips/{trip_id}/reservations" in routes
    assert "/api/travel/trips/{trip_id}/documents" in routes
    assert "/api/travel/upcoming" in routes


def test_butler_db_constant():
    """The travel router uses 'travel' as BUTLER_DB."""
    assert _travel_router_mod.BUTLER_DB == "travel"


def test_router_discovery_finds_travel():
    """Auto-discovery finds the travel butler router."""
    from butlers.api.router_discovery import discover_butler_routers

    roster_dir = Path(__file__).parents[2]  # roster/
    routers = discover_butler_routers(roster_dir=roster_dir)
    butler_names = [name for name, _ in routers]
    assert "travel" in butler_names
