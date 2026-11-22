"""Receiver admission for the existing remote Health medication calls."""

from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_http_request
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools.base import ToolResult
from mcp.types import CallToolRequestParams

from butlers.core.service_plane.health_peer import (
    HEALTH_PEER_AUDIENCE,
    MEDICATION_SERVICE_TOOL,
    MEDICATION_TOOL,
)
from butlers.core.service_plane.messenger_delivery import authenticate_caller, cached_verifier
from butlers.core.service_plane.relays import ServiceCaller
from butlers.core.service_plane.workload_headers import DAEMON_HEADER, TOKEN_HEADER
from butlers.core.service_plane.workload_identity import VerifierUnavailable

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
