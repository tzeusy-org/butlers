"""Private D3 launch control over mutually authenticated TLS, without HTTP.

The runner supplies model authority, never argv, environment, adapter evidence,
or endpoints. TLS authenticates the service role before application frames are
read. Each connection owns one finite invocation. Results are untrusted model
output, not effect receipts. No request, credential, or result is logged.
"""

from __future__ import annotations

import asyncio
import json
import ssl
from dataclasses import dataclass
from pathlib import Path

from cryptography import x509
from cryptography.x509.oid import ExtendedKeyUsageOID

from butlers.session_host.launch import LaunchRequest, LaunchResult, Refused

CONTROL_IDENTITY = "spiffe://butlers/control/session-runner"
HOST_IDENTITY = "spiffe://butlers/session-host"
_MAX_FRAME = 262144
_MAX_REPLY = 1024 * 1024
_MAX_CALL_SECONDS = 340  # launch <=300, containment settlement <=30, wire/close reserve


@dataclass(frozen=True)
class TLSFiles:
    """Operator-mounted purpose-bound transport identity; no broker signing key."""

    ca: Path
    certificate: Path
    private_key: Path

    def server_context(self) -> ssl.SSLContext:
        context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH, cafile=str(self.ca))
        context.minimum_version = ssl.TLSVersion.TLSv1_3
        context.verify_mode = ssl.CERT_REQUIRED
        context.load_cert_chain(str(self.certificate), str(self.private_key))
        return context

    def client_context(self) -> ssl.SSLContext:
        context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=str(self.ca))
        context.minimum_version = ssl.TLSVersion.TLSv1_3
        context.load_cert_chain(str(self.certificate), str(self.private_key))
        return context


def require_peer(writer: asyncio.StreamWriter, identity: str, *, server: bool) -> None:
    """Check the actual negotiated peer, never a header or caller-supplied DTO."""
    try:
        negotiated = writer.get_extra_info("ssl_object")
        if negotiated is None:
            raise ValueError
        certificate = x509.load_der_x509_certificate(negotiated.getpeercert(binary_form=True))
        names = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        usages = certificate.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
        wanted_usage = (
            ExtendedKeyUsageOID.SERVER_AUTH if server else ExtendedKeyUsageOID.CLIENT_AUTH
        )
        # Identity aliases are not accepted: this role certificate cannot also
        # authorize a dashboard or domain process. DNS SAN still binds the
        # client connection's configured host through SSL hostname checking.
        if names.get_values_for_type(x509.UniformResourceIdentifier) != [identity]:
            raise ValueError
        if list(usages) != [wanted_usage]:
            raise ValueError
        if certificate.extensions.get_extension_for_class(x509.BasicConstraints).value.ca:
            raise ValueError
    except (ValueError, TypeError, x509.ExtensionNotFound):
        raise Refused("unauthenticated_control") from None


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def decode_request(value):
    from butlers.session_host.model_function import ModelFunctionRequest

    if type(value) is dict and value.get("type") == "model_function":
        return ModelFunctionRequest.from_wire(value)
    return LaunchRequest.from_wire(value)


