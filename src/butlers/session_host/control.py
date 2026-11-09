"""Dark, loopback-only mTLS runner control. No deployment authority is implied.

One length-prefixed JSON command per TLS connection. Identity, origin and inert
profile factory come only from trusted construction, never request fields.
Responses/logs never contain prompt, credential, token or runtime output, with
one exception: ``codex_auth_read`` returns the credential volume's document to
the pinned caller, so the control plane can collect a rotation (bu-kiuh8v.3).
"""

from __future__ import annotations

import asyncio
import base64
import fcntl
import hashlib
import hmac
import json
import logging
import math
import os
import re
import ssl
import struct
from collections.abc import Callable
from typing import cast
from uuid import UUID

from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding

from .contracts import BrokerBinding, LaunchRequest, Refused, RuntimeTimeout
from .credential_volume import CredentialVolume
from .lifecycle import Domain, InvocationOwner

logger = logging.getLogger(__name__)

_MAX_FRAME = 280 * 1024
_IO_SECONDS = 3
#: The most invocations one host runs at once; a launch beyond the bound is refused ``host_busy``.
_MAX_CONCURRENT = 64
_DEFAULT_CONCURRENT = 4
#: Invocation ids remembered (launched or revoked early). A host that has used them all refuses
#: new work rather than forget an id, because forgetting one would allow a replay.
_MAX_SEEN = 65536
#: What the owner may spend settling a cancelled or timed-out invocation (``InvocationOwner``'s
#: own cap is 30s), plus slack for the reply to travel. Added to a launch's own ``timeout_s``.
_SETTLE_ALLOWANCE_S = 35.0
_LARGEST_LAUNCH_TIMEOUT_S = 300.0
_USAGE_KEYS = frozenset({"input_tokens", "output_tokens", "cached_input_tokens"})
_USAGE_MAX = 2**53
_CREDENTIAL_OPERATIONS = frozenset({"codex_auth_status", "codex_auth_read", "codex_auth_install"})


def _pin(value: str) -> str:
    if type(value) is not str or len(value) != 64:
        raise Refused("invalid_control_identity")
    try:
        bytes.fromhex(value)
    except ValueError:
        raise Refused("invalid_control_identity") from None
    return value.lower()


def _server_context(
    certificate_pem: bytes, private_key_pem: bytes, client_ca_pem: bytes
) -> tuple[ssl.SSLContext, str]:
    """Load exactly the hashed leaf via kernel-sealed FDs, never an injected context.

    PEM material comes from trusted construction, not a request. Only one server
    leaf is supported in this dark profile; chain/rotation authority remains HOLD.
    Key bytes never enter errors, logs or filesystem staging.
    """
    descriptors = []
    try:
        if any(
            type(value) is not bytes or not 0 < len(value) <= 65536
            for value in (certificate_pem, private_key_pem, client_ca_pem)
        ):
            raise ValueError
        pem = certificate_pem.decode("ascii").strip()
        if (
            pem.count("-----BEGIN CERTIFICATE-----") != 1
            or pem.count("-----END CERTIFICATE-----") != 1
            or not pem.startswith("-----BEGIN CERTIFICATE-----")
            or not pem.endswith("-----END CERTIFICATE-----")
        ):
            raise ValueError
        certificate = x509.load_pem_x509_certificate(certificate_pem)
        # Normalize the parsed object: ignored PEM/DER bytes must never make
        # our hash describe something other than OpenSSL's installed leaf.
        actual = hashlib.sha256(certificate.public_bytes(Encoding.DER)).hexdigest()
        for material in (certificate.public_bytes(Encoding.PEM), private_key_pem):
            descriptor = os.memfd_create(
                "butlers-control-tls", os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING
            )
            descriptors.append(descriptor)
            remaining = memoryview(material)
            while remaining:
                count = os.write(descriptor, remaining)
                if count <= 0:
                    raise ValueError
                remaining = remaining[count:]
            # Linux UAPI: WRITE|GROW|SHRINK|SEAL; no unsealed fallback.
            fcntl.fcntl(descriptor, 1033, 0x000F)
            if fcntl.fcntl(descriptor, 1034) & 0x000F != 0x000F:
                raise ValueError
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_3
        context.load_cert_chain(
            f"/proc/self/fd/{descriptors[0]}",
            f"/proc/self/fd/{descriptors[1]}",
            password=lambda: b"",
        )
        context.load_verify_locations(cadata=client_ca_pem.decode("ascii"))
        context.verify_mode = ssl.CERT_REQUIRED
        return context, actual
    except Exception:
        raise Refused("invalid_control_identity") from None
    finally:
        for descriptor in descriptors:
            os.close(descriptor)


