"""Regression: the central-writer tool ``relationship_assert_fact`` must stay
registered on the relationship daemon regardless of the pruned LLM tool surface.

Owner carve-out (RFC 0017 §2.3) and the family-confidence gate create
``pending_actions`` rows stamped ``tool_name="relationship_assert_fact"``. The
daemon's approval-dispatch executor resolves that name against *this* daemon's
MCP registry (``daemon.py::_execute_approved_tool``). If the tool is not
registered, approving/retrying an owner fact fails at dispatch with::

    No registered handler for approved tool: relationship_assert_fact

which surfaces to the dashboard as a 502 "No reachable butler to dispatch
action". The tool used to be gated behind the ``entity`` group, which the tool-
surface prune (f19cf8a2a) dropped from the relationship butler — silently
breaking dispatch. It is now registered unconditionally; this test guards that.
"""

from __future__ import annotations

import pytest
from fastmcp import FastMCP

from butlers.modules._roster_relationship import (
    RelationshipModule,
    RelationshipModuleConfig,
)

# Groups the production relationship butler actually enables (roster/
# relationship/butler.toml) — deliberately excludes "entity".
_PRODUCTION_GROUPS = ["contacts", "interactions", "management", "tracking"]


async def _register(groups: list[str]) -> set[str]:
    mod = RelationshipModule()
    cfg = RelationshipModuleConfig(groups=groups)
    mcp = FastMCP("test-relationship")
    await mod.register_tools(mcp, cfg, db=None, butler_name="relationship")
    return {t.name for t in await mcp.list_tools()}


async def test_assert_fact_registered_without_entity_group():
    """The central writer registers even though ``entity`` is not enabled."""
    names = await _register(_PRODUCTION_GROUPS)
    assert "relationship_assert_fact" in names, (
        "relationship_assert_fact must stay registered for approval dispatch "
        "even with the pruned production group set"
    )


async def test_assert_fact_resolvable_via_get_tool():
    """Dispatch resolves the tool via ``mcp.get_tool`` — it must return it."""
    mod = RelationshipModule()
    cfg = RelationshipModuleConfig(groups=_PRODUCTION_GROUPS)
    mcp = FastMCP("test-relationship")
    await mod.register_tools(mcp, cfg, db=None, butler_name="relationship")

    tool = await mcp.get_tool("relationship_assert_fact")
    assert tool is not None
    assert callable(getattr(tool, "fn", None))


async def test_pruned_entity_reads_stay_pruned():
    """The prune still holds: entity-group *read* tools are not re-exposed."""
    names = await _register(_PRODUCTION_GROUPS)
    # These share the old ``entity`` group but are superseded by the memory
    # module's ``memory_entity_*`` tools; the writer is the only exception.
    assert "entity_resolve" not in names
    assert "relationship_lookup" not in names


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
