"""A call or connect that gets no answer ends at its deadline (bu-dhloi1.132).

The frozen-server tests are the ones that matter: a real FastMCP server in its own process
is stopped with SIGSTOP, so its sockets stay open and nothing answers. That is what a stalled
Switchboard looks like, and on SSE it held a caller for about ten minutes before this.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import socket
import sys
import textwrap
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from butlers.connectors import mcp_client
from butlers.connectors.mcp_client import (
    DEFAULT_CALL_TIMEOUT_S,
    DEFAULT_CONNECT_TIMEOUT_S,
    CachedMCPClient,
    ToolCallTimeoutError,
)
from butlers.connectors.metrics import get_error_type

pytestmark = pytest.mark.unit

_SERVER = textwrap.dedent(
    """
    import sys
    from fastmcp import FastMCP

    server = FastMCP("switchboard-stub")

    @server.tool
    def ingest(ok: bool) -> dict:
        return {"status": "accepted"}

    server.run(transport=sys.argv[1], host="127.0.0.1", port=int(sys.argv[2]), show_banner=False)
    """
)
# Cleanup is fastmcp's own disconnect bound; these leave room for it on a loaded runner.
_DEADLINE_S = 1.0
_END_WITHIN_S = 25.0


class _Server:
    def __init__(self, process: asyncio.subprocess.Process, url: str) -> None:
        self.process = process
        self.url = url

    def freeze(self) -> None:
        self.process.send_signal(signal.SIGSTOP)

    def thaw(self) -> None:
        self.process.send_signal(signal.SIGCONT)


@pytest.fixture(params=["sse", "http"])
async def server(request: pytest.FixtureRequest) -> AsyncIterator[_Server]:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        _SERVER,
        request.param,
        str(port),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    path = "sse" if request.param == "sse" else "mcp"
    try:
        async with httpx.AsyncClient(timeout=1) as http, asyncio.timeout(30):
            while True:
                try:
                    await http.get(f"http://127.0.0.1:{port}/health")
                    break
                except httpx.RequestError:
                    await asyncio.sleep(0.05)
        yield _Server(process, f"http://127.0.0.1:{port}/{path}")
    finally:
        process.kill()
        await process.wait()


async def test_a_frozen_server_ends_the_call_at_its_deadline(server: _Server, caplog) -> None:
    client = CachedMCPClient(
        server.url,
        client_name="test",
        call_timeout_s=_DEADLINE_S,
        connect_timeout_s=_DEADLINE_S,
    )
    assert await client.call_tool("ingest", {"ok": True}) == {"status": "accepted"}

    server.freeze()
    try:
        started = time.monotonic()
        with caplog.at_level(logging.WARNING, logger=mcp_client.logger.name):
            with pytest.raises(ToolCallTimeoutError, match="outcome unknown") as excinfo:
                async with asyncio.timeout(_END_WITHIN_S):
                    await client.call_tool("ingest", {"ok": True})
        assert _DEADLINE_S <= time.monotonic() - started < _END_WITHIN_S
        assert isinstance(excinfo.value, ConnectionError)
        assert "outcome unknown" in caplog.text
        assert "not ingested" not in str(excinfo.value)

        # The dead session was dropped, so the next caller fails at the connect bound.
        started = time.monotonic()
        with pytest.raises(ConnectionError, match="Failed to connect"):
            async with asyncio.timeout(_END_WITHIN_S):
                await client.call_tool("ingest", {"ok": True})
        assert time.monotonic() - started < _END_WITHIN_S
    finally:
        server.thaw()

    # And the client recovers once the server does, with no restart.
    async with asyncio.timeout(_END_WITHIN_S):
        assert await client.call_tool("ingest", {"ok": True}) == {"status": "accepted"}
    await client.aclose()


class _Fake:
    """Stand-in client: ``hangs`` calls never answer, then calls raise ``error`` or answer."""

    def __init__(self, *, hangs: int = 0, enter_hangs: bool = False, error: Any = None) -> None:
        self.hangs = hangs
        self.enter_hangs = enter_hangs
        self.error = error
        self.calls = 0
        self.closed = 0

    async def __aenter__(self) -> _Fake:
        if self.enter_hangs:
            await asyncio.Event().wait()
        return self

    async def __aexit__(self, *exc: object) -> None:
        self.closed += 1

    def is_connected(self) -> bool:
        return True

    async def call_tool(self, name: str, args: dict[str, Any]) -> Any:
        self.calls += 1
        if self.hangs:
            self.hangs -= 1
            await asyncio.Event().wait()
        if self.error is not None:
            raise self.error
        return [_Text('{"status": "accepted"}')]


class _Text:
    def __init__(self, text: str) -> None:
        self.text = text


def _client(monkeypatch, *fakes: _Fake, call: float = 0.05, connect: float = 0.05):
    pending = list(fakes)
    monkeypatch.setattr(mcp_client, "MCPClient", lambda url, *, name, **_kwargs: pending.pop(0))
    return CachedMCPClient(
        "http://switchboard/sse", client_name="test", call_timeout_s=call, connect_timeout_s=connect
    )


def test_the_default_bounds() -> None:
    client = CachedMCPClient("http://switchboard/sse")
    assert client._call_timeout_s == DEFAULT_CALL_TIMEOUT_S == 180.0
    assert client._connect_timeout_s == DEFAULT_CONNECT_TIMEOUT_S == 30.0


def test_a_timeout_is_labelled_timeout_in_the_error_metric() -> None:
    assert get_error_type(ToolCallTimeoutError("x")) == "timeout"


async def test_a_timeout_is_not_retried_and_the_next_call_reconnects(monkeypatch) -> None:
    hung, fresh = _Fake(hangs=1), _Fake()
    client = _client(monkeypatch, hung, fresh)

    with pytest.raises(ToolCallTimeoutError):
        await client.call_tool("ingest", {})

    assert hung.calls == 1
    assert hung.closed == 1
    assert fresh.calls == 0

    assert await client.call_tool("ingest", {}) == {"status": "accepted"}
    assert fresh.calls == 1


async def test_a_timeout_never_surfaces_as_a_bare_timeout_error(monkeypatch) -> None:
    """Connector holds catch ``ConnectionError``; a bare ``TimeoutError`` would pass them by."""

    class _HangingClose(_Fake):
        async def __aexit__(self, *exc: object) -> None:
            await asyncio.Event().wait()

    hung, fresh = _HangingClose(hangs=1), _Fake()
    client = _client(monkeypatch, hung, fresh)

    with pytest.raises(ConnectionError) as excinfo:
        async with asyncio.timeout(5):
            await client.call_tool("ingest", {})

    assert isinstance(excinfo.value, ToolCallTimeoutError)
    assert not isinstance(excinfo.value, TimeoutError)
    # The overrun close still cleared the slot, so the next call reconnects.
    assert await client.call_tool("ingest", {}) == {"status": "accepted"}
    assert fresh.calls == 1


async def test_a_connect_timeout_never_surfaces_as_a_bare_timeout_error(monkeypatch) -> None:
    client = _client(monkeypatch, _Fake(enter_hangs=True))

    with pytest.raises(ConnectionError) as excinfo:
        async with asyncio.timeout(5):
            await client.call_tool("ingest", {})

    assert not isinstance(excinfo.value, TimeoutError)


async def test_a_timeout_does_not_close_a_client_already_replaced(monkeypatch) -> None:
    hung, replacement = _Fake(hangs=1), _Fake()
    client = _client(monkeypatch, hung, replacement, call=0.2)
    call = asyncio.create_task(client.call_tool("ingest", {}))
    async with asyncio.timeout(5):
        while not hung.calls:  # noqa: ASYNC110 - reads a plain counter
            await asyncio.sleep(0)
    async with client._lock:
        await client._connect()

    with pytest.raises(ToolCallTimeoutError):
        await call

    assert replacement.closed == 0
    assert client._client is replacement


async def test_a_timeout_on_the_reconnect_attempt_is_still_unknown(monkeypatch) -> None:
    dropped = _Fake(error=RuntimeError("Server session was closed unexpectedly"))
    hung = _Fake(hangs=1)
    client = _client(monkeypatch, dropped, hung)

    with pytest.raises(ToolCallTimeoutError, match="outcome unknown"):
        await client.call_tool("ingest", {})

    assert (dropped.calls, hung.calls) == (1, 1)


async def test_a_library_timeout_inside_the_deadline_still_earns_the_reconnect(monkeypatch) -> None:
    """Only our deadline means "stop"; a read timeout from below is an ordinary drop."""
    timed_out, fresh = _Fake(error=TimeoutError("read timed out")), _Fake()
    client = _client(monkeypatch, timed_out, fresh, call=5)

    assert await client.call_tool("ingest", {}) == {"status": "accepted"}

    assert (timed_out.calls, fresh.calls) == (1, 1)


async def test_connect_is_bounded_and_says_so(monkeypatch) -> None:
    client = _client(monkeypatch, _Fake(enter_hangs=True))

    with pytest.raises(ConnectionError, match="Failed to connect.*TimeoutError"):
        async with asyncio.timeout(5):
            await client.call_tool("ingest", {})


async def test_a_real_cancel_during_the_deadline_stays_a_cancel(monkeypatch) -> None:
    hung = _Fake(hangs=1)
    client = _client(monkeypatch, hung, call=60)
    call = asyncio.create_task(client.call_tool("ingest", {}))
    async with asyncio.timeout(5):
        while not hung.calls:  # noqa: ASYNC110 - reads a plain counter
            await asyncio.sleep(0)

    call.cancel()

    with pytest.raises(asyncio.CancelledError):
        await call
    assert hung.closed == 0


async def test_the_initialize_handshake_gets_the_connect_deadline(monkeypatch) -> None:
    """fastmcp's ``initialize`` has no deadline by default (bu-dhloi1.246); the client passes
    its own connect deadline so the handshake ends and cleans up by itself."""
    seen: dict[str, Any] = {}

    class _Stub:
        async def __aenter__(self) -> _Stub:
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

    def factory(url: str, *, name: str, **kwargs: Any) -> _Stub:
        seen.update(kwargs)
        return _Stub()

    monkeypatch.setattr(mcp_client, "MCPClient", factory)
    client = CachedMCPClient("http://switchboard/sse", connect_timeout_s=7.0)
    await client._connect()

    assert seen == {"init_timeout": 7.0}