def _peer(writer: asyncio.StreamWriter, expected: str) -> None:
    tls = writer.get_extra_info("ssl_object")
    cert = tls.getpeercert(binary_form=True) if tls else None
    if not cert or not hmac.compare_digest(hashlib.sha256(cert).hexdigest(), expected):
        raise Refused("control_identity_refused")


async def _read(reader: asyncio.StreamReader, seconds: float | None = None) -> dict:
    async with asyncio.timeout(_IO_SECONDS if seconds is None else seconds):
        length = struct.unpack("!I", await reader.readexactly(4))[0]
        if not 0 < length <= _MAX_FRAME:
            raise Refused("invalid_control_frame")
        raw = await reader.readexactly(length)
    try:

        def pairs(items):
            result = {}
            for key, value in items:
                if key in result:
                    raise ValueError
                result[key] = value
            return result

        data = json.loads(raw, object_pairs_hook=pairs)
        if type(data) is not dict:
            raise ValueError
        return data
    except (ValueError, UnicodeError, RecursionError):
        raise Refused("invalid_control_frame") from None


async def _write(writer: asyncio.StreamWriter, data: dict) -> None:
    raw = json.dumps(data, separators=(",", ":")).encode()
    if len(raw) > _MAX_FRAME:
        raise Refused("invalid_control_frame")
    writer.write(struct.pack("!I", len(raw)) + raw)
    async with asyncio.timeout(_IO_SECONDS):
        await writer.drain()


def _reply_timeout(command: dict) -> float:
    """How long the runner side waits for a reply.

    A launch's reply arrives when the invocation settles, so it is tied to the launch's own
    ``timeout_s`` plus the owner's settlement allowance, never a fixed short bound. A timeout
    that is not a plausible launch timeout gets the largest bound, not an unbounded wait. Every
    other command keeps the short I/O bound.
    """
    if command.get("operation") != "launch":
        return _IO_SECONDS
    requested = command.get("timeout_s")
    timeout = _LARGEST_LAUNCH_TIMEOUT_S
    if (
        isinstance(requested, (int, float))
        and not isinstance(requested, bool)
        and math.isfinite(requested)
        and 0 < requested <= _LARGEST_LAUNCH_TIMEOUT_S
    ):
        timeout = float(requested)
    return timeout + _SETTLE_ALLOWANCE_S


def _failure_class(task: asyncio.Task) -> str | None:
    """The D14 class of a settled invocation: ``None`` for a clean exit.

    The host never inspects runtime output, so it reports only what it observed: a timeout, a
    runtime that failed or exited unsuccessfully, a cleanup it could not prove (containment), or
    a cancellation (Stop or shutdown). A launch that never started (profile or staging refused,
    the host unhealthy) is not a class: its reply is a bare ``refused``, as before.
    Provider classes (auth, quota) are not knowable here; the runner treats an unknown class as
    non-retryable.
    """
    if task.cancelled():
        return "cancelled"
    error = task.exception()
    if error is None:
        return None
    if isinstance(error, RuntimeTimeout):
        return "timeout"
    if isinstance(error, Refused):
        reason = str(error)
        if reason in ("domain_cleanup_unproven", "stage_cleanup_failed"):
            return "containment_failed"
    return "runtime_failed"


