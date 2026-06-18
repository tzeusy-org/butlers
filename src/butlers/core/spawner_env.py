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
from typing import Any

from butlers.config import ButlerConfig
from butlers.core.telemetry import get_traceparent_env
from butlers.credential_store import CredentialStore

logger = logging.getLogger(__name__)


def _capture_pipeline_routing_context() -> dict[str, Any] | None:
    """Best-effort capture of switchboard routing context when available."""
    from butlers.core.routing_context import _routing_ctx_var

    payload = _routing_ctx_var.get()
    if not isinstance(payload, dict) or not payload:
        return None
    return dict(payload)


async def _build_env(
    config: ButlerConfig,
    module_credentials_env: dict[str, list[str]] | None = None,
    credential_store: CredentialStore | None = None,
) -> dict[str, str]:
    """Build an explicit env dict for the runtime instance.

    Includes a minimal runtime baseline (`PATH`) plus declared credentials.
    This keeps runtime shebang resolution (for example ``#!/usr/bin/env node``)
    working in spawned subprocesses without requiring machine-specific paths.

    Other than `PATH`, only declared variables are included — undeclared env
    vars do not leak through.  Includes butler-level required/optional vars
    and module credential vars.

    Runtime authentication is handled by CLI-level OAuth tokens (device-code
    flow via the dashboard), not API keys.

    When *credential_store* is provided, credentials are resolved from the
    DB only via ``CredentialStore.resolve()`` (no env fallback).
    When no store is provided (e.g. in unit tests without a DB pool),
    resolution falls back directly to ``os.environ``.
    """
    env: dict[str, str] = {}

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
        value = await _resolve(var)
        if value is not None:
            env[var] = value

    # Module credentials (DB-first passthrough to spawned instances)
    if module_credentials_env:
        for _module_name, cred_vars in module_credentials_env.items():
            for var in cred_vars:
                value = await _resolve(var)
                if value is not None:
                    env[var] = value

    # Include traceparent for distributed tracing
    env.update(get_traceparent_env())

    return env
