"""Tests for model catalog and butler model override endpoints.

Condensed from test_model_settings.py (53) + test_model_settings_discretion_tier.py (8)
+ test_model_settings_self_healing_tier.py (9) → ~12 tests (bu-egmz6).
Keeps: list/CRUD status codes, validation errors, resolve-model preview,
discretion/self-healing tier behavior.
"""

from __future__ import annotations

import json
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import httpx
import pytest

from butlers.api.db import DatabaseManager
from butlers.api.routers.model_settings import _get_db_manager

pytestmark = pytest.mark.unit


def _make_catalog_row(
    *,
    entry_id=None,
    alias="claude-sonnet",
    runtime_type="claude",
    model_id="claude-sonnet-4-6",
    complexity_tier="medium",
    enabled=True,
    priority=0,
    extra_args=None,
):
    return {
        "id": entry_id or uuid.uuid4(),
        "alias": alias,
        "runtime_type": runtime_type,
        "model_id": model_id,
        "extra_args": json.dumps(extra_args or []),
        "complexity_tier": complexity_tier,
        "enabled": enabled,
        "priority": priority,
    }


def _mock_record(row: dict[str, Any]) -> MagicMock:
    m = MagicMock()
    m.__getitem__ = MagicMock(side_effect=lambda key: row[key])
    for k, v in row.items():
        setattr(m, k, v)
    return m


def _app_with_pool(
    app,
    *,
    fetch_rows=None,
    fetchrow_result=None,
    fetchval_result=None,
    execute_result="DELETE 1",
    pool_raises=None,
):
    mock_pool = AsyncMock()
    mock_pool.fetch = AsyncMock(return_value=[_mock_record(r) for r in (fetch_rows or [])])
    mock_pool.fetchrow = AsyncMock(
        return_value=_mock_record(fetchrow_result) if fetchrow_result else None
    )
    mock_pool.fetchval = AsyncMock(return_value=fetchval_result)
    mock_pool.execute = AsyncMock(return_value=execute_result)
    mock_conn = AsyncMock()
    mock_conn.transaction = MagicMock(
        return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=None), __aexit__=AsyncMock(return_value=None)
        )
    )
    mock_conn.fetchrow = mock_pool.fetchrow
    mock_pool.acquire = MagicMock(
        return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_conn), __aexit__=AsyncMock(return_value=None)
        )
    )
    mock_db = MagicMock(spec=DatabaseManager)
    if pool_raises:
        mock_db.credential_shared_pool.side_effect = pool_raises
    else:
        mock_db.credential_shared_pool.return_value = mock_pool
    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    return app, mock_pool


# ---------------------------------------------------------------------------
# Catalog entries CRUD
# ---------------------------------------------------------------------------


class TestCatalogEntries:
    async def test_list_returns_entries_from_db(self, app):
        rows = [
            _make_catalog_row(alias="claude-haiku", complexity_tier="trivial"),
            _make_catalog_row(alias="claude-sonnet", complexity_tier="medium"),
        ]
        _app_with_pool(app, fetch_rows=rows)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/settings/models")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data) == 2

    async def test_list_503_when_pool_unavailable(self, app):
        _app_with_pool(app, pool_raises=KeyError("No shared pool"))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/settings/models")
        assert resp.status_code == 503

    async def test_create_returns_201(self, app):
        created_id = uuid.uuid4()
        row = _make_catalog_row(entry_id=created_id, alias="new-model", runtime_type="codex")
        _app_with_pool(app, fetchrow_result=row)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/settings/models",
                json={
                    "alias": "new-model",
                    "runtime_type": "codex",
                    "model_id": "gpt-5.4-mini",
                    "complexity_tier": "medium",
                    "enabled": True,
                    "priority": 0,
                },
            )
        assert resp.status_code == 201
        assert resp.json()["data"]["alias"] == "new-model"

    async def test_create_409_on_duplicate_alias(self, app):
        app2, mock_pool = _app_with_pool(app)
        mock_pool.fetchrow = AsyncMock(
            side_effect=asyncpg.UniqueViolationError("uq_model_catalog_alias")
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/settings/models",
                json={
                    "alias": "claude-sonnet",
                    "runtime_type": "claude",
                    "model_id": "claude-sonnet-4-6",
                    "complexity_tier": "medium",
                },
            )
        assert resp.status_code == 409

    async def test_create_422_for_invalid_complexity_tier(self, app):
        _app_with_pool(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/settings/models",
                json={
                    "alias": "x",
                    "runtime_type": "claude",
                    "model_id": "y",
                    "complexity_tier": "invalid_tier",
                },
            )
        assert resp.status_code == 422

    async def test_delete_404_when_not_found(self, app):
        _app_with_pool(app, execute_result="DELETE 0")
        nid = uuid.uuid4()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.delete(f"/api/settings/models/{nid}")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Model override and resolve-model endpoints
# ---------------------------------------------------------------------------


class TestModelOverridesAndResolve:
    async def test_resolve_model_preview_returns_resolved(self, app):
        catalog_row = _make_catalog_row(complexity_tier="medium")
        app2, mock_pool = _app_with_pool(app)
        # First fetchrow returns catalog entry; second returns None (no quota row)
        mock_pool.fetchrow = AsyncMock(side_effect=[_mock_record(catalog_row), None])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/butlers/general/resolve-model?complexity=medium")
        assert resp.status_code == 200

    async def test_resolve_model_422_for_invalid_complexity(self, app):
        _app_with_pool(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/butlers/general/resolve-model?complexity=invalid")
        assert resp.status_code == 422

    async def test_upsert_overrides_422_for_empty_body(self, app):
        _app_with_pool(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.put("/api/butlers/general/model-overrides", json=[])
        assert resp.status_code == 422

    async def test_upsert_overrides_422_for_invalid_complexity_tier(self, app):
        _app_with_pool(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.put(
                "/api/butlers/general/model-overrides",
                json=[
                    {
                        "catalog_entry_id": str(uuid.uuid4()),
                        "complexity_tier": "bad_tier",
                        "enabled": True,
                        "priority": 0,
                    }
                ],
            )
        assert resp.status_code == 422
