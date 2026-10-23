"""Private CLI-auth mTLS records over the EXISTING control service listener.

WebSocket carries opaque TLS records, not authority or credential JSON. Python's
SSLObject owns the actual handshake and certificate; no header asserts a peer.
Plain HTTP MCP/service callers keep their existing listener and behavior. No
additional control socket or TLS-terminating proxy is required.
"""

from __future__ import annotations

import asyncio
import json
import os
import ssl
import stat
from functools import lru_cache
from pathlib import Path

from butlers.cli_auth.sandbox import SandboxUnavailableError
from butlers.session_host.auth import (
    AUTH_CONTROL_IDENTITY,
    DASHBOARD_AUTH_IDENTITY,
    AuthEndpoint,
    AuthService,
    RemoteAuthSandbox,
)
from butlers.session_host.control import HOST_IDENTITY, TLSFiles, _unique_object, require_peer
from butlers.session_host.launch import Refused

SERVICE_PATH = "/internal/session-host/cli-auth"
_WIRE_LIMIT = 8 * 1024 * 1024
_RECORD_CHUNK = 32768
_CONTROL_CONFIG = Path("/etc/session-host/control-auth.json")
_DASHBOARD_CONFIG = Path("/etc/session-host/dashboard-auth.json")


def _enabled():
    value = os.environ.get("BUTLERS_SESSION_HOST_CLI_AUTH", "0")
    if value not in {"0", "1"}:
        raise Refused("invalid_control_config")
    return value == "1"


def _configuration(path):
    descriptor = None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != 0o444
            or metadata.st_nlink != 1
            or not 0 < metadata.st_size <= 8192
        ):
            raise ValueError
        content = os.read(descriptor, 8193)
        after = os.fstat(descriptor)
        if (metadata.st_ino, metadata.st_mtime_ns, metadata.st_size) != (
            after.st_ino,
            after.st_mtime_ns,
            after.st_size,
        ) or len(content) != metadata.st_size:
            raise ValueError
        value = json.loads(content, object_pairs_hook=_unique_object)
        if type(value) is not dict:
            raise ValueError
        return value
    except (OSError, ValueError, UnicodeError, RecursionError):
        raise Refused("invalid_control_config") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _tls(value):
    if (
        type(value) is not dict
        or set(value) != {"ca", "certificate", "private_key"}
        or any(type(v) is not str or not Path(v).is_absolute() for v in value.values())
    ):
        raise Refused("invalid_control_config")
    return TLSFiles(**{k: Path(v) for k, v in value.items()})


def _endpoint(value, *, service):
    if type(value) is not dict or set(value) != {"hostname", "port", "tls"}:
        raise Refused("invalid_control_config")
    return AuthEndpoint(
        value["hostname"],
        value["port"],
        _tls(value["tls"]),
        AUTH_CONTROL_IDENTITY if service else HOST_IDENTITY,
        service_path=SERVICE_PATH if service else None,
    )


@lru_cache(maxsize=1)
def _dashboard_backend():
    return RemoteAuthSandbox(_endpoint(_configuration(_DASHBOARD_CONFIG), service=True))


def configured_dashboard_sandbox():
    # OFF does not read a configuration, certificate or key, or construct a
    # client. ON never falls back to the local launcher after a refusal.
    try:
        return _dashboard_backend() if _enabled() else None
    except Refused:
        # Preserve CLIAuthSession's existing unavailable-launcher ABI. A bad
        # optional configuration cannot select the legacy local launcher.
        return _UnavailableSandbox()


def configured_control_relay(*, authority=None):
    try:
        if not _enabled():
            return None
        if authority is None:
            raise Refused("credential_authority_unavailable")
        value = _configuration(_CONTROL_CONFIG)
        if set(value) != {"tls", "host"}:
            raise Refused("invalid_control_config")
        return ControlAuthRelay(
            RemoteAuthSandbox(_endpoint(value["host"], service=False)),
            _tls(value["tls"]),
            authority=authority,
        )
    except Refused:
        # Refuse ONLY this private endpoint. Existing deterministic service
        # traffic must not depend on optional CLI-auth material being present.
        return ControlAuthRelay(None, None)


async def reconcile_runtime_codex(authority):
    """Synchronize the fixed host volume using control-held authority only.

    This is not a runtime dispatch or rotation receipt. Legacy callers must
    refuse their local spawn until the real runner owns an eligible host call.
    """
    from butlers.session_host.credential_volume import ControlCodexSync

    value = _configuration(_CONTROL_CONFIG)
    if set(value) != {"tls", "host"}:
        raise Refused("invalid_control_config")
    backend = RemoteAuthSandbox(_endpoint(value["host"], service=False))
    await ControlCodexSync(backend, authority).prepare()


class _UnavailableSandbox:
    async def launch_device_auth(self, provider):
        raise SandboxUnavailableError("CLI auth transport unavailable")

    async def run_readonly_command(self, provider, *, command, authority, timeout_s):
        raise SandboxUnavailableError("CLI auth transport unavailable")

    async def sync_codex(self):
        raise SandboxUnavailableError("CLI auth transport unavailable")

    async def sync_provider(self, provider):
        raise SandboxUnavailableError("CLI auth transport unavailable")

    async def probe_provider(self, provider, *, record=False):
        raise SandboxUnavailableError("CLI auth transport unavailable")


