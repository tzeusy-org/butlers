"""Scope the deployed Switchboard CA to its configured service origin."""

from __future__ import annotations

import os
import ssl
from typing import Any, TypedDict
from urllib.parse import urlsplit

import httpx


class _HTTPOptions(TypedDict, total=False):
    verify: ssl.SSLContext
    trust_env: bool
    mounts: dict[str, httpx.AsyncBaseTransport]


def _origin(url: str) -> tuple[str, str | None, int | None]:
    parts = urlsplit(url)
    if parts.username is not None or parts.password is not None:
        raise ValueError("unexpected user information")
    return parts.scheme, parts.hostname, parts.port


def switchboard_http_options(
    url: str | None = None,
    *,
    require_origin: bool = False,
    trust_env: bool | None = None,
) -> _HTTPOptions:
    """Return HTTPX options for the named peer or a client shared with other peers."""
    options: _HTTPOptions = {}
    if trust_env is not None:
        options["trust_env"] = trust_env
    ca_file = os.environ.get("BUTLERS_SWITCHBOARD_CA_FILE")
    if ca_file is None:
        return options

    try:
        expected = _origin(os.environ.get("SWITCHBOARD_MCP_URL", ""))
        scheme, host, port = expected
        if scheme != "https" or not host or port != 41100:
            raise ValueError("unexpected Switchboard origin")
        if url is not None:
            if not require_origin and urlsplit(url).hostname != host:
                return options
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
        options["mounts"] = {
            f"https://{host}:{port}": httpx.AsyncHTTPTransport(verify=context, trust_env=False),
        }
    else:
        options["verify"] = context
        options["trust_env"] = False
    return options


def switchboard_mcp_transport(url: str) -> Any:
    """Keep FastMCP's transport inference for ordinary endpoints."""
    options = switchboard_http_options(url)
    if not options:
        return url

    from fastmcp.client.transports import SSETransport, StreamableHttpTransport
    from fastmcp.mcp_config import infer_transport_type_from_url
    from mcp.shared._httpx_utils import MCP_DEFAULT_SSE_READ_TIMEOUT, MCP_DEFAULT_TIMEOUT

    def client_factory(
        headers: dict[str, str] | None = None,
        timeout: httpx.Timeout | None = None,
        auth: httpx.Auth | None = None,
        *,
        follow_redirects: bool = False,
    ) -> httpx.AsyncClient:
        # FastMCP can request redirects. Keep this client on the configured origin.
        return httpx.AsyncClient(
            headers=headers,
            timeout=timeout
            or httpx.Timeout(MCP_DEFAULT_TIMEOUT, read=MCP_DEFAULT_SSE_READ_TIMEOUT),
            auth=auth,
            follow_redirects=False,
            **options,
        )

    transport = (
        SSETransport if infer_transport_type_from_url(url) == "sse" else StreamableHttpTransport
    )
    return transport(url, httpx_client_factory=client_factory)
