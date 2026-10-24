"""Purpose-only TLS -> ASGI -> real FastMCP bridge.

The supplied operation implementation is a software boundary, not an already
implemented A2 database producer. This module never exposes ordinary/model tools.
"""

from __future__ import annotations

import asyncio
import socket
import ssl
from contextlib import AsyncExitStack, asynccontextmanager

import uvicorn
from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_request
from fastmcp.tools import ToolResult
from mcp.types import TextContent
from uvicorn.protocols.http.h11_impl import H11Protocol
from uvicorn.server import ServerState

from butlers.core.capability_json import canonical_bytes

from .config import OPERATION_SECONDS, REQUEST_LIMIT, PeerPolicy
from .protocol import PURPOSE_TOOLS, TOOLS, ProtocolError, reply, request, rpc_request, strict_json
from .tls import HeadGate, verify_peer


class PrivateApp:
    def __init__(self, domain, operation):
        self.domain, self.operation = domain, operation
        self.sentinel = object()
        self.mcp = FastMCP("private-goal-approvals")
        for op, tool in TOOLS.items():
            self._register(op, tool)
        for op, tool in PURPOSE_TOOLS.items():
            self._register(op, tool, authorized=True)
        self.app = self.mcp.http_app(path="/mcp", stateless_http=True, json_response=True)

    def _register(self, op, tool, *, authorized=False):
        async def process(envelope: dict) -> ToolResult:
            http_request = get_http_request()
            binding = http_request.scope.get("private.broker.peer")
            if (
                type(binding) is not tuple
                or len(binding) != 2
                or binding[0] is not self.sentinel
                or binding[1].role != "broker-control"
                or binding[1].domain != self.domain
            ):
                raise ProtocolError("private_peer_required")
            value = request(envelope, self.domain)
            if value["operation"] != op:
                raise ProtocolError("tool_binding")
            assertion = http_request.headers.get("x-butlers-assertion")
            if op == "PARK" and not authorized:
                if assertion is None or not 1 <= len(assertion) <= 8192:
                    raise ProtocolError("park_assertion_required")
            elif assertion is not None:
                raise ProtocolError("unexpected_assertion")
            try:
                result = reply(await self.operation(value, binding[1], assertion), value)
            except Exception:
                # FastMCP serializes/logs exception messages. Owning storage or
                # adapter errors must never become a raw private RPC payload.
                raise ProtocolError("owning_operation_refused") from None
            return ToolResult(
                content=[TextContent(type="text", text=canonical_bytes(result).decode())],
                structured_content=result,
                is_error=False,
            )

        if authorized:

            async def execute(
                protocol: str,
                operation: str,
                request_id: str,
                identity: dict,
                handoff: str,
                arguments: dict | None = None,
            ) -> ToolResult:
                value = dict(
                    protocol=protocol,
                    operation=operation,
                    request_id=request_id,
                    identity=identity,
                    handoff=handoff,
                )
                if op == "PARK":
                    value["arguments"] = arguments
                elif arguments is not None:
                    raise ProtocolError("unexpected_arguments")
                return await process(value)
        elif op == "PARK":

            async def execute(
                protocol: str,
                operation: str,
                request_id: str,
                identity: dict,
                arguments: dict,
                allow_standing_rule: bool,
            ) -> ToolResult:
                return await process(
                    dict(
                        protocol=protocol,
                        operation=operation,
                        request_id=request_id,
                        identity=identity,
                        arguments=arguments,
                        allow_standing_rule=allow_standing_rule,
                    )
                )
        else:

            async def execute(
                protocol: str, operation: str, request_id: str, identity: dict
            ) -> ToolResult:
                return await process(
                    dict(
                        protocol=protocol,
                        operation=operation,
                        request_id=request_id,
                        identity=identity,
                    )
                )

        self.mcp.tool(execute, name=tool, output_schema=None)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        body = bytearray()
        async with asyncio.timeout(10):
            while True:
                msg = await receive()
                if msg["type"] != "http.request":
                    raise ProtocolError("request_disconnected")
                body.extend(msg.get("body", b""))
                if len(body) > REQUEST_LIMIT:
                    raise ProtocolError("request_limit")
                if not msg.get("more_body", False):
                    break
            rpc_request(strict_json(bytes(body)), self.domain)

            async def body_receive():
                return {"type": "http.request", "body": bytes(body), "more_body": False}

            # Starlette's normal JSON Response supplies Content-Length. Do not
            # add a parallel HTTP business protocol or loosen the wire profile.
            await self.app(scope, body_receive, send)


