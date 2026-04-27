"""Helpers for runtime MCP endpoint URL and transport selection.

Runtime sessions should target streamable HTTP MCP endpoints by default.
During cutover we still accept legacy SSE URLs where explicitly provided.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal
from urllib.parse import urlparse, urlunparse

RuntimeMcpTransport = Literal["http", "sse"]

_STREAMABLE_HTTP_PATH = "/mcp"
_SSE_PATH = "/sse"
_HTTP_ALIASES = frozenset({"http", "streamable-http", "streamable_http"})


def runtime_mcp_url(port: int, *, host: str = "localhost") -> str:
    """Build the canonical runtime MCP URL for a butler daemon."""
    return f"http://{host}:{port}{_STREAMABLE_HTTP_PATH}"


def prefer_ipv4_loopback_url(url: str) -> str:
    """Rewrite bare ``localhost`` hosts to IPv4 loopback.

    Butler MCP daemons bind an IPv4 socket. Some clients prefer ``::1`` for
    ``localhost`` and do not reliably fall back to ``127.0.0.1``, which makes
    loopback-only probes fail even though the daemon is listening.

    Restrict the rewrite to exact ``localhost`` hosts so remote endpoints and
    explicit IP literals preserve their original meaning.
    """
    parsed = urlparse(url)
    if (parsed.hostname or "").lower() != "localhost":
        return url

    netloc = parsed.netloc
    if parsed.username is not None:
        userinfo = parsed.username
        if parsed.password is not None:
            userinfo += f":{parsed.password}"
        host_port = "127.0.0.1"
        if parsed.port is not None:
            host_port += f":{parsed.port}"
        netloc = f"{userinfo}@{host_port}"
    else:
        netloc = "127.0.0.1"
        if parsed.port is not None:
            netloc += f":{parsed.port}"

    return urlunparse(parsed._replace(netloc=netloc))


def canonical_runtime_mcp_url(url: str) -> str:
    """Prefer the canonical runtime MCP endpoint for legacy runtime URLs.

    Exact legacy runtime SSE endpoints (``.../sse`` or ``.../sse/``) are
    rewritten to the canonical streamable HTTP endpoint (``.../mcp``).
    Other paths are left unchanged so non-runtime or nested paths like
    ``.../mcp/sse`` keep their original meaning.
    """
    parsed = urlparse(url)
    normalized_path = parsed.path.rstrip("/") or "/"
    if normalized_path != _SSE_PATH:
        return url
    return urlunparse(parsed._replace(path=_STREAMABLE_HTTP_PATH))


def runtime_mcp_transport_from_url(url: str) -> RuntimeMcpTransport:
    """Infer runtime MCP transport from endpoint URL path.

    `/sse` is treated as legacy SSE transport for backward compatibility.
    All other paths default to streamable HTTP.
    """
    path = urlparse(url).path.rstrip("/") or "/"
    if path == _SSE_PATH:
        return "sse"
    return "http"


def resolve_runtime_mcp_transport(server_cfg: Mapping[str, Any]) -> RuntimeMcpTransport:
    """Resolve runtime MCP transport from config with URL-based fallback.

    Explicit `transport` wins when provided; URL path inference is fallback.
    """
    raw_transport = server_cfg.get("transport")
    if isinstance(raw_transport, str):
        normalized = raw_transport.strip().lower()
        if normalized in _HTTP_ALIASES:
            return "http"
        if normalized == "sse":
            return "sse"

    raw_url = server_cfg.get("url")
    if isinstance(raw_url, str) and raw_url.strip():
        return runtime_mcp_transport_from_url(raw_url)
    return "http"
