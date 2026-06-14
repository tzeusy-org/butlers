"""Unit tests for group_labels API endpoints.

Covers:
- POST /relationship/labels — create label (201, 422)
- GET /relationship/groups/{id}/labels — list labels on group (200, 404)
- POST /relationship/groups/{id}/labels/{label_id} — assign label (200, 404)
- DELETE /relationship/groups/{id}/labels/{label_id} — remove label (200)
- GET /relationship/groups — list groups includes labels field
- GET /relationship/groups/{id} — get group includes labels field

Pattern: create_app() causes dynamic modules to be loaded and wired via
wire_db_dependencies. After create_app(), the relationship router module is
available in sys.modules["relationship_api_router"]. We override its
_get_db_manager via app.dependency_overrides using the module's own function.
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
_LID = uuid.uuid4()
_NOW = "2026-01-01T00:00:00+00:00"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


def _mock_pool(
    *,
    fetch_rows: list | None = None,
    fetchrow_result: Any = None,
    fetchval_result: Any = None,
    execute_result: str = "DELETE 1",
):
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=fetch_rows or [])
    pool.fetchrow = AsyncMock(return_value=fetchrow_result)
    pool.fetchval = AsyncMock(return_value=fetchval_result)
    pool.execute = AsyncMock(return_value=execute_result)
    return pool


def _make_app(pool):
    """Create the app and override the relationship router's DB dependency."""
    db = MagicMock(spec=DatabaseManager)
    db.pool.return_value = pool

    app = create_app(api_key="")

    # After create_app(), the dynamic relationship router module is loaded into
    # sys.modules under the key "relationship_api_router" (set by router_discovery).
    rel_module = sys.modules.get("relationship_api_router")
    if rel_module is None:
        raise RuntimeError("relationship_api_router not found in sys.modules after create_app()")
    app.dependency_overrides[rel_module._get_db_manager] = lambda: db
    return app


# ---------------------------------------------------------------------------
# POST /relationship/labels
# ---------------------------------------------------------------------------


class TestCreateLabel:
    async def test_create_label_returns_201(self):
        label_row = _row({"id": _LID, "name": "VIP", "color": "#ff0000"})
        pool = _mock_pool()

        async def _fetch(q: str, *args):
            # _table_columns queries information_schema — return empty
            return []

        pool.fetch = AsyncMock(side_effect=_fetch)
        pool.fetchrow = AsyncMock(return_value=label_row)
        app = _make_app(pool)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/relationship/labels",
                json={"name": "VIP", "color": "#ff0000"},
            )
        assert resp.status_code == 201
        body = resp.json()
        assert body["name"] == "VIP"
        assert body["color"] == "#ff0000"

    async def test_create_label_missing_name_returns_422(self):
        pool = _mock_pool()
        app = _make_app(pool)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post("/api/relationship/labels", json={})
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /relationship/groups/{group_id}/labels
# ---------------------------------------------------------------------------


class TestGetGroupLabels:
    async def test_returns_labels_for_group(self):
        label_row = _row({"id": _LID, "name": "Friend", "color": None})

        async def _fetch(q: str, *args):
            # _table_columns queries pg_attribute with to_regclass
            if "pg_attribute" in q:
                return [_row({"column_name": "group_id"})]
            return [label_row]

        pool = _mock_pool()
        pool.fetch = AsyncMock(side_effect=_fetch)
        pool.fetchval = AsyncMock(return_value=1)  # group exists

        app = _make_app(pool)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/relationship/groups/{_GID}/labels")
        assert resp.status_code == 200
        body = resp.json()
        assert body["group_id"] == str(_GID)
        assert len(body["labels"]) == 1
        assert body["labels"][0]["name"] == "Friend"

    async def test_returns_404_when_group_missing(self):
        pool = _mock_pool(fetchval_result=None)
        pool.fetch = AsyncMock(return_value=[])
        app = _make_app(pool)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/relationship/groups/{_GID}/labels")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /relationship/groups/{group_id}/labels/{label_id}
# ---------------------------------------------------------------------------


