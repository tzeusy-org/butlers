"""Spawner — environment and pipeline routing context seam.

Responsible for:
 - building the explicit environment dict passed to the runtime instance
 - capturing the switchboard pipeline routing context (when available)

Extracted from butlers.core.spawner as part of bu-ipo5v (structural
decomposition into internal seams, follow-on to bu-dl98i.7.1).  The Spawner
continues to use these via re-exports so existing import paths and test patches
that reference ``butlers.core.spawner.<name>`` remain valid.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from typing import Any

from butlers.config import ButlerConfig
from butlers.core.child_env import without_owner_auth
from butlers.core.telemetry import get_traceparent_env
from butlers.credential_store import CredentialStore
from butlers.egress.http_transport import GatewayConfig

logger = logging.getLogger(__name__)
_TRANSPORT_KEYS = frozenset({"BUTLERS_EGRESS_MODE", "BUTLERS_EGRESS_GATEWAY"})


def _with_workload_egress(environment: Mapping[str, str]) -> dict[str, str]:
    """Carry only validated deployment transport controls into any child path."""
    gateway = GatewayConfig.from_environment()
    result = {key: value for key, value in environment.items() if key not in _TRANSPORT_KEYS}
    if gateway is not None:
        result.update(
            BUTLERS_EGRESS_MODE="request-v1",
            BUTLERS_EGRESS_GATEWAY=f"http://egress-gateway:{gateway.port}",
        )
    return result


def _capture_pipeline_routing_context() -> dict[str, Any] | None:
    """Best-effort capture of switchboard routing context when available."""
    from butlers.core.routing_context import _routing_ctx_var

    payload = _routing_ctx_var.get()
    if not isinstance(payload, dict) or not payload:
        return None
    return dict(payload)


async def _build_env(
    config: ButlerConfig,
    credential_store: CredentialStore | None = None,
) -> dict[str, str]:
    """Build an explicit env dict for the runtime instance.

    Includes a minimal runtime baseline (`PATH`) plus declared credentials.
    This keeps runtime shebang resolution (for example ``#!/usr/bin/env node``)
    working in spawned subprocesses without requiring machine-specific paths.

    Other than `PATH` and validated deployment egress selection, only declared
    variables are included — undeclared env vars do not leak through. Module
    channel credentials remain daemon-owned
    unless explicitly declared in the butler required/optional environment.
    Owner-auth and administrative database variables are always withheld, even
    if declared. Selected runtime authentication is added by the invocation path.

    When *credential_store* is provided, credentials are resolved from the
    DB only via ``CredentialStore.resolve()`` (no env fallback).
    When no store is provided (e.g. in unit tests without a DB pool),
    resolution falls back directly to ``os.environ``.
    """
    env = _with_workload_egress({})
    # These nonsecret transport controls belong to the workload deployment,
    # never a credential-store value or a model-supplied environment override.

    # Runtime baseline needed for CLI shebang resolution (e.g. /usr/bin/env node).
    host_path = os.environ.get("PATH")
    if host_path:
        env["PATH"] = host_path

    async def _resolve(key: str) -> str | None:
        """Resolve a credential key: DB-first when store available, else env."""
        if credential_store is not None:
            return await credential_store.resolve(key)
        return os.environ.get(key) or None

    # Butler-level required + optional env vars
    for var in config.env_required + config.env_optional:
        if var in _TRANSPORT_KEYS:
            continue
        value = await _resolve(var)
        if value is not None:
            env[var] = value

    # Include traceparent for distributed tracing
    env.update(get_traceparent_env())

    return without_owner_auth(env)
