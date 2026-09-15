"""Keep dashboard owner authority out of runtime child environments."""

from __future__ import annotations

from collections.abc import Mapping


def without_owner_auth(env: Mapping[str, str]) -> dict[str, str]:
    """Copy a child environment without owner auth or administrative DB authority.

    Database variables can authorize the host enrollment CLI just as directly
    as dashboard credentials. The prefixes cover their complete config families.
    This narrow boundary
    preserves provider credentials and each caller's existing environment policy.
    It deliberately neither reads the host environment nor imports the API layer.
    """
    return {
        key: value
        for key, value in env.items()
        if key not in {"DASHBOARD_API_KEY", "DATABASE_URL"}
        and not key.startswith(("DASHBOARD_AUTH_", "POSTGRES_"))
    }
