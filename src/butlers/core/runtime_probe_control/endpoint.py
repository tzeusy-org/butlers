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

Beside it sits one ``GET``, and it is the single deliberate exception to the
no-query-string rule.  REQ-core-credentials-002 specifies it literally as ``GET
/_control/runtime-probe/v1/readiness?kid=<kid>``, and the exception holds
because the rule protects *capabilities*: a ``kid`` is a key identifier, not
key material, and it already travels in the clear in the protected header of
every capability this plane carries.  A proxy log line naming one therefore
discloses nothing the capability itself would not.

What the readiness route must not disclose is which key IDs a deployment
actually loaded.  It answers exactly one bit --- can this verifier issue for
that ``kid`` right now --- through a single not-ready rendering, so an unknown
key ID, a malformed one, a request shaped wrongly, and an unmounted keyring are
byte-identical by construction rather than by four branches that happen to
agree today.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Final

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from butlers.core.runtime_probe_control.coordinator import (
    HTTP_STATUS,
    ProbeResult,
    ProbeStatus,
)
from butlers.core.runtime_probe_control.keys import VerifierSnapshot, verifier_snapshot

#: The private path.  Not mounted under ``/mcp`` and not a FastMCP tool.
CONTROL_PATH: Final = "/_control/runtime-probe/v1"

#: The readiness gate the signed client waits on during full-stack startup.
READINESS_PATH: Final = f"{CONTROL_PATH}/readiness"

#: The one affirmative readiness word.  Every other outcome reuses the probe
#: vocabulary's ``unavailable``, so readiness adds no new status to the plane.
READY_STATUS: Final = "ready"

#: The only query parameter any route here accepts, on the only route that
#: accepts one at all.
READINESS_KEY_ID_PARAM: Final = "kid"

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


def _readiness_key_id(request: Request) -> str | None:
    """Extract the one ``kid``, or ``None`` if the request shape is wrong.

    This mirrors the ``POST`` route's refusals with one inversion in each
    direction.  A query string is *required* here, and must be exactly ``kid``
    and nothing else; an ``Authorization`` header is refused outright, because
    readiness accepts no capability and a capability offered here would be a
    second copy of the one thing that may exist in exactly one place.
    """
    headers = request.headers
    if "cookie" in headers or "authorization" in headers:
        return None
    items = request.query_params.multi_items()
    if len(items) != 1:
        return None
    name, value = items[0]
    if name != READINESS_KEY_ID_PARAM or not value:
        return None
    return value


def _render_readiness(ready: bool) -> JSONResponse:
    """Render the readiness answer, which has exactly two possible bodies.

    Every not-ready reason renders through this one call.  There is no
    malformed-key-ID branch at all: a syntactically impossible ``kid`` simply
    fails to match a loaded entry, which is the same code path an unknown one
    takes, so indistinguishability is structural rather than asserted.
    """
    if ready:
        return JSONResponse({"status": READY_STATUS}, status_code=200, headers=_NO_STORE)
    return JSONResponse(
        {"status": ProbeStatus.UNAVAILABLE.value},
        status_code=HTTP_STATUS[ProbeStatus.UNAVAILABLE],
        headers=_NO_STORE,
    )


def build_runtime_probe_readiness_route(
    verifier: Callable[[], VerifierSnapshot] = verifier_snapshot,
) -> Route:
    """Build the ``GET`` route the signed client gates its startup on.

    It reads the frozen keyring snapshot and nothing else: no capability, no
    catalog lookup, no runtime launch, and no coordinator.  ``ready`` answers
    the question the client actually has --- *if I sign under this key now,
    will Switchboard verify it?* --- which is issuance eligibility, not
    acceptance: a retiring key that may still be verified but may no longer
    issue is not ready, because signing under it would sign into a rejection.
    """

    async def _runtime_probe_readiness(request: Request) -> JSONResponse:
        kid = _readiness_key_id(request)
        # Read the body unconditionally, for the same reason the POST route
        # does: a request that carried one is refused, and draining it first
        # keeps the connection usable for the response.
        body = await request.body()
        ready = kid is not None and not body and verifier().is_ready_for(kid, datetime.now(UTC))
        return _render_readiness(ready)

    return Route(
        READINESS_PATH,
        _runtime_probe_readiness,
        methods=["GET"],
        name="runtime_probe_readiness",
        include_in_schema=False,
    )
