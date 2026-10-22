"""Fixed private MCP client; no owning commit or central ACK is implemented here."""

from __future__ import annotations

import asyncio
import ssl
import time
from urllib.parse import urlsplit

import httpcore
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from .config import OPERATION_SECONDS, REPLY_LIMIT, REQUEST_LIMIT, TransportConfig
from .protocol import TOOLS, ProtocolError, reply, request, rpc_request, rpc_result, strict_json
from .tls import PurposeBackend


class PrivateHTTPTransport(httpx.AsyncBaseTransport):
    def __init__(self, config: TransportConfig, context: ssl.SSLContext):
        self.config = config
        target = urlsplit(config.endpoint)
        self.backend = PurposeBackend(target.hostname, target.port, config.peer)
        self.pool = httpcore.AsyncConnectionPool(
            ssl_context=context,
            network_backend=self.backend,
            max_connections=4,
            max_keepalive_connections=4,
            http1=True,
            http2=False,
            retries=0,
        )

    def abort(self):
        for stream in tuple(self.backend.streams):
            stream.abort()

    async def handle_async_request(self, req):
        try:
            if str(req.url) != self.config.endpoint or req.method != "POST":
                raise ProtocolError("endpoint_binding")
            body = bytearray()
            async for chunk in req.stream:
                body.extend(chunk)
                if len(body) > REQUEST_LIMIT:
                    raise ProtocolError("request_limit")
            rpc = rpc_request(strict_json(bytes(body)), self.config.peer.domain)
            notification = rpc["method"] == "notifications/initialized"
            # Do not forward caller sni_hostname/trace/proxy transport extensions.
            headers = [
                (k, v)
                for k, v in req.headers.raw
                if k.lower() not in {b"transfer-encoding", b"content-length"}
            ]
            headers.append((b"Content-Length", str(len(body)).encode()))
            core_req = httpcore.Request(
                req.method,
                httpcore.URL(
                    scheme=req.url.raw_scheme,
                    host=req.url.raw_host,
                    port=req.url.port,
                    target=req.url.raw_path,
                ),
                headers=headers,
                content=bytes(body),
                extensions={
                    "timeout": {
                        k: self.backend.remaining(cap)
                        for k, cap in {"connect": 3, "pool": 1, "read": 9, "write": 3}.items()
                    }
                },
            )
            response = await self.pool.handle_async_request(core_req)
            try:
                raw = bytearray()
                async for chunk in response.stream:
                    raw.extend(chunk)
                    if len(raw) > REPLY_LIMIT:
                        raise ProtocolError("reply_limit")
                if notification:
                    if response.status != 202 or raw:
                        raise ProtocolError("notification_response")
                else:
                    if response.status != 200:
                        raise ProtocolError("http_status")
                    rpc_result(bytes(raw), rpc, self.config.peer.domain)
                return httpx.Response(response.status, headers=response.headers, content=bytes(raw))
            finally:
                await response.aclose()
        except BaseException:
            self.abort()
            raise

    async def aclose(self):
        streams = tuple(self.backend.streams)
        self.abort()
        previous = self.backend.close_deadline
        self.backend.close_deadline = self.backend.close_bound()
        try:
            async with asyncio.timeout_at(self.backend.close_deadline):
                await self.pool.aclose()
                # Pool membership is not FD settlement. Retain and await every
                # actual owned stream even if pool cleanup already removed it.
                await asyncio.gather(*(stream.aclose() for stream in streams))
        finally:
            for stream in tuple(self.backend.streams):
                stream.abort()
            self.backend.close_deadline = previous


class PrivateClient:
    def __init__(self, config: TransportConfig, context: ssl.SSLContext):
        self.config = config
        self.transport = PrivateHTTPTransport(config, context)
        self.http = httpx.AsyncClient(
            transport=self.transport,
            follow_redirects=False,
            trust_env=False,
            timeout=httpx.Timeout(9, connect=3, write=3, pool=1),
        )
        self.lock = asyncio.Lock()

    async def call(self, value, *, assertion: str | None = None):
        deadline = time.monotonic() + OPERATION_SECONDS
        # Snapshot caller values before any await, including lock acquisition.
        from butlers.core.capability_json import canonical_bytes

        encoded = canonical_bytes(value)
        if len(encoded) > REQUEST_LIMIT:
            raise ProtocolError("request_limit")
        value = request(strict_json(encoded), self.config.peer.domain)
        if value["operation"] == "PARK":
            if (
                type(assertion) is not str
                or not 1 <= len(assertion) <= 8192
                or "\n" in assertion
                or "\r" in assertion
            ):
                raise ProtocolError("park_assertion_required")
        elif assertion is not None:
            raise ProtocolError("unexpected_assertion")
        async with asyncio.timeout_at(deadline):
            async with asyncio.timeout_at(deadline - 1):
                await self.lock.acquire()
            try:
                self.transport.backend.deadline = deadline
                self.transport.backend.active = True
                if assertion is not None:
                    self.http.headers["X-Butlers-Assertion"] = assertion
                else:
                    self.http.headers.pop("X-Butlers-Assertion", None)
                async with (
                    asyncio.timeout_at(deadline - 1),
                    streamable_http_client(
                        self.config.endpoint, http_client=self.http, terminate_on_close=False
                    ) as (read, write, _),
                    ClientSession(read, write) as session,
                ):
                    await session.initialize()
                    await session.list_tools()
                    result = await session.call_tool(TOOLS[value["operation"]], value)
                    return reply(result.structuredContent, value)
            except BaseException:
                self.transport.abort()
                cleanup = asyncio.create_task(self.transport.aclose())
                # This task owns the lock/resources until finite cleanup ends.
                # A second caller cancellation cannot abandon its close task.
                while not cleanup.done():
                    try:
                        await asyncio.shield(cleanup)
                    except asyncio.CancelledError:
                        continue
                    except Exception:
                        break
                try:
                    cleanup.result()
                except BaseException:
                    self.transport.abort()
                raise
            finally:
                self.http.headers.pop("X-Butlers-Assertion", None)
                self.transport.backend.active = False
                self.lock.release()

    async def aclose(self):
        # Do not race an active call's owner or refresh a budget after waiting.
        deadline = time.monotonic() + 5
        async with asyncio.timeout_at(deadline):
            async with self.lock:
                previous = self.transport.backend.close_deadline
                self.transport.backend.close_deadline = deadline
                try:
                    await self.http.aclose()
                finally:
                    self.transport.backend.close_deadline = previous


def build_client(config: TransportConfig | None, context_factory):
    """Dark path returns before material lookup or client/SSL construction."""
    if config is None:
        return None
    return PrivateClient(config, context_factory())
