"""Workload authentication for the existing remote Health medication calls."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from fastmcp.client.progress import ProgressHandler
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_http_request
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools.base import ToolResult
from mcp.types import CallToolRequestParams, CallToolResult

from butlers.core.mcp_urls import canonical_runtime_mcp_url
from butlers.core.service_plane.messenger_delivery import authenticate_caller, cached_verifier
from butlers.core.service_plane.relays import ServiceCaller
from butlers.core.service_plane.workload_identity import (
    DAEMON_HEADER,
    TOKEN_DIR,
    TOKEN_HEADER,
    VerifierUnavailable,
)
from butlers.core.switchboard_tls import health_http_options, scoped_mcp_httpx_factory
from butlers.mcp_liveness import LivenessClient

HEALTH_PEER_AUDIENCE = "butlers.health-medication-peer.v1"
MEDICATION_TOOL = "medication_travel_snapshot"
MEDICATION_SERVICE_TOOL = "medication_travel_snapshot_service"
_SWITCHBOARD = ServiceCaller("butlers-control", "switchboard")
_DASHBOARD = ServiceCaller("dashboard-api", "dashboard")
_CALLERS = {
    MEDICATION_TOOL: frozenset({_SWITCHBOARD}),
    MEDICATION_SERVICE_TOOL: frozenset({_SWITCHBOARD, _DASHBOARD}),
}


class HealthMedicationPeerAdmission(Middleware):
    """Authenticate remote medication calls before the Health handler runs."""

    async def on_call_tool(
        self,
        context: MiddlewareContext[CallToolRequestParams],
        call_next: CallNext[CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        callers = _CALLERS.get(context.message.name)
        if callers is None:
            return await call_next(context)

        try:
            request = get_http_request()
        except RuntimeError:
            raise ToolError("health_medication_peer_refused") from None
        server = request.scope.get("server")
        if server is not None and server[0] == "127.0.0.1":
            return await call_next(context)
        if request.scope.get("scheme") != "https":
            raise ToolError("health_medication_peer_refused")
        if any(len(request.headers.getlist(key)) != 1 for key in (TOKEN_HEADER, DAEMON_HEADER)):
            raise ToolError("health_medication_peer_refused")

        verifier = cached_verifier(HEALTH_PEER_AUDIENCE)
        if verifier is None:
            raise ToolError("health_medication_peer_auth_unavailable")
        try:
            caller = await authenticate_caller(request.headers, verifier)
        except VerifierUnavailable:
            raise ToolError("health_medication_peer_auth_unavailable") from None
        if caller not in callers:
            raise ToolError("health_medication_peer_refused")
        return await call_next(context)


def health_medication_transport(
    url: str,
    tool: str,
    daemon: str,
    *,
    headers: dict[str, str] | None = None,
    token_dir: Path = TOKEN_DIR,
) -> StreamableHttpTransport | None:
    """Open a request-scoped transport only for the configured Health peer."""
    if tool not in _CALLERS:
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
