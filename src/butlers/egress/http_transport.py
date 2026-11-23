"""Explicit external HTTPX request execution through the workload gateway.

Internal/site-local clients do not use this factory. Selection never consults
proxy environment variables and never falls back to a direct origin connection.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import h11
import httpx

from butlers.egress import gateway

_MODE_ENV = "BUTLERS_EGRESS_MODE"
_GATEWAY_ENV = "BUTLERS_EGRESS_GATEWAY"
_PREFIX = b"/_butlers/egress/v1/https/"
_HOP = frozenset({b"host", b"connection", b"keep-alive", b"proxy-connection"})


@dataclass(frozen=True, slots=True)
class GatewayConfig:
    port: int

    @classmethod
    def from_environment(cls, environment: Mapping[str, str] | None = None) -> GatewayConfig | None:
        values = os.environ if environment is None else environment
        mode, address = values.get(_MODE_ENV), values.get(_GATEWAY_ENV)
        if mode is None and address is None:
            return None
        if mode != "request-v1" or not address:
            raise ValueError("incomplete or unsupported external egress configuration")
        try:
            parsed = urlsplit(address)
            port = parsed.port
        except ValueError:
            raise ValueError("invalid external egress gateway") from None
        if port is None or not 1024 <= port <= 65535 or address != f"http://egress-gateway:{port}":
            raise ValueError("invalid external egress gateway")
        return cls(port)

    def base_url(self, host: str, port: int = 443) -> str:
        """Same wire prefix usable by explicit supported API-base adapters."""
        return f"http://egress-gateway:{self.port}{_PREFIX.decode()}{host}/{port}"


def _timeout(request: httpx.Request, phase: str, maximum: float) -> float:
    configured = request.extensions.get("timeout", {}).get(phase)
    return maximum if configured is None else min(float(configured), maximum)


async def _open_hop(config: GatewayConfig):
    # This is the one configured internal service, never a URL-selected origin.
    return await asyncio.open_connection("egress-gateway", config.port, limit=gateway._HEAD_LIMIT)


class _HopStream(httpx.AsyncByteStream):
    def __init__(self, owner, request, connection, reader, writer):
        self.owner, self.request, self.connection = owner, request, connection
        self.reader, self.writer = reader, writer
        self._closing = None

    async def __aiter__(self):
        received = 0
        try:
            while True:
                event = await gateway._event(
                    self.connection,
                    self.reader,
                    read_budget=_timeout(self.request, "read", gateway._IDLE_TIMEOUT_S),
                )
                if isinstance(event, h11.EndOfMessage):
                    if event.headers:
                        raise httpx.RemoteProtocolError(
                            "gateway response trailers refused", request=self.request
                        )
                    return
                if not isinstance(event, h11.Data):
                    raise httpx.RemoteProtocolError(
                        "incomplete gateway response", request=self.request
                    )
                received += len(event.data)
                if received > gateway._DOWNLOAD_LIMIT:
                    raise httpx.RemoteProtocolError("gateway response limit", request=self.request)
                yield bytes(event.data)
        except TimeoutError:
            raise httpx.ReadTimeout("gateway response timed out", request=self.request) from None
        except (h11.RemoteProtocolError, gateway.Refused):
            raise httpx.RemoteProtocolError(
                "invalid gateway response", request=self.request
            ) from None
        except OSError:
            raise httpx.ReadError("gateway response failed", request=self.request) from None
        finally:
            await self.aclose()

    async def aclose(self):
        if self._closing is None:

            async def close():
                try:
                    await gateway._settle_writers(self.writer)
                finally:
                    self.owner._streams.discard(self)

            self._closing = asyncio.create_task(close())
        await gateway._finish(self._closing)


class _RequestHopTransport(httpx.AsyncBaseTransport):
    """One bounded full-duplex HTTP/1.1 hop; no pool, proxy or origin fallback."""

    def __init__(self, config):
        self.config = config
        self._streams = set()
        self._closed = False

    async def handle_async_request(self, request):
        if self._closed:
            raise httpx.TransportError("gateway transport is closed", request=request)
        body = request.stream
        if not isinstance(body, httpx.AsyncByteStream):
            raise httpx.LocalProtocolError(
                "gateway requires an asynchronous request stream", request=request
            )
        writer = None
        stream = None
        try:
            try:
                reader, writer = await asyncio.wait_for(
                    _open_hop(self.config), _timeout(request, "connect", gateway._CONNECT_TIMEOUT_S)
                )
            except TimeoutError:
                raise httpx.ConnectTimeout(
                    "gateway connection timed out", request=request
                ) from None
            except OSError:
                raise httpx.ConnectError("gateway connection failed", request=request) from None
            connection = h11.Connection(h11.CLIENT, max_incomplete_event_size=gateway._HEAD_LIMIT)
            stream = _HopStream(self, request, connection, reader, writer)
            self._streams.add(stream)
            if self._closed:
                raise httpx.TransportError("gateway transport is closed", request=request)

            connected_writer = writer

            async def write(data):
                try:
                    await gateway._write(
                        connected_writer,
                        data,
                        write_budget=_timeout(request, "write", gateway._IDLE_TIMEOUT_S),
                    )
                except TimeoutError:
                    raise httpx.WriteTimeout("gateway upload timed out", request=request) from None
                except OSError:
                    raise httpx.WriteError("gateway upload failed", request=request) from None

            await write(
                connection.send(
                    h11.Request(
                        method=request.method.encode(),
                        target=request.url.raw_path,
                        headers=request.headers.raw,
                    )
                )
            )

            async def upload():
                sent = 0
                try:
                    async for chunk in body:
                        # Bound each write even when an application yields a large buffer.
                        for offset in range(0, len(chunk), 16384):
                            part = chunk[offset : offset + 16384]
                            sent += len(part)
                            if sent > gateway._UPLOAD_LIMIT:
                                raise httpx.LocalProtocolError(
                                    "gateway upload limit", request=request
                                )
                            await write(connection.send(h11.Data(data=part)))
                    await write(connection.send(h11.EndOfMessage()))
                finally:
                    await body.aclose()

            response = await gateway._upload_and_response(
                connection,
                reader,
                upload(),
                header_timeout=_timeout(request, "read", gateway._HEAD_TIMEOUT_S),
                upload_errors=(OSError, httpx.WriteError, httpx.WriteTimeout),
            )
            writer = None  # Transfer connection ownership to the response stream.
            return httpx.Response(
                response.status_code,
                headers=list(response.headers),
                stream=stream,
                extensions={"http_version": b"HTTP/1.1"},
            )
        except TimeoutError:
            raise httpx.ReadTimeout("gateway response timed out", request=request) from None
        except h11.LocalProtocolError:
            raise httpx.LocalProtocolError("invalid gateway request", request=request) from None
        except (h11.RemoteProtocolError, gateway.Refused):
            raise httpx.RemoteProtocolError("invalid gateway response", request=request) from None
        except OSError:
            raise httpx.ReadError("gateway response failed", request=request) from None
        finally:
            if writer is not None:
                if stream is not None:
                    await stream.aclose()
                else:
                    await gateway._settle_writers(writer)

    async def aclose(self):
        self._closed = True
        await gateway._finish(
            asyncio.gather(
                *(stream.aclose() for stream in list(self._streams)), return_exceptions=True
            )
        )


class _LogicalStream(httpx.AsyncByteStream):
    def __init__(self, stream, request):
        self.stream, self.request = stream, request

    async def __aiter__(self):
        try:
            async for chunk in self.stream:
                yield chunk
        except httpx.RequestError as exc:
            exc.request = self.request
            raise

    async def aclose(self):
        await self.stream.aclose()


class GatewayTransport(httpx.AsyncBaseTransport):
    """Map a logical HTTPS request to one framed internal gateway request."""

    def __init__(
        self,
        config: GatewayConfig,
        *,
        hop_transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.config = config
        self._hop = hop_transport if hop_transport is not None else _RequestHopTransport(config)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        if url.scheme != "https" or url.userinfo or url.fragment or not url.host:
            raise httpx.UnsupportedProtocol(
                "gateway requires an exact HTTPS origin", request=request
            )
        authority = url.raw_host + (b":" + str(url.port).encode() if url.port else b"")
        hosts = [value for name, value in request.headers.raw if name.lower() == b"host"]
        if hosts != [authority]:
            raise httpx.LocalProtocolError("conflicting logical origin Host", request=request)
        # HTTPX emits canonical ASCII/IDNA host bytes. The gateway independently
        # validates exact membership before DNS/dial; no URL input grants a route.
        prefix = _PREFIX + url.raw_host + b"/" + str(url.port or 443).encode()
        hop_url = url.copy_with(
            scheme="http",
            host="egress-gateway",
            port=self.config.port,
            raw_path=prefix + url.raw_path,
        )
        headers = [(k, v) for k, v in request.headers.raw if k.lower() not in _HOP]
        headers += [
            (b"Host", f"egress-gateway:{self.config.port}".encode()),
            (b"Connection", b"close"),
        ]
        hop = httpx.Request(
            request.method,
            hop_url,
            headers=headers,
            stream=request.stream,
            extensions=request.extensions,
        )
        try:
            response = await self._hop.handle_async_request(hop)
        except httpx.RequestError as exc:
            exc.request = request
            raise
        # HTTPX associates this response with the original logical request. Keep
        # its streaming body/close semantics; do not buffer downloads or SSE.
        return httpx.Response(
            response.status_code,
            headers=response.headers,
            stream=_LogicalStream(response.stream, request),
            extensions=response.extensions,
        )

    async def aclose(self) -> None:
        await self._hop.aclose()


def external_async_client(
    *,
    client_factory: Callable[..., httpx.AsyncClient] | None = None,
    **kwargs: Any,
) -> httpx.AsyncClient:
    """Preserve off-mode construction; selected mode owns the only transport."""
    config = GatewayConfig.from_environment()
    if config is not None:
        if any(key in kwargs for key in ("transport", "mounts", "proxy")):
            raise ValueError("selected gateway cannot combine external transports")
        kwargs["transport"] = GatewayTransport(config)
        kwargs["trust_env"] = False
    return (httpx.AsyncClient if client_factory is None else client_factory)(**kwargs)