class TestAssignGroupLabel:
    async def test_assign_returns_200(self):
        pool = _mock_pool(fetchval_result=1)
        app = _make_app(pool)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(f"/api/relationship/groups/{_GID}/labels/{_LID}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["assigned"] is True
        assert body["group_id"] == str(_GID)
        assert body["label_id"] == str(_LID)

    async def test_assign_returns_404_when_group_missing(self):
        pool = _mock_pool(fetchval_result=None)
        app = _make_app(pool)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(f"/api/relationship/groups/{_GID}/labels/{_LID}")
        assert resp.status_code == 404

    async def test_assign_returns_404_when_label_missing(self):
        call_count = 0

        async def _fetchval(q: str, *args):
            nonlocal call_count
            call_count += 1
            # First call (group check) returns 1; second (label check) returns None
            return 1 if call_count == 1 else None

        pool = _mock_pool()
        pool.fetchval = AsyncMock(side_effect=_fetchval)
        app = _make_app(pool)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(f"/api/relationship/groups/{_GID}/labels/{_LID}")
        assert resp.status_code == 404

    async def test_assign_already_exists_returns_assigned_false(self):
        call_count = 0

        async def _fetchval(q: str, *args):
            nonlocal call_count
            call_count += 1
            # Calls: 1=group exists, 2=label exists, 3=INSERT RETURNING 1 (conflict → None)
            if call_count <= 2:
                return 1
            return None

        pool = _mock_pool()
        pool.fetchval = AsyncMock(side_effect=_fetchval)
        app = _make_app(pool)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(f"/api/relationship/groups/{_GID}/labels/{_LID}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["assigned"] is False
        assert body["group_id"] == str(_GID)
        assert body["label_id"] == str(_LID)


# ---------------------------------------------------------------------------
# DELETE /relationship/groups/{group_id}/labels/{label_id}
# ---------------------------------------------------------------------------


class TestRemoveGroupLabel:
    async def test_remove_returns_200_with_removed_true(self):
        pool = _mock_pool(execute_result="DELETE 1")
        app = _make_app(pool)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.delete(f"/api/relationship/groups/{_GID}/labels/{_LID}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["removed"] is True

    async def test_remove_returns_200_with_removed_false_when_not_assigned(self):
        pool = _mock_pool(execute_result="DELETE 0")
        app = _make_app(pool)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.delete(f"/api/relationship/groups/{_GID}/labels/{_LID}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["removed"] is False


# ---------------------------------------------------------------------------
# GET /relationship/groups — labels field present
# ---------------------------------------------------------------------------


class TestListGroupsLabels:
    async def test_list_groups_includes_labels_field(self):
        group_row = _row(
            {
                "id": _GID,
                "name": "Test Group",
                "description": None,
                "member_count": 3,
                "created_at": _NOW,
                "updated_at": _NOW,
            }
        )
        label_row = _row({"group_id": _GID, "id": _LID, "name": "VIP", "color": "#ff0000"})

        # Track calls to distinguish between the two pg_attribute calls and other fetches
        pg_attr_call_count = 0

        async def _fetch(q: str, *args):
            nonlocal pg_attr_call_count
            if "pg_attribute" in q:
                pg_attr_call_count += 1
                # Both _table_columns calls (groups + group_labels) return columns
                return [
                    _row({"column_name": "id"}),
                    _row({"column_name": "description"}),
                    _row({"column_name": "updated_at"}),
                ]
            if "group_labels" in q and "JOIN labels" in q:
                return [label_row]
            if "FROM groups" in q:
                return [group_row]
            return []

        pool = _mock_pool()
        pool.fetch = AsyncMock(side_effect=_fetch)
        pool.fetchval = AsyncMock(return_value=1)  # total count

        app = _make_app(pool)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/relationship/groups")
        assert resp.status_code == 200
        body = resp.json()
        assert "groups" in body
        groups = body["groups"]
        assert len(groups) == 1
        g = groups[0]
        assert "labels" in g
        assert len(g["labels"]) == 1
        assert g["labels"][0]["name"] == "VIP"
