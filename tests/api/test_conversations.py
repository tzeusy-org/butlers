"""Tests for dashboard conversation API endpoints.

Condensed from 44 tests to ~8 tests (bu-egmz6) → 3 tests (bu-2yw2d).
Keeps: list 200 + 503 combined, 422/404/400 error paths (parametrized), summary 200.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from butlers.api.db import DatabaseManager
from butlers.api.routers.conversations import _get_db_manager

pytestmark = pytest.mark.unit

_NOW = datetime.now(tz=UTC)
_CONV_ID = uuid4()
_BUTLER = "atlas"


def _make_conversation_row(**kw):
    defaults = {
        "id": _CONV_ID,
        "butler_name": _BUTLER,
        "title": "Hello world",
        "status": "active",
        "created_at": _NOW,
        "updated_at": _NOW,
        "message_count": 2,
        "total_input_tokens": 100,
        "total_output_tokens": 200,
        "total_duration_ms": 1500,
    }
    defaults.update(kw)
    return defaults


def _app_with_mock_db(
    app: FastAPI,
    *,
    fetch_rows=None,
    fetchval_result=0,
    fetchrow_result=None,
    execute_result=None,
    db_raises=None,
):
    mock_pool = AsyncMock()
    mock_pool.fetch = AsyncMock(return_value=fetch_rows or [])
    mock_pool.fetchval = AsyncMock(return_value=fetchval_result)
    mock_pool.fetchrow = AsyncMock(return_value=fetchrow_result)
    mock_pool.execute = AsyncMock(return_value=execute_result)

    mock_db = MagicMock(spec=DatabaseManager)
    if db_raises:
        mock_db.credential_shared_pool.side_effect = db_raises
    else:
        mock_db.credential_shared_pool.return_value = mock_pool

    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    return app


# ---------------------------------------------------------------------------
# List conversations — 200 structure + 503 fallback
# ---------------------------------------------------------------------------


async def test_list_conversations_200_and_503(app):
    row = _make_conversation_row()
    _app_with_mock_db(app, fetch_rows=[row], fetchval_result=1)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/api/butlers/{_BUTLER}/conversations")
    assert resp.status_code == 200
    body = resp.json()
    assert "data" in body and "meta" in body
    assert body["data"][0]["title"] == "Hello world"

    # 503 when db unavailable
    _app_with_mock_db(app, db_raises=RuntimeError("no shared pool"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp_503 = await client.get(f"/api/butlers/{_BUTLER}/conversations")
    assert resp_503.status_code == 503


# ---------------------------------------------------------------------------
# Error paths (parametrized)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path,method,body,expected",
    [
        (f"/api/butlers/{_BUTLER}/conversations?status=invalid", "GET", None, 422),
        (f"/api/butlers/{_BUTLER}/conversations/{_CONV_ID}", "PATCH", {"title": "X"}, 404),
        (f"/api/butlers/{_BUTLER}/conversations/search", "GET", None, 400),
        (f"/api/butlers/{_BUTLER}/conversations/{uuid4()}/messages", "GET", None, 404),
    ],
    ids=["invalid-status-422", "patch-404", "search-no-query-400", "messages-conv-404"],
)
async def test_conversations_error_paths(app, path, method, body, expected):
    _app_with_mock_db(app, fetchrow_result=None)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        if method == "GET":
            resp = await client.get(path)
        else:
            resp = await client.patch(path, json=body or {})
    assert resp.status_code == expected


# ---------------------------------------------------------------------------
# Summary endpoint
# ---------------------------------------------------------------------------


async def test_conversation_summary_returns_stats(app):
    row = {
        "total_conversations": 5,
        "active_conversations": 3,
        "total_messages": 12,
        "total_input_tokens": 1000,
        "total_output_tokens": 500,
        "total_duration_ms": 3000,
    }
    _app_with_mock_db(app, fetchrow_result=row)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/api/butlers/{_BUTLER}/conversations/summary")
    assert resp.status_code == 200
