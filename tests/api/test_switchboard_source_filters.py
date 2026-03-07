"""Tests for switchboard source-filter API endpoints.

Covers:
- GET    /api/switchboard/source-filters
- POST   /api/switchboard/source-filters
- GET    /api/switchboard/source-filters/{filter_id}
- PATCH  /api/switchboard/source-filters/{filter_id}
- DELETE /api/switchboard/source-filters/{filter_id}
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import httpx
import pytest

from butlers.api.db import DatabaseManager

_roster_root = Path(__file__).resolve().parents[2] / "roster"
_router_path = _roster_root / "switchboard" / "api" / "router.py"
_MODULE_NAME = "switchboard_api_router"

if _MODULE_NAME in sys.modules:
    switchboard_module = sys.modules[_MODULE_NAME]
else:
    import importlib.util

    spec = importlib.util.spec_from_file_location(_MODULE_NAME, _router_path)
    if spec is None or spec.loader is None:
        raise ValueError(f"Could not load spec from {_router_path}")
    switchboard_module = importlib.util.module_from_spec(spec)
    sys.modules[_MODULE_NAME] = switchboard_module
    spec.loader.exec_module(switchboard_module)

pytestmark = pytest.mark.unit

_FILTER_ID = "11111111-1111-1111-1111-111111111111"

_FILTER_ROW = {
    "id": _FILTER_ID,
    "name": "Important senders",
    "description": "Only allow key sources",
    "filter_mode": "whitelist",
    "source_key_type": "sender_address",
    "patterns": ["ceo@example.com", "alerts@bank.com"],
    "created_at": "2026-03-07T00:00:00+00:00",
    "updated_at": "2026-03-07T00:00:00+00:00",
}



def _current_get_db_manager():
    return sys.modules[_MODULE_NAME]._get_db_manager



def _app_with_mock_db(
    app,
    *,
    fetch_rows: list | None = None,
    fetchrow_result=None,
    fetchrow_side_effect=None,
    execute_result: str = "DELETE 1",
    pool_available: bool = True,
):
    mock_pool = AsyncMock()
    mock_pool.fetch = AsyncMock(return_value=fetch_rows or [])
    if fetchrow_side_effect is not None:
        mock_pool.fetchrow = AsyncMock(side_effect=fetchrow_side_effect)
    else:
        mock_pool.fetchrow = AsyncMock(return_value=fetchrow_result)
    mock_pool.execute = AsyncMock(return_value=execute_result)

    mock_db = MagicMock(spec=DatabaseManager)
    if pool_available:
        mock_db.pool.return_value = mock_pool
    else:
        mock_db.pool.side_effect = KeyError("No pool for butler: switchboard")

    app.dependency_overrides[_current_get_db_manager()] = lambda: mock_db
    return app, mock_pool


class TestListSourceFilters:
    async def test_returns_api_response_envelope(self, app):
        app, _ = _app_with_mock_db(app, fetch_rows=[_FILTER_ROW])

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/switchboard/source-filters")

        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body
        assert len(body["data"]) == 1
        assert body["data"][0]["id"] == _FILTER_ID


class TestCreateSourceFilter:
    async def test_create_returns_201(self, app):
        app, _ = _app_with_mock_db(app, fetchrow_result=_FILTER_ROW)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/switchboard/source-filters",
                json={
                    "name": "Important senders",
                    "description": "Only allow key sources",
                    "filter_mode": "whitelist",
                    "source_key_type": "sender_address",
                    "patterns": ["ceo@example.com"],
                },
            )

        assert resp.status_code == 201
        body = resp.json()
        assert body["data"]["name"] == "Important senders"
        assert body["data"]["filter_mode"] == "whitelist"

    async def test_create_duplicate_name_returns_409(self, app):
        app, _ = _app_with_mock_db(
            app,
            fetchrow_side_effect=asyncpg.UniqueViolationError("duplicate"),
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/switchboard/source-filters",
                json={
                    "name": "Important senders",
                    "filter_mode": "whitelist",
                    "source_key_type": "sender_address",
                    "patterns": ["ceo@example.com"],
                },
            )

        assert resp.status_code == 409

    async def test_create_empty_patterns_returns_422(self, app):
        app, _ = _app_with_mock_db(app)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/switchboard/source-filters",
                json={
                    "name": "Important senders",
                    "filter_mode": "whitelist",
                    "source_key_type": "sender_address",
                    "patterns": [],
                },
            )

        assert resp.status_code == 422


class TestGetSourceFilter:
    async def test_get_returns_filter(self, app):
        app, _ = _app_with_mock_db(app, fetchrow_result=_FILTER_ROW)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/switchboard/source-filters/{_FILTER_ID}")

        assert resp.status_code == 200
        assert resp.json()["data"]["id"] == _FILTER_ID

    async def test_get_unknown_filter_returns_404(self, app):
        app, _ = _app_with_mock_db(app, fetchrow_result=None)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/switchboard/source-filters/{_FILTER_ID}")

        assert resp.status_code == 404


class TestPatchSourceFilter:
    async def test_patch_updates_allowed_fields(self, app):
        updated_row = {
            **_FILTER_ROW,
            "name": "Updated filter",
            "description": "Updated description",
            "patterns": ["new@example.com"],
            "updated_at": "2026-03-07T00:01:00+00:00",
        }
        app, mock_pool = _app_with_mock_db(
            app,
            fetchrow_side_effect=[_FILTER_ROW, updated_row],
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.patch(
                f"/api/switchboard/source-filters/{_FILTER_ID}",
                json={
                    "name": "Updated filter",
                    "description": "Updated description",
                    "patterns": ["new@example.com"],
                },
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["name"] == "Updated filter"
        assert body["data"]["description"] == "Updated description"
        assert body["data"]["patterns"] == ["new@example.com"]

        update_sql = mock_pool.fetchrow.call_args_list[1][0][0]
        assert "updated_at" in update_sql

    async def test_patch_unknown_filter_returns_404(self, app):
        app, _ = _app_with_mock_db(app, fetchrow_result=None)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.patch(
                f"/api/switchboard/source-filters/{_FILTER_ID}",
                json={"name": "Updated filter"},
            )

        assert resp.status_code == 404

    async def test_patch_duplicate_name_returns_409(self, app):
        app, _ = _app_with_mock_db(
            app,
            fetchrow_side_effect=[
                _FILTER_ROW,
                asyncpg.UniqueViolationError("duplicate"),
            ],
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.patch(
                f"/api/switchboard/source-filters/{_FILTER_ID}",
                json={"name": "Name taken"},
            )

        assert resp.status_code == 409

    async def test_patch_rejects_immutable_filter_mode(self, app):
        app, mock_pool = _app_with_mock_db(app)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.patch(
                f"/api/switchboard/source-filters/{_FILTER_ID}",
                json={"filter_mode": "blacklist"},
            )

        assert resp.status_code == 422
        mock_pool.fetchrow.assert_not_called()

    async def test_patch_rejects_immutable_source_key_type(self, app):
        app, mock_pool = _app_with_mock_db(app)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.patch(
                f"/api/switchboard/source-filters/{_FILTER_ID}",
                json={"source_key_type": "chat_id"},
            )

        assert resp.status_code == 422
        mock_pool.fetchrow.assert_not_called()


class TestDeleteSourceFilter:
    async def test_delete_returns_deleted_id(self, app):
        app, _ = _app_with_mock_db(app, execute_result="DELETE 1")

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.delete(f"/api/switchboard/source-filters/{_FILTER_ID}")

        assert resp.status_code == 200
        assert resp.json()["data"]["id"] == _FILTER_ID

    async def test_delete_unknown_filter_returns_404(self, app):
        app, _ = _app_with_mock_db(app, execute_result="DELETE 0")

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.delete(f"/api/switchboard/source-filters/{_FILTER_ID}")

        assert resp.status_code == 404
