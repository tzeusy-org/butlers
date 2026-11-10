"""Explicit RFC6455 handshake and bounded frame relay for approved WSS origins.

This is a request-aware route, never a generic TCP tunnel. Compression is not
negotiated, so framing, masking, fragmentation and message limits stay visible.
The original key/subprotocol negotiation and frame bytes survive both hops.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import codecs
import hashlib
import time
from dataclasses import dataclass
from typing import Literal, overload

import h11
from websockets.exceptions import ProtocolError
from websockets.frames import Close

from butlers.egress import gateway

PREFIX = b"/_butlers/egress/v1/wss/"
_MESSAGE_LIMIT = 64 * 1024 * 1024
_GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
_TOKEN = frozenset(b"!#$%&'*+-.^_`|~0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ")


@overload
def _one(headers, name: bytes, *, required: Literal[True] = True) -> bytes: ...


@overload
def _one(headers, name: bytes, *, required: Literal[False]) -> bytes | None: ...


def _one(headers, name: bytes, *, required: bool = True) -> bytes | None:
    values = [value for key, value in headers if key == name]
    if len(values) > 1 or (required and not values):
        raise gateway.Refused("malformed_websocket", 400)
    return values[0] if values else None


def validate_request(request: h11.Request) -> tuple[bytes, list[bytes]]:
    """Validate the finite handshake before any origin DNS or connection."""
    headers = request.headers
    key = _one(headers, b"sec-websocket-key")
    try:
        decoded = base64.b64decode(key, validate=True)
    except binascii.Error:
        raise gateway.Refused("malformed_websocket", 400) from None
    protocols = _one(headers, b"sec-websocket-protocol", required=False)
    offered = [token.strip() for token in protocols.split(b",")] if protocols is not None else []
    if (
        request.method != b"GET"
        or _one(headers, b"upgrade").lower() != b"websocket"
        or _one(headers, b"connection").lower() != b"upgrade"
        or _one(headers, b"sec-websocket-version") != b"13"
        or len(decoded) != 16
        or base64.b64encode(decoded) != key
        or any(not token or not set(token) <= _TOKEN for token in offered)
        or len(set(offered)) != len(offered)
        or any(name in {b"content-length", b"transfer-encoding"} for name, _ in headers)
    ):
        raise gateway.Refused("malformed_websocket", 400)
    return key, offered


@dataclass(frozen=True, slots=True)
class Handshake:
    connection: h11.Connection
    response: h11.Response | None = None
    head: bytes | None = None


async def handshake(reader, writer, request, host, port, target) -> Handshake:
    """Validate 101; retain ordinary provider responses for bounded HTTP forwarding."""
    key, offered = validate_request(request)
    headers = [
        (name, value)
        for name, value in request.headers
        if name not in gateway._HOP_HEADERS | {b"host", b"sec-websocket-extensions"}
    ]
    authority = host if port == 443 else f"{host}:{port}"
    headers += [
        (b"host", authority.encode()),
        (b"connection", b"Upgrade"),
        (b"upgrade", b"websocket"),
    ]
    connection = h11.Connection(h11.CLIENT, max_incomplete_event_size=gateway._HEAD_LIMIT)
    await gateway._write(
        writer, connection.send(h11.Request(method=b"GET", target=target, headers=headers))
    )
    head = await gateway._head(reader)
    gateway._check_framing(head, response=True)
    connection.receive_data(head)
    response = connection.next_event()
    if isinstance(response, h11.Response):
        if 300 <= response.status_code <= 399:
            raise gateway.Refused("redirect_refused")
        return Handshake(connection, response=response)
    if not isinstance(response, h11.InformationalResponse) or response.status_code != 101:
        raise gateway.Refused("websocket_handshake_refused", 502)
    values = response.headers
    expected = base64.b64encode(hashlib.sha1(key + _GUID).digest())
    try:
        selected = _one(values, b"sec-websocket-protocol", required=False)
        connection_tokens = [
            token.strip().lower() for token in _one(values, b"connection").split(b",")
        ]
        nominated = set(connection_tokens)
        valid = (
            _one(values, b"upgrade").lower() == b"websocket"
            and b"upgrade" in nominated
            and all(token and set(token) <= _TOKEN for token in connection_tokens)
            and not nominated.intersection({b"sec-websocket-accept", b"sec-websocket-protocol"})
            and _one(values, b"sec-websocket-accept") == expected
            and (selected is None or (bool(selected) and selected in offered))
            and not any(
                name in {b"sec-websocket-extensions", b"content-length", b"transfer-encoding"}
                for name, _ in values
            )
        )
    except gateway.Refused:
        valid = False
    if not valid:
        raise gateway.Refused("invalid_websocket_handshake", 502)
    # Handshake fields are reconstructed. Provider headers such as session IDs
    # survive; hop/identity/refusal metadata and extension negotiation do not.
    headers = [
        (name, value)
        for name, value in values
        if name not in gateway._HOP_HEADERS | nominated | {b"x-egress-refusal"}
        and not name.startswith((b"x-forwarded-", b"x-egress-", b"x-butlers-"))
        and name != b"forwarded"
    ]
    headers += [(b"connection", b"Upgrade"), (b"upgrade", b"websocket")]
    return Handshake(
        connection,
        head=(
            b"HTTP/1.1 101 Switching Protocols\r\n"
            + b"".join(name + b": " + value + b"\r\n" for name, value in headers)
            + b"\r\n"
        ),
    )


class _Progress:
    def __init__(self):
        self.latest = time.monotonic()

    async def read(self, reader, count):
        content = bytearray()
        while len(content) < count:
            remaining = gateway._IDLE_TIMEOUT_S - (time.monotonic() - self.latest)
            if remaining <= 0:
                raise TimeoutError
            try:
                chunk = await asyncio.wait_for(reader.read(count - len(content)), remaining)
            except TimeoutError:
                # Either direction can keep a bidirectional session alive.
                if time.monotonic() - self.latest < gateway._IDLE_TIMEOUT_S:
                    continue
                raise
            if not chunk:
                raise gateway.Refused("incomplete_websocket", 502)
            self.latest = time.monotonic()
            content.extend(chunk)
        return bytes(content)


async def _frames(reader, writer, *, masked: bool, count, progress):
    fragmented = False
    size = 0
    decoder = None
    while True:
        head = await progress.read(reader, 2)
        fin, opcode = bool(head[0] & 0x80), head[0] & 15
        length = head[1] & 127
        if head[0] & 0x70 or bool(head[1] & 0x80) != masked or opcode not in {0, 1, 2, 8, 9, 10}:
            raise gateway.Refused("malformed_websocket_frame", 502)
        if length >= 126:
            extra = await progress.read(reader, 2 if length == 126 else 8)
            value = int.from_bytes(extra)
            if value < (126 if length == 126 else 65536) or value >= 1 << 63:
                raise gateway.Refused("malformed_websocket_frame", 502)
            head += extra
            length = value
        control = opcode >= 8
        if control:
            if not fin or length > 125:
                raise gateway.Refused("malformed_websocket_frame", 502)
        else:
            if (opcode == 0) != fragmented:
                raise gateway.Refused("malformed_websocket_frame", 502)
            if opcode:
                size = 0
                decoder = codecs.getincrementaldecoder("utf-8")() if opcode == 1 else None
            size += length
            if size > _MESSAGE_LIMIT:
                raise gateway.Refused("websocket_message_limit", 502)
            fragmented = not fin
        mask = await progress.read(reader, 4) if masked else b""
        # Small controls are validated atomically; data streams with bounded
        # backpressure. Even a declared large frame never allocates its length.
        if not control:
            await gateway._write(writer, head + mask)
        offset = 0
        control_data = b""
        while offset < length:
            chunk = await progress.read(reader, min(16384, length - offset))
            decoded = (
                bytes(value ^ mask[(offset + i) % 4] for i, value in enumerate(chunk))
                if masked
                else chunk
            )
            count(len(chunk))
            if control:
                control_data = decoded
            elif decoder is not None:
                try:
                    decoder.decode(decoded, final=fin and offset + len(chunk) == length)
                except UnicodeDecodeError:
                    raise gateway.Refused("malformed_websocket_frame", 502) from None
            if not control:
                await gateway._write(writer, chunk)
            offset += len(chunk)
        if not control and not length and decoder is not None and fin:
            try:
                decoder.decode(b"", final=True)
            except UnicodeDecodeError:
                raise gateway.Refused("malformed_websocket_frame", 502) from None
        if control:
            if opcode == 8:
                try:
                    Close.parse(control_data)
                except (ProtocolError, UnicodeDecodeError):
                    raise gateway.Refused("malformed_websocket_frame", 502) from None
            encoded = (
                bytes(value ^ mask[i % 4] for i, value in enumerate(control_data))
                if masked
                else control_data
            )
            await gateway._write(writer, head + mask + encoded)
            if opcode == 8:
                return True


async def relay(reader, writer, origin_reader, upstream, *, upload, download):
    """Relay validated frames; join both directions on close, cancellation or failure."""
    progress = _Progress()
    sending = asyncio.create_task(
        _frames(reader, upstream, masked=True, count=upload, progress=progress)
    )
    receiving = asyncio.create_task(
        _frames(origin_reader, writer, masked=False, count=download, progress=progress)
    )
    try:
        done, pending = await asyncio.wait(
            {sending, receiving}, return_when=asyncio.FIRST_COMPLETED
        )
        closed = all([task.result() for task in done])
        if closed and pending:
            async with asyncio.timeout(gateway._CONNECT_TIMEOUT_S):
                await asyncio.gather(*pending)
    finally:
        await gateway._cancel_and_join(sending, receiving)
