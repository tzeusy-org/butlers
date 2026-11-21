"""RFC 0028's physical-risk map, read for broker ``actuate`` dispositions.

The Home butler owns the map (``roster/home/modules/actuation.py``); this adapter
only translates a goal call into that map's inputs and its tier into the
broker's ``ActuationRisk``. Within RFC 0028's automatic subset, ``safe`` and
``reversible`` are ``safe`` and follow the tool's approval policy. Consequential
and protected services always need per-event approval; the map makes a service
it does not declare protected. A capability outside the map, an argument shape
it cannot read, or a failure to read it is ``unknown`` and parks the same way.
The broker holds no entity unit cache, so a climate set-point is promoted
exactly as RFC 0028 promotes an unknown unit.

The Home module re-checks the same map with its own cache when the call reaches
it; the broker's tier only decides whether a goal may reach it unasked.

Critical entities (REQ-module-home-assistant-002, RFC 0028 Amendment 1): a goal call that
names an entity the owner marked critical is ``consequential`` and parks per event, with no
standing rule. If the registry cannot be read, every Home call except a ``safe`` read is
``unknown``, which parks the same way; a broker given no registry reader cannot read it. The
broker holds no entity cache, so it does not expand groups: the Home module does, on its own
cache, when the call reaches it.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from .contracts import ActuationRisk, Capability

logger = logging.getLogger(__name__)

_SCENE_PREFIX = "scene."
_TIERS: dict[str, ActuationRisk] = {
    "safe": "safe",
    "reversible": "safe",
    "consequential": "consequential",
    "protected": "protected",
}


def _risk_map():
    # Lazy: the roster module loads only when a goal calls an actuate capability.
    from butlers.roster_imports import roster_module

    return roster_module("home", "modules/actuation")


def _home_call(capability: Capability, args: dict[str, Any]) -> tuple[Any, ...] | None:
    """The (domain, service, target, data) a Home Assistant capability performs."""
    if capability.name == "ha_call_service":
        call = (args.get("domain"), args.get("service"), args.get("target"), args.get("data"))
        if type(call[0]) is str and type(call[1]) is str:
            return call
    elif capability.name == "ha_activate_scene":
        entity_id = args.get("entity_id")
        if type(entity_id) is str and entity_id.startswith(_SCENE_PREFIX):
            return ("scene", "turn_on", {"entity_id": entity_id}, None)
    return None


#: Reads the owner's critical entity ids now; ``None`` when they cannot be read.
CriticalRegistryReader = Callable[[], Awaitable[frozenset[str] | None]]
_REGISTRY_TIMEOUT = 2.0


def _ordinary_entity(_entity_id: str) -> dict[str, Any]:
    return {}


async def critical_actuation_risk(
    risk: ActuationRisk,
    capability: Capability,
    args: dict[str, Any],
    reader: CriticalRegistryReader | None,
) -> ActuationRisk:
    """Apply the critical-entity registry to a Home call's RFC 0028 tier.

    Never lowers a tier. Calls that are not Home Assistant calls, and ``safe`` reads, keep
    their tier whatever the registry says.
    """
    call = _home_call(capability, args)
    if call is None:
        return risk
    domain, service, target, data = call
    try:
        actuation = _risk_map()
        target, data = actuation.snapshot_actuation_arguments(target, data)
        if actuation.classify_actuation(domain, service).value == "safe":
            return risk
        registry = None
        if reader is not None:
            try:
                registry = await asyncio.wait_for(reader(), _REGISTRY_TIMEOUT)
            except Exception:  # noqa: BLE001 - an unreadable registry fails closed.
                logger.debug("capability_broker.critical_registry_unreadable")
        if registry is None:
            return "unknown"
        critical = actuation.names_critical_entity(
            domain, service, target, data, registry=registry, attributes=_ordinary_entity
        )
    except Exception:  # noqa: BLE001 - an unreadable call parks, never dispatches.
        logger.debug("capability_broker.actuation_risk_unreadable")
        return "unknown"
    if not critical or risk in ("consequential", "protected", "unknown"):
        return risk
    return "consequential"


def rfc0028_actuation_risk(capability: Capability, args: dict[str, Any]) -> ActuationRisk:
    call = _home_call(capability, args)
    if call is None:
        return "unknown"
    domain, service, target, data = call
    try:
        actuation = _risk_map()
        target, data = actuation.snapshot_actuation_arguments(target, data)
        tier = actuation.effective_actuation_risk(domain, service, target, data).risk
    except Exception:  # noqa: BLE001 - an unreadable call parks, never dispatches.
        logger.debug("capability_broker.actuation_risk_unreadable")
        return "unknown"
    return _TIERS.get(str(tier), "unknown")
