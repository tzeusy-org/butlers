"""The dashboard's debugging tool call reads results through the shared parser (bu-2c1g7v)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from fastmcp import Client, FastMCP
from fastmcp.client.client import CallToolResult
from mcp.types import TextContent

from butlers.api.deps import (
    ButlerConnectionInfo,
    MCPClientManager,
    get_butler_configs,
    get_mcp_manager,
)

pytestmark = pytest.mark.unit


def _server() -> FastMCP:
    server = FastMCP("general")

    @server.tool
    async def lookup(key: str) -> dict:
        return {"key": key, "found": True}

    @server.tool
    async def echo(text: str) -> str:
        return text

    return server


async def _post(app, client: Any, tool: str, arguments: dict) -> httpx.Response:
    manager = AsyncMock(spec=MCPClientManager)
    manager.get_client = AsyncMock(return_value=client)
    app.dependency_overrides[get_mcp_manager] = lambda: manager
    app.dependency_overrides[get_butler_configs] = lambda: [ButlerConnectionInfo("general", 41200)]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as http:
        return await http.post(
            "/api/butlers/general/mcp/call", json={"tool_name": tool, "arguments": arguments}
        )


async def test_a_json_result_through_the_real_client_is_parsed(app) -> None:
    async with Client(_server()) as client:
        response = await _post(app, client, "lookup", {"key": "k1"})

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["result"] == {"key": "k1", "found": True}
    assert data["is_error"] is False
    assert data["raw_text"] is not None


async def test_a_text_result_through_the_real_client_stays_text(app) -> None:
    async with Client(_server()) as client:
        response = await _post(app, client, "echo", {"text": "plain words"})

    data = response.json()["data"]
    assert (data["result"], data["raw_text"]) == ("plain words", "plain words")


async def test_an_sdk_error_result_is_reported_with_its_text(app) -> None:
    client = AsyncMock()
    client.call_tool = AsyncMock(
        return_value=CallToolResult(
            content=[TextContent(type="text", text="tool refused")],
            structured_content=None,
            meta=None,
            is_error=True,
        )
    )

    response = await _post(app, client, "lookup", {"key": "k1"})

    data = response.json()["data"]
    assert (data["is_error"], data["result"]) == (True, "tool refused")
