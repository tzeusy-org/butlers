"""Tests for POST /api/butlers/{name}/tick — force scheduler tick.

Verifies that the endpoint correctly calls the butler's MCP ``tick`` tool,
handles 404 for unknown butlers, and returns 503 when the butler is
unreachable or the request times out.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.api.deps import (
    ButlerConnectionInfo,
    ButlerUnreachableError,
    MCPClientManager,
    get_butler_configs,
    get_mcp_manager,
)
from butlers.api.models import TickResponse
from butlers.api.routers.butlers import _get_db_manager

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_tick_result(text: str = "Tick completed") -> MagicMock:
    """Create a mock CallToolResult from the tick MCP tool."""
    content_block = MagicMock()
    content_block.text = text
    result = MagicMock()
    result.content = [content_block]
    result.is_error = False
    return result


def _mock_mcp_manager(
    *,
    tick_result: MagicMock | None = None,
    unreachable: bool = False,
    timeout: bool = False,
) -> MCPClientManager:
    """Create a mock MCPClientManager for tick endpoint tests."""
    mgr = MagicMock(spec=MCPClientManager)
    if unreachable:
        mgr.get_client = AsyncMock(
            side_effect=ButlerUnreachableError("test", cause=ConnectionRefusedError("refused"))
        )
    elif timeout:
        mgr.get_client = AsyncMock(side_effect=TimeoutError("timed out"))
    else:
        mock_client = MagicMock()
        mock_client.call_tool = AsyncMock(return_value=tick_result)
        mgr.get_client = AsyncMock(return_value=mock_client)
    return mgr


def _create_test_app(
    configs: list[ButlerConnectionInfo],
    mcp_manager: MCPClientManager,
):
    """Create a FastAPI test app with dependency overrides."""
    # Mock DatabaseManager for audit logging
    mock_audit_pool = AsyncMock()
    mock_audit_pool.execute = AsyncMock()
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = mock_audit_pool

    app = create_app()
    app.dependency_overrides[get_butler_configs] = lambda: configs
    app.dependency_overrides[get_mcp_manager] = lambda: mcp_manager
    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    return app


# ---------------------------------------------------------------------------
# POST /api/butlers/{name}/tick endpoint tests
# ---------------------------------------------------------------------------


class TestForceTick:
    async def test_returns_404_for_unknown_butler(self):
        """Unknown butler name returns 404."""
        configs = [ButlerConnectionInfo("general", 40101)]
        mgr = _mock_mcp_manager(unreachable=True)
        app = _create_test_app(configs, mgr)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post("/api/butlers/nonexistent/tick")

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    async def test_successful_tick(self):
        """Successful tick returns 200 with success=True and message."""
        configs = [ButlerConnectionInfo("general", 40101)]
        tick_result = _mock_tick_result("Tick completed successfully")
        mgr = _mock_mcp_manager(tick_result=tick_result)
        app = _create_test_app(configs, mgr)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post("/api/butlers/general/tick")

        assert response.status_code == 200
        body = response.json()
        assert "data" in body
        assert "meta" in body
        assert body["data"]["success"] is True
        assert body["data"]["message"] == "Tick completed successfully"

    async def test_successful_tick_response_model(self):
        """Response data can be parsed as TickResponse."""
        configs = [ButlerConnectionInfo("general", 40101)]
        tick_result = _mock_tick_result("OK")
        mgr = _mock_mcp_manager(tick_result=tick_result)
        app = _create_test_app(configs, mgr)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post("/api/butlers/general/tick")

        assert response.status_code == 200
        tick_resp = TickResponse.model_validate(response.json()["data"])
        assert tick_resp.success is True
        assert tick_resp.message == "OK"

    async def test_returns_503_when_butler_unreachable(self):
        """Returns 503 with error payload when butler is unreachable."""
        configs = [ButlerConnectionInfo("general", 40101)]
        mgr = _mock_mcp_manager(unreachable=True)
        app = _create_test_app(configs, mgr)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post("/api/butlers/general/tick")

        assert response.status_code == 503
        body = response.json()
        assert "error" in body
        assert body["error"]["code"] == "butler_unreachable"
        assert body["error"]["butler"] == "general"

    async def test_returns_503_on_timeout(self):
        """Returns 503 with error payload when tick request times out."""
        configs = [ButlerConnectionInfo("general", 40101)]
        mgr = _mock_mcp_manager(timeout=True)
        app = _create_test_app(configs, mgr)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post("/api/butlers/general/tick")

        assert response.status_code == 503
        body = response.json()
        assert "error" in body
        assert body["error"]["code"] == "butler_timeout"
        assert body["error"]["butler"] == "general"

    async def test_tick_with_empty_content(self):
        """Tick with empty content returns success=True and message=None."""
        configs = [ButlerConnectionInfo("general", 40101)]
        result = MagicMock()
        result.content = []
        result.is_error = False
        mgr = _mock_mcp_manager(tick_result=result)
        app = _create_test_app(configs, mgr)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post("/api/butlers/general/tick")

        assert response.status_code == 200
        body = response.json()
        assert body["data"]["success"] is True
        assert body["data"]["message"] is None
