"""Messenger's delivery endpoint admits only Switchboard ``deliver()`` (RFC 0038 D7).

notify.v1 delivery shares the ``route.execute`` tool name with ordinary routed
requests. Today Messenger checks ``request_context.source_endpoint_identity``,
a string from the request body. Behind ``BUTLERS_SERVICE_PLANE_AUTH`` it
instead requires the caller to authenticate as the ``butlers-control``
workload, with the delivery audience, presenting the daemon ``switchboard``.

The switch defaults off. While it is off, nothing here reads a header, a file
or the key set, and both sides behave exactly as before.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from butlers.core.service_plane.relays import ServiceCaller
from butlers.core.service_plane.workload_identity import (
    DAEMON_HEADER,
    DELIVERY_AUDIENCE,
    TOKEN_DIR,
    TOKEN_HEADER,
    ApiServerJwks,
    WorkloadTokenPolicy,
    WorkloadTokenVerifier,
)

SWITCH_ENV = "BUTLERS_SERVICE_PLANE_AUTH"
ISSUER_ENV = "BUTLERS_SERVICE_PLANE_ISSUER"
DEFAULT_ISSUER = "https://kubernetes.default.svc.cluster.local"
_NAMESPACE_FILE = Path("/var/run/secrets/kubernetes.io/serviceaccount/namespace")

#: Each deployment ServiceAccount is its own workload label.
WORKLOAD_ACCOUNTS = ("butlers-control", "butlers-up", "dashboard-api")
MESSENGER_DELIVERY_CALLERS = frozenset({ServiceCaller("butlers-control", "switchboard")})
REFUSED = "messenger_delivery_caller_refused"


def service_plane_auth_enabled(environ: Mapping[str, str] = os.environ) -> bool:
    """The default-off verification switch for every service-plane check."""
    return environ.get(SWITCH_ENV) == "1"


def build_verifier(audience: str, environ: Mapping[str, str] = os.environ) -> WorkloadTokenVerifier:
    policy = WorkloadTokenPolicy(
        issuer=environ.get(ISSUER_ENV, DEFAULT_ISSUER),
        audience=audience,
        namespace=_NAMESPACE_FILE.read_text(encoding="utf-8").strip(),
        workloads={account: account for account in WORKLOAD_ACCOUNTS},
    )
    return WorkloadTokenVerifier(policy, ApiServerJwks())


_VERIFIERS: dict[str, WorkloadTokenVerifier] = {}


def cached_verifier(audience: str) -> WorkloadTokenVerifier | None:
    """One verifier per audience per process; ``None`` (refuse) outside a pod."""
    if audience not in _VERIFIERS:
        try:
            _VERIFIERS[audience] = build_verifier(audience)
        except OSError:
            return None
    return _VERIFIERS[audience]


async def admit_delivery_request(butler_name: str) -> str | None:
    """The route.execute call site: switch read, headers read, verdict."""
    if not service_plane_auth_enabled() or butler_name != "messenger":
        return None
    from fastmcp.server.dependencies import get_http_headers

    return await messenger_delivery_refusal(
        butler_name,
        get_http_headers(include_all=True),
        enabled=True,
        verifier=cached_verifier(DELIVERY_AUDIENCE),
    )


async def authenticate_caller(
    headers: Mapping[str, str], verifier: WorkloadTokenVerifier
) -> ServiceCaller | None:
    """The authenticated workload plus its presented daemon, or ``None``."""
    token, daemon = headers.get(TOKEN_HEADER), headers.get(DAEMON_HEADER)
    if not token or not daemon:
        return None
    workload = await verifier.verify(token)
    return ServiceCaller(workload, daemon) if workload is not None else None


async def messenger_delivery_refusal(
    butler_name: str,
    headers: Mapping[str, str],
    *,
    enabled: bool,
    verifier: WorkloadTokenVerifier | None,
) -> str | None:
    """``REFUSED`` when Messenger must not accept this delivery, else ``None``."""
    if not enabled or butler_name != "messenger":
        return None
    if verifier is None:
        return REFUSED
    caller = await authenticate_caller(headers, verifier)
    return None if caller in MESSENGER_DELIVERY_CALLERS else REFUSED


def messenger_delivery_headers(
    target_butler: str,
    tool_name: str,
    *,
    enabled: bool,
    token_dir: Path = TOKEN_DIR,
) -> dict[str, str]:
    """Headers Switchboard adds to a delivery; empty while the switch is off."""
    if not enabled or target_butler != "messenger" or tool_name != "route.execute":
        return {}
    token = (token_dir / DELIVERY_AUDIENCE).read_text(encoding="utf-8").strip()
    return {TOKEN_HEADER: token, DAEMON_HEADER: "switchboard"}
