"""The named-relay HTTP endpoint on Switchboard's service listener (RFC 0038 D7).

``POST /internal/service-plane/relay/{name}`` with body ``{"args": {...}}``.
It is attached beside ``/health`` on the service listener, like the runtime
probe control route: it is not an MCP tool, so no model session enumerates or
reaches it, and the control plane's session listener never serves it.

Every refusal happens before any target is contacted: switch off (``404``),
unknown relay (``404``), malformed body (``400``), and an unauthenticated or
unlisted caller, an LLM-presentable target or bad arguments (``403``). The
response names the refusal code and nothing about the target.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from butlers.core.service_plane.messenger_delivery import authenticate_caller
from butlers.core.service_plane.relays import Dispatch, RelayEndpoint, RelayRefused, relay_call
from butlers.core.service_plane.workload_identity import WorkloadTokenVerifier

RELAY_PATH = "/internal/service-plane/relay/{name}"


def _refused(code: str, status: int) -> JSONResponse:
    return JSONResponse({"status": "refused", "code": code}, status_code=status)


def build_relay_route(
    *,
    enabled: Callable[[], bool],
    relays: Callable[[], Mapping[str, RelayEndpoint]],
    verifier: Callable[[], WorkloadTokenVerifier | None],
    dispatch_for: Callable[[RelayEndpoint], Dispatch],
) -> Route:
    async def handle(request: Request) -> JSONResponse:
        if not enabled():
            return _refused("not_found", 404)
        endpoint = relays().get(request.path_params["name"])
        if endpoint is None:
            return _refused("unknown_relay", 404)
        try:
            body: Any = await request.json()
        except ValueError:
            return _refused("args_invalid", 400)
        if (
            not isinstance(body, dict)
            or set(body) != {"args"}
            or not isinstance(body["args"], dict)
        ):
            return _refused("args_invalid", 400)
        current = verifier()
        caller = await authenticate_caller(request.headers, current) if current else None
        try:
            result = await relay_call(endpoint, caller, body["args"], dispatch_for(endpoint))
        except RelayRefused as refused:
            return _refused(refused.code, 403)
        return JSONResponse({"status": "ok", "result": result})

    return Route(RELAY_PATH, handle, methods=["POST"])
