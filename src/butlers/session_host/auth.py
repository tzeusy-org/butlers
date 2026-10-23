"""Closed CLI-auth service protocol, shared by the control relay and host.

The dashboard authenticates to the control service; only the control service's
runner identity can reach the host. Neither hop accepts commands, filenames or
environment. Existing CLIAuthSession still owns parsing and credential-store
CAS. The host's existing PID1 owner must settle before returning staged bytes.
No wire frame, provider output or credential is logged.
"""

from __future__ import annotations

import asyncio
import base64
import re
from dataclasses import dataclass

from butlers.cli_auth.registry import PROVIDERS, CLIAuthProviderDef
from butlers.cli_auth.sandbox import SandboxUnavailableError
from butlers.session_host.control import (
    CONTROL_IDENTITY,
    HOST_IDENTITY,
    TLSFiles,
    _close,
    read_frame,
    require_peer,
    write_frame,
)
from butlers.session_host.launch import Refused

AUTH_CONTROL_IDENTITY = "spiffe://butlers/control/cli-auth"
DASHBOARD_AUTH_IDENTITY = "spiffe://butlers/dashboard/cli-auth"
_AUTH_SECONDS = 900
_SETTLE_SECONDS = 32
AUTH_RELAY_SECONDS = _AUTH_SECONDS + _SETTLE_SECONDS + 10
_MAX_BYTES = 1024 * 1024


def _provider(name) -> CLIAuthProviderDef:
    # Registry lookup is server-side. A matching name never imports caller
    # commands or a forged dataclass; unsupported/new providers need admission.
    if type(name) is not str or name not in {"codex", "opencode-openai"}:
        raise Refused("auth_refused")
    return PROVIDERS[name]


def _bytes(value) -> bytes:
    try:
        if type(value) is not str or len(value) > 90000:
            raise ValueError
        content = base64.b64decode(value, validate=True)
        if len(content) > 65536:
            raise ValueError
        return content
    except (ValueError, TypeError):
        raise Refused("auth_refused") from None


async def _send_bytes(writer, kind, content):
    if type(content) is not bytes or len(content) > _MAX_BYTES:
        raise Refused("auth_refused")
    for offset in range(0, len(content), 32768):
        await write_frame(
            writer,
            {"type": kind, "data": base64.b64encode(content[offset : offset + 32768]).decode()},
        )


