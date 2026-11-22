"""Scoped client transport for the existing remote Health medication calls."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from fastmcp.client.progress import ProgressHandler
from fastmcp.client.transports import StreamableHttpTransport
from mcp.types import CallToolResult

from butlers.core.mcp_urls import canonical_runtime_mcp_url
from butlers.core.service_plane.workload_headers import DAEMON_HEADER, TOKEN_DIR, TOKEN_HEADER
from butlers.core.switchboard_tls import health_http_options, scoped_mcp_httpx_factory
from butlers.mcp_liveness import LivenessClient

HEALTH_PEER_AUDIENCE = "butlers.health-medication-peer.v1"
MEDICATION_TOOL = "medication_travel_snapshot"
MEDICATION_SERVICE_TOOL = "medication_travel_snapshot_service"


def health_medication_transport(
    url: str,
    tool: str,
    daemon: str,
    *,
    headers: dict[str, str] | None = None,
    token_dir: Path = TOKEN_DIR,
) -> StreamableHttpTransport | None:
    """Open a request-scoped transport only for the configured Health peer."""
    if tool not in (MEDICATION_TOOL, MEDICATION_SERVICE_TOOL):
        return None

    options = health_http_options(url, require_origin=True)
    if not options:
        return None
    url = canonical_runtime_mcp_url(url)
    endpoint = urlsplit(url)
    if endpoint.path not in {"/mcp", "/mcp/"} or endpoint.query or endpoint.fragment:
        raise httpx.RequestError("Health peer MCP endpoint is invalid")
    try:
        token = (token_dir / HEALTH_PEER_AUDIENCE).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        raise httpx.RequestError("Health peer credential is unavailable") from None
    if not token:
        raise httpx.RequestError("Health peer credential is unavailable")

    return StreamableHttpTransport(
        url,
        headers={**(headers or {}), TOKEN_HEADER: token, DAEMON_HEADER: daemon},
        httpx_client_factory=scoped_mcp_httpx_factory(options),
    )


async def call_dashboard_health_service(
    url: str,
    arguments: dict[str, Any],
    progress_handler: ProgressHandler | None,
    timeout: timedelta | float | int | None,  # noqa: ASYNC109
    meta: dict[str, Any] | None,
) -> CallToolResult | None:
    """Send the existing direct Health service call on its own authenticated session."""
    transport = health_medication_transport(url, MEDICATION_SERVICE_TOOL, "dashboard")
    if transport is None:
        return None
    async with LivenessClient(transport) as client:
        return await client.call_tool_mcp(
            MEDICATION_SERVICE_TOOL, arguments, progress_handler, timeout, meta
        )
