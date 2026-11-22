"""Scope the deployed Switchboard CA to its configured service origin."""

from __future__ import annotations

import os
import ssl
from typing import Any
from urllib.parse import urlsplit

import httpx


def _origin(url: str) -> tuple[str, str | None, int | None]:
    parts = urlsplit(url)
    if parts.username is not None or parts.password is not None:
        raise ValueError("unexpected user information")
    return parts.scheme, parts.hostname, parts.port


def switchboard_http_options(
    url: str | None = None, *, require_origin: bool = False
) -> dict[str, Any]:
    """Return HTTPX options for the named peer or a client shared with other peers."""
    ca_file = os.environ.get("BUTLERS_SWITCHBOARD_CA_FILE")
    if ca_file is None:
        return {}

    try:
        expected = _origin(os.environ.get("SWITCHBOARD_MCP_URL", ""))
        scheme, host, port = expected
        if scheme != "https" or not host or port != 41100:
            raise ValueError("unexpected Switchboard origin")
        if url is not None:
            if not require_origin and urlsplit(url).hostname != host:
                return {}
            if _origin(url) != expected:
                raise ValueError("unexpected Switchboard origin")

        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.minimum_version = ssl.TLSVersion.TLSv1_3
        context.hostname_checks_common_name = False
        context.load_verify_locations(cafile=ca_file)
    except (OSError, ValueError):
        raise httpx.RequestError(
            "Switchboard TLS configuration is unavailable or invalid"
        ) from None

    if url is None:
        return {
            "mounts": {
                f"https://{host}:{port}": httpx.AsyncHTTPTransport(verify=context, trust_env=False),
            }
        }
    return {"verify": context, "trust_env": False}


def switchboard_mcp_transport(url: str) -> Any:
    """Keep FastMCP's transport inference for ordinary endpoints."""
    options = switchboard_http_options(url)
    if not options:
        return url

    from fastmcp.client.transports import SSETransport, StreamableHttpTransport
    from fastmcp.mcp_config import infer_transport_type_from_url

    def client_factory(**kwargs: Any) -> httpx.AsyncClient:
        return httpx.AsyncClient(**(kwargs | options | {"follow_redirects": False}))

    transport = (
        SSETransport if infer_transport_type_from_url(url) == "sse" else StreamableHttpTransport
    )
    return transport(url, httpx_client_factory=client_factory)
