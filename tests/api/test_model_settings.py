"""Tests for model catalog and butler model override endpoints.

Condensed from test_model_settings.py (53) + test_model_settings_discretion_tier.py (8)
+ test_model_settings_self_healing_tier.py (9) → ~12 tests (bu-egmz6) → 3 tests (bu-2yw2d).
Keeps: list/503 fallback, create 201 + conflict 409 + invalid-tier 422 (parametrized),
       resolve-model 200.
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
    session_timeout_s=1800,
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
        "session_timeout_s": session_timeout_s,
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
    fetchrow_side_effect=None,
):
    mock_pool = AsyncMock()
    mock_pool.fetch = AsyncMock(return_value=[_mock_record(r) for r in (fetch_rows or [])])
    if fetchrow_side_effect is not None:
        mock_pool.fetchrow = AsyncMock(side_effect=fetchrow_side_effect)
    else:
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
# Catalog list + 503 fallback
# ---------------------------------------------------------------------------


async def test_catalog_list_and_503(app):
    rows = [
        _make_catalog_row(alias="claude-haiku", complexity_tier="trivial"),
        _make_catalog_row(alias="claude-sonnet", complexity_tier="medium"),
    ]
    # Happy path
    _app_with_pool(app, fetch_rows=rows)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/settings/models")
    assert resp.status_code == 200
    assert len(resp.json()["data"]) == 2

    # 503 when pool unavailable
    _app_with_pool(app, pool_raises=KeyError("No shared pool"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp_503 = await client.get("/api/settings/models")
    assert resp_503.status_code == 503


# ---------------------------------------------------------------------------
# Catalog CRUD error paths (parametrized)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload,fetchrow_side_effect,execute_result,expected",
    [
        # Create 201
        (
            {
                "alias": "new-model",
                "runtime_type": "codex",
                "model_id": "gpt-5",
                "complexity_tier": "medium",
                "enabled": True,
                "priority": 0,
            },
            None,
            "INSERT 1",
            201,
        ),
        # Create 409 duplicate alias
        (
            {
                "alias": "claude-sonnet",
                "runtime_type": "claude",
                "model_id": "claude-sonnet-4-6",
                "complexity_tier": "medium",
            },
            asyncpg.UniqueViolationError("uq_model_catalog_alias"),
            "INSERT 1",
            409,
        ),
        # Create 422 invalid complexity tier
        (
            {
                "alias": "x",
                "runtime_type": "claude",
                "model_id": "y",
                "complexity_tier": "invalid_tier",
            },
            None,
            "INSERT 1",
            422,
        ),
    ],
    ids=["create-201", "create-409-duplicate", "create-422-bad-tier"],
)
async def test_catalog_create_error_paths(
    app, payload, fetchrow_side_effect, execute_result, expected
):
    created_row = _make_catalog_row(alias=payload.get("alias", "x"))
    _app_with_pool(
        app,
        fetchrow_side_effect=fetchrow_side_effect,
        fetchrow_result=created_row if fetchrow_side_effect is None else None,
        execute_result=execute_result,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/settings/models", json=payload)
    assert resp.status_code == expected


# ---------------------------------------------------------------------------
# Resolve-model preview
# ---------------------------------------------------------------------------


async def test_resolve_model_preview_200_and_422_for_invalid(app):
    catalog_row = _make_catalog_row(complexity_tier="medium")
    app2, mock_pool = _app_with_pool(app)
    mock_pool.fetchrow = AsyncMock(side_effect=[_mock_record(catalog_row), None])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        r200 = await client.get("/api/butlers/general/resolve-model?complexity=medium")
        r422 = await client.get("/api/butlers/general/resolve-model?complexity=invalid")
    assert r200.status_code == 200
    assert r422.status_code == 422
