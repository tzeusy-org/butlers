"""Verified public network backend and small pre-parser resource gates.

HTTP grammar stays in h11; TLS stays in asyncio/SSL. No connection becomes
HTTP-writable until its complete configured certificate identity is verified.
"""

from __future__ import annotations

import asyncio
import hashlib
import ssl
import time
from dataclasses import dataclass
from datetime import UTC, datetime

import h11
import httpcore
from cryptography import x509
from cryptography.x509.oid import ExtendedKeyUsageOID

from .config import HEADER_LIMIT, REPLY_LIMIT, REQUEST_LIMIT, PeerPolicy
from .protocol import ProtocolError


@dataclass(frozen=True)
class VerifiedPeer:
    role: str
    domain: str
    certificate_digest: str


def verify_peer(ssl_object, policy: PeerPolicy) -> VerifiedPeer:
    if ssl_object is None:
        raise ProtocolError("tls_required")
    try:
        if ssl_object.selected_alpn_protocol() not in {None, "http/1.1"}:
            raise ValueError
        der = ssl_object.getpeercert(binary_form=True)
        cert = x509.load_der_x509_certificate(der)
        now = datetime.now(UTC)
        if not cert.not_valid_before_utc <= now < cert.not_valid_after_utc:
            raise ValueError
        if cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca:
            raise ValueError
        eku = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
        expected = (
            ExtendedKeyUsageOID.CLIENT_AUTH
            if policy.client_certificate
            else ExtendedKeyUsageOID.SERVER_AUTH
        )
        if set(eku) != {expected}:
            raise ValueError
        sans = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        actual = set()
        for name in sans:
            if isinstance(name, x509.DNSName):
                actual.add("DNS:" + name.value)
            elif isinstance(name, x509.UniformResourceIdentifier):
                actual.add("URI:" + name.value)
            elif isinstance(name, x509.IPAddress):
                actual.add("IP:" + str(name.value))
            else:
                raise ValueError
        if frozenset(actual) != policy.sans:
            raise ValueError
    except (ValueError, TypeError, x509.ExtensionNotFound):
        raise ProtocolError("peer_identity_refused") from None
    return VerifiedPeer(policy.role, policy.domain, hashlib.sha256(der).hexdigest())


class HeadGate:
    """Hold an entire raw head until its size AND standard h11 grammar pass."""

    def __init__(self, *, response: bool):
        self.response = response
        self.buffer = bytearray()
        self.length: int | None = None
        self.received = 0
        self.status: int | None = None

    def feed(self, data: bytes) -> bytes:
        if self.length is not None:
            self.received += len(data)
            if self.received > self.length:
                raise ProtocolError("extra_entity_bytes")
            return data
        self.buffer.extend(data)
        end = self.buffer.find(b"\r\n\r\n")
        if end < 0:
            if len(self.buffer) > HEADER_LIMIT:
                raise ProtocolError("header_limit")
            return b""
        end += 4
        if end > HEADER_LIMIT:
            raise ProtocolError("header_limit")
        head, body = bytes(self.buffer[:end]), bytes(self.buffer[end:])
        parser = h11.Connection(h11.CLIENT if self.response else h11.SERVER)
        if self.response:
            parser.send(h11.Request(method=b"POST", target=b"/mcp", headers=[(b"host", b"x")]))
            parser.send(h11.EndOfMessage())
        try:
            parser.receive_data(head)
            event = parser.next_event()
        except h11.ProtocolError:
            raise ProtocolError("http_head") from None
        if self.response:
            if not isinstance(event, h11.Response) or event.status_code not in {200, 202}:
                raise ProtocolError("http_status")
            self.status = event.status_code
        elif (
            not isinstance(event, h11.Request) or event.method != b"POST" or event.target != b"/mcp"
        ):
            raise ProtocolError("private_http_route")
        headers = dict(event.headers)
        if any(
            k in headers
            for k in (
                b"transfer-encoding",
                b"content-encoding",
                b"upgrade",
                b"trailer",
                b"mcp-session-id",
            )
        ):
            raise ProtocolError("unsupported_http_profile")
        raw_length = headers.get(b"content-length")
        if raw_length is None or not raw_length.isdigit():
            raise ProtocolError("content_length_required")
        length = int(raw_length)
        maximum = REPLY_LIMIT if self.response else REQUEST_LIMIT
        if length > maximum:
            raise ProtocolError("entity_limit")
        if self.response and self.status == 202:
            if length != 0:
                raise ProtocolError("notification_response")
        elif headers.get(b"content-type", b"").split(b";", 1)[0].strip() != b"application/json":
            raise ProtocolError("json_required")
        self.length = length
        self.received = len(body)
        if self.received > length:
            raise ProtocolError("extra_entity_bytes")
        self.buffer.clear()
        return head + body

    @property
    def complete(self):
        return self.length is not None and self.received == self.length


