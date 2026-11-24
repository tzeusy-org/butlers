"""The two ASGI apps: MCP on ``MCP_PATH`` and metrics (RFC 0039 D1, D6, D16).

The MCP app is a raw ASGI callable, not FastMCP's transport: FastMCP's stateless-HTTP app
reads and dispatches the JSON-RPC body before any hook could check the hop bearer, and its
server declares prompt and resource capabilities. Here :meth:`CapabilityHost.handle` sees
every request first and pulls the body through ``read_body`` only after the bearer passes,
so no byte of an unauthenticated body is ever read. The wire shapes come from the MCP SDK
(:mod:`butlers.mcp_public.catalogue`); responses are JSON only, with no SSE stream and no
session id.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

from butlers.capability_host import MCP_PATH, CapabilityHost, HostResponse

logger = logging.getLogger(__name__)

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]

METRICS_PATH = "/metrics"
HEALTH_PATH = "/healthz"
_METRICS_TYPE = "text/plain; version=0.0.4; charset=utf-8"


async def _respond(send: Send, response: HostResponse) -> None:
    headers = [(k.encode("latin-1"), v.encode("latin-1")) for k, v in response.headers]
    headers.append((b"content-length", str(len(response.body)).encode()))
    await send({"type": "http.response.start", "status": response.status, "headers": headers})
    await send({"type": "http.response.body", "body": response.body})


async def _lifespan(receive: Receive, send: Send) -> None:
    while True:
        message = await receive()
        if message["type"] == "lifespan.startup":
            await send({"type": "lifespan.startup.complete"})
        elif message["type"] == "lifespan.shutdown":
            await send({"type": "lifespan.shutdown.complete"})
            return


def _body_reader(receive: Receive) -> Callable[[int], Awaitable[bytes]]:
    """Read the request body, stopping as soon as it exceeds ``limit`` bytes."""

    async def read_body(limit: int) -> bytes:
        chunks: list[bytes] = []
        size = 0
        while size <= limit:
            message = await receive()
            if message["type"] != "http.request":
                break  # http.disconnect: the partial body fails the strict parse
            chunk = message.get("body", b"")
            chunks.append(chunk)
            size += len(chunk)
            if not message.get("more_body", False):
                break
        return b"".join(chunks)

    return read_body


class McpApp:
    """``POST MCP_PATH`` through the host; every other path is 404 with nothing read."""

    def __init__(self, host: CapabilityHost):
        self.host = host

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await _lifespan(receive, send)
            return
        if scope["type"] != "http":
            return  # websocket: closed unanswered
        if scope["path"] != MCP_PATH:
            await _respond(send, HostResponse(404))
            return
        headers = [(k.decode("latin-1"), v.decode("latin-1")) for k, v in scope["headers"]]
        try:
            response = await self.host.handle(
                scope["method"],
                headers,
                scope.get("query_string", b"").decode("latin-1"),
                _body_reader(receive),
            )
        except Exception as exc:  # a host defect fails closed, with no request content logged
            logger.error("public MCP host failed: %s", type(exc).__name__)
            response = HostResponse(500)
        await _respond(send, response)


class MetricsApp:
    """``GET /metrics`` (Prometheus text) and ``GET /healthz``; neither touches the pool."""

    def __init__(self, host: CapabilityHost):
        self.host = host

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await _lifespan(receive, send)
            return
        if scope["type"] != "http":
            return
        if scope["method"] not in ("GET", "HEAD"):
            response = HostResponse(405, headers=(("allow", "GET"),))
        elif scope["path"] == METRICS_PATH:
            body = self.host.metrics.render().encode()
            response = HostResponse(200, body, (("content-type", _METRICS_TYPE),))
        elif scope["path"] == HEALTH_PATH:
            response = HostResponse(200, b"ok\n", (("content-type", "text/plain"),))
        else:
            response = HostResponse(404)
        await _respond(send, response)
