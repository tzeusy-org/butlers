"""The private control-plane route Switchboard exposes for runtime probes.

REQ-dashboard-model-settings-001 puts this endpoint deliberately outside the
MCP surface: it is a plain Starlette route on the daemon's ASGI app, not a
FastMCP tool, so no model session and no ordinary MCP client can enumerate or
call it.

The request shape is as narrow as it can be made.  A capability may appear in
exactly one place --- a single ``Authorization: Bearer`` header --- and the
request carries no query string, no body, and no cookies.  Those are not
redundant checks:

*Cookies* are ambient.  A browser that had ever been given one would attach it
to a cross-origin request without the caller intending it, so any ``Cookie``
header at all is refused, even beside a perfectly valid bearer token.

*Query strings* end up in access logs and proxy logs, which is precisely where
a capability must never be.

*Bodies* would be the natural place to put a catalog entry id, a prompt, or
runtime arguments --- all of which the caller is forbidden to choose.  The
catalog entry comes from the signed claim; refusing every body means there is
no parameter surface to attack in the first place.

Responses are the closed typed vocabulary and nothing else.  No provider text,
no capability material, no ``WWW-Authenticate`` hint about what a working
credential would look like.
"""

from __future__ import annotations

from typing import Any, Final

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from butlers.core.runtime_probe_control.coordinator import ProbeResult, ProbeStatus

#: The private path.  Not mounted under ``/mcp`` and not a FastMCP tool.
CONTROL_PATH: Final = "/_control/runtime-probe/v1"

_BEARER_PREFIX: Final = "Bearer "

_NO_STORE: Final = {"Cache-Control": "no-store"}


def _bearer_capability(request: Request) -> str | None:
    """Extract the one capability, or ``None`` if the request shape is wrong."""
    if request.url.query:
        return None
    headers = request.headers
    if "cookie" in headers:
        return None
    authorizations = headers.getlist("authorization")
    if len(authorizations) != 1:
        return None
    value = authorizations[0]
    if not value.startswith(_BEARER_PREFIX):
        return None
    compact = value[len(_BEARER_PREFIX) :]
    # The capability grammar has no whitespace anywhere, so a padded or split
    # token is a malformed request rather than a capability to go and verify.
    if not compact or any(character.isspace() for character in compact):
        return None
    return compact


def _render(result: ProbeResult) -> JSONResponse:
    payload: dict[str, Any] = {"status": result.status.value}
    if result.status is ProbeStatus.COMPLETED:
        payload["ok"] = result.ok
        payload["latency_ms"] = result.latency_ms
    return JSONResponse(payload, status_code=result.http_status, headers=_NO_STORE)


def build_runtime_probe_control_route(coordinator: Any) -> Route:
    """Build the ``POST`` route that hands verified requests to *coordinator*."""

    async def _runtime_probe_control(request: Request) -> JSONResponse:
        compact = _bearer_capability(request)
        # Read the body unconditionally: a request that carried one is refused,
        # and draining it first keeps the connection usable for the response.
        body = await request.body()
        if compact is None or body:
            return _render(ProbeResult(ProbeStatus.UNAUTHORIZED))
        return _render(await coordinator.run(compact))

    return Route(
        CONTROL_PATH,
        _runtime_probe_control,
        methods=["POST"],
        name="runtime_probe_control",
        include_in_schema=False,
    )
