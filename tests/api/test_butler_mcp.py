"""Tests for per-butler MCP debugging endpoints.

Covers:
- GET /api/butlers/{name}/mcp/tools
- POST /api/butlers/{name}/mcp/call
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.deps import (
    ButlerConnectionInfo,
    ButlerUnreachableError,
    MCPClientManager,
    get_butler_configs,
    get_mcp_manager,
)

pytestmark = pytest.mark.unit


def _mock_call_tool_result(payload: str, *, is_error: bool = False) -> MagicMock:
    """Create a mock CallToolResult with a single text content block."""
    content_block = MagicMock()
    content_block.text = payload
    result = MagicMock()
    result.content = [content_block]
    result.is_error = is_error
    return result


def _mock_mcp_manager(
    *,
    tools: list[object] | None = None,
    call_result: MagicMock | None = None,
    unreachable: bool = False,
    timeout: bool = False,
) -> MCPClientManager:
    """Create a mock MCPClientManager for MCP endpoint tests."""
    mgr = MagicMock(spec=MCPClientManager)

    if unreachable:
        mgr.get_client = AsyncMock(
            side_effect=ButlerUnreachableError("general", cause=ConnectionRefusedError("refused"))
        )
        return mgr

    if timeout:
        mgr.get_client = AsyncMock(side_effect=TimeoutError("timed out"))
        return mgr

    mock_client = MagicMock()
    mock_client.list_tools = AsyncMock(return_value=tools or [])
    mock_client.call_tool = AsyncMock(return_value=call_result)
    mgr.get_client = AsyncMock(return_value=mock_client)
    return mgr


def _create_test_app(
    configs: list[ButlerConnectionInfo],
    mcp_manager: MCPClientManager,
):
    """Create a FastAPI test app with dependency overrides."""
    app = create_app()
    app.dependency_overrides[get_butler_configs] = lambda: configs
    app.dependency_overrides[get_mcp_manager] = lambda: mcp_manager
    return app


class TestListButlerMcpTools:
    async def test_returns_tool_catalog(self):
        """Endpoint returns normalized tool metadata from list_tools()."""
        tools = [
            {
                "name": "state_get",
                "description": "Get a state value",
                "inputSchema": {"type": "object"},
            },
            SimpleNamespace(
                name="state_set",
                description="Set a state value",
                input_schema={"type": "object"},
            ),
        ]
        configs = [ButlerConnectionInfo("general", 40101)]
        mgr = _mock_mcp_manager(tools=tools)
        app = _create_test_app(configs, mgr)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers/general/mcp/tools")

        assert response.status_code == 200
        body = response.json()
        assert "data" in body
        assert "meta" in body
        assert [tool["name"] for tool in body["data"]] == ["state_get", "state_set"]
        assert body["data"][0]["description"] == "Get a state value"
        assert body["data"][0]["input_schema"] == {"type": "object"}

        mock_client = await mgr.get_client("general")
        mock_client.list_tools.assert_called_once_with()

    async def test_returns_404_for_unknown_butler(self):
        """Unknown butler name returns 404."""
        configs = [ButlerConnectionInfo("general", 40101)]
        mgr = _mock_mcp_manager(unreachable=True)
        app = _create_test_app(configs, mgr)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers/unknown/mcp/tools")

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    async def test_returns_503_when_unreachable(self):
        """Unreachable butler MCP server returns 503."""
        configs = [ButlerConnectionInfo("general", 40101)]
        mgr = _mock_mcp_manager(unreachable=True)
        app = _create_test_app(configs, mgr)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers/general/mcp/tools")

        assert response.status_code == 503
        assert "unreachable" in response.json()["detail"].lower()


class TestCallButlerMcpTool:
    async def test_calls_tool_with_arguments(self):
        """POST endpoint proxies tool call and returns parsed JSON payload."""
        configs = [ButlerConnectionInfo("general", 40101)]
        call_result = _mock_call_tool_result('{"ok": true, "value": 123}')
        mgr = _mock_mcp_manager(call_result=call_result)
        app = _create_test_app(configs, mgr)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/butlers/general/mcp/call",
                json={
                    "tool_name": "state_get",
                    "arguments": {"key": "dashboard.debug"},
                },
            )

        assert response.status_code == 200
        body = response.json()
        assert body["data"]["tool_name"] == "state_get"
        assert body["data"]["arguments"] == {"key": "dashboard.debug"}
        assert body["data"]["result"] == {"ok": True, "value": 123}
        assert body["data"]["is_error"] is False
        assert body["data"]["raw_text"] == '{"ok": true, "value": 123}'

        mock_client = await mgr.get_client("general")
        mock_client.call_tool.assert_called_once_with("state_get", {"key": "dashboard.debug"})

    async def test_returns_text_when_result_not_json(self):
        """Non-JSON tool output is returned as plain text."""
        configs = [ButlerConnectionInfo("general", 40101)]
        call_result = _mock_call_tool_result("plain-text-result")
        mgr = _mock_mcp_manager(call_result=call_result)
        app = _create_test_app(configs, mgr)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/butlers/general/mcp/call",
                json={"tool_name": "ping", "arguments": {}},
            )

        assert response.status_code == 200
        body = response.json()
        assert body["data"]["tool_name"] == "ping"
        assert body["data"]["result"] == "plain-text-result"
        assert body["data"]["raw_text"] == "plain-text-result"

    async def test_returns_404_for_unknown_butler(self):
        """Unknown butler name returns 404."""
        configs = [ButlerConnectionInfo("general", 40101)]
        call_result = _mock_call_tool_result(json.dumps({"ok": True}))
        mgr = _mock_mcp_manager(call_result=call_result)
        app = _create_test_app(configs, mgr)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/butlers/unknown/mcp/call",
                json={"tool_name": "ping", "arguments": {}},
            )

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    async def test_returns_503_when_unreachable(self):
        """Unreachable butler MCP server returns 503."""
        configs = [ButlerConnectionInfo("general", 40101)]
        mgr = _mock_mcp_manager(unreachable=True)
        app = _create_test_app(configs, mgr)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/butlers/general/mcp/call",
                json={"tool_name": "ping", "arguments": {}},
            )

        assert response.status_code == 503
        assert "unreachable" in response.json()["detail"].lower()