_NEVER_STARTED = frozenset(
    {
        "host_unavailable",
        "domain_unavailable",
        "staging_failed",
        "domain_task_unavailable",
        "invocation_already_started",
    }
)


def _never_started(task: asyncio.Task) -> bool:
    """Whether the owner refused before any process could have started."""
    if task.cancelled():
        return False
    error = task.exception()
    return isinstance(error, Refused) and str(error) in _NEVER_STARTED


def _usage(domain: object | None) -> dict[str, int] | None:
    """The runtime's final usage, if the domain reports one in the closed shape; else ``None``.

    Counts only: no key outside ``_USAGE_KEYS``, no bool, negative or oversized value. A report
    that raises or is malformed is dropped, never forwarded.
    """
    report = getattr(domain, "usage", None)
    if not callable(report):
        return None
    try:
        value = report()
    except Exception:
        return None
    if (
        type(value) is not dict
        or not value
        or not set(value) <= _USAGE_KEYS
        or any(type(v) is not int or not 0 <= v <= _USAGE_MAX for v in value.values())
    ):
        return None
    return dict(value)


def _uuid(value: object) -> UUID:
    if type(value) is not str:
        raise Refused("invalid_control_request")
    try:
        parsed = UUID(value)
        if str(parsed) != value:
            raise ValueError
        return parsed
    except ValueError:
        raise Refused("invalid_control_request") from None


class _Busy(Refused):
    """No free invocation slot; nothing was started or remembered."""