class AuthService:
    """One bounded login per connection, with the same backend on both hops.

    Host construction supplies BubblewrapDashboardCLIAuthSandbox and runner
    peer identity. Control construction supplies RemoteAuthSandbox pointing at
    the host, and the distinct dashboard peer identity. TLSFiles and targets
    are server configuration; no request can select either.
    """

    def __init__(
        self,
        backend,
        *,
        peer_identity=CONTROL_IDENTITY,
        volume=None,
        sync=None,
        opencode_volume=None,
        opencode_sync=None,
        login_authorizer=None,
    ):
        if peer_identity not in {CONTROL_IDENTITY, DASHBOARD_AUTH_IDENTITY}:
            raise Refused("invalid_control_config")
        self._backend, self._peer_identity = backend, peer_identity
        self._volume = volume
        self._sync = sync
        self._opencode_volume, self._opencode_sync = opencode_volume, opencode_sync
        self._login_authorizer = login_authorizer
        self._active, self._cleanup = set(), set()
        self._closing = False
        self._close_deadline = None

    def _settled(self, task):
        self._cleanup.discard(task)
        try:
            task.exception()
        except asyncio.CancelledError:
            pass

    async def _terminate(self, handle, peer):
        # Retain one cleanup task; caller cancellation never cancels PID1
        # settlement a second time. Timeout cannot authorize stage/UID reuse.
        async def settle():
            if peer is not None:
                if not peer.done():
                    peer.cancel()
                await asyncio.gather(peer, return_exceptions=True)
            if handle is not None:
                await handle.terminate()

        cleanup = asyncio.create_task(settle())
        self._cleanup.add(cleanup)
        cleanup.add_done_callback(self._settled)
        deadline = asyncio.get_running_loop().time() + _SETTLE_SECONDS
        pending = {cleanup}
        cancelled = False
        while pending and asyncio.get_running_loop().time() < deadline:
            try:
                _, pending = await asyncio.wait(
                    pending, timeout=max(0, deadline - asyncio.get_running_loop().time())
                )
            except asyncio.CancelledError:
                cancelled = True
        if pending:
            raise Refused("auth_cleanup_incomplete")
        cleanup.result()
        if cancelled:
            raise asyncio.CancelledError

    async def _login(self, reader, writer, provider):
        handle = None
        peer = None
        try:
            handle = await self._backend.launch_device_auth(provider)
            if not isinstance(handle.process.stdout, asyncio.StreamReader):
                raise Refused("auth_refused")
            await write_frame(writer, {"type": "started"})
            # No legal client frame exists until stdout EOF. Watching the peer
            # concurrently means a lost dashboard cancels a waiting login.
            peer = asyncio.create_task(read_frame(reader))
            total = 0
            while True:
                output = asyncio.create_task(handle.process.stdout.read(32768))
                try:
                    done, _ = await asyncio.wait(
                        {output, peer}, return_when=asyncio.FIRST_COMPLETED
                    )
                    if peer in done:
                        raise Refused("auth_peer_lost")
                    content = output.result()
                finally:
                    if not output.done():
                        output.cancel()
                    await asyncio.gather(output, return_exceptions=True)
                if not content:
                    break
                total += len(content)
                if total > _MAX_BYTES:
                    raise Refused("auth_refused")
                await _send_bytes(writer, "stdout", content)
            await write_frame(writer, {"type": "output_closed"})
            command = await peer
            if (
                set(command) != {"type", "succeeded"}
                or command["type"] != "finalize"
                or type(command["succeeded"]) is not bool
            ):
                raise Refused("auth_refused")
            # Finalize is the existing real containment/validated-stage owner.
            content = await handle.finalize(succeeded=command["succeeded"])
            if content is not None:
                await _send_bytes(writer, "artifact", content)
            returncode = handle.process.returncode
            if type(returncode) is not int:
                raise Refused("auth_refused")
            await write_frame(writer, {"type": "closed", "returncode": returncode})
        finally:
            # No interruptible await may precede retaining the WHOLE cleanup:
            # another shutdown cancellation must not skip handle termination.
            await self._terminate(handle, peer)

    async def _volume_operation(self, request, writer):
        kind = request.get("type", "").split("_", 1)[0]
        volume = self._volume if kind == "codex" else self._opencode_volume
        if self._peer_identity != CONTROL_IDENTITY or volume is None:
            raise Refused("auth_refused")
        if kind not in {"codex", "opencode"}:
            raise Refused("auth_refused")
        if request == {"type": kind + "_read"}:
            snapshot = await volume.read()
        elif (
            set(request) == {"type", "expected", "content"} and request["type"] == kind + "_replace"
        ):
            snapshot = await volume.replace(
                expected=request["expected"], content=_bytes(request["content"])
            )
        else:
            raise Refused("auth_refused")
        await write_frame(
            writer,
            {
                "type": kind + "_snapshot",
                "content": (
                    None
                    if snapshot.content is None
                    else base64.b64encode(snapshot.content).decode()
                ),
            },
        )

    async def _owned_readonly(self, provider, content):
        """The real fixed manifest command owns one retained cleanup lifetime."""
        from pathlib import Path

        from butlers.cli_auth.sandbox import ReadonlySandboxAuthority
        from butlers.cli_auth.sandbox_platform import resolve_readonly_runtime_inputs

        if provider is not PROVIDERS["opencode-openai"] or not provider.status_command:
            raise Refused("auth_refused")
        command = tuple(provider.status_command)
        invocation = resolve_readonly_runtime_inputs(provider, command)
        authority = ReadonlySandboxAuthority(provider.sandbox_authority_relative_path, content)
        worker = asyncio.create_task(
            self._backend.run_staged_command(
                provider,
                command=command,
                expected_executable=Path(invocation.command[0]),
                authorities=(authority,),
                timeout_s=15,
            )
        )
        self._cleanup.add(worker)
        worker.add_done_callback(self._settled)
        deadline = asyncio.get_running_loop().time() + 47
        try:
            done, _ = await asyncio.wait({worker}, timeout=47)
            if not done:
                raise Refused("auth_cleanup_incomplete")
            return worker.result()
        finally:
            if not worker.done():
                worker.cancel()
                while not worker.done() and asyncio.get_running_loop().time() < deadline:
                    try:
                        await asyncio.wait(
                            {worker}, timeout=max(0, deadline - asyncio.get_running_loop().time())
                        )
                    except asyncio.CancelledError:
                        pass
                if not worker.done():
                    raise Refused("auth_cleanup_incomplete")

    async def _status(self, provider, *, record=False):
        from butlers.cli_auth.health import AuthHealthResult, AuthHealthState

        if provider not in {"codex", "opencode-openai", "opencode-go"}:
            raise Refused("auth_refused")
        if self._peer_identity == DASHBOARD_AUTH_IDENTITY:
            if provider == "codex":
                if self._sync is None:
                    raise Refused("auth_unavailable")
                return await self._sync.health(record=record)
            if record and provider == "opencode-go":
                # OpenCode Go's model-producing test is not a status operation
                # or an invented D18 kind. Its real caller/profile stays gated.
                raise Refused("auth_unavailable")
            if self._opencode_sync is None:
                raise Refused("auth_unavailable")
            if provider == "opencode-openai":
                return await self._opencode_sync.probe(self._backend, record=record)
            await self._opencode_sync.prepare()
            return await self._backend.probe_provider(provider)
        if record or provider == "codex" or self._opencode_volume is None:
            raise Refused("auth_refused")
        snapshot = await self._opencode_volume.read()
        import json

        document = {} if snapshot.content is None else json.loads(snapshot.content)
        key = "openai" if provider == "opencode-openai" else "opencode-go"
        if key not in document:
            return AuthHealthResult(
                provider, AuthHealthState.not_authenticated, "No credential configured."
            )
        if provider == "opencode-go":
            return AuthHealthResult(provider, AuthHealthState.authenticated, "API key configured.")
        definition = PROVIDERS[provider]
        result = await self._owned_readonly(definition, snapshot.content)
        if (
            type(result.returncode) is not int
            or type(result.output) is not bytes
            or len(result.output) > 1048576
        ):
            raise Refused("auth_unavailable")
        from butlers.cli_auth.session import _strip_ansi

        ok = result.returncode == 0 and definition.status_ok_pattern.search(
            _strip_ansi(result.output.decode(errors="replace"))
        )
        return AuthHealthResult(
            provider,
            AuthHealthState.authenticated if ok else AuthHealthState.not_authenticated,
            "Provider CLI status check succeeded." if ok else "Provider CLI status check failed.",
        )

    async def handle(self, reader, writer, *, initial_request=None):
        task = asyncio.current_task()
        admitted = False
        try:
            require_peer(writer, self._peer_identity, server=False)
            if self._closing or len(self._active) >= 4:
                raise Refused("auth_refused")
            self._active.add(task)
            admitted = True
            async with asyncio.timeout(_AUTH_SECONDS + _SETTLE_SECONDS + 5):
                async with asyncio.timeout(5):
                    request = (
                        await read_frame(reader) if initial_request is None else initial_request
                    )
                if request.get("type") in {
                    "codex_read",
                    "codex_replace",
                    "opencode_read",
                    "opencode_replace",
                }:
                    async with asyncio.timeout(5):
                        await self._volume_operation(request, writer)
                    return
                if request.get("type") == "credential_sync":
                    if (
                        self._peer_identity != DASHBOARD_AUTH_IDENTITY
                        or set(request) != {"type", "provider"}
                        or request["provider"] not in {"codex", "opencode-openai", "opencode-go"}
                    ):
                        raise Refused("auth_refused")
                    sync = self._sync if request["provider"] == "codex" else self._opencode_sync
                    if sync is None:
                        raise Refused("auth_unavailable")
                    async with asyncio.timeout(15):
                        await sync.prepare()
                    await write_frame(
                        writer, {"type": "credential_synced", "provider": request["provider"]}
                    )
                    return
                if request.get("type") in {"credential_status", "credential_test"}:
                    if set(request) != {"type", "provider"}:
                        raise Refused("auth_refused")
                    async with asyncio.timeout(51):
                        result = await self._status(
                            request["provider"], record=request["type"] == "credential_test"
                        )
                    await write_frame(
                        writer,
                        {
                            "type": "credential_health",
                            "provider": result.provider,
                            "state": result.state.value,
                            "detail": result.detail,
                        },
                    )
                    return
                if request == {"type": "codex_sync"}:
                    if self._peer_identity != DASHBOARD_AUTH_IDENTITY or self._sync is None:
                        raise Refused("auth_refused")
                    # No credential/path from the dashboard can become the
                    # volume authority: load the selected CURRENT DB value in
                    # control, then stage through its one shared CAS owner.
                    async with asyncio.timeout(15):
                        await self._sync.prepare()
                        await write_frame(writer, {"type": "codex_synced"})
                    return
                if set(request) != {"type", "provider"} or request["type"] != "cli_login":
                    raise Refused("auth_refused")
                provider = _provider(request["provider"])
                if (
                    self._peer_identity == DASHBOARD_AUTH_IDENTITY
                    and provider.name == "opencode-openai"
                    and self._opencode_sync is None
                ):
                    raise Refused("auth_unavailable")
                if self._login_authorizer is not None:
                    async with asyncio.timeout(2):
                        await self._login_authorizer(provider)
                async with asyncio.timeout(_AUTH_SECONDS):
                    await self._login(reader, writer, provider)
        except Exception:
            try:
                async with asyncio.timeout(1):
                    await write_frame(writer, {"type": "refused"})
            except Exception:
                pass
        finally:
            if admitted:
                self._active.discard(task)
            await _close(writer)

    async def close(self, *, deadline=None):
        if not self._closing:
            self._closing = True
            self._close_deadline = asyncio.get_running_loop().time() + _SETTLE_SECONDS
            for task in tuple(self._active):
                task.cancel()
        if deadline is not None:
            self._close_deadline = min(deadline, self._close_deadline)
        pending = self._active | self._cleanup
        cancelled = False
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
        failure = None
        for volume in (self._volume, self._opencode_volume):
            if volume is None:
                continue
            try:
                await volume.close(deadline=self._close_deadline)
            except asyncio.CancelledError:
                cancelled = True
            except Exception as error:
                if failure is None:
                    failure = error
        if failure is not None:
            raise failure
        if cancelled:
            raise asyncio.CancelledError


