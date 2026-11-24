"""Scope private service CAs to their configured internal origins."""

from __future__ import annotations

import os
import ssl
from collections.abc import Callable
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
    """Return HTTPX options scoped to the Switchboard service."""
    return _scoped_http_options(
        url,
        service="Switchboard",
        expected_url=os.environ.get("SWITCHBOARD_MCP_URL", ""),
        port=41100,
        ca_file=os.environ.get("BUTLERS_SWITCHBOARD_CA_FILE"),
        require_origin=require_origin,
        trust_env=trust_env,
    )


def health_http_options(url: str | None = None, *, require_origin: bool = False) -> _HTTPOptions:
    """Return HTTPX options scoped to the Health service."""
    expected_url = os.environ.get("HEALTH_MCP_URL")
    ca_file = os.environ.get("BUTLERS_HEALTH_CA_FILE")
    if expected_url is None and ca_file is None:
        return {}
    if url is not None and not require_origin:
        destination = urlsplit(url)
        if destination.port != 41103 or destination.hostname in {"localhost", "127.0.0.1", "::1"}:
            return {}
    if (expected_url is None) != (ca_file is None):
        raise httpx.RequestError("Health TLS configuration is incomplete")
    return _scoped_http_options(
        url,
        service="Health",
        expected_url=expected_url or "",
        port=41103,
        ca_file=ca_file,
        require_origin=require_origin,
        shared_host=True,
    )


def _scoped_http_options(
    url: str | None,
    *,
    service: str,
    expected_url: str,
    port: int,
    ca_file: str | None,
    require_origin: bool,
    trust_env: bool | None = None,
    shared_host: bool = False,
) -> _HTTPOptions:
    options: _HTTPOptions = {}
    if trust_env is not None:
        options["trust_env"] = trust_env
    if ca_file is None:
        return options

    try:
        expected = _origin(expected_url)
        scheme, host, actual_port = expected
        if scheme != "https" or not host or actual_port != port:
            raise ValueError("unexpected service origin")
        if url is not None:
            candidate = urlsplit(url)
            if not require_origin and (
                candidate.hostname != host or (shared_host and candidate.port != port)
            ):
                return options
            if _origin(url) != expected:
                raise ValueError("unexpected service origin")

        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.minimum_version = ssl.TLSVersion.TLSv1_3
        context.hostname_checks_common_name = False
        context.load_verify_locations(cafile=ca_file)
    except (OSError, ValueError):
        raise httpx.RequestError(f"{service} TLS configuration is unavailable or invalid") from None

    if url is None:
        mount_origin = str(httpx.URL(scheme=scheme, host=host, port=port))
        options["mounts"] = {
            mount_origin: httpx.AsyncHTTPTransport(verify=context, trust_env=False),
        }
    else:
        options["verify"] = context
        options["trust_env"] = False
    return options


def internal_service_http_options(
    url: str | None = None, *, trust_env: bool | None = None
) -> _HTTPOptions:
    """Use service-specific trust for HTTP clients that reach multiple daemons."""
    options = switchboard_http_options(url, trust_env=trust_env)
    health = health_http_options(url)
    if url is None and "mounts" in health:
        options["mounts"] = {**options.get("mounts", {}), **health["mounts"]}
    elif url is not None:
        options.update(health)
    return options


def switchboard_mcp_transport(url: str) -> Any:
    """Keep FastMCP's transport inference for ordinary Switchboard endpoints."""
    return _mcp_transport(url, switchboard_http_options(url))


def internal_service_mcp_transport(url: str) -> Any:
    """Create an MCP transport with the CA scoped to the selected peer."""
    return _mcp_transport(url, internal_service_http_options(url))


def _mcp_transport(url: str, options: _HTTPOptions) -> Any:
    if not options:
        return url

    from fastmcp.client.transports import SSETransport, StreamableHttpTransport
    from fastmcp.mcp_config import infer_transport_type_from_url

    transport = (
        SSETransport if infer_transport_type_from_url(url) == "sse" else StreamableHttpTransport
    )
    return transport(url, httpx_client_factory=scoped_mcp_httpx_factory(options))


def scoped_mcp_httpx_factory(options: _HTTPOptions) -> Callable[..., httpx.AsyncClient]:
    """Keep FastMCP on a validated peer origin even when it requests redirects."""
    from mcp.shared._httpx_utils import MCP_DEFAULT_SSE_READ_TIMEOUT, MCP_DEFAULT_TIMEOUT

    def client_factory(
        headers: dict[str, str] | None = None,
        timeout: httpx.Timeout | None = None,
        auth: httpx.Auth | None = None,
        *,
        follow_redirects: bool = False,
    ) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            headers=headers,
            timeout=timeout
            or httpx.Timeout(MCP_DEFAULT_TIMEOUT, read=MCP_DEFAULT_SSE_READ_TIMEOUT),
            auth=auth,
            follow_redirects=False,
            **options,
        )

    return client_factory