class LocalControl:
    """One in-memory launch/revoke owner, reachable only on IPv4 loopback port0.

    Disconnecting does not abandon the invocation: launch runs to bounded owner
    settlement, and its reply arrives then (``completed`` or ``failed`` with a D14
    class, plus usage when the domain reports it). Up to ``max_concurrent``
    invocations run at once. Revoke cancels that exact owned task, waits for owner
    cleanup, and never claims broker token revocation. A revoke for an id not yet
    launched tombstones it, so a later launch of that id refuses (a Stop that lands
    before its launch). Unknown/replayed IDs refuse.
    """

    def __init__(
        self,
        *,
        owner: InvocationOwner,
        factory: Callable[[str, str], Domain],
        certificate_pem: bytes,
        private_key_pem: bytes,
        client_ca_pem: bytes,
        caller_sha256: str,
        server_sha256: str,
        broker_origin: str,
        credentials: CredentialVolume | None = None,
        max_concurrent: int = _DEFAULT_CONCURRENT,
    ) -> None:
        if (
            type(max_concurrent) is not int
            or isinstance(max_concurrent, bool)
            or not 1 <= max_concurrent <= _MAX_CONCURRENT
        ):
            raise Refused("invalid_control_identity")
        self._max_concurrent = max_concurrent
        self._caller = _pin(caller_sha256)
        self._credentials = credentials
        context, actual_server = _server_context(certificate_pem, private_key_pem, client_ca_pem)
        if (
            not hmac.compare_digest(_pin(server_sha256), actual_server)
            or self._caller == actual_server
        ):
            raise Refused("invalid_control_identity")
        self._owner = owner
        self._factory = factory
        self._context = context
        self._origin = broker_origin
        self._active: dict[UUID, asyncio.Task] = {}
        self._seen: set[UUID] = set()
        self._tombstones: set[UUID] = set()
        self._handlers: set[asyncio.Task] = set()
        self._server: asyncio.Server | None = None
        self._closing = False

    async def start(self) -> int:
        if self._server is not None or self._closing:
            raise Refused("control_unavailable")
        self._server = await asyncio.start_server(
            self._handle,
            "127.0.0.1",
            0,
            ssl=self._context,
            ssl_handshake_timeout=_IO_SECONDS,
            ssl_shutdown_timeout=1,
            limit=_MAX_FRAME + 4,
        )
        return self._server.sockets[0].getsockname()[1]

    def _request(self, data: dict) -> LaunchRequest:
        fields = {
            "version",
            "operation",
            "invocation_id",
            "goal_id",
            "endpoint",
            "token",
            "runtime",
            "model",
            "credential",
            "prompt",
            "timeout_s",
        }
        if set(data) != fields or data["operation"] != "launch":
            raise Refused("invalid_control_request")
        if type(data["credential"]) is not str or len(data["credential"]) > 87384:
            raise Refused("invalid_control_request")
        try:
            credential = base64.b64decode(data["credential"], validate=True)
        except (ValueError, TypeError):
            raise Refused("invalid_control_request") from None
        return LaunchRequest(
            invocation_id=_uuid(data["invocation_id"]),
            broker=BrokerBinding(
                _uuid(data["goal_id"]), data["endpoint"], data["token"], self._origin
            ),
            runtime=data["runtime"],
            model=data["model"],
            model_credential=credential,
            prompt=data["prompt"],
            timeout_s=data["timeout_s"],
            version=data["version"],
        )

    def _credential_command(self, data: dict) -> dict:
        """The runner's Codex auth operations on this host's credential volume.

        ``codex_auth_read`` is the one reply that carries credential bytes: the
        control plane needs a rotated document back to update its authority.
        It goes only to the pinned caller over this channel, and is never
        logged. A host built without a volume refuses all three.
        """
        operation = data["operation"]
        fields = {
            "codex_auth_status": {"version", "operation"},
            "codex_auth_read": {"version", "operation"},
            "codex_auth_install": {"version", "operation", "document", "expected_sha256"},
        }[operation]
        if (
            self._credentials is None
            or set(data) != fields
            or type(data["version"]) is not int
            or data["version"] != 1
        ):
            raise Refused("invalid_control_request")
        if operation == "codex_auth_status":
            digest = self._credentials.status()
            if digest is None:
                return {"status": "absent"}
            return {"status": "present", "sha256": digest}
        if operation == "codex_auth_read":
            found = self._credentials.read()
            if found is None:
                return {"status": "absent"}
            document, digest = found
            encoded = base64.b64encode(document).decode()
            return {"status": "present", "document": encoded, "sha256": digest}
        expected = data["expected_sha256"]
        if expected is not None and (
            type(expected) is not str or re.fullmatch(r"[0-9a-f]{64}", expected) is None
        ):
            raise Refused("invalid_control_request")
        if type(data["document"]) is not str or len(data["document"]) > 87384:
            raise Refused("invalid_control_request")
        try:
            document = base64.b64decode(data["document"], validate=True)
        except (ValueError, TypeError):
            raise Refused("invalid_control_request") from None
        installed = self._credentials.install(document, expected_sha256=expected)
        return {"status": "installed" if installed else "conflict"}

    async def _command(self, data: dict) -> dict:
        if self._closing:
            raise Refused("control_unavailable")
        operation = data.get("operation")
        if type(operation) is str and operation in _CREDENTIAL_OPERATIONS:
            # Off the event loop: a volume write fsyncs.
            return await asyncio.to_thread(self._credential_command, data)
        if data.get("operation") == "revoke":
            if (
                set(data) != {"version", "operation", "invocation_id"}
                or type(data["version"]) is not int
                or data["version"] != 1
            ):
                raise Refused("invalid_control_request")
            invocation = _uuid(data["invocation_id"])
            task = self._active.get(invocation)
            if task is None:
                if invocation in self._tombstones:
                    return {"status": "revoked"}
                if invocation in self._seen or len(self._seen) >= _MAX_SEEN:
                    raise Refused("invocation_unavailable")
                # Not launched yet (a Stop that outran its launch): remember it, so the launch
                # that follows refuses instead of starting a session on a dead token.
                self._seen.add(invocation)
                self._tombstones.add(invocation)
                return {"status": "revoked"}
            task.cancel()
            await self._observe(task)
            return {"status": "revoked" if self._owner.healthy else "quarantined"}
        request = self._request(data)
        if request.invocation_id in self._seen or len(self._seen) >= _MAX_SEEN:
            raise Refused("invocation_unavailable")
        if len(self._active) >= self._max_concurrent:
            # Never started and not remembered: the same id may be offered again.
            raise _Busy("host_busy")
        self._seen.add(request.invocation_id)
        domains: list[object] = []

        def resolve() -> Domain:
            domain = self._factory(request.runtime, request.model)
            domains.append(domain)
            return domain

        # No await between ownership and task publication. Profile resolution is
        # a trusted factory closure, not an import/path supplied in this frame.
        coroutine = self._owner.run(request, resolve)
        try:
            task = asyncio.create_task(coroutine)
        except BaseException:
            coroutine.close()
            raise Refused("control_unavailable") from None
        self._active[request.invocation_id] = task
        try:
            await self._observe(task)
            return self._reply(task, domains[0] if domains else None)
        finally:
            if self._active.get(request.invocation_id) is task:
                del self._active[request.invocation_id]

    @staticmethod
    def _reply(task: asyncio.Task, domain: object | None) -> dict:
        if _never_started(task):
            return {"status": "refused"}
        failure = _failure_class(task)
        reply: dict = {"status": "completed"} if failure is None else {"status": "failed"}
        if failure is not None:
            reply["failure_class"] = failure
        usage = _usage(domain)
        if usage is not None:
            reply["usage"] = usage
        return reply

    async def _observe(self, task: asyncio.Task) -> bool:
        while True:
            try:
                # A cancelled handler still owns settlement, so it cancels the
                # invocation and keeps observing its bounded owner cleanup.
                await asyncio.shield(task)
                return True
            except asyncio.CancelledError:
                if task.done():
                    return False
                task.cancel()
            except Exception:
                return False

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        handler = cast(asyncio.Task, asyncio.current_task())
        self._handlers.add(handler)
        try:
            _peer(writer, self._caller)
            response = await self._command(await _read(reader))
            await _write(writer, response)
        except Exception as error:
            try:
                reply = {"status": "refused"}
                if isinstance(error, _Busy):
                    reply["reason"] = "host_busy"  # the one refusal a runner may retry
                await _write(writer, reply)
            except Exception:
                pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            self._handlers.discard(handler)

    async def close(self) -> None:
        self._closing = True
        if self._server:
            self._server.close()
        # Settle every running invocation before waiting on the listener: a connection
        # stays open until its launch replies, so waiting first would sit out the launch.
        for task in tuple(self._active.values()):
            task.cancel()
        for task in tuple(self._active.values()):
            await self._observe(task)
        if self._server:
            await self._server.wait_closed()
        if self._handlers:
            await asyncio.gather(*tuple(self._handlers), return_exceptions=True)


async def local_command(
    port: int,
    *,
    context: ssl.SSLContext,
    server_hostname: str,
    server_sha256: str,
    command: dict,
    reply_timeout_s: float | None = None,
    host: str = "127.0.0.1",
) -> dict:
    """Runner side: CA+hostname AND exact out-of-band leaf pin.

    ``host`` is the address dialed, by trusted construction only; TLS still checks
    ``server_hostname`` and the pin, so a wrong address reaches no other identity.
    A launch's reply arrives when its invocation settles, so the wait for it is tied to the
    launch's ``timeout_s`` (``_reply_timeout``); every other command keeps the short bound.
    """
    expected = _pin(server_sha256)
    if not context.check_hostname or context.verify_mode != ssl.CERT_REQUIRED:
        raise Refused("invalid_control_identity")
    reader, writer = await asyncio.open_connection(
        host,
        port,
        ssl=context,
        server_hostname=server_hostname,
        ssl_handshake_timeout=_IO_SECONDS,
        ssl_shutdown_timeout=1,
    )
    try:
        _peer(writer, expected)
        await _write(writer, command)
        return await _read(
            reader, _reply_timeout(command) if reply_timeout_s is None else reply_timeout_s
        )
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
