"""Wire the named relays into Switchboard and into the calling daemons (RFC 0038 D7)."""

from __future__ import annotations

import functools
import importlib
import json
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any, Final

from butlers.core.service_plane.endpoint import build_relay_route
from butlers.core.service_plane.messenger_delivery import (
    cached_verifier,
    service_plane_auth_enabled,
)
from butlers.core.service_plane.relays import Dispatch, RelayEndpoint
from butlers.core.service_plane.workload_identity import RELAY_AUDIENCE

#: route() adds this carrier to the target's arguments while a span is active.
_TRACE_CONTEXT_KEY: Final = "trace_context"


def build_switchboard_relay_route(pool: Any, roster_dir: Path) -> Any:
    """The relay route, dispatching through Switchboard's own ``route()``.

    ``route()`` keeps its registry, staleness, quarantine and routing-log
    behaviour; the relay only decides who may reach which target with what.
    The route answers 404 for every relay while the switch is off.
    """
    from butlers.core.domain_event_contracts import get_contract_registry
    from butlers.core.service_plane.registry import (
        build_relays,
        roster_daemons,
        roster_module_daemons,
    )
    from butlers.core_tools._memory_catalog import catalog_pointer_reader

    @functools.cache
    def relays() -> Mapping[str, RelayEndpoint]:
        return build_relays(
            roster_daemons(roster_dir),
            get_contract_registry(),
            contacts_daemons=roster_module_daemons("contacts", roster_dir),
            memory_daemons=roster_module_daemons("memory", roster_dir),
            catalog_pointer=catalog_pointer_reader(pool),
        )

    return build_relay_route(
        enabled=service_plane_auth_enabled,
        relays=relays,
        verifier=lambda: cached_verifier(RELAY_AUDIENCE),
        dispatch_for=lambda endpoint: relay_dispatch(pool, endpoint),
    )


def relay_dispatch(pool: Any, endpoint: RelayEndpoint) -> Dispatch:
    """Forward one relay call through Switchboard's ``route()`` with ``relay_call_fn``."""

    async def dispatch(butler: str, tool: str, args: dict[str, Any]) -> Any:
        # Switchboard's routing package is loaded from the roster at runtime.
        routing = importlib.import_module("butlers.tools.switchboard.routing.route")
        return await routing.route(
            pool,
            butler,
            tool,
            args,
            source_butler=f"relay:{endpoint.name}",
            allow_stale=endpoint.allow_stale,
            call_fn=relay_call_fn(routing),
        )

    return dispatch


def relay_call_fn(routing: Any) -> Callable[[str, str, dict[str, Any]], Awaitable[Any]]:
    """``route()``'s tool call for a relay target.

    Two differences from ``route()``'s default call:

    * the ``trace_context`` carrier ``route()`` adds while a span is active is
      dropped. Relay targets are strict service twins whose fixed signatures
      refuse an unexpected keyword, and no relay's arguments carry that key;
    * a target error result (FastMCP's ``is_error``; ``isError`` is the wire
      name) is ``TransportRejected``, so ``route()`` reports a refused,
      non-retryable call instead of a delivered one.

    A successful result is read as ``route()`` reads it: the first text block,
    JSON-decoded when it parses.
    """

    async def call(endpoint_url: str, tool: str, args: dict[str, Any]) -> Any:
        sent = {key: value for key, value in args.items() if key != _TRACE_CONTEXT_KEY}
        result = await routing._call_tool_with_router_client(endpoint_url, tool, sent)
        if getattr(result, "is_error", False) is True or getattr(result, "isError", False) is True:
            text = routing._extract_mcp_error_text(result)
            raise routing.TransportRejected(text or f"Tool '{tool}' returned an error.")
        for block in getattr(result, "content", None) or []:
            text = getattr(block, "text", None)
            if isinstance(text, str):
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    return text
        raise routing.TransportUncertain(f"Tool '{tool}' returned no text content blocks")

    return call