class PrivateServer:
    """Explicit owned software listener; no production config/startup attachment."""

    def __init__(
        self,
        app: PrivateApp,
        context: ssl.SSLContext,
        peer: PeerPolicy,
        *,
        host="127.0.0.1",
        port=0,
    ):
        if (
            context.verify_mode != ssl.CERT_REQUIRED
            or peer.role != "broker-control"
            or not peer.client_certificate
        ):
            raise ValueError("mutual_tls_required")
        self.app, self.context, self.peer = app, context, peer
        self.host, self.port = host, port
        self.slots = asyncio.Semaphore(16)
        self.protocols = set()
        self.handshakes = set()
        self.http_bytes = 0
        self.slot_count = 0
        self._resource_changed = asyncio.Event()
        self.server_state = ServerState()
        self._lifetime_task = None
        self._cleanup_deadline = None

    def _protocol(self):
        owner = self

        class PrivateProtocol(H11Protocol):
            def __init__(self):
                super().__init__(config=owner.config, server_state=owner.server_state, app_state={})
                self.registered = False
                self.released = False
                self.gate = HeadGate(response=False)
                self.timer = None
                self.request_deadline = None
                self.body_timer = None

            def release(self):
                if not self.released:
                    self.released = True
                    owner.slots.release()
                    owner.slot_count -= 1
                    owner._resource_changed.set()

            def connection_made(self, transport):
                try:
                    peer = verify_peer(transport.get_extra_info("ssl_object"), owner.peer)
                except ProtocolError:
                    transport.abort()
                    return
                actual_app = self.app

                async def bound_app(scope, receive, send):
                    scope = dict(scope)
                    scope["private.broker.peer"] = (owner.app.sentinel, peer)
                    return await actual_app(scope, receive, send)

                self.peer = peer
                self.app = bound_app
                super().connection_made(transport)
                self.registered = True
                owner.protocols.add(self)
                self.timer = self.loop.call_later(5, transport.abort)

            def data_received(self, data):
                if not self.registered:
                    return
                if data and self.request_deadline is None:
                    # Raw prefixes have not reached H11 yet, so cancel BOTH
                    # idle handles before buffering the next request header.
                    if self.timer:
                        self.timer.cancel()
                    self._unset_keepalive_if_required()
                    self.request_deadline = self.loop.time() + OPERATION_SECONDS
                    self.timer = self.loop.call_at(self.request_deadline, self.transport.abort)
                    self.body_timer = self.loop.call_later(5, self.transport.abort)
                owner.http_bytes += len(data)
                try:
                    if (
                        verify_peer(self.transport.get_extra_info("ssl_object"), owner.peer)
                        != self.peer
                    ):
                        raise ProtocolError("peer_changed")
                    if self.gate.complete:
                        raise ProtocolError("pipelining_refused")
                    ready = self.gate.feed(data)
                    if self.gate.complete and self.body_timer:
                        self.body_timer.cancel()
                    if ready:
                        super().data_received(ready)
                except ProtocolError:
                    self.transport.abort()

            def on_response_complete(self):
                if self.timer:
                    self.timer.cancel()
                if self.body_timer:
                    self.body_timer.cancel()
                self.request_deadline = None
                self.gate = HeadGate(response=False)
                super().on_response_complete()
                self.timer = self.loop.call_later(2, self.transport.abort)

            def connection_lost(self, exc):
                if self.timer:
                    self.timer.cancel()
                if self.body_timer:
                    self.body_timer.cancel()
                if self.registered:
                    owner.protocols.discard(self)
                    super().connection_lost(exc)
                self.release()
                owner._resource_changed.set()

        return PrivateProtocol()

    async def _handshake(self, conn):
        protocol = None
        try:

            def factory():
                nonlocal protocol
                protocol = self._protocol()
                return protocol

            # Ownership transfers to the loop. Never close a transferred socket
            # or retain it as a post-TLS teardown escape.
            await asyncio.get_running_loop().connect_accepted_socket(
                factory,
                conn,
                ssl=self.context,
                ssl_handshake_timeout=5,
                ssl_shutdown_timeout=5,
            )
        except BaseException:
            if protocol is None or not protocol.released:
                if protocol is not None:
                    protocol.release()
                else:
                    self.slots.release()
                    self.slot_count -= 1
                    self._resource_changed.set()

    async def _accept(self):
        loop = asyncio.get_running_loop()
        while True:
            await self.slots.acquire()
            try:
                conn, _ = await loop.sock_accept(self.socket)
            except BaseException:
                self.slots.release()
                raise
            self.slot_count += 1
            task = asyncio.create_task(self._handshake(conn))
            self.handshakes.add(task)
            task.add_done_callback(self.handshakes.discard)

    @asynccontextmanager
    async def run(self):
        if self._lifetime_task is not None:
            raise RuntimeError("private_server_already_started")
        self.config = uvicorn.Config(self.app, log_config=None, access_log=False, lifespan="off")
        self.config.load()
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        stop = asyncio.Event()
        ready = asyncio.get_running_loop().create_future()

        async def lifetime():
            # AnyIO's MCP task group must enter and exit in this SAME task.
            lifespan = AsyncExitStack()
            accept = None
            try:
                async with asyncio.timeout(5):
                    await lifespan.enter_async_context(self.app.app.lifespan(self.app.app))
                accept = asyncio.create_task(self._accept())
                ready.set_result(None)
                await stop.wait()
            except BaseException as exc:
                if not ready.done():
                    ready.set_exception(exc)
                raise
            finally:
                self.socket.close()
                if self._cleanup_deadline is None:
                    self._cleanup_deadline = asyncio.get_running_loop().time() + 5
                deadline = self._cleanup_deadline
                for protocol in tuple(self.protocols):
                    protocol.transport.abort()
                tasks = ([accept] if accept is not None else []) + list(self.handshakes)
                tasks += list(self.server_state.tasks)
                for task in tasks:
                    task.cancel()
                failure = None
                try:
                    # Reserve the final second for the entered MCP lifespan.
                    if tasks:
                        _, pending = await asyncio.wait(
                            tasks, timeout=max(0, deadline - 1 - asyncio.get_running_loop().time())
                        )
                        if pending:
                            raise TimeoutError("private_request_cleanup_incomplete")
                    async with asyncio.timeout_at(deadline - 1):
                        while self.protocols or self.slot_count:
                            # Predicate and clear run without yielding, so a
                            # native release callback cannot be lost between
                            # observing actual resources and awaiting change.
                            self._resource_changed.clear()
                            await self._resource_changed.wait()
                except BaseException as exc:
                    failure = exc
                finally:
                    # Attempt actual lifespan exit even when task/slot cleanup
                    # failed; no extra grace and no second-task scope exit.
                    try:
                        async with asyncio.timeout_at(deadline):
                            await lifespan.aclose()
                    except BaseException as exc:
                        if failure is None:
                            failure = exc
                if failure is not None:
                    raise failure

        failure = None
        try:
            self.socket.setblocking(False)
            self.socket.bind((self.host, self.port))
            self.socket.listen(16)
            self.port = self.socket.getsockname()[1]
            self._lifetime_task = asyncio.create_task(lifetime())
            async with asyncio.timeout(5):
                await asyncio.shield(ready)
            yield self
        except BaseException as exc:
            failure = exc
        finally:
            self.socket.close()  # also covers bind and pre-lifespan failures
            if self._cleanup_deadline is None:
                self._cleanup_deadline = asyncio.get_running_loop().time() + 5
            stop.set()
            owned = self._lifetime_task
            if owned is not None:
                if not ready.done():
                    owned.cancel()  # interrupt partial entry in its own task
                # One retained wait, one absolute budget. Repeated external
                # cancellation cannot refresh grace or abandon ownership.
                waiter = asyncio.create_task(
                    asyncio.wait(
                        [owned],
                        timeout=max(0, self._cleanup_deadline - asyncio.get_running_loop().time()),
                    )
                )
                while not waiter.done():
                    try:
                        await asyncio.shield(waiter)
                    except asyncio.CancelledError as exc:
                        if failure is None:
                            failure = exc
                _, pending = waiter.result()
                if pending:
                    for protocol in tuple(self.protocols):
                        protocol.transport.abort()
                    cleanup_error = TimeoutError("private_lifespan_cleanup_incomplete")
                    # Keep the actual lifetime task retained for diagnosis;
                    # timeout is not successful task/FD/lifespan settlement.
                else:
                    try:
                        owned.result()
                        cleanup_error = None
                    except BaseException as exc:
                        cleanup_error = exc
                if cleanup_error is not None:
                    if failure is None:
                        failure = cleanup_error
                    else:
                        failure.add_note("private_cleanup_failed:" + type(cleanup_error).__name__)
            if ready.done() and not ready.cancelled():
                ready.exception()  # retrieve a failed entry even after cancellation
        if failure is not None:
            raise failure


def build_server(config, factory):
    if config is None:
        return None
    return factory(config)