class _TLSChannel:
    """One bounded real TLS connection with independent RX/TX pumps.

    SSL operations never suspend. The pumps therefore share SSLObject safely
    on one event loop while a blocked application read cannot block a write.
    Application callers retain the original closed frame protocol and limits.
    """

    def __init__(self, context, receive, send, close, *, server_hostname=None):
        self._incoming, self._outgoing = ssl.MemoryBIO(), ssl.MemoryBIO()
        self._ssl = context.wrap_bio(
            self._incoming,
            self._outgoing,
            server_side=server_hostname is None,
            server_hostname=server_hostname,
        )
        self.reader = asyncio.StreamReader(limit=65536)
        self._receive, self._send, self._transport_close = receive, send, close
        self._ready = asyncio.get_running_loop().create_future()
        self._flush = asyncio.Event()
        self._flushed = asyncio.Event()
        self._closing = None
        self._sent = self._received = self._plain = 0
        self._tasks = [
            asyncio.create_task(self._receive_records()),
            asyncio.create_task(self._send_records()),
        ]

    def _failed(self):
        error = Refused("auth_transport_unavailable")
        if not self._ready.done():
            self._ready.set_exception(error)
        if not self.reader.at_eof():
            self.reader.set_exception(error)
        self._flushed.set()

    def _flush_records(self):
        if self._outgoing.pending > 524288:
            raise Refused("auth_transport_unavailable")
        if self._outgoing.pending:
            self._flushed.clear()
            self._flush.set()

    async def _receive_records(self):
        try:
            while True:
                if not self._ready.done():
                    try:
                        self._ssl.do_handshake()
                    except ssl.SSLWantReadError:
                        pass
                    else:
                        self._ready.set_result(None)
                self._flush_records()
                if self._ready.done() and self._ready.exception() is None:
                    while True:
                        try:
                            data = self._ssl.read(_RECORD_CHUNK)
                        except ssl.SSLWantReadError:
                            break
                        if not data:
                            self.reader.feed_eof()
                            return
                        self._plain += len(data)
                        if self._plain > _WIRE_LIMIT:
                            raise Refused("auth_transport_unavailable")
                        self.reader.feed_data(data)
                    self._flush_records()
                data = await self._receive()
                if type(data) is not bytes or not 0 < len(data) <= 65536:
                    raise Refused("auth_transport_unavailable")
                self._received += len(data)
                if self._received > _WIRE_LIMIT:
                    raise Refused("auth_transport_unavailable")
                self._incoming.write(data)
        except (Exception, asyncio.CancelledError):
            self._failed()

    async def _send_records(self):
        try:
            while True:
                await self._flush.wait()
                self._flush.clear()
                while self._outgoing.pending:
                    data = self._outgoing.read(_RECORD_CHUNK)
                    self._sent += len(data)
                    if self._sent > _WIRE_LIMIT:
                        raise Refused("auth_transport_unavailable")
                    await self._send(data)
                self._flushed.set()
        except (Exception, asyncio.CancelledError):
            self._failed()

    async def handshake(self, identity, *, server):
        async with asyncio.timeout(5):
            await asyncio.shield(self._ready)
            require_peer(self, identity, server=server)
        return self.reader, self

    def get_extra_info(self, name):
        return self._ssl if name == "ssl_object" else None

    def write(self, data):
        if self._closing is not None or not self._ready.done():
            raise Refused("auth_transport_unavailable")
        self._ready.result()
        try:
            while data:
                written = self._ssl.write(data[:_RECORD_CHUNK])
                data = data[written:]
            self._flush_records()
        except ssl.SSLError:
            raise Refused("auth_transport_unavailable") from None

    async def drain(self):
        await self._flushed.wait()
        if self.reader.exception() is not None:
            raise Refused("auth_transport_unavailable")

    def close(self):
        if self._closing is None:
            self._closing = asyncio.create_task(self._close())

    async def _close(self):
        try:
            async with asyncio.timeout(1):
                if self._ready.done() and self._ready.exception() is None:
                    # Deliver TLS close_notify after the final drained record.
                    # Otherwise a normal WebSocket close becomes a truncated
                    # TLS stream and can invalidate the buffered final receipt.
                    try:
                        self._ssl.unwrap()
                    except ssl.SSLError:
                        pass
                    self._flush_records()
                    await self._flushed.wait()
        finally:
            for task in self._tasks:
                task.cancel()
            try:
                async with asyncio.timeout(1):
                    await self._transport_close()
            finally:
                await asyncio.gather(*self._tasks, return_exceptions=True)
                if self._ready.done() and not self._ready.cancelled():
                    self._ready.exception()

    async def wait_closed(self):
        self.close()
        await asyncio.shield(self._closing)