async def read_frame(reader: asyncio.StreamReader) -> dict:
    try:
        size = int.from_bytes(await reader.readexactly(4), "big")
        if not 0 < size <= _MAX_FRAME:
            raise ValueError
        # Allocation is bounded before reading/parsing the body, including a
        # malicious one-chunk frame. The caller owns the total operation bound.
        payload = await reader.readexactly(size)
        value = json.loads(
            payload,
            object_pairs_hook=_unique_object,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
        if type(value) is not dict:
            raise ValueError
        return value
    except (ValueError, TypeError, UnicodeError, RecursionError, asyncio.IncompleteReadError):
        raise Refused("invalid_control_frame") from None


async def write_frame(writer: asyncio.StreamWriter, value: dict) -> None:
    try:
        payload = json.dumps(
            value, ensure_ascii=True, separators=(",", ":"), allow_nan=False
        ).encode()
        if not 0 < len(payload) <= _MAX_FRAME:
            raise ValueError
    except (ValueError, TypeError, UnicodeError):
        raise Refused("invalid_control_frame") from None
    writer.write(len(payload).to_bytes(4, "big") + payload)
    await writer.drain()


async def _close(writer):
    writer.close()
    try:
        async with asyncio.timeout(2):
            await writer.wait_closed()
    except (OSError, TimeoutError):
        pass


class ControlService:
    """Host-side authenticated consumer; bounded work, no unbounded launch queue."""

    def __init__(self, host, *, concurrency: int = 4, auth_service=None):
        if type(concurrency) is not int or not 1 <= concurrency <= 32:
            raise Refused("invalid_control_config")
        self._host, self._limit = host, concurrency
        self._auth_service = auth_service
        self._active: set[asyncio.Task] = set()
        self._launches: set[asyncio.Task] = set()
        self._closing = False
        self._close_deadline = None

    def _launch_finished(self, task):
        self._launches.discard(task)
        try:
            task.exception()  # Retrieve quarantined errors without logging private context.
        except asyncio.CancelledError:
            pass

    async def _launch(self, request, reader):
        launch = asyncio.create_task(self._host.launch(request))
        self._launches.add(launch)
        launch.add_done_callback(self._launch_finished)
        # After the one request there are no legal client bytes. EOF, extra
        # input or a read error revokes this connection's invocation lifetime.
        peer = asyncio.create_task(reader.read(1))
        try:
            done, _ = await asyncio.wait({launch, peer}, return_when=asyncio.FIRST_COMPLETED)
            if peer in done:
                raise Refused("control_peer_lost")
            return launch.result()
        finally:
            if not peer.done():
                peer.cancel()
            if not launch.done() and not launch.cancelling():
                launch.cancel()
            pending = {peer, launch}
            deadline = asyncio.get_running_loop().time() + 32
            cancelled = False
            while pending and asyncio.get_running_loop().time() < deadline:
                try:
                    _, pending = await asyncio.wait(
                        pending, timeout=max(0, deadline - asyncio.get_running_loop().time())
                    )
                except asyncio.CancelledError:
                    # A second shutdown/caller cancellation cannot cancel the
                    # same platform containment cleanup a second time.
                    cancelled = True
            for task in {peer, launch} - pending:
                try:
                    task.exception()
                except asyncio.CancelledError:
                    pass
            if pending:
                # The launch stays retained; no output or UID reuse is earned.
                raise Refused("control_cleanup_incomplete")
            if cancelled:
                raise asyncio.CancelledError

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        task = asyncio.current_task()
        admitted = False
        try:
            require_peer(writer, CONTROL_IDENTITY, server=False)
            if self._closing or len(self._active) >= self._limit:
                raise Refused("control_unavailable")
            self._active.add(task)
            admitted = True
            async with asyncio.timeout(5):
                value = await read_frame(reader)
            if value.get("type") in {
                "cli_login",
                "codex_read",
                "codex_replace",
                "opencode_read",
                "opencode_replace",
                "credential_status",
            }:
                if self._auth_service is None:
                    raise Refused("auth_unavailable")
                await self._auth_service.handle(reader, writer, initial_request=value)
                return
            async with asyncio.timeout(_MAX_CALL_SECONDS):
                request = decode_request(value)
                result = await self._launch(request, reader)
                # Sandbox has already proved descendant-domain death. Slice
                # completed model text into closed typed events, never expose
                # arbitrary runtime records/error details or credentials.
                if type(result) is not LaunchResult or len(result.text.encode()) > _MAX_REPLY:
                    raise Refused("invalid_runtime_output")
                for sequence, offset in enumerate(range(0, len(result.text), 16384)):
                    await write_frame(
                        writer,
                        {
                            "type": "runtime_event",
                            "invocation_id": str(request.invocation_id),
                            "sequence": sequence,
                            "event": "agent_message",
                            "text": result.text[offset : offset + 16384],
                        },
                    )
                await write_frame(
                    writer, {"type": "completed", "invocation_id": str(request.invocation_id)}
                )
        except Exception:
            # Transport errors and detailed adapter failures never become wire
            # diagnostics. Refusal has no model text or credential content.
            try:
                async with asyncio.timeout(1):
                    await write_frame(writer, {"type": "refused", "code": "launch_refused"})
            except (Refused, TimeoutError, OSError):
                pass
        finally:
            if admitted:
                self._active.discard(task)
            await _close(writer)

    async def close(self):
        """Stop admission and join the same retained tasks after one cancellation.

        Resistant platform cleanup stays retained in the sandbox quarantine;
        timeout is a named non-pass, never permission to reuse its identity.
        Repeated close joins do not inject a second cancellation into cleanup.
        """
        if not self._closing:
            self._closing = True
            self._close_deadline = asyncio.get_running_loop().time() + 32
            for task in tuple(self._active):
                task.cancel()
        pending = self._active | self._launches
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
            raise Refused("control_cleanup_incomplete")
        if self._auth_service is not None:
            try:
                await self._auth_service.close(deadline=self._close_deadline)
            except asyncio.CancelledError:
                cancelled = True
        if cancelled:
            raise asyncio.CancelledError


class ControlClient:
    """Runner-side sender. Only server-held configuration chooses the target."""

    def __init__(self, hostname: str, port: int, tls: TLSFiles):
        if (
            type(hostname) is not str
            or not hostname
            or type(port) is not int
            or not 0 < port < 65536
        ):
            raise Refused("invalid_control_config")
        self._hostname, self._port, self._tls = hostname, port, tls

    async def launch(self, request: LaunchRequest) -> LaunchResult:
        from butlers.session_host.model_function import ModelFunctionRequest

        if type(request) not in (LaunchRequest, ModelFunctionRequest):
            raise Refused("invalid_launch")
        writer = None
        try:
            async with asyncio.timeout(_MAX_CALL_SECONDS):
                reader, writer = await asyncio.open_connection(
                    self._hostname,
                    self._port,
                    ssl=self._tls.client_context(),
                    server_hostname=self._hostname,
                    ssl_handshake_timeout=5,
                    ssl_shutdown_timeout=2,
                )
                require_peer(writer, HOST_IDENTITY, server=True)
                await write_frame(writer, request.to_wire())
                chunks, total = [], 0
                while True:
                    frame = await read_frame(reader)
                    if frame == {"type": "completed", "invocation_id": str(request.invocation_id)}:
                        return LaunchResult("".join(chunks))
                    if (
                        set(frame) != {"type", "invocation_id", "sequence", "event", "text"}
                        or frame["type"] != "runtime_event"
                        or frame["invocation_id"] != str(request.invocation_id)
                        or type(frame["sequence"]) is not int
                        or frame["sequence"] != len(chunks)
                        or frame["event"] != "agent_message"
                        or type(frame["text"]) is not str
                    ):
                        raise Refused("invalid_control_result")
                    total += len(frame["text"].encode("utf-8"))
                    if total > _MAX_REPLY or len(chunks) >= 1024:
                        raise Refused("invalid_control_result")
                    chunks.append(frame["text"])
        except (OSError, TimeoutError, UnicodeError):
            raise Refused("control_unavailable") from None
        finally:
            if writer is not None:
                await _close(writer)
