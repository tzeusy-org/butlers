"""Explicit external HTTPX request execution through the workload gateway.

Internal/site-local clients do not use this factory. Selection never consults
proxy environment variables and never falls back to a direct origin connection.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

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


class GatewayTransport(httpx.AsyncBaseTransport):
    """Map a logical HTTPS request to one framed internal gateway request."""

    def __init__(
        self,
        config: GatewayConfig,
        *,
        hop_transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.config = config
        self._hop = (
            hop_transport
            if hop_transport is not None
            else httpx.AsyncHTTPTransport(
                trust_env=False,
            )
        )

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
        response = await self._hop.handle_async_request(hop)
        # HTTPX associates this response with the original logical request. Keep
        # its streaming body/close semantics; do not buffer downloads or SSE.
        return httpx.Response(
            response.status_code,
            headers=response.headers,
            stream=response.stream,
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
