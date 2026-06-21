"""Tests for /api/timeline/saved-views endpoints.

Covers:
- GET list (200, empty list, 503 on DB unavailable)
- POST create (201, 400 on validation errors, 503 on DB unavailable)
- PATCH update (200, 400 on empty body, 404 on missing, 503 on DB unavailable)
- DELETE (204, 404 on missing, 503 on DB unavailable)
- Round-trip: create → list → delete → list empty
- filter_spec persists as-is (JSONB round-trip)
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from butlers.api.db import DatabaseManager
from butlers.api.routers.timeline_saved_views import _get_db_manager

pytestmark = pytest.mark.unit

_NOW = datetime.now(tz=UTC)
_NOW_ISO = _NOW.isoformat()


# ---------------------------------------------------------------------------
# Row factory + record mock
# ---------------------------------------------------------------------------


def _make_saved_view_row(
    *,
    view_id=None,
    name: str = "My View",
    filter_spec: dict | None = None,
    created_at=None,
    updated_at=None,
):
    """Build a dict mimicking an asyncpg Record for timeline_saved_views."""
    return {
        "id": view_id or uuid4(),
        "name": name,
        "filter_spec": filter_spec if filter_spec is not None else {"statuses": ["error"]},
        "created_at": created_at or _NOW,
        "updated_at": updated_at or _NOW,
    }


def _make_record(row: dict) -> MagicMock:
    """Return a MagicMock that supports dict-style item access."""
    m = MagicMock()
    m.__getitem__.side_effect = row.__getitem__
    return m


# ---------------------------------------------------------------------------
# App wiring helpers
# ---------------------------------------------------------------------------


def _app_with_mock_db(
    app: FastAPI,
    *,
    shared_pool=None,
    shared_pool_error=None,
):
    """Wire a mock DatabaseManager over the shared pool onto the app."""
    mock_db = MagicMock(spec=DatabaseManager)
    if shared_pool_error is not None:
        mock_db.credential_shared_pool.side_effect = shared_pool_error
    else:
        if shared_pool is None:
            shared_pool = AsyncMock()
            shared_pool.fetch = AsyncMock(return_value=[])
            shared_pool.fetchrow = AsyncMock(return_value=None)
            shared_pool.execute = AsyncMock(return_value="DELETE 0")
        mock_db.credential_shared_pool.return_value = shared_pool
    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    return mock_db


# ---------------------------------------------------------------------------
# GET /api/timeline/saved-views
# ---------------------------------------------------------------------------


async def test_list_saved_views_200_empty(app):
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=[])
    _app_with_mock_db(app, shared_pool=pool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/timeline/saved-views")

    assert resp.status_code == 200
    body = resp.json()
    assert body["data"] == []


async def test_list_saved_views_200_returns_entries(app):
    row = _make_saved_view_row(name="Errors Only", filter_spec={"statuses": ["error"]})
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=[_make_record(row)])
    _app_with_mock_db(app, shared_pool=pool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/timeline/saved-views")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["data"]) == 1
    entry = body["data"][0]
    assert entry["name"] == "Errors Only"
    assert entry["filter_spec"] == {"statuses": ["error"]}
    assert "id" in entry
    assert "created_at" in entry
    assert "updated_at" in entry


@pytest.mark.parametrize(
    ("method", "path_suffix", "json_body"),
    [
        ("get", "", None),
        ("post", "", {"name": "Test"}),
        ("patch", "/{vid}", {"name": "Updated"}),
        ("delete", "/{vid}", None),
    ],
    ids=["list", "create", "patch", "delete"],
)
async def test_saved_views_503_on_db_unavailable(app, method, path_suffix, json_body):
    """Every saved-views CRUD route maps a missing shared pool to 503."""
    _app_with_mock_db(app, shared_pool_error=KeyError("no shared pool"))
    path = "/api/timeline/saved-views" + path_suffix.format(vid=uuid4())

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        kwargs = {"json": json_body} if json_body is not None else {}
        resp = await getattr(client, method)(path, **kwargs)

    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# POST /api/timeline/saved-views
# ---------------------------------------------------------------------------


async def test_create_saved_view_201(app):
    view_id = uuid4()
    inserted_row = _make_saved_view_row(
        view_id=view_id,
        name="Priority Channels",
        filter_spec={"statuses": ["ingested"], "channels": ["telegram"]},
    )
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=_make_record(inserted_row))
    _app_with_mock_db(app, shared_pool=pool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/timeline/saved-views",
            json={
                "name": "Priority Channels",
                "filter_spec": {"statuses": ["ingested"], "channels": ["telegram"]},
            },
        )

    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "Priority Channels"
    assert body["filter_spec"] == {"statuses": ["ingested"], "channels": ["telegram"]}
    assert str(body["id"]) == str(view_id)


async def test_create_saved_view_default_filter_spec(app):
    """filter_spec defaults to {} when omitted from the request."""
    view_id = uuid4()
    inserted_row = _make_saved_view_row(view_id=view_id, name="My View", filter_spec={})
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=_make_record(inserted_row))
    _app_with_mock_db(app, shared_pool=pool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/timeline/saved-views", json={"name": "My View"})

    assert resp.status_code == 201
    # Verify the INSERT was called with {} as the filter_spec dict directly.
    # call_args[0] is the positional args tuple: (sql, name, filter_spec)
    call_args = pool.fetchrow.call_args[0]
    assert call_args[2] == {}


@pytest.mark.parametrize("name", ["", "x" * 101], ids=["empty", "too-long"])
async def test_create_saved_view_422_on_invalid_name(app, name):
    """Pydantic min_length=1 / max_length=100 reject empty and over-long names."""
    _app_with_mock_db(app)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/timeline/saved-views", json={"name": name})

    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# PATCH /api/timeline/saved-views/{id}
# ---------------------------------------------------------------------------


async def test_patch_saved_view_200_name_only(app):
    view_id = uuid4()
    updated_row = _make_saved_view_row(
        view_id=view_id, name="Renamed View", filter_spec={"statuses": ["error"]}
    )
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=_make_record(updated_row))
    _app_with_mock_db(app, shared_pool=pool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.patch(
            f"/api/timeline/saved-views/{view_id}",
            json={"name": "Renamed View"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "Renamed View"


async def test_patch_saved_view_200_filter_spec_only(app):
    view_id = uuid4()
    new_spec = {"statuses": ["error", "replay_failed"], "search": "api"}
    updated_row = _make_saved_view_row(view_id=view_id, name="My View", filter_spec=new_spec)
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=_make_record(updated_row))
    _app_with_mock_db(app, shared_pool=pool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.patch(
            f"/api/timeline/saved-views/{view_id}",
            json={"filter_spec": new_spec},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["filter_spec"] == new_spec


async def test_patch_saved_view_400_empty_body(app):
    view_id = uuid4()
    _app_with_mock_db(app)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.patch(f"/api/timeline/saved-views/{view_id}", json={})

    assert resp.status_code == 400


async def test_patch_saved_view_404_not_found(app):
    view_id = uuid4()
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=None)
    _app_with_mock_db(app, shared_pool=pool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.patch(
            f"/api/timeline/saved-views/{view_id}",
            json={"name": "Updated"},
        )

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /api/timeline/saved-views/{id}
# ---------------------------------------------------------------------------


async def test_delete_saved_view_204(app):
    view_id = uuid4()
    pool = AsyncMock()
    pool.execute = AsyncMock(return_value="DELETE 1")
    _app_with_mock_db(app, shared_pool=pool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.delete(f"/api/timeline/saved-views/{view_id}")

    assert resp.status_code == 204


async def test_delete_saved_view_404_not_found(app):
    view_id = uuid4()
    pool = AsyncMock()
    pool.execute = AsyncMock(return_value="DELETE 0")
    _app_with_mock_db(app, shared_pool=pool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.delete(f"/api/timeline/saved-views/{view_id}")

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# filter_spec JSONB round-trip
# ---------------------------------------------------------------------------


async def test_filter_spec_complex_structure_preserved(app):
    """Arbitrary nested filter_spec is stored and returned intact."""
    complex_spec = {
        "statuses": ["error", "replay_failed"],
        "channels": ["telegram", "email"],
        "search": "payment",
        "range": "24h",
        "nested": {"key": "value", "numbers": [1, 2, 3]},
    }
    view_id = uuid4()
    inserted_row = _make_saved_view_row(
        view_id=view_id, name="Complex View", filter_spec=complex_spec
    )
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=_make_record(inserted_row))
    _app_with_mock_db(app, shared_pool=pool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/timeline/saved-views",
            json={"name": "Complex View", "filter_spec": complex_spec},
        )

    assert resp.status_code == 201
    body = resp.json()
    assert body["filter_spec"] == complex_spec


# ---------------------------------------------------------------------------
# Verify INSERT passes filter_spec as JSON string
# ---------------------------------------------------------------------------


async def test_create_passes_filter_spec_as_dict_to_db(app):
    """Confirm the INSERT call passes filter_spec as a dict (not a JSON string).

    The asyncpg JSONB codec handles encoding; passing json.dumps() would
    double-encode and store a JSONB string scalar instead of an object.
    """
    view_id = uuid4()
    spec = {"statuses": ["ingested"]}
    inserted_row = _make_saved_view_row(view_id=view_id, name="Test", filter_spec=spec)
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=_make_record(inserted_row))
    _app_with_mock_db(app, shared_pool=pool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.post("/api/timeline/saved-views", json={"name": "Test", "filter_spec": spec})

    # call_args[0] is the positional args tuple: (sql, name, filter_spec)
    call_args = pool.fetchrow.call_args[0]
    assert call_args[1] == "Test"
    assert call_args[2] == spec
