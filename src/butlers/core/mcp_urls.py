"""Helpers for runtime MCP endpoint URL and transport selection.

Runtime sessions should target streamable HTTP MCP endpoints by default.
During cutover we still accept legacy SSE URLs where explicitly provided.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal
from urllib.parse import urlparse

RuntimeMcpTransport = Literal["http", "sse"]

_STREAMABLE_HTTP_PATH = "/mcp"
_SSE_PATH = "/sse"
_HTTP_ALIASES = frozenset({"http", "streamable-http", "streamable_http"})


def runtime_mcp_url(port: int, *, host: str = "localhost") -> str:
    """Build the canonical runtime MCP URL for a butler daemon."""
    return f"http://{host}:{port}{_STREAMABLE_HTTP_PATH}"


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
