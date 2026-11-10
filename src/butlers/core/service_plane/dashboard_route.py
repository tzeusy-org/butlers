"""The dashboard service-call route on Switchboard's service listener (RFC 0038 D7).

``POST /internal/service-plane/dashboard-call`` with one
``Authorization: Bearer <service-call capability>`` header and the body
``{"butler": ..., "tool": ..., "args": {...}}``. It sits beside the
runtime-probe control route and the named relays: a plain Starlette route,
never an MCP tool, so no model session enumerates or reaches it.

Every refusal happens before the target is contacted, in this order:

* switch off --- ``404 not_found``;
* no single bearer capability, a cookie or a query string --- ``401``;
* a body that is not exactly the three fields --- ``400 args_invalid``;
* no verifier keyring here --- ``503 auth_unavailable`` (retryable);
* a capability that does not verify, or verifies for a different butler,
  tool or argument digest --- ``401 unauthorized``;
* a tool that is declared service-only --- ``403 not_presentable``: only
  model-reachable tools take this path;
* a nonce already consumed --- ``409 replay``, after the durable receipt.

Then the call goes through Switchboard's own ``route()`` (registry,
staleness, quarantine and the routing log) as ``source_butler="dashboard"``.
The target's whole MCP result comes back --- content, structured content and
``isError`` --- so the dashboard parses it exactly as it would a direct call.
A target ``route()`` could not reach is ``503 target_unavailable`` with its
``retryable`` flag. No refusal names anything about the target.
"""

from __future__ import annotations

import functools
import importlib
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Final

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from butlers.core.runtime_probe_control.capability import CapabilityRejected
from butlers.core.runtime_probe_control.endpoint import _bearer_capability
from butlers.core.runtime_probe_control.service_call import (
    ServiceCallVerifierUnavailable,
    VerifiedServiceCall,
    args_digest,
    verify_deployed_service_call,
)
from butlers.core.service_plane.dashboard_call import routes_through_control_plane

logger = logging.getLogger(__name__)

SERVICE_CALL_PATH: Final = "/internal/service-plane/dashboard-call"

#: The source every routed dashboard call is logged under.
DASHBOARD_SOURCE: Final = "dashboard"

_BODY_FIELDS: Final = frozenset({"butler", "tool", "args"})

#: route() pops this key as its own context; a dashboard argument may not carry it.
_ROUTE_CONTEXT_KEY: Final = "__switchboard_route_context"

#: route() adds this carrier to arbitrary tool kwargs while a span is active.
_TRACE_CONTEXT_KEY: Final = "trace_context"

_NO_STORE: Final = {"Cache-Control": "no-store"}
# route()'s transport outcome when the failure proves nothing reached the target.
_NOT_ATTEMPTED: Final = "not_attempted"

Dispatch = Callable[[str, str, dict[str, Any]], Awaitable[dict[str, Any]]]


def _refused(code: str, status: int, **extra: Any) -> JSONResponse:
    return JSONResponse(
        {"status": "refused", "code": code, **extra}, status_code=status, headers=_NO_STORE
    )


def _body(raw: bytes) -> tuple[str, str, dict[str, Any]] | None:
    try:
        body: Any = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(body, dict) or frozenset(body) != _BODY_FIELDS:
        return None
    butler, tool, args = body["butler"], body["tool"], body["args"]
    if not isinstance(butler, str) or not isinstance(tool, str) or not isinstance(args, dict):
        return None
    if _ROUTE_CONTEXT_KEY in args:
        return None
    return butler, tool, args


