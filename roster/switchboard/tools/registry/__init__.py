"""Registry tools — butler registration and discovery."""

from butlers.tools.switchboard.registry.registry import (
    DEFAULT_ROUTE_CONTRACT_VERSION,
    ELIGIBILITY_ACTIVE,
    ELIGIBILITY_QUARANTINED,
    ELIGIBILITY_STALE,
    discover_butlers,
    list_butlers,
    register_butler,
    resolve_routing_target,
    validate_route_target,
)
from butlers.tools.switchboard.registry.sweep import run_eligibility_sweep

__all__ = [
    "DEFAULT_ROUTE_CONTRACT_VERSION",
    "ELIGIBILITY_ACTIVE",
    "ELIGIBILITY_QUARANTINED",
    "ELIGIBILITY_STALE",
    "discover_butlers",
    "list_butlers",
    "register_butler",
    "resolve_routing_target",
    "run_eligibility_sweep",
    "validate_route_target",
]
