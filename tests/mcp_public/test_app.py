"""The ASGI transport in front of the host (REQ-public-mcp-surface-001; RFC 0039 D1, D6, D16).

Requests are driven through the raw ASGI interface so the tests can count every ``receive``
the app makes: the hop bearer is checked before one body byte is read.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from butlers.capability_host import MCP_PATH, HostResponse
from butlers.mcp_public.app import McpApp, MetricsApp
from butlers.mcp_public.catalogue import STATIC_HANDLERS, tools_list_result
from tests.capability_host.helpers import BEARER, Env, call_body, claims, rpc, sign

pytestmark = pytest.mark.unit


class Exchange:
    """One ASGI request: the messages the app pulled and the response it sent."""

    def __init__(self, body: bytes, chunk: int | None = None):
        size = chunk or max(len(body), 1)
        parts = [body[i : i + size] for i in range(0, len(body), size)] or [b""]
        self.inbox = [
            {"type": "http.request", "body": p, "more_body": i < len(parts) - 1}
            for i, p in enumerate(parts)
        ]
        self.receives = 0
        self.sent: list[dict[str, Any]] = []

    async def receive(self) -> dict[str, Any]:
        self.receives += 1
        return self.inbox.pop(0) if self.inbox else {"type": "http.disconnect"}

    async def send(self, message: dict[str, Any]) -> None:
        self.sent.append(message)

    @property
    def status(self) -> int:
        return self.sent[0]["status"]

    @property
    def headers(self) -> dict[str, str]:
        return {k.decode(): v.decode() for k, v in self.sent[0]["headers"]}

    @property
    def body(self) -> bytes:
        return b"".join(m.get("body", b"") for m in self.sent[1:])


def _scope(path: str, method: str, headers: list[tuple[str, str]], query: str = "") -> dict:
    return {
        "type": "http",
        "method": method,
        "path": path,
        "query_string": query.encode(),
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers],
    }


async def _request(
    app,
    body: bytes,
    *,
    path: str = MCP_PATH,
    method: str = "POST",
    bearer: str | None = BEARER,
    assertion: bool = True,
    chunk: int | None = None,
    query: str = "",
) -> Exchange:
    headers = [("content-type", "application/json")]
    if bearer is not None:
        headers.append(("authorization", f"Bearer {bearer}"))
    if assertion:
        headers.append(("x-mcp-assertion", sign(claims(body, path=query))))
    exchange = Exchange(body, chunk)
    await app(_scope(path, method, headers, query), exchange.receive, exchange.send)
    return exchange


@pytest.fixture
def env(tmp_path) -> Env:
    return Env(tmp_path, static=STATIC_HANDLERS)


async def test_no_bearer_is_refused_before_the_body_is_read(env: Env):
    exchange = await _request(McpApp(env.host), call_body("entity_lookup"), bearer=None)
    assert exchange.status == 401 and exchange.body == b""
    assert exchange.receives == 0 and env.calls == [] and env.projection.acquired == 0


async def test_a_wrong_bearer_is_refused_before_the_body_is_read(env: Env):
    exchange = await _request(McpApp(env.host), rpc("tools/list"), bearer="guessed")
    assert exchange.status == 401 and exchange.receives == 0


async def test_initialize_declares_exactly_tools_through_the_host(env: Env):
    body = rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}})
    exchange = await _request(McpApp(env.host), body, assertion=False)
    assert exchange.status == 200 and exchange.headers["content-type"] == "application/json"
    result = json.loads(exchange.body)["result"]
    assert result["capabilities"] == {"tools": {"listChanged": False}}
    assert env.projection.acquired == 0


async def test_tools_list_is_byte_identical_whatever_the_switches(env: Env):
    app = McpApp(env.host)
    first = await _request(app, rpc("tools/list"), assertion=False)
    env.write("switches/disabled_domains", "finance\n")
    env.reload()
    env.projection.disabled = ["health"]
    second = await _request(app, rpc("tools/list"))  # asserted: a different caller path
    assert first.body == second.body
    assert json.loads(first.body)["result"] == json.loads(tools_list_result())
    assert env.projection.acquired == 0


async def test_a_tool_call_runs_through_the_host(env: Env):
    exchange = await _request(McpApp(env.host), call_body("entity_lookup", {"q": "x"}))
    result = json.loads(exchange.body)["result"]
    assert result["isError"] is False and env.projection.acquired == 1
    assert json.loads(env.lines[-1])["tool"] == "entity_lookup"


async def test_a_chunked_body_is_reassembled(env: Env):
    exchange = await _request(McpApp(env.host), call_body("entity_lookup"), chunk=7)
    assert json.loads(exchange.body)["result"]["isError"] is False


async def test_an_oversized_streamed_body_stops_being_read_past_the_limit(env: Env):
    body = rpc("ping", {"pad": "x" * (2 * 1024 * 1024)})
    exchange = await _request(McpApp(env.host), body, assertion=False, chunk=64 * 1024)
    assert json.loads(exchange.body)["error"]["data"] == {"reason": "unauthorized"}
    assert exchange.receives <= 1024 * 1024 // (64 * 1024) + 2  # never the whole 2 MiB
    assert json.loads(env.lines[-1])["cause"] == "size"


async def test_a_notification_is_accepted_with_no_body(env: Env):
    body = json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}).encode()
    exchange = await _request(McpApp(env.host), body, assertion=False)
    assert exchange.status == 202 and exchange.body == b""


@pytest.mark.parametrize("method", ["GET", "DELETE"])
async def test_no_sse_stream_and_no_session_to_delete(env: Env, method: str):
    exchange = await _request(McpApp(env.host), b"", method=method, assertion=False)
    assert exchange.status == 405 and exchange.headers["allow"] == "POST"
    assert "mcp-session-id" not in exchange.headers


@pytest.mark.parametrize("path", ["/", "/mcp/", "/metrics", "/mcp/x"])
async def test_other_paths_are_not_found_with_nothing_read(env: Env, path: str):
    exchange = await _request(McpApp(env.host), rpc("tools/list"), path=path)
    assert exchange.status == 404 and exchange.receives == 0


async def test_a_host_defect_fails_closed_without_request_content(env: Env, caplog):
    async def broken(*_args, **_kwargs) -> HostResponse:
        raise RuntimeError("secret-bearing detail " + BEARER)

    env.host.handle = broken  # type: ignore[method-assign]
    exchange = await _request(McpApp(env.host), rpc("tools/list"))
    assert exchange.status == 500 and exchange.body == b""
    assert BEARER not in caplog.text and "RuntimeError" in caplog.text


async def test_the_lifespan_protocol_completes():
    exchange = Exchange(b"")
    exchange.inbox = [{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}]
    await McpApp(None)({"type": "lifespan"}, exchange.receive, exchange.send)  # type: ignore[arg-type]
    assert [m["type"] for m in exchange.sent] == [
        "lifespan.startup.complete",
        "lifespan.shutdown.complete",
    ]


async def test_metrics_render_the_host_counters(env: Env):
    await _request(McpApp(env.host), rpc("tools/list"), bearer=None)
    exchange = await _request(MetricsApp(env.host), b"", path="/metrics", method="GET")
    assert exchange.status == 200
    assert exchange.headers["content-type"].startswith("text/plain; version=0.0.4")
    assert b"butlers_public_mcp_bearer_refusals_total 1" in exchange.body


async def test_metrics_port_health_and_refusals(env: Env):
    app = MetricsApp(env.host)
    assert (await _request(app, b"", path="/healthz", method="GET")).body == b"ok\n"
    assert (await _request(app, b"", path="/mcp", method="GET")).status == 404
    assert (await _request(app, b"", path="/metrics", method="POST")).status == 405
    assert env.projection.acquired == 0