def build_dashboard_call_route(
    *,
    enabled: Callable[[], bool],
    receipts: Any,
    dispatch: Dispatch,
    verify: Callable[..., VerifiedServiceCall] = verify_deployed_service_call,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> Route:
    async def handle(request: Request) -> JSONResponse:
        raw = await request.body()
        if not enabled():
            return _refused("not_found", 404)
        compact = _bearer_capability(request)
        if compact is None:
            return _refused("unauthorized", 401)
        parsed = _body(raw)
        if parsed is None:
            return _refused("args_invalid", 400)
        butler, tool, args = parsed

        try:
            call = verify(compact, now=clock())
            digest = args_digest(args)
        except ServiceCallVerifierUnavailable:
            return _refused("auth_unavailable", 503)
        except (CapabilityRejected, ValueError) as exc:
            logger.warning("dashboard service call refused: %s", exc)
            return _refused("unauthorized", 401)
        if (call.butler, call.tool, call.args_digest) != (butler, tool, digest):
            logger.warning("dashboard service call refused: capability bound to another call")
            return _refused("unauthorized", 401)
        if not routes_through_control_plane(tool):
            return _refused("not_presentable", 403)
        if not await receipts.claim(nonce=call.nonce, kid=call.kid, expires_at=call.expires_at):
            logger.warning("dashboard service call refused: capability already consumed")
            return _refused("replay", 409)

        outcome = await dispatch(butler, tool, args)
        result = outcome.get("result") if isinstance(outcome, dict) else None
        if isinstance(result, dict):
            return JSONResponse({"status": "ok", "result": result}, headers=_NO_STORE)
        retryable = isinstance(outcome, dict) and outcome.get("retryable") is True
        return _refused("target_unavailable", 503, retryable=retryable)

    return Route(
        SERVICE_CALL_PATH,
        handle,
        methods=["POST"],
        name="dashboard_service_call",
        include_in_schema=False,
    )


def mcp_result_call_fn(
    call_tool: Callable[[str, str, dict[str, Any]], Awaitable[Any]],
    original_args: dict[str, Any],
) -> Callable[[str, str, dict[str, Any]], Awaitable[dict[str, Any]]]:
    """A ``route()`` ``call_fn`` that returns the target's whole MCP result.

    ``route()``'s default tool call keeps only parsed text and turns a tool
    error into a transport failure; the dashboard needs the result as the
    target produced it. The trace carrier ``route()`` injects is dropped
    unless the dashboard's own arguments carried that key: strict tool
    signatures refuse an unexpected keyword.
    """
    import mcp.types

    async def call(endpoint_url: str, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        if _TRACE_CONTEXT_KEY not in original_args:
            args = {key: value for key, value in args.items() if key != _TRACE_CONTEXT_KEY}
        result = await call_tool(endpoint_url, tool, args)
        return mcp.types.CallToolResult(
            content=list(result.content),
            structuredContent=result.structured_content,
            isError=result.is_error is True,
        ).model_dump(mode="json", by_alias=True, exclude_none=True)

    return call


def switchboard_dashboard_dispatch(pool: Any) -> Dispatch:
    """Dispatch one dashboard call through Switchboard's own ``route()``.

    The call is never resent once it may have reached the target: a reset or
    timeout after the request went out fails the call (``503
    target_unavailable``) instead of repeating a dashboard write. A failure
    that proves nothing was sent still reconnects once, as route() always does.

    For the same reason a failure is retryable only when route() reports that
    nothing was sent. route() calls a timeout retryable whether or not the
    request went out; a dashboard retry after an ambiguous send could repeat
    the write, so that failure is reported not retryable.
    """

    async def dispatch(butler: str, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        # Switchboard's routing package is loaded from the roster at runtime.
        routing = importlib.import_module("butlers.tools.switchboard.routing.route")
        call_tool = functools.partial(
            routing._call_tool_with_router_client, resend_after_send=False
        )
        outcome = await routing.route(
            pool,
            butler,
            tool,
            args,
            source_butler=DASHBOARD_SOURCE,
            call_fn=mcp_result_call_fn(call_tool, args),
        )
        return _retryable_only_if_not_sent(outcome)

    return dispatch


def _retryable_only_if_not_sent(outcome: Any) -> Any:
    if not isinstance(outcome, dict) or "retryable" not in outcome:
        return outcome
    transport = outcome.get("transport")
    not_sent = isinstance(transport, dict) and transport.get("outcome") == _NOT_ATTEMPTED
    return {**outcome, "retryable": outcome["retryable"] is True and not_sent}


def build_switchboard_dashboard_call_route(pool: Any) -> Route:
    """The route, dispatching through Switchboard's own ``route()``."""
    from butlers.core.runtime_probe_control.receipts import RuntimeProbeControlReceipts
    from butlers.core.runtime_probe_control.service_call import SERVICE_CALL_AUDIENCE
    from butlers.core.service_plane.messenger_delivery import service_plane_auth_enabled

    return build_dashboard_call_route(
        enabled=service_plane_auth_enabled,
        receipts=RuntimeProbeControlReceipts(pool, audience=SERVICE_CALL_AUDIENCE),
        dispatch=switchboard_dashboard_dispatch(pool),
    )
