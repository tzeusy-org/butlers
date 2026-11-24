"""Caller-owned HTTP clients for the connector's sole WhatsApp bridge.

Ordinary requests use HTTPX's UDS transport. Only /backfill needs a public
httpcore network-stream adapter to count raw bytes before the HTTP parser.
The raw read ceiling does not measure asyncio or kernel socket-buffer memory.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import ssl
from collections.abc import AsyncGenerator, AsyncIterator, Callable, Iterable
from typing import Any

import httpcore
import httpx

DEFAULT_STANDALONE_BRIDGE_SOCKET = "/tmp/wa-bridge.sock"
DEFAULT_SHARED_BRIDGE_SOCKET = "/tmp/wa-bridge/bridge.sock"
DEFAULT_INVALIDATED_SESSION_THRESHOLD_S = 300.0
_BACKFILL_ACK_MAX_BYTES = 4096
_BACKFILL_ACK_TIMEOUT_S = 10.0
_SSE_HEADER_TIMEOUT_S = 30.0
_SSE_LINE_TIMEOUT_S = 90.0
_SSE_LINE_MAX_BYTES = 65536  # StreamReader.readline's previous effective limit.
logger = logging.getLogger(__name__)


class BridgeProtocolError(ValueError):
    """A fixed, content-blind response-format refusal."""


class BridgeTransportError(ConnectionError):
    """A fixed, content-blind socket failure."""


class _AckStream(httpcore.AsyncNetworkStream):
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._reader = reader
        self._writer = writer
        self._used = 0
        self._closed = False

    async def read(
        self,
        max_bytes: int,
        timeout: float | None = None,  # noqa: ASYNC109 - httpcore API.
    ) -> bytes:
        allowance = min(max_bytes, _BACKFILL_ACK_MAX_BYTES - self._used + 1)
        data = await asyncio.wait_for(self._reader.read(allowance), timeout)
        self._used += len(data)
        if self._used > _BACKFILL_ACK_MAX_BYTES:
            raise RuntimeError("bridge_ack_raw_limit")
        return data

    async def write(
        self,
        buffer: bytes,
        timeout: float | None = None,  # noqa: ASYNC109 - httpcore API.
    ) -> None:
        self._writer.write(buffer)
        await asyncio.wait_for(self._writer.drain(), timeout)

    async def aclose(self) -> None:
        if not self._closed:
            self._closed = True
            self._writer.close()
            with contextlib.suppress(ConnectionError):
                await self._writer.wait_closed()

    def get_extra_info(self, info: str) -> Any:
        return self._writer.get_extra_info(info)

    async def start_tls(
        self,
        ssl_context: ssl.SSLContext,
        server_hostname: str | None = None,
        timeout: float | None = None,  # noqa: ASYNC109 - httpcore API.
    ) -> httpcore.AsyncNetworkStream:
        raise BridgeTransportError("bridge_tls_refused")


class _AckBackend(httpcore.AsyncNetworkBackend):
    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,  # noqa: ASYNC109 - httpcore API.
        socket_options: Iterable[tuple[int, int, int | bytes]] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        if socket_options:
            raise BridgeTransportError("bridge_socket_options_refused")
        reader, writer = await asyncio.wait_for(asyncio.open_unix_connection(path), timeout)
        return _AckStream(reader, writer)

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,  # noqa: ASYNC109 - httpcore API.
        local_address: str | None = None,
        socket_options: Iterable[tuple[int, int, int | bytes]] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        raise BridgeTransportError("bridge_tcp_refused")

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


class _AckBody(httpx.AsyncByteStream):
    def __init__(self, response: httpcore.Response) -> None:
        self._response = response

    async def __aiter__(self) -> AsyncIterator[bytes]:
        try:
            async for part in self._response.aiter_stream():
                yield part
        except httpcore.TimeoutException:
            raise TimeoutError("bridge_ack_timeout") from None
        except httpcore.ProtocolError:
            raise BridgeProtocolError("bridge_http_protocol") from None
        except (httpcore.NetworkError, OSError):
            raise BridgeTransportError("bridge_socket_failure") from None

    async def aclose(self) -> None:
        await self._response.aclose()


class _AckTransport(httpx.AsyncBaseTransport):
    def __init__(self, socket_path: str) -> None:
        self._pool = httpcore.AsyncConnectionPool(
            uds=socket_path,
            network_backend=_AckBackend(),
            retries=0,
            max_connections=1,
            max_keepalive_connections=0,
        )

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        try:
            response = await self._pool.handle_async_request(
                httpcore.Request(
                    method=request.method,
                    url=httpcore.URL(
                        scheme=request.url.raw_scheme,
                        host=request.url.raw_host,
                        port=request.url.port,
                        target=request.url.raw_path,
                    ),
                    headers=request.headers.raw,
                    content=request.stream,
                    extensions=request.extensions,
                )
            )
        except httpcore.TimeoutException:
            raise TimeoutError("bridge_ack_timeout") from None
        except httpcore.ProtocolError:
            raise BridgeProtocolError("bridge_http_protocol") from None
        except (httpcore.NetworkError, OSError):
            raise BridgeTransportError("bridge_socket_failure") from None
        return httpx.Response(
            response.status,
            headers=response.headers,
            stream=_AckBody(response),
            extensions=response.extensions,
        )

    async def aclose(self) -> None:
        await self._pool.aclose()


async def _event_lines(chunks: AsyncIterator[bytes]) -> AsyncGenerator[bytes, None]:
    """Preserve LF/CRLF and final partial-line behavior with bounded buffering."""
    pending = bytearray()
    async for chunk in chunks:
        start = 0
        while start < len(chunk):
            end = chunk.find(b"\n", start)
            fragment = chunk[start:] if end < 0 else chunk[start:end]
            if len(pending) + len(fragment) > _SSE_LINE_MAX_BYTES:
                raise BridgeProtocolError("bridge_event_line_limit")
            pending.extend(fragment)
            if end < 0:
                break
            yield bytes(pending)
            pending.clear()
            start = end + 1
    if pending:
        yield bytes(pending)


def _json_object(body: bytes) -> dict[str, Any]:
    try:
        value = json.loads(body)
    except (ValueError, UnicodeError):
        raise BridgeProtocolError("bridge_json_invalid") from None
    if not isinstance(value, dict):
        raise BridgeProtocolError("bridge_json_not_object")
    return value


class BridgeClient:
    """A socket binding, not a shared process/session or bridge owner.

    Each method owns/closes its client; stream_events owns one for the stream's
    lifetime. Consumers must aclose an event iterator when leaving it early.
    """

    def __init__(
        self,
        socket_path: str,
        *,
        timeout: float | None = None,  # noqa: ASYNC109 - httpcore API.
        transport_factory: Callable[[str], httpx.AsyncBaseTransport] | None = None,
    ) -> None:
        self.socket_path = socket_path
        self.timeout = timeout
        self._transport_factory = transport_factory or self.make_transport

    @staticmethod
    def make_transport(socket_path: str) -> httpx.AsyncHTTPTransport:
        return httpx.AsyncHTTPTransport(uds=socket_path, retries=0)

    def _client(self, transport: httpx.AsyncBaseTransport) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=transport,
            base_url="http://bridge",
            timeout=self.timeout,
            trust_env=False,
            follow_redirects=False,
            headers={"Accept-Encoding": "identity"},
        )

    async def request_json(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        check_status: bool = False,
    ) -> dict[str, Any]:
        async with self._client(self._transport_factory(self.socket_path)) as client:
            async with client.stream(method, path, json=body) as response:
                if check_status:
                    response.raise_for_status()
                if response.headers.get("content-encoding", "identity").lower() != "identity":
                    raise BridgeProtocolError("bridge_encoding_refused")
                return _json_object(b"".join([part async for part in response.aiter_raw()]))

    async def request_backfill(
        self,
        window_hours: int | None,
        *,
        deadline_s: float = _BACKFILL_ACK_TIMEOUT_S,
    ) -> int:
        # One absolute deadline includes connection/write, framing, body and validation.
        async with asyncio.timeout(deadline_s):
            async with self._client(_AckTransport(self.socket_path)) as client:
                async with client.stream(
                    "POST",
                    "/backfill",
                    headers={"Connection": "close"},
                    json={"schema_version": "whatsapp.backfill.v1", "window_hours": window_hours},
                ) as response:
                    if response.status_code != 200:
                        raise RuntimeError("bridge_ack_status")
                    if response.headers.get("content-encoding", "identity").lower() != "identity":
                        raise RuntimeError("bridge_ack_encoding")
                    ack = _json_object(b"".join([part async for part in response.aiter_raw()]))
                    if (
                        ack.get("schema_version") != "whatsapp.backfill.v1"
                        or ack.get("status") != "accepted"
                        or type(ack.get("window_hours")) is not type(window_hours)
                        or ack.get("window_hours") != window_hours
                    ):
                        raise RuntimeError("bridge_ack_schema")
                    count = ack.get("replay_event_count")
                    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
                        raise RuntimeError("bridge_ack_count")
                    return count

    async def stream_events(
        self,
        *,
        header_timeout: float = _SSE_HEADER_TIMEOUT_S,
        line_timeout: float = _SSE_LINE_TIMEOUT_S,
    ) -> AsyncGenerator[dict[str, Any], None]:
        async with self._client(self._transport_factory(self.socket_path)) as client:
            # HTTPX owns framing. Bound the whole header phase, then complete lines.
            async with contextlib.AsyncExitStack() as stack:
                async with asyncio.timeout(header_timeout):
                    response = await stack.enter_async_context(
                        client.stream(
                            "GET",
                            "/events",
                            headers={"Accept": "text/event-stream"},
                        )
                    )
                if response.status_code != 200:
                    raise ConnectionError("bridge_events_status")
                if response.headers.get("content-encoding", "identity").lower() != "identity":
                    raise BridgeProtocolError("bridge_encoding_refused")
                lines = _event_lines(response.aiter_raw())
                try:
                    while True:
                        try:
                            line = await asyncio.wait_for(anext(lines), timeout=line_timeout)
                        except StopAsyncIteration:
                            return
                        text = line.decode(errors="replace").rstrip()
                        if not text or text.startswith(":") or not text.startswith("data:"):
                            continue
                        data = text[len("data:") :].strip()
                        if not data:
                            continue
                        try:
                            yield json.loads(data)
                        except json.JSONDecodeError:
                            logger.warning(
                                "WhatsApp bridge SSE: malformed JSON in event "
                                "(redacted, length=%d bytes)",
                                len(data),
                            )
                finally:
                    await lines.aclose()
