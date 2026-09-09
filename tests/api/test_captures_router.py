"""Tests for GET /api/captures."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from butlers.api.db import DatabaseManager
from butlers.api.routers.captures import _get_db_manager

pytestmark = pytest.mark.unit


class _Row(dict):
    def __getitem__(self, key):
        return super().__getitem__(key)


def _capture_row(**overrides) -> _Row:
    base = {
        "capture_id": uuid.uuid4(),
        "channel": "telegram",
        "content": "a stray thought",
        "receipt_state": "held",
        "routed_kind": None,
        "target_schema": None,
        "target_table": None,
        "target_row_id": None,
        "refusal_reason": None,
        "source_butler": "general",
        "created_at": datetime(2026, 9, 9, 12, 0, tzinfo=UTC),
        "updated_at": datetime(2026, 9, 9, 12, 0, tzinfo=UTC),
    }
    base.update(overrides)
    return _Row(base)


async def _get(app, params: str = "") -> httpx.Response:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        return await c.get(f"/api/captures{params}")


class TestCapturesHeldState:
    async def test_returns_held_orphan(self, app):
        row = _capture_row()
        mock_pool = AsyncMock()
        mock_pool.fetch = AsyncMock(return_value=[row])
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.pool.return_value = mock_pool
        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        resp = await _get(app, "?state=held")

        assert resp.status_code == 200
        body = resp.json()
        assert len(body["data"]) == 1
        assert body["data"][0]["receipt_state"] == "held"
        assert body["data"][0]["capture_id"] == str(row["capture_id"])
        assert body["meta"]["has_more"] is False
        assert body["meta"].get("sources_degraded") is None

        fetch_sql = mock_pool.fetch.await_args.args[0]
        assert "receipt_state = $1" in fetch_sql
        assert mock_pool.fetch.await_args.args[1] == "held"

    async def test_degraded_when_pool_missing(self, app):
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.pool.side_effect = KeyError("general")
        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        resp = await _get(app, "?state=held")

        assert resp.status_code == 200
        body = resp.json()
        assert body["data"] == []
        assert body["meta"]["sources_degraded"] == ["general"]

    async def test_pagination_has_more(self, app):
        rows = [_capture_row(capture_id=uuid.uuid4()) for _ in range(3)]
        mock_pool = AsyncMock()
        mock_pool.fetch = AsyncMock(return_value=rows)  # limit=2 requested below
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.pool.return_value = mock_pool
        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        resp = await _get(app, "?limit=2")

        assert resp.status_code == 200
        body = resp.json()
        assert len(body["data"]) == 2
        assert body["meta"]["has_more"] is True
        assert body["meta"]["next_cursor"] is not None
