"""Readable permission, quota and ceiling state for tests that drive a mock pool.

The pre-dispatch gates fail closed (bu-svqpzo.3, bu-svqpzo.15): a pool that cannot answer the
permission, quota, ceiling or spend-rule read refuses the dispatch. Most Spawner and model-call tests hand the code a
bare mock pool and want the gates out of the way, so this autouse fixture (imported into the
``conftest.py`` of the directories that need it) gives them the readable, unrestricted answer.

A test that exercises a gate itself patches that gate explicitly, which overrides this, or opts
out with ``@pytest.mark.real_gate_state`` to run the real functions against its own pool.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from butlers.core.model_routing import CeilingStatus, QuotaStatus, SpendRoutingResult
from butlers.core.permissions import PermissionStatus

_TARGETS = (
    ("check_permission", PermissionStatus(allowed=True)),
    (
        "check_token_quota",
        QuotaStatus(allowed=True, usage_24h=0, limit_24h=None, usage_30d=0, limit_30d=None),
    ),
    ("check_monthly_ceiling", CeilingStatus(allowed=True, mtd_usd=0.0, ceiling_usd=None)),
)


async def _no_spend_rules(pool, butler_name, tier, resolved, **kwargs):
    """An empty, readable spend-rule set (bu-svqpzo.15): the tier-resolved model, no cap."""
    return SpendRoutingResult(resolved=resolved)


_MODULES = (
    "butlers.core.spawner",
    "butlers.core.model_call",
    "butlers.core_tools._notifications",
    "butlers.core_tools._switchboard",
)


@pytest.fixture(autouse=True)
def readable_gate_state(request: pytest.FixtureRequest):
    if request.node.get_closest_marker("real_gate_state"):
        yield
        return
    patches = []
    for module in _MODULES:
        for name, answer in _TARGETS:
            try:
                module_obj = __import__(module, fromlist=[name])
            except ImportError:
                continue
            if hasattr(module_obj, name):
                patches.append(patch(f"{module}.{name}", AsyncMock(return_value=answer)))
        module_obj = __import__(module, fromlist=["apply_spend_routing_rules"])
        if hasattr(module_obj, "apply_spend_routing_rules"):
            patches.append(
                patch(f"{module}.apply_spend_routing_rules", AsyncMock(side_effect=_no_spend_rules))
            )
    for active in patches:
        active.start()
    try:
        yield
    finally:
        for active in reversed(patches):
            active.stop()
