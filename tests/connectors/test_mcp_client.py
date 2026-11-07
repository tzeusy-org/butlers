"""CachedMCPClient error contract against the pinned fastmcp client.

fastmcp 3.x raises ``ToolError`` (not ``RuntimeError``) when a tool returns
``is_error``, and raises ``RuntimeError`` for dropped sessions. The cached
client must surface the first as an application error without retrying and
treat the second as a transport failure that earns one reconnect.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError

from butlers.connectors import mcp_client, telegram_bot
from butlers.connectors.heartbeat import ConnectorHeartbeat, HeartbeatConfig
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


async def test_transient_tool_reply_raises_a_typed_connection_error(monkeypatch):
    """A ``transient_error`` reply fails closed for callers that ignore results."""
    calls: list[str] = []
    server = FastMCP("switchboard-stub")

    @server.tool
    def ingest(ok: bool) -> dict[str, Any]:
        calls.append("ingest")
        return {"status": "transient_error", "retryable": True, "error": "retry later"}

    monkeypatch.setattr(mcp_client, "MCPClient", lambda url, *, name: Client(server, name=name))
    client = CachedMCPClient("http://switchboard/sse", client_name="test")
    try:
        with pytest.raises(mcp_client.TransientToolError, match="retry later") as excinfo:
            await client.call_tool("ingest", {"ok": True})
    finally:
        await client.aclose()

    assert isinstance(excinfo.value, ConnectionError)
    assert calls == ["ingest"]


# ---------------------------------------------------------------------------
# A CancelledError nobody asked for (bu-dhloi1.131)
# ---------------------------------------------------------------------------


async def _raise_stray() -> None:
    """What fastmcp raises from ``session_task.exception()`` on a cancelled session."""
    future = asyncio.get_running_loop().create_future()
    future.cancel()
    await future


class _StrayClient(_DroppingClient):
    """A client whose session was cancelled: calls raise a bare ``CancelledError``."""

    def __init__(self) -> None:
        super().__init__("unused")

    async def call_tool(self, name: str, args: dict[str, Any]) -> Any:
        await _raise_stray()


def _holding_server() -> tuple[FastMCP, asyncio.Event]:
    """The first ``ingest`` holds until its session goes away; later ones answer."""
    server = FastMCP("switchboard-stub")
    holding = asyncio.Event()

    @server.tool
    async def ingest(ok: bool) -> dict[str, Any]:
        if not holding.is_set():
            holding.set()
            await asyncio.Event().wait()
        return {"status": "accepted"}

    return server, holding


async def test_the_pinned_fastmcp_raises_a_stray_when_its_session_is_cancelled_mid_call():
    """The real library path this guard exists for; a fastmcp bump that drops it shows here."""
    server, holding = _holding_server()
    async with Client(server, name="test") as client:
        call = asyncio.create_task(client.call_tool("ingest", {"ok": True}))
        async with asyncio.timeout(5):
            await holding.wait()
        client._session_state.session_task.cancel()

        with pytest.raises(asyncio.CancelledError):
            async with asyncio.timeout(5):
                await call
        assert call.cancelling() == 0


async def test_a_stray_from_a_real_cancelled_session_reconnects_and_succeeds(monkeypatch):
    server, holding = _holding_server()
    built: list[Any] = []

    def factory(url: str, *, name: str) -> Any:
        built.append(Client(server, name=name))
        return built[-1]

    monkeypatch.setattr(mcp_client, "MCPClient", factory)
    client = CachedMCPClient("http://switchboard/sse", client_name="test")
    try:
        call = asyncio.create_task(client.call_tool("ingest", {"ok": True}))
        async with asyncio.timeout(5):
            await holding.wait()
        built[0]._session_state.session_task.cancel()

        async with asyncio.timeout(5):
            assert await call == {"status": "accepted"}
    finally:
        await client.aclose()

    assert len(built) == 2


async def test_a_stray_on_both_attempts_is_a_connection_error_that_names_it(monkeypatch):
    built: list[Any] = []

    def factory(url: str, *, name: str) -> Any:
        built.append(_StrayClient())
        return built[-1]

    monkeypatch.setattr(mcp_client, "MCPClient", factory)
    client = CachedMCPClient("http://switchboard/sse", client_name="test")

    with pytest.raises(ConnectionError, match="CancelledError"):
        await client.call_tool("ingest", {"ok": True})

    assert len(built) == 2
    assert asyncio.current_task().cancelling() == 0


async def test_a_stray_while_connecting_is_a_connection_error(monkeypatch):
    class _StrayEnter(_DroppingClient):
        async def __aenter__(self) -> Any:
            await _raise_stray()

    monkeypatch.setattr(mcp_client, "MCPClient", lambda url, *, name: _StrayEnter("unused"))
    client = CachedMCPClient("http://switchboard/sse", client_name="test")

    with pytest.raises(ConnectionError, match="CancelledError"):
        await client.call_tool("ingest", {"ok": True})


async def test_a_stray_while_closing_does_not_stop_the_reconnect(monkeypatch):
    class _StrayExit(_DroppingClient):
        async def __aexit__(self, *exc: object) -> None:
            await _raise_stray()

    calls: list[str] = []
    server = _server(calls)
    built: list[Any] = []

    def factory(url: str, *, name: str) -> Any:
        built.append(
            _StrayExit("Server session was closed unexpectedly")
            if not built
            else Client(server, name=name)
        )
        return built[-1]

    monkeypatch.setattr(mcp_client, "MCPClient", factory)
    client = CachedMCPClient("http://switchboard/sse", client_name="test")
    try:
        assert await client.call_tool("ingest", {"ok": True}) == {"status": "accepted"}
    finally:
        await client.aclose()

    assert calls == ["ingest"]


@pytest.mark.parametrize("stage", ["call", "connect", "close"])
async def test_a_real_cancel_still_ends_the_call(monkeypatch, stage):
    entered = asyncio.Event()

    async def block() -> None:
        entered.set()
        await asyncio.Event().wait()

    class _Blocking(_DroppingClient):
        async def __aenter__(self) -> Any:
            if stage == "connect":
                await block()
            return self

        async def __aexit__(self, *exc: object) -> None:
            if stage == "close":
                await block()

        async def call_tool(self, name: str, args: dict[str, Any]) -> Any:
            if stage == "call":
                await block()
            raise RuntimeError("Server session was closed unexpectedly")

    built: list[Any] = []

    def factory(url: str, *, name: str) -> Any:
        built.append(_Blocking("unused"))
        return built[-1]

    monkeypatch.setattr(mcp_client, "MCPClient", factory)
    client = CachedMCPClient("http://switchboard/sse", client_name="test")
    task = asyncio.create_task(client.call_tool("ingest", {"ok": True}))
    async with asyncio.timeout(5):
        await entered.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        async with asyncio.timeout(5):
            await task
    assert task.cancelled()
    # Cancelled where it stood: nothing went on to build a replacement client.
    assert len(built) == 1


async def test_a_stray_does_not_end_the_telegram_poll_loop(monkeypatch):
    """The poll loop re-raises ``CancelledError``, so a stray used to end it for good."""
    monkeypatch.setattr(mcp_client, "MCPClient", lambda url, *, name: _StrayClient())
    monkeypatch.setattr(telegram_bot, "wait_for_switchboard_ready", AsyncMock())
    connector = telegram_bot.TelegramBotConnector(
        telegram_bot.TelegramBotConnectorConfig(
            switchboard_mcp_url="http://switchboard/sse",
            provider="telegram",
            channel="telegram_bot",
            endpoint_identity="telegram:bot:test",
            telegram_token="test-token",
            poll_interval_s=0,
        ),
        cursor_pool=MagicMock(),
    )
    for name in (
        "_start_health_server",
        "_start_heartbeat",
        "_start_owner_delivery_sweep",
    ):
        monkeypatch.setattr(connector, name, MagicMock())
    for name in ("_load_checkpoint", "_connect_transcription_client", "_save_checkpoint"):
        monkeypatch.setattr(connector, name, AsyncMock())
    monkeypatch.setattr(connector, "_flush_and_drain", AsyncMock())
    connector._ingestion_policy = MagicMock(ensure_loaded=AsyncMock())
    connector._global_ingestion_policy = MagicMock(ensure_loaded=AsyncMock())

    polls = 0

    async def get_updates() -> list[dict[str, Any]]:
        nonlocal polls
        polls += 1
        if polls == 3:
            connector._running = False
        return [{"update_id": polls}]

    async def process_update(update: dict[str, Any]) -> None:
        await connector._mcp_client.call_tool("ingest", update)

    monkeypatch.setattr(connector, "_get_updates", get_updates)
    monkeypatch.setattr(connector, "_process_update", process_update)

    async with asyncio.timeout(5):
        await connector.start_polling()

    assert polls == 3
    assert connector._consecutive_failures > 0


async def test_a_stray_does_not_end_the_connector_heartbeat_loop(monkeypatch):
    """Same shape on the loop that keeps a connector registered."""
    monkeypatch.setattr(mcp_client, "MCPClient", lambda url, *, name: _StrayClient())
    built_client = CachedMCPClient("http://switchboard/sse", client_name="test")
    sends = 0
    real_call = built_client.call_tool
    two_sends = asyncio.Event()

    async def counting_call(name: str, args: dict[str, Any]) -> Any:
        nonlocal sends
        sends += 1
        if sends == 2:
            two_sends.set()
        return await real_call(name, args)

    built_client.call_tool = counting_call  # type: ignore[method-assign]
    heartbeat = ConnectorHeartbeat(
        HeartbeatConfig(
            connector_type="home_assistant",
            endpoint_identity="home_assistant:house",
            interval_s=120,
        ),
        built_client,
        MagicMock(),
        get_health_state=lambda: ("healthy", None),
    )
    heartbeat._config.interval_s = 0
    loop = asyncio.create_task(heartbeat._heartbeat_loop())
    try:
        async with asyncio.timeout(5):
            await two_sends.wait()
        assert not loop.done()
    finally:
        loop.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await loop
