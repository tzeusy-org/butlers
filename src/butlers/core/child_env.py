"""Keep dashboard owner authority out of runtime child environments."""

from __future__ import annotations

from collections.abc import Mapping


def without_owner_auth(env: Mapping[str, str]) -> dict[str, str]:
    """Copy a child environment without dashboard-only keys or auth configuration.

    The prefix also covers future owner-auth credentials. This narrow boundary
    preserves provider credentials and each caller's existing environment policy.
    It deliberately neither reads the host environment nor imports the API layer.
    """
    return {
        key: value
        for key, value in env.items()
        if key != "DASHBOARD_API_KEY" and not key.startswith("DASHBOARD_AUTH_")
    }