class _RemoteProcess:
    def __init__(self, writer):
        self.stdout = asyncio.StreamReader(limit=65536)
        self.returncode = None
        self._writer = writer
        self._closed = asyncio.Event()

    def terminate(self):
        self._writer.close()

    kill = terminate

    async def wait(self):
        await self._closed.wait()
        return self.returncode


class _RemoteHandle:
    def __init__(self, reader, writer):
        self.process = _RemoteProcess(writer)
        self._reader, self._writer = reader, writer
        self._pump = asyncio.create_task(self._stdout())
        self._finalize = None

    async def _stdout(self):
        total = 0
        try:
            async with asyncio.timeout(_AUTH_SECONDS + 5):
                while True:
                    frame = await read_frame(self._reader)
                    if frame == {"type": "output_closed"}:
                        self.process.stdout.feed_eof()
                        return
                    if set(frame) != {"type", "data"} or frame["type"] != "stdout":
                        raise Refused("auth_refused")
                    content = _bytes(frame["data"])
                    total += len(content)
                    if total > _MAX_BYTES:
                        raise Refused("auth_refused")
                    self.process.stdout.feed_data(content)
        except Exception:
            # StreamReader's ValueError is the existing CLIAuthSession's
            # malformed-output path; no provider error content crosses it.
            self.process.stdout.set_exception(ValueError("CLI auth transport unavailable"))
            self._writer.close()
            raise SandboxUnavailableError("CLI auth transport unavailable") from None

    async def _finish(self, succeeded):
        try:
            async with asyncio.timeout(_SETTLE_SECONDS + 5):
                await self._pump
                await write_frame(self._writer, {"type": "finalize", "succeeded": succeeded})
                chunks, total = [], 0
                while True:
                    frame = await read_frame(self._reader)
                    if set(frame) == {"type", "returncode"} and frame["type"] == "closed":
                        if type(frame["returncode"]) is not int:
                            raise Refused("auth_refused")
                        self.process.returncode = frame["returncode"]
                        return b"".join(chunks) if chunks else None
                    if set(frame) != {"type", "data"} or frame["type"] != "artifact":
                        raise Refused("auth_refused")
                    content = _bytes(frame["data"])
                    total += len(content)
                    if total > _MAX_BYTES or not succeeded:
                        raise Refused("auth_refused")
                    chunks.append(content)
        except (OSError, TimeoutError, Refused):
            raise SandboxUnavailableError("CLI auth transport unavailable") from None
        finally:
            self.process._closed.set()
            await _close(self._writer)

    async def finalize(self, *, succeeded):
        if type(succeeded) is not bool:
            raise SandboxUnavailableError("CLI auth transport unavailable")
        if self._finalize is None:
            self._finalize = asyncio.create_task(self._finish(succeeded))
        return await asyncio.shield(self._finalize)

    async def terminate(self):
        self._writer.close()
        if not self._pump.done():
            self._pump.cancel()
        await asyncio.gather(self._pump, return_exceptions=True)
        if self._finalize is not None:
            # Closing the transport refuses any unfinished reply. Retrieve its
            # terminal exception without cancelling remote containment again.
            done, pending = await asyncio.wait({self._finalize}, timeout=_SETTLE_SECONDS + 5)
            for task in done:
                try:
                    task.exception()
                except asyncio.CancelledError:
                    pass
            if pending:
                raise SandboxUnavailableError("CLI auth cleanup incomplete")
        # Socket closure causes server containment; it is not a remote PID1
        # receipt. No artifact or successful outcome is manufactured here.
        self.process._closed.set()
        await _close(self._writer)


