"""A call on a dead or frozen MCP server fails in bounded time (bu-dhloi1.147).

A raw ``fastmcp`` client waits for a reply forever: after ``kill -9`` of the server, or with
it frozen, a call in flight hung past 330s and 620s in measurement. No blanket deadline fits,
because ``route`` waits on real work. ``LivenessClient`` instead pings the session while a call
is in flight, and ends the call when the server stops answering. A server doing slow work still
answers pings, so a long call is left alone.

These run a real fastmcp server in a subprocess and kill or freeze it. No Docker needed.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import socket
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from butlers.mcp_liveness import LivenessClient, ServerUnresponsive

pytestmark = pytest.mark.unit

_SERVER = textwrap.dedent(
    """
    import asyncio, sys
    from fastmcp import FastMCP

    mcp = FastMCP("liveness-test")

    @mcp.tool
    async def slow(seconds: float) -> str:
        await asyncio.sleep(seconds)
        return "done"

    @mcp.tool
    async def quick() -> str:
        return "ok"

    mcp.run(transport="http", host="127.0.0.1", port=int(sys.argv[1]), show_banner=False)
    """
)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def server(tmp_path: Path):
    script = tmp_path / "server.py"
    script.write_text(_SERVER)
    port = _free_port()
    process = subprocess.Popen(  # noqa: S603
        [sys.executable, str(script), str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.1)
    else:
        process.kill()
        pytest.fail("test MCP server did not start")
    try:
        yield process, f"http://127.0.0.1:{port}/mcp"
    finally:
        try:
            os.kill(process.pid, signal.SIGCONT)
        except ProcessLookupError:
            pass
        process.kill()
        process.wait(timeout=10)


def _client(url: str) -> LivenessClient:
    return LivenessClient(url, ping_interval_s=0.3, ping_timeout_s=0.3, max_missed_pings=3)


async def test_a_call_in_flight_ends_when_the_server_is_killed(server) -> None:
    process, url = server
    client = _client(url)
    await client.__aenter__()
    try:
        call = asyncio.create_task(client.call_tool("slow", {"seconds": 60}))
        await asyncio.sleep(0.5)
        process.kill()

        started = time.monotonic()
        # The ping's own failure usually ends the call with the transport's error first.
        with pytest.raises(Exception) as raised:  # noqa: B017, PT011
            await asyncio.wait_for(call, timeout=15)

        assert not isinstance(raised.value, TimeoutError)
        assert time.monotonic() - started < 10
    finally:
        with contextlib.suppress(Exception):  # closing a session to a dead server fails
            await client.__aexit__(None, None, None)


async def test_a_call_in_flight_ends_when_the_server_freezes(server) -> None:
    process, url = server
    async with _client(url) as client:
        call = asyncio.create_task(client.call_tool("slow", {"seconds": 60}))
        await asyncio.sleep(0.5)
        os.kill(process.pid, signal.SIGSTOP)

        started = time.monotonic()
        with pytest.raises(ServerUnresponsive):
            await asyncio.wait_for(call, timeout=15)

        assert time.monotonic() - started < 10


async def test_a_slow_call_on_a_live_server_is_left_alone(server) -> None:
    """The point of pinging instead of a deadline: real work that outlasts any blanket number."""
    _, url = server
    async with _client(url) as client:
        result = await client.call_tool("slow", {"seconds": 3})  # ten ping intervals

    assert result.data == "done"


async def test_a_quick_call_returns_normally(server) -> None:
    _, url = server
    async with _client(url) as client:
        assert (await client.call_tool("quick", {})).data == "ok"


async def test_cancelling_the_caller_cancels_the_call_and_stops_pinging(server) -> None:
    _, url = server
    async with _client(url) as client:
        call = asyncio.create_task(client.call_tool("slow", {"seconds": 60}))
        await asyncio.sleep(0.5)
        call.cancel()
        with pytest.raises(asyncio.CancelledError):
            await call
        # The session is still usable and nothing was left running.
        assert (await client.call_tool("quick", {})).data == "ok"
    assert not [t for t in asyncio.all_tasks() if "liveness" in (t.get_name() or "")]


def test_the_daemon_to_daemon_call_sites_use_the_liveness_client() -> None:
    """A raw ``fastmcp.Client`` at one of these sites would hang on a dead peer again."""
    from butlers import switchboard_wiring
    from butlers.api import deps
    from butlers.capability_broker import forwarder
    from butlers.roster_imports import roster_module

    route = roster_module("switchboard", "tools/routing/route")

    assert switchboard_wiring.MCPClient is LivenessClient
    assert deps.MCPClient is LivenessClient
    assert forwarder.Client is LivenessClient
    assert route.MCPClient is LivenessClient
