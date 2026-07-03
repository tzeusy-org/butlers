"""Unit tests for GET /relationship/groups/{group_id}/members (bu-5umz4).

Covers:
- 200 with a member roster (contact_id, entity_id, name, entity_type)
- 200 with an empty roster for a group with no linked members
- 404 for a nonexistent group

Pattern mirrors tests/relationship/test_group_labels_api.py: create_app()
loads the relationship router dynamically into
sys.modules["relationship_api_router"]; override its _get_db_manager.
"""

from __future__ import annotations

import sys
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager

pytestmark = pytest.mark.unit

_GID = uuid.uuid4()
_CID = uuid.uuid4()
_EID = uuid.uuid4()


class _Row(dict):
    """dict subclass that mimics asyncpg Record."""

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name) from None

    def get(self, key: str, default: Any = None) -> Any:
        return super().get(key, default)


def _row(data: dict) -> _Row:
    return _Row(data)


def _mock_pool(*, fetch_rows: list | None = None, fetchval_result: Any = None):
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=fetch_rows or [])
    pool.fetchval = AsyncMock(return_value=fetchval_result)
    return pool


def _make_app(pool):
    """Create the app and override the relationship router's DB dependency."""
    db = MagicMock(spec=DatabaseManager)
    db.pool.return_value = pool

    app = create_app(api_key="")

    rel_module = sys.modules.get("relationship_api_router")
    if rel_module is None:
        raise RuntimeError("relationship_api_router not found in sys.modules after create_app()")
    app.dependency_overrides[rel_module._get_db_manager] = lambda: db
    return app


class TestGetGroupMembers:
    async def test_returns_members_for_group(self):
        member_row = _row(
            {
                "id": _CID,
                "entity_id": _EID,
                "name": "Ada Lovelace",
                "entity_type": "person",
            }
        )
        pool = _mock_pool(fetch_rows=[member_row], fetchval_result=1)  # group exists

        app = _make_app(pool)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/relationship/groups/{_GID}/members")
        assert resp.status_code == 200
        body = resp.json()
        assert body["group_id"] == str(_GID)
        assert len(body["members"]) == 1
        member = body["members"][0]
        assert member["id"] == str(_CID)
        assert member["entity_id"] == str(_EID)
        assert member["name"] == "Ada Lovelace"
        assert member["entity_type"] == "person"

    async def test_returns_empty_roster_for_group_with_no_linked_members(self):
        pool = _mock_pool(fetch_rows=[], fetchval_result=1)  # group exists, no rows

        app = _make_app(pool)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/relationship/groups/{_GID}/members")
        assert resp.status_code == 200
        body = resp.json()
        assert body["group_id"] == str(_GID)
        assert body["members"] == []

    async def test_returns_404_when_group_missing(self):
        pool = _mock_pool(fetchval_result=None)  # group does not exist

        app = _make_app(pool)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/relationship/groups/{_GID}/members")
        assert resp.status_code == 404
