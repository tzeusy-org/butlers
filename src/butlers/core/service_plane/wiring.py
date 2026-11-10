"""Wire the named relays into Switchboard and into the calling daemons (RFC 0038 D7)."""

from __future__ import annotations

import functools
import importlib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from butlers.core.service_plane.endpoint import build_relay_route
from butlers.core.service_plane.messenger_delivery import (
    cached_verifier,
    service_plane_auth_enabled,
)
from butlers.core.service_plane.relays import Dispatch, RelayEndpoint
from butlers.core.service_plane.workload_identity import RELAY_AUDIENCE


def build_switchboard_relay_route(pool: Any, roster_dir: Path) -> Any:
    """The relay route, dispatching through Switchboard's own ``route()``.

    ``route()`` keeps its registry, staleness, quarantine and routing-log
    behaviour; the relay only decides who may reach which target with what.
    The route answers 404 for every relay while the switch is off.
    """
    from butlers.core.domain_event_contracts import get_contract_registry
    from butlers.core.service_plane.registry import build_relays, roster_daemons

    @functools.cache
    def relays() -> Mapping[str, RelayEndpoint]:
        return build_relays(roster_daemons(roster_dir), get_contract_registry())

    def dispatch_for(endpoint: RelayEndpoint) -> Dispatch:
        async def dispatch(butler: str, tool: str, args: dict[str, Any]) -> Any:
            # Switchboard's routing package is loaded from the roster at runtime.
            route = importlib.import_module("butlers.tools.switchboard.routing.route").route

            return await route(
                pool,
                butler,
                tool,
                args,
                source_butler=f"relay:{endpoint.name}",
                allow_stale=endpoint.allow_stale,
            )

        return dispatch

    return build_relay_route(
        enabled=service_plane_auth_enabled,
        relays=relays,
        verifier=lambda: cached_verifier(RELAY_AUDIENCE),
        dispatch_for=dispatch_for,
    )
