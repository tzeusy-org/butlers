"""Tests for state API endpoints.

Verifies the API contract (status codes, response shapes) for state
endpoints.  Uses mocked DatabaseManager and MCPClientManager so no
real database or butler daemon is required.

Issues: butlers-26h.5.4, 5.5, 5.6
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.api.deps import ButlerUnreachableError, MCPClientManager, get_mcp_manager
from butlers.api.routers.state import _get_db_manager

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime.now(tz=UTC)


def _make_state_record(
    *,
    key: str = "test_key",
    value: dict | None = None,
    updated_at: datetime = _NOW,
) -> dict:
    """Create a dict mimicking an asyncpg Record for state columns."""
    return {
        "key": key,
        "value": value or {"foo": "bar"},
        "updated_at": updated_at,
    }


def _make_scalar_record(key: str, value: object, updated_at: datetime = _NOW) -> dict:
    """Create a state record with an arbitrary JSON value (scalar, null, etc.)."""
    return {"key": key, "value": value, "updated_at": updated_at}


def _app_with_mock_db(
    *,
    fetch_rows: list | None = None,
    fetchrow_result: dict | None = None,
    pool_side_effect: Exception | None = None,
):
    """Create a FastAPI app with a mocked DatabaseManager."""
    mock_pool = AsyncMock()
    mock_pool.fetch = AsyncMock(return_value=fetch_rows or [])
    mock_pool.fetchrow = AsyncMock(return_value=fetchrow_result)

    mock_db = MagicMock(spec=DatabaseManager)
    if pool_side_effect:
        mock_db.pool.side_effect = pool_side_effect
    else:
        mock_db.pool.return_value = mock_pool

    app = create_app()
    app.dependency_overrides[_get_db_manager] = lambda: mock_db

    return app


def _app_with_mock_mcp(
    *,
    call_tool_result: MagicMock | None = None,
    unreachable: bool = False,
):
    """Create a FastAPI app with a mocked MCPClientManager for write endpoints."""
    mock_mgr = MagicMock(spec=MCPClientManager)

    if unreachable:
        mock_mgr.get_client = AsyncMock(side_effect=ButlerUnreachableError("test-butler"))
    else:
        mock_client = AsyncMock()
        if call_tool_result is not None:
            mock_client.call_tool = AsyncMock(return_value=call_tool_result)
        else:
            mock_client.call_tool = AsyncMock(return_value=MagicMock())
        mock_mgr.get_client = AsyncMock(return_value=mock_client)

    # Mock DatabaseManager for audit logging in write endpoints
    mock_audit_pool = AsyncMock()
    mock_audit_pool.execute = AsyncMock()
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = mock_audit_pool

    app = create_app()
    app.dependency_overrides[get_mcp_manager] = lambda: mock_mgr
    app.dependency_overrides[_get_db_manager] = lambda: mock_db

    return app


# ---------------------------------------------------------------------------
# GET /api/butlers/{name}/state — list all state entries
# ---------------------------------------------------------------------------


class TestListState:
    async def test_returns_array_of_state_entries(self):
        """Response should wrap a list of StateEntry in ApiResponse envelope."""
        rows = [
            _make_state_record(key="alpha", value={"count": 1}),
            _make_state_record(key="beta", value={"count": 2}),
        ]
        app = _app_with_mock_db(fetch_rows=rows)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/butlers/atlas/state")

        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body
        assert isinstance(body["data"], list)
        assert len(body["data"]) == 2
        assert body["data"][0]["key"] == "alpha"
        assert body["data"][0]["value"] == {"count": 1}
        assert body["data"][1]["key"] == "beta"
        assert body["data"][1]["value"] == {"count": 2}

    async def test_empty_state_returns_empty_array(self):
        """When no state entries exist, return empty data list."""
        app = _app_with_mock_db(fetch_rows=[])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/butlers/atlas/state")

        assert resp.status_code == 200
        body = resp.json()
        assert body["data"] == []

    async def test_butler_db_unavailable_returns_503(self):
        """When the butler's DB pool doesn't exist, return 503."""
        app = _app_with_mock_db(pool_side_effect=KeyError("no pool"))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/butlers/nonexistent/state")

        assert resp.status_code == 503

    async def test_boolean_value_returns_200(self):
        """Regression: list endpoint must not 400 when a state row has a boolean value."""
        rows = [_make_scalar_record(key="flag", value=True)]
        app = _app_with_mock_db(fetch_rows=rows)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/butlers/switchboard/state")

        assert resp.status_code == 200
        body = resp.json()
        assert body["data"][0]["key"] == "flag"
        assert body["data"][0]["value"] is True

    async def test_null_value_returns_200(self):
        """Regression: list endpoint must not 400 when a state row has a null value."""
        rows = [_make_scalar_record(key="empty", value=None)]
        app = _app_with_mock_db(fetch_rows=rows)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/butlers/switchboard/state")

        assert resp.status_code == 200
        body = resp.json()
        assert body["data"][0]["value"] is None

    async def test_integer_value_returns_200(self):
        """Regression: list endpoint must not 400 when a state row has an integer value."""
        rows = [_make_scalar_record(key="count", value=42)]
        app = _app_with_mock_db(fetch_rows=rows)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/butlers/switchboard/state")

        assert resp.status_code == 200
        assert resp.json()["data"][0]["value"] == 42

    async def test_array_value_returns_200(self):
        """Regression: list endpoint must not 400 when a state row has an array value."""
        rows = [_make_scalar_record(key="tags", value=["a", "b", "c"])]
        app = _app_with_mock_db(fetch_rows=rows)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/butlers/switchboard/state")

        assert resp.status_code == 200
        assert resp.json()["data"][0]["value"] == ["a", "b", "c"]

    async def test_mixed_value_types_return_200(self):
        """Regression: list endpoint returns 200 when state rows have mixed value types."""
        rows = [
            _make_scalar_record(key="flag", value=True),
            _make_state_record(key="config", value={"theme": "dark"}),
            _make_scalar_record(key="count", value=0),
        ]
        app = _app_with_mock_db(fetch_rows=rows)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/butlers/switchboard/state")

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data) == 3
        assert data[0]["value"] is True
        assert data[1]["value"] == {"theme": "dark"}
        assert data[2]["value"] == 0


# ---------------------------------------------------------------------------
# GET /api/butlers/{name}/state/{key} — get single state entry
# ---------------------------------------------------------------------------


class TestGetState:
    async def test_returns_single_entry(self):
        """Response should wrap a single StateEntry in ApiResponse envelope."""
        row = _make_state_record(key="my_key", value={"data": "hello"})
        app = _app_with_mock_db(fetchrow_result=row)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/butlers/atlas/state/my_key")

        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body
        assert body["data"]["key"] == "my_key"
        assert body["data"]["value"] == {"data": "hello"}
        assert "updated_at" in body["data"]

    async def test_missing_key_returns_404(self):
        """A non-existent key should return 404."""
        app = _app_with_mock_db(fetchrow_result=None)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/butlers/atlas/state/nonexistent")

        assert resp.status_code == 404

    async def test_butler_db_unavailable_returns_503(self):
        """When the butler's DB pool doesn't exist, return 503."""
        app = _app_with_mock_db(pool_side_effect=KeyError("no pool"))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/butlers/nonexistent/state/any_key")

        assert resp.status_code == 503

    async def test_boolean_value_returns_200(self):
        """Regression: get endpoint must not 400 when the row value is a boolean."""
        row = _make_scalar_record(key="flag", value=False)
        app = _app_with_mock_db(fetchrow_result=row)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/butlers/switchboard/state/flag")

        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["key"] == "flag"
        assert body["data"]["value"] is False

    async def test_null_value_returns_200(self):
        """Regression: get endpoint must not 400 when the row value is null."""
        row = _make_scalar_record(key="empty", value=None)
        app = _app_with_mock_db(fetchrow_result=row)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/butlers/switchboard/state/empty")

        assert resp.status_code == 200
        assert resp.json()["data"]["value"] is None


# ---------------------------------------------------------------------------
# PUT /api/butlers/{name}/state/{key} — set state via MCP
# ---------------------------------------------------------------------------


class TestSetState:
    async def test_sets_value_via_mcp(self):
        """PUT should call MCP state_set and return success."""
        app = _app_with_mock_mcp()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.put(
                "/api/butlers/atlas/state/my_key",
                json={"value": {"foo": "bar"}},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["key"] == "my_key"
        assert body["data"]["status"] == "ok"

    async def test_butler_unreachable_returns_503(self):
        """When the butler MCP server is unreachable, return 503."""
        app = _app_with_mock_mcp(unreachable=True)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.put(
                "/api/butlers/unreachable/state/my_key",
                json={"value": {"foo": "bar"}},
            )

        assert resp.status_code == 503

    async def test_calls_correct_mcp_tool(self):
        """PUT should call the state_set MCP tool with correct arguments."""
        mock_mgr = MagicMock(spec=MCPClientManager)
        mock_client = AsyncMock()
        mock_client.call_tool = AsyncMock(return_value=MagicMock())
        mock_mgr.get_client = AsyncMock(return_value=mock_client)

        mock_audit_pool = AsyncMock()
        mock_audit_pool.execute = AsyncMock()
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.pool.return_value = mock_audit_pool

        app = create_app()
        app.dependency_overrides[get_mcp_manager] = lambda: mock_mgr
        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            await client.put(
                "/api/butlers/atlas/state/my_key",
                json={"value": {"count": 42}},
            )

        mock_client.call_tool.assert_called_once_with(
            "state_set", {"key": "my_key", "value": {"count": 42}}
        )


# ---------------------------------------------------------------------------
# DELETE /api/butlers/{name}/state/{key} — delete state via MCP
# ---------------------------------------------------------------------------


class TestDeleteState:
    async def test_deletes_via_mcp(self):
        """DELETE should call MCP state_delete and return success."""
        app = _app_with_mock_mcp()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.delete("/api/butlers/atlas/state/my_key")

        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["key"] == "my_key"
        assert body["data"]["status"] == "deleted"

    async def test_butler_unreachable_returns_503(self):
        """When the butler MCP server is unreachable, return 503."""
        app = _app_with_mock_mcp(unreachable=True)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.delete("/api/butlers/unreachable/state/my_key")

        assert resp.status_code == 503

    async def test_calls_correct_mcp_tool(self):
        """DELETE should call the state_delete MCP tool with correct key."""
        mock_mgr = MagicMock(spec=MCPClientManager)
        mock_client = AsyncMock()
        mock_client.call_tool = AsyncMock(return_value=MagicMock())
        mock_mgr.get_client = AsyncMock(return_value=mock_client)

        mock_audit_pool = AsyncMock()
        mock_audit_pool.execute = AsyncMock()
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.pool.return_value = mock_audit_pool

        app = create_app()
        app.dependency_overrides[get_mcp_manager] = lambda: mock_mgr
        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            await client.delete("/api/butlers/atlas/state/some_key")

        mock_client.call_tool.assert_called_once_with("state_delete", {"key": "some_key"})