@dataclass(frozen=True)
class AuthEndpoint:
    hostname: str
    port: int
    tls: TLSFiles
    identity: str
    service_path: str | None = None


class RemoteAuthSandbox:
    """Dashboard→control or control→host, with no local-launch fallback."""

    def __init__(self, endpoint: AuthEndpoint):
        if (
            type(endpoint) is not AuthEndpoint
            or type(endpoint.hostname) is not str
            or not endpoint.hostname
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{0,252}", endpoint.hostname) is None
            or type(endpoint.port) is not int
            or not 0 < endpoint.port < 65536
            or type(endpoint.tls) is not TLSFiles
            or endpoint.identity not in {AUTH_CONTROL_IDENTITY, HOST_IDENTITY}
            or (
                endpoint.service_path is not None
                and (
                    endpoint.service_path != "/internal/session-host/cli-auth"
                    or endpoint.identity != AUTH_CONTROL_IDENTITY
                )
            )
        ):
            raise Refused("invalid_control_config")
        self._endpoint = endpoint

    async def _connect(self):
        target = self._endpoint
        if target.service_path is not None:
            from butlers.session_host.service_auth import connect_service

            return await connect_service(target)
        return await asyncio.open_connection(
            target.hostname,
            target.port,
            ssl=target.tls.client_context(),
            server_hostname=target.hostname,
            ssl_handshake_timeout=3,
            ssl_shutdown_timeout=2,
        )

    async def launch_device_auth(self, provider):
        writer = None
        try:
            # Reject forged registry definitions even though only the name is
            # sent. The server independently chooses its own registry entry.
            if provider is not _provider(provider.name):
                raise Refused("auth_refused")
            target = self._endpoint
            async with asyncio.timeout(5):
                reader, writer = await self._connect()
                require_peer(writer, target.identity, server=True)
                await write_frame(writer, {"type": "cli_login", "provider": provider.name})
                if await read_frame(reader) != {"type": "started"}:
                    raise Refused("auth_refused")
            return _RemoteHandle(reader, writer)
        except (OSError, TimeoutError, Refused, AttributeError):
            if writer is not None:
                await _close(writer)
            raise SandboxUnavailableError("CLI auth transport unavailable") from None

    async def run_readonly_command(self, provider, *, command, authority, timeout_s):
        # Never turn the migration into a general command service. Existing
        # health/test callers fail closed until a closed operation is mapped.
        raise SandboxUnavailableError("CLI auth operation unavailable")

    async def probe_provider(self, provider, *, record=False):
        from butlers.cli_auth.health import AuthHealthResult, AuthHealthState

        if provider not in {"codex", "opencode-openai", "opencode-go"}:
            raise Refused("auth_refused")
        writer = None
        try:
            async with asyncio.timeout(55):
                reader, writer = await self._connect()
                require_peer(writer, self._endpoint.identity, server=True)
                await write_frame(
                    writer,
                    {
                        "type": "credential_test" if record else "credential_status",
                        "provider": provider,
                    },
                )
                reply = await read_frame(reader)
                if (
                    type(reply) is not dict
                    or set(reply) != {"type", "provider", "state", "detail"}
                    or reply["type"] != "credential_health"
                    or reply["provider"] != provider
                    or (
                        reply["detail"] is not None
                        and (type(reply["detail"]) is not str or len(reply["detail"]) > 256)
                    )
                ):
                    raise Refused("auth_unavailable")
                return AuthHealthResult(provider, AuthHealthState(reply["state"]), reply["detail"])
        except (OSError, TimeoutError, ValueError):
            raise SandboxUnavailableError("CLI auth transport unavailable") from None
        finally:
            if writer is not None:
                await _close(writer)

    async def sync_codex(self):
        if self._endpoint.service_path is None:
            raise Refused("auth_refused")
        writer = None
        try:
            async with asyncio.timeout(20):
                reader, writer = await self._connect()
                await write_frame(writer, {"type": "codex_sync"})
                if await read_frame(reader) != {"type": "codex_synced"}:
                    raise Refused("auth_unavailable")
        except (OSError, TimeoutError):
            raise Refused("auth_unavailable") from None
        finally:
            if writer is not None:
                await _close(writer)

    async def sync_provider(self, provider):
        if self._endpoint.service_path is None or provider not in {
            "codex",
            "opencode-openai",
            "opencode-go",
        }:
            raise Refused("auth_refused")
        writer = None
        try:
            async with asyncio.timeout(20):
                reader, writer = await self._connect()
                require_peer(writer, self._endpoint.identity, server=True)
                await write_frame(writer, {"type": "credential_sync", "provider": provider})
                if await read_frame(reader) != {"type": "credential_synced", "provider": provider}:
                    raise Refused("auth_unavailable")
        except (OSError, TimeoutError):
            raise Refused("auth_unavailable") from None
        finally:
            if writer is not None:
                await _close(writer)

    async def _volume_operation(self, request):
        from butlers.session_host.credential_volume import (
            VolumeSnapshot,
            codex_document,
            opencode_document,
        )

        if self._endpoint.identity != HOST_IDENTITY:
            raise Refused("auth_refused")
        writer = None
        try:
            target = self._endpoint
            async with asyncio.timeout(10):
                reader, writer = await asyncio.open_connection(
                    target.hostname,
                    target.port,
                    ssl=target.tls.client_context(),
                    server_hostname=target.hostname,
                    ssl_handshake_timeout=3,
                    ssl_shutdown_timeout=2,
                )
                require_peer(writer, HOST_IDENTITY, server=True)
                await write_frame(writer, request)
                reply = await read_frame(reader)
                kind = request["type"].split("_", 1)[0]
                if (
                    kind not in {"codex", "opencode"}
                    or set(reply) != {"type", "content"}
                    or reply["type"] != kind + "_snapshot"
                ):
                    raise Refused("auth_refused")
                content = None if reply["content"] is None else _bytes(reply["content"])
                if content is not None:
                    (codex_document if kind == "codex" else opencode_document)(content)
                return VolumeSnapshot(content)
        except (OSError, TimeoutError):
            raise Refused("auth_unavailable") from None
        finally:
            if writer is not None:
                await _close(writer)

    async def read(self):
        return await self._volume_operation({"type": "codex_read"})

    async def replace(self, *, expected, content):
        from butlers.session_host.credential_volume import codex_document

        content = codex_document(content)
        return await self._volume_operation(
            {
                "type": "codex_replace",
                "expected": expected,
                "content": base64.b64encode(content).decode(),
            }
        )

    async def read_opencode(self):
        return await self._volume_operation({"type": "opencode_read"})

    async def replace_opencode(self, *, expected, content):
        from butlers.session_host.credential_volume import opencode_document

        content = opencode_document(content)
        return await self._volume_operation(
            {
                "type": "opencode_replace",
                "expected": expected,
                "content": base64.b64encode(content).decode(),
            }
        )
