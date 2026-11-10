"""The dashboard's control-plane client for model-reachable tools (RFC 0038 D7).

One call in, one MCP result out. The client signs a service-call capability
for exactly this butler, tool and argument set with the runtime-probe control
signer, reached only through
:func:`~butlers.core.runtime_probe_control.activation.activated_signer_snapshot`,
and posts it to Switchboard's service listener. The result is the target's
``mcp.types.CallToolResult`` as the target produced it, so FastMCP's own
result parsing (``ToolError`` on ``isError``, structured data) runs unchanged.

Fail-closed: with no usable signer, an unreachable Switchboard or any refusal,
the call raises :class:`ServiceCallUnavailable` and nothing is sent to the
butler directly. There is no fallback path.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from typing import Any, Final

import httpx
import mcp.types
from pydantic import ValidationError
from pydantic_core import to_jsonable_python

from butlers.core.runtime_probe_control.capability import CapabilityRejected
from butlers.core.runtime_probe_control.keys import SignerSnapshot
from butlers.core.runtime_probe_control.service_call import sign_service_call
from butlers.core.service_plane.dashboard_route import SERVICE_CALL_PATH

#: A routed call has no deadline of its own beyond the caller's; this bounds a
#: caller that set none, past Switchboard's own route timeouts.
DEFAULT_TIMEOUT_S: Final = 120.0


class ServiceCallUnavailable(Exception):
    """The control plane did not carry the call. The message is a fixed string."""


def _seconds(timeout: dt.timedelta | float | int | None) -> float:
    if timeout is None:
        return DEFAULT_TIMEOUT_S
    if isinstance(timeout, dt.timedelta):
        return timeout.total_seconds()
    return float(timeout)


def _default_signer() -> SignerSnapshot:
    from butlers.core.runtime_probe_control.activation import activated_signer_snapshot

    return activated_signer_snapshot()


def _default_base_url() -> str:
    from butlers.core.runtime_probe_control.activation import switchboard_control_base_url

    return switchboard_control_base_url()


async def call_via_control_plane(
    butler: str,
    tool: str,
    arguments: dict[str, Any] | None,
    *,
    request_timeout: dt.timedelta | float | int | None = None,
    signer: Callable[[], SignerSnapshot] = _default_signer,
    base_url: Callable[[], str] = _default_base_url,
    transport: httpx.AsyncBaseTransport | None = None,
    now: dt.datetime | None = None,
) -> mcp.types.CallToolResult:
    """Carry one tool call through Switchboard, or raise ``ServiceCallUnavailable``."""
    instant = now or dt.datetime.now(dt.UTC)
    # The arguments as MCP would put them on the wire: the digest must cover
    # the same JSON Switchboard parses back out of the body.
    args = to_jsonable_python(arguments or {})
    snapshot = signer()
    if snapshot.signer is None or not snapshot.may_issue_at(instant):
        raise ServiceCallUnavailable("no control signer may issue a service call")
    try:
        compact = sign_service_call(
            snapshot.signer, butler=butler, tool=tool, args=args, now=instant
        )
    except (CapabilityRejected, ValueError) as exc:
        raise ServiceCallUnavailable("service call could not be signed") from exc

    try:
        async with httpx.AsyncClient(
            transport=transport, timeout=_seconds(request_timeout)
        ) as client:
            response = await client.post(
                f"{base_url().rstrip('/')}{SERVICE_CALL_PATH}",
                headers={"Authorization": f"Bearer {compact}"},
                json={"butler": butler, "tool": tool, "args": args},
            )
    except httpx.TimeoutException as exc:
        raise ServiceCallUnavailable("service call timed out at the control plane") from exc
    except httpx.HTTPError as exc:
        raise ServiceCallUnavailable("service call could not reach the control plane") from exc

    try:
        payload: Any = response.json()
    except ValueError:
        payload = None
    if response.status_code != 200 or not isinstance(payload, dict):
        code = payload.get("code") if isinstance(payload, dict) else None
        raise ServiceCallUnavailable(
            f"control plane refused the service call ({response.status_code}"
            f"{', ' + code if isinstance(code, str) and code.isidentifier() else ''})"
        )
    try:
        return mcp.types.CallToolResult.model_validate(payload.get("result"))
    except ValidationError as exc:
        raise ServiceCallUnavailable("control plane returned no tool result") from exc
