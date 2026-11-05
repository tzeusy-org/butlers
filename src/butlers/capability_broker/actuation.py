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
"""

from __future__ import annotations

import logging
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