async def connect_service(endpoint):
    """Configured target only; redirects/proxies/cookies cannot redirect TLS."""
    import aiohttp

    trace = aiohttp.TraceConfig()

    async def refuse_redirect(*_):
        raise Refused("auth_transport_unavailable")

    trace.on_request_redirect.append(refuse_redirect)
    session = aiohttp.ClientSession(
        trust_env=False, cookie_jar=aiohttp.DummyCookieJar(), trace_configs=[trace]
    )
    channel = None
    try:
        websocket = await session.ws_connect(
            f"http://{endpoint.hostname}:{endpoint.port}{SERVICE_PATH}",
            max_msg_size=65536,
            autoping=False,
        )

        async def receive():
            message = await websocket.receive()
            if message.type != aiohttp.WSMsgType.BINARY:
                raise Refused("auth_transport_unavailable")
            return message.data

        async def close():
            try:
                await websocket.close()
            finally:
                await session.close()

        channel = _TLSChannel(
            endpoint.tls.client_context(),
            receive,
            websocket.send_bytes,
            close,
            server_hostname=endpoint.hostname,
        )
        return await channel.handshake(endpoint.identity, server=True)
    except BaseException as error:
        try:
            if channel is not None:
                channel.close()
                await channel.wait_closed()
            else:
                await session.close()
        finally:
            if isinstance(error, Exception):
                # aiohttp failures contain URLs/headers. Keep the public
                # launcher boundary value-free and within its refusal ABI.
                raise Refused("auth_transport_unavailable") from None
        raise


class ControlAuthRelay:
    """Four finite authenticated sessions; no MCP registration or public API."""

    def __init__(self, backend, tls, *, authority=None):
        from butlers.session_host.credential_volume import ControlCodexSync, ControlOpenCodeSync

        sync = ControlCodexSync(backend, authority) if authority is not None else None
        self._service = AuthService(
            backend,
            peer_identity=DASHBOARD_AUTH_IDENTITY,
            sync=sync,
            opencode_sync=ControlOpenCodeSync(backend, authority)
            if authority is not None
            else None,
            login_authorizer=self._authorize_login,
        )
        self._tls = tls
        self._active = set()
        self._channels = set()
        self._closing = False
        self._settlement = None
        self._close_deadline = None

    async def _authorize_login(self, provider):
        sync = self._service._sync if provider.name == "codex" else self._service._opencode_sync
        if sync is None:
            raise Refused("credential_authority_unavailable")
        await sync.check_login_authority()

    def wrap(self, app):
        async def service(scope, receive, send):
            if scope["type"] == "lifespan":
                try:
                    return await app(scope, receive, send)
                finally:
                    await self.close()
            if scope["type"] != "websocket" or scope["path"] != SERVICE_PATH:
                return await app(scope, receive, send)
            from starlette.websockets import WebSocket

            websocket = WebSocket(scope, receive, send)
            if (
                self._closing
                or self._tls is None
                or len(self._active) >= 4
                or scope.get("query_string")
            ):
                await websocket.close(code=1008)
                return
            task = asyncio.current_task()
            self._active.add(task)
            channel = None
            try:
                await websocket.accept()
                channel = _TLSChannel(
                    self._tls.server_context(),
                    websocket.receive_bytes,
                    websocket.send_bytes,
                    websocket.close,
                )
                self._channels.add(channel)
                reader, writer = await channel.handshake(DASHBOARD_AUTH_IDENTITY, server=False)
                await self._service.handle(reader, writer)
            except Exception:
                pass  # Fixed close only; no TLS/provider/credential exception reaches logs.
            finally:
                try:
                    if channel is not None:
                        channel.close()
                        channel._closing.add_done_callback(
                            lambda _, owned=channel: self._channels.discard(owned)
                        )
                        await channel.wait_closed()
                    else:
                        await websocket.close()
                finally:
                    self._active.discard(task)

        return service

    async def close(self):
        if self._settlement is None:
            self._close_deadline = asyncio.get_running_loop().time() + 34
            self._settlement = asyncio.create_task(self._settle())
        cancelled = False
        pending = {self._settlement}
        while pending and asyncio.get_running_loop().time() < self._close_deadline:
            try:
                _, pending = await asyncio.wait(
                    pending,
                    timeout=max(0, self._close_deadline - asyncio.get_running_loop().time()),
                )
            except asyncio.CancelledError:
                cancelled = True
        if pending:
            raise Refused("auth_cleanup_incomplete")
        self._settlement.result()
        if cancelled:
            raise asyncio.CancelledError

    async def _settle(self):
        self._closing = True
        try:
            await self._service.close()
        finally:
            tasks = set(self._active)
            for task in tasks:
                task.cancel()
            for channel in tuple(self._channels):
                channel.close()
                tasks.add(channel._closing)
            if tasks:
                _, pending = await asyncio.wait(
                    tasks,
                    timeout=max(0, self._close_deadline - asyncio.get_running_loop().time()),
                )
                if pending:
                    raise Refused("auth_cleanup_incomplete")
                for task in tasks:
                    if not task.cancelled():
                        task.result()
