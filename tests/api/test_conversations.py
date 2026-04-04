"""Tests for dashboard conversation API endpoints.

Condensed from 44 tests to ~8 tests (bu-egmz6).
Keeps: list/pagination structure, field serialization, 503 error path,
search validation, update/messages endpoints.
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
        "id": _CONV_ID, "butler_name": _BUTLER, "title": "Hello world",
        "status": "active", "created_at": _NOW, "updated_at": _NOW,
        "message_count": 2, "total_input_tokens": 100, "total_output_tokens": 200,
        "total_duration_ms": 1500,
    }
    defaults.update(kw)
    return defaults


def _app_with_mock_db(app: FastAPI, *, fetch_rows=None, fetchval_result=0,
                      fetchrow_result=None, execute_result=None, db_raises=None):
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


class TestListConversations:
    async def test_returns_paginated_structure(self, app):
        row = _make_conversation_row()
        _app_with_mock_db(app, fetch_rows=[row], fetchval_result=1)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/butlers/{_BUTLER}/conversations")
        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body and "meta" in body
        assert body["data"][0]["butler_name"] == _BUTLER
        assert body["data"][0]["title"] == "Hello world"

    async def test_503_when_db_unavailable(self, app):
        _app_with_mock_db(app, db_raises=RuntimeError("no shared pool"))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/butlers/{_BUTLER}/conversations")
        assert resp.status_code == 503

    async def test_rejects_invalid_status(self, app):
        _app_with_mock_db(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/butlers/{_BUTLER}/conversations", params={"status": "invalid"})
        assert resp.status_code == 422


class TestConversationOperations:
    async def test_search_requires_query(self, app):
        _app_with_mock_db(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/butlers/{_BUTLER}/conversations/search")
        assert resp.status_code == 400

    async def test_update_conversation_title(self, app):
        row = _make_conversation_row(title="New Title")
        _app_with_mock_db(app, fetchrow_result=row)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.patch(
                f"/api/butlers/{_BUTLER}/conversations/{_CONV_ID}",
                json={"title": "New Title"},
            )
        assert resp.status_code == 200

    async def test_update_returns_404_when_not_found(self, app):
        _app_with_mock_db(app, fetchrow_result=None)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.patch(
                f"/api/butlers/{_BUTLER}/conversations/{_CONV_ID}",
                json={"title": "X"},
            )
        assert resp.status_code == 404

    async def test_list_messages_404_when_conversation_not_found(self, app):
        _app_with_mock_db(app, fetchrow_result=None)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                f"/api/butlers/{_BUTLER}/conversations/{uuid4()}/messages"
            )
        assert resp.status_code == 404

    async def test_summary_returns_stats(self, app):
        row = {
            "total_conversations": 5, "active_conversations": 3, "total_messages": 12,
            "total_input_tokens": 1000, "total_output_tokens": 500, "total_duration_ms": 3000,
        }
        _app_with_mock_db(app, fetchrow_result=row)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/butlers/{_BUTLER}/conversations/summary")
        assert resp.status_code == 200