class PurposeStream(httpcore.AsyncNetworkStream):
    def __init__(self, reader, writer, backend):
        self.reader, self.writer, self.backend = reader, writer, backend
        self.peer: VerifiedPeer | None = None
        self.gate: HeadGate | None = None
        self.pending = bytearray()
        self.unusable = False

    def abort(self):
        self.unusable = True
        # The current public writer transport changes after start_tls. Never
        # retain an obsolete transport or close a transferred socket facade.
        self.writer.transport.abort()

    async def aclose(self):
        self.abort()  # synchronous initiation precedes EVERY cancellation point
        try:
            async with asyncio.timeout_at(self.backend.close_bound()):
                await self.writer.wait_closed()
        finally:
            self.backend.streams.discard(self)

    async def start_tls(self, ssl_context, server_hostname=None, timeout=None):  # noqa: ASYNC109 -- httpcore stream ABI; owned absolute deadline below
        try:
            if self.peer or self.unusable or server_hostname != self.backend.hostname:
                raise ProtocolError("tls_transition")
            if ssl_context.verify_mode != ssl.CERT_REQUIRED or not ssl_context.check_hostname:
                raise ProtocolError("tls_verification_required")
            async with asyncio.timeout(self.backend.remaining(3)):
                await self.writer.start_tls(
                    ssl_context,
                    server_hostname=server_hostname,
                    ssl_handshake_timeout=self.backend.remaining(3),
                    ssl_shutdown_timeout=self.backend.remaining(5),
                )
            self.peer = verify_peer(self.writer.get_extra_info("ssl_object"), self.backend.policy)
            return self
        except BaseException:
            self.abort()
            raise

    async def write(self, buffer, timeout=None):  # noqa: ASYNC109 -- httpcore stream ABI; owned absolute deadline below
        try:
            if self.unusable or self.peer is None:
                raise ProtocolError("unverified_write")
            if (
                verify_peer(self.writer.get_extra_info("ssl_object"), self.backend.policy)
                != self.peer
            ):
                raise ProtocolError("peer_changed")
            if self.gate is None or self.gate.complete:
                self.gate = HeadGate(response=True)
            async with asyncio.timeout(self.backend.remaining(3)):
                self.writer.write(buffer)
                await self.writer.drain()
        except BaseException:
            self.abort()
            raise

    async def read(self, max_bytes, timeout=None):  # noqa: ASYNC109 -- httpcore stream ABI; owned absolute deadline below
        try:
            if self.unusable or self.peer is None or self.gate is None:
                raise ProtocolError("unverified_read")
            while not self.pending:
                head_remaining = HEADER_LIMIT + 1 - len(self.gate.buffer)
                bound = min(max_bytes, head_remaining) if self.gate.length is None else max_bytes
                async with asyncio.timeout(self.backend.remaining(9)):
                    data = await self.reader.read(bound)
                if not data:
                    if not self.gate.complete:
                        raise ProtocolError("truncated_response")
                    return b""
                self.pending.extend(self.gate.feed(data))
            data = bytes(self.pending[:max_bytes])
            del self.pending[:max_bytes]
            return data
        except BaseException:
            self.abort()
            raise

    def get_extra_info(self, info):
        return self.writer.get_extra_info(info)


class PurposeBackend(httpcore.AsyncNetworkBackend):
    def __init__(self, hostname: str, port: int, policy: PeerPolicy):
        self.hostname, self.port, self.policy = hostname, port, policy
        self.deadline = 0.0
        self.active = False
        self.close_deadline: float | None = None
        self.streams: set[PurposeStream] = set()

    def close_bound(self):
        # Nested pool/stream closes share one absolute budget. An expired
        # operation is never revived; an idle resource has no operation owner.
        if self.close_deadline is not None:
            return self.close_deadline
        deadline = time.monotonic() + 5
        return min(deadline, self.deadline) if self.active else deadline

    def remaining(self, cap):
        remaining = min(cap, self.deadline - time.monotonic())
        if remaining <= 0:
            raise TimeoutError("transport_deadline")
        return remaining

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):  # noqa: ASYNC109 -- httpcore backend ABI; owned absolute deadline below
        if (
            host != self.hostname
            or port != self.port
            or local_address is not None
            or socket_options
        ):
            raise ProtocolError("endpoint_binding")
        async with asyncio.timeout(self.remaining(3)):
            reader, writer = await asyncio.open_connection(host, port, limit=HEADER_LIMIT)
        stream = PurposeStream(reader, writer, self)
        self.streams.add(stream)
        return stream

    async def connect_unix_socket(self, *args, **kwargs):
        raise ProtocolError("unix_socket_refused")

    async def sleep(self, seconds):
        raise ProtocolError("retry_refused")
