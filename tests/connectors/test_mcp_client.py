"""CachedMCPClient error contract against the pinned fastmcp client.

fastmcp 3.x raises ``ToolError`` (not ``RuntimeError``) when a tool returns
``is_error``, and raises ``RuntimeError`` for dropped sessions. The cached
client must surface the first as an application error without retrying and
treat the second as a transport failure that earns one reconnect.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError

from butlers.connectors import mcp_client
from butlers.connectors.mcp_client import CachedMCPClient

pytestmark = pytest.mark.unit


def _server(calls: list[str]) -> FastMCP:
    server = FastMCP("switchboard-stub")

    @server.tool
    def ingest(ok: bool) -> dict[str, Any]:
        calls.append("ingest")
        if not ok:
            raise ToolError("ingest rejected: bad envelope")
        return {"status": "accepted"}

    return server


async def test_tool_error_is_an_application_error_and_not_retried(monkeypatch):
    calls: list[str] = []
    server = _server(calls)
    connects: list[str] = []

    def factory(url: str, *, name: str) -> Client:
        connects.append(url)
        return Client(server, name=name)

    monkeypatch.setattr(mcp_client, "MCPClient", factory)
    client = CachedMCPClient("http://switchboard/sse", client_name="test")
    try:
        assert await client.call_tool("ingest", {"ok": True}) == {"status": "accepted"}
        with pytest.raises(RuntimeError, match="ingest rejected: bad envelope") as excinfo:
            await client.call_tool("ingest", {"ok": False})
    finally:
        await client.aclose()

    assert not isinstance(excinfo.value, ConnectionError)
    assert isinstance(excinfo.value.__cause__, ToolError)
    assert calls == ["ingest", "ingest"]
    assert len(connects) == 1


class _DroppingClient:
    """Stand-in for a fastmcp client whose session dies mid-call."""

    def __init__(self, error: str) -> None:
        self._error = error

    async def __aenter__(self) -> _DroppingClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    def is_connected(self) -> bool:
        return True

    async def call_tool(self, name: str, args: dict[str, Any]) -> Any:
        raise RuntimeError(self._error)


@pytest.mark.parametrize(
    "error",
    [
        # Raised verbatim by fastmcp.client.client in the pinned release.
        "Server session was closed unexpectedly",
        "Session task completed unexpectedly",
        "Client is not connected. Use the 'async with client:' context manager first.",
    ],
)
async def test_session_drop_reconnects_once_then_succeeds(monkeypatch, error):
    calls: list[str] = []
    server = _server(calls)
    built: list[Any] = []

    def factory(url: str, *, name: str) -> Any:
        client = _DroppingClient(error) if not built else Client(server, name=name)
        built.append(client)
        return client

    monkeypatch.setattr(mcp_client, "MCPClient", factory)
    client = CachedMCPClient("http://switchboard/sse", client_name="test")
    try:
        assert await client.call_tool("ingest", {"ok": True}) == {"status": "accepted"}
    finally:
        await client.aclose()

    assert len(built) == 2
    assert calls == ["ingest"]


async def test_session_drop_on_both_attempts_raises_connection_error(monkeypatch):
    built: list[Any] = []

    def factory(url: str, *, name: str) -> Any:
        client = _DroppingClient("Server session was closed unexpectedly")
        built.append(client)
        return client

    monkeypatch.setattr(mcp_client, "MCPClient", factory)
    client = CachedMCPClient("http://switchboard/sse", client_name="test")
    with pytest.raises(ConnectionError, match="session was closed unexpectedly"):
        await client.call_tool("ingest", {"ok": True})

    assert len(built) == 2
