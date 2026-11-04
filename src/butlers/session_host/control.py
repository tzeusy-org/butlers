"""Dark, loopback-only mTLS runner control. No deployment authority is implied.

One length-prefixed JSON command per TLS connection. Identity, origin and inert
profile factory come only from trusted construction, never request fields.
Responses/logs never contain prompt, credential, token or runtime output.
"""

from __future__ import annotations

import asyncio
import base64
import fcntl
import hashlib
import hmac
import json
import os
import ssl
import struct
from collections.abc import Callable
from typing import cast
from uuid import UUID

from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding

from .contracts import BrokerBinding, LaunchRequest, Refused
from .lifecycle import Domain, InvocationOwner

_MAX_FRAME = 280 * 1024
_IO_SECONDS = 3


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


async def _read(reader: asyncio.StreamReader) -> dict:
    async with asyncio.timeout(_IO_SECONDS):
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


class LocalControl:
    """One in-memory launch/revoke owner, reachable only on IPv4 loopback port0.

    Disconnecting does not abandon the invocation: launch runs to bounded owner
    settlement. Revoke cancels that exact owned task, waits for owner cleanup,
    and never claims broker token revocation. Unknown/replayed IDs refuse.
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
    ) -> None:
        self._caller = _pin(caller_sha256)
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
        self._active: tuple[UUID, asyncio.Task] | None = None
        self._seen: set[UUID] = set()
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

    async def _command(self, data: dict) -> dict:
        if self._closing:
            raise Refused("control_unavailable")
        if data.get("operation") == "revoke":
            if (
                set(data) != {"version", "operation", "invocation_id"}
                or type(data["version"]) is not int
                or data["version"] != 1
            ):
                raise Refused("invalid_control_request")
            invocation = _uuid(data["invocation_id"])
            if self._active is None or self._active[0] != invocation:
                raise Refused("invocation_unavailable")
            task = self._active[1]
            task.cancel()
            await self._observe(task)
            return {"status": "revoked" if self._owner.healthy else "quarantined"}
        request = self._request(data)
        if request.invocation_id in self._seen or self._active is not None:
            raise Refused("invocation_unavailable")
        self._seen.add(request.invocation_id)
        # No await between ownership and task publication. Profile resolution is
        # a trusted factory closure, not an import/path supplied in this frame.
        coroutine = self._owner.run(request, lambda: self._factory(request.runtime, request.model))
        try:
            task = asyncio.create_task(coroutine)
        except BaseException:
            coroutine.close()
            raise Refused("control_unavailable") from None
        self._active = (request.invocation_id, task)
        try:
            success = await self._observe(task)
            return {"status": "completed" if success else "refused"}
        finally:
            if self._active and self._active[1] is task:
                self._active = None

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
        except Exception:
            try:
                await _write(writer, {"status": "refused"})
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
            await self._server.wait_closed()
        if self._active:
            task = self._active[1]
            task.cancel()
            await self._observe(task)
        if self._handlers:
            await asyncio.gather(*tuple(self._handlers), return_exceptions=True)


async def local_command(
    port: int,
    *,
    context: ssl.SSLContext,
    server_hostname: str,
    server_sha256: str,
    command: dict,
) -> dict:
    """Synthetic/local runner side: CA+hostname AND exact out-of-band leaf pin."""
    expected = _pin(server_sha256)
    if not context.check_hostname or context.verify_mode != ssl.CERT_REQUIRED:
        raise Refused("invalid_control_identity")
    reader, writer = await asyncio.open_connection(
        "127.0.0.1",
        port,
        ssl=context,
        server_hostname=server_hostname,
        ssl_handshake_timeout=_IO_SECONDS,
        ssl_shutdown_timeout=1,
    )
    try:
        _peer(writer, expected)
        await _write(writer, command)
        return await _read(reader)
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
