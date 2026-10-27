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

import tomllib
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastmcp import FastMCP

from butlers.modules._roster_relationship import (
    RelationshipModule,
    RelationshipModuleConfig,
)

# Explicit legacy eight-group pruned configuration. Keep its original inventory
# assertions separate from the actual current roster loaded below.
_PRODUCTION_GROUPS = [
    "contacts",
    "contacts_extended",
    "interactions",
    "relationships",
    "social",
    "notes",
    "tracking",
    "management",
]


async def _register(groups: list[str]) -> set[str]:
    mod = RelationshipModule()
    cfg = RelationshipModuleConfig(groups=groups)
    mcp = FastMCP("test-relationship")
    await mod.register_tools(mcp, cfg, db=None, butler_name="relationship")
    return {t.name for t in await mcp.list_tools()}


async def test_assert_fact_registered_on_production_surface():
    """The central writer registers on the current approved production surface."""
    names = await _register(_PRODUCTION_GROUPS)
    assert "relationship_assert_fact" in names, (
        "relationship_assert_fact must stay registered for approval dispatch "
        "on the production group set"
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


async def test_approved_groups_register_expected_relationship_surface():
    """Eight approved groups expose 60 tools plus the mandatory fact writer."""
    names = await _register(_PRODUCTION_GROUPS)
    assert len(names - {"identity_resolve_channels", "identity_assert_sender_channel"}) == 61
    assert {"identity_resolve_channels", "identity_assert_sender_channel"} <= names
    assert {
        "address_add",
        "upcoming_dates",
        "note_create",
        "relationship_add",
        "feed_get",
    } <= names
    assert {
        "entity_resolve",
        "entity_get",
        "entity_neighbors",
        "relationship_fact_evidence",
        "relationship_predicate_coverage",
        "relationship_lookup",
        "entity_update",
        "relationship_record_coverage",
    }.isdisjoint(names)


async def test_assert_fact_closure_invokes_library_writer(monkeypatch):
    """Invoking the registered tool must reach the library writer.

    Guards the closure body: ``butlers.tools.relationship`` re-exports the
    ``relationship_assert_fact`` *function* at package level, so the old
    ``_raf.relationship_assert_fact(...)`` access raised ``'function' object has
    no attribute 'relationship_assert_fact'`` at dispatch time — the exact
    failure that surfaced as a 502 on approval retry. A registration-only test
    does not catch this; the closure must actually be called.
    """
    outcome = MagicMock()
    outcome.as_dict.return_value = {"outcome": "inserted", "fact_id": str(uuid.uuid4())}
    writer = AsyncMock(return_value=outcome)

    # Patch the source function BEFORE registration so the closure's local
    # import binds the mock. Patch the module object directly (not a dotted
    # string) — ``butlers.tools.relationship`` is a roster-loaded package whose
    # submodules live in ``sys.modules`` but are not reachable via attribute
    # traversal, which monkeypatch's string form requires.
    import importlib

    writer_mod = importlib.import_module("butlers.tools.relationship.relationship_assert_fact")
    monkeypatch.setattr(writer_mod, "relationship_assert_fact", writer)

    mod = RelationshipModule()
    mod._db = MagicMock()  # _get_pool() returns self._db.pool
    cfg = RelationshipModuleConfig(groups=_PRODUCTION_GROUPS)
    mcp = FastMCP("test-relationship")
    await mod.register_tools(mcp, cfg, db=mod._db, butler_name="relationship")

    tool = await mcp.get_tool("relationship_assert_fact")
    subject = uuid.uuid4()
    result = await tool.fn(subject=subject, predicate="has-email", object="a@b.com")

    writer.assert_awaited_once()
    assert writer.await_args.kwargs.get("src") == "relationship"
    assert result == outcome.as_dict.return_value


_READS = {
    "entity_resolve",
    "entity_get",
    "entity_neighbors",
    "relationship_fact_evidence",
    "relationship_predicate_coverage",
    "relationship_lookup",
}
_WRITES = {"entity_update", "relationship_record_coverage", "entity_set_posture"}


def _roster_modules():
    return tomllib.loads((Path(__file__).parents[1] / "butler.toml").read_text())["modules"]


@pytest.mark.parametrize("groups,expected", [(["entity"], _READS), (["entity_write"], _WRITES)])
async def test_entity_groups_register_exact_membership(groups, expected):
    names = await _register(groups)
    assert names == expected | {"relationship_assert_fact"}
    assert "entity_create" not in names


async def test_actual_roster_adds_only_six_reads_to_current_approved_surface():
    groups = _roster_modules()["relationship"]["groups"]
    assert set(groups) == set(_PRODUCTION_GROUPS) | {"contacts_ingress", "entity"}
    assert "entity_write" not in groups
    current_pruned = await _register([g for g in groups if g != "entity"])
    active = await _register(groups)
    assert active - current_pruned == _READS
    assert current_pruned <= active
    assert len(current_pruned) == 65
    assert len(active) == 71
    assert _WRITES.isdisjoint(active)
    assert "relationship_assert_fact" in active


async def test_omitting_entity_groups_keeps_unconditional_writer():
    names = await _register(["contacts"])
    assert (_READS | _WRITES).isdisjoint(names)
    assert "relationship_assert_fact" in names


async def test_actual_roster_preserves_memory_entity_creation_owner():
    from butlers.modules.memory import MemoryModule, MemoryModuleConfig

    modules = _roster_modules()
    mcp = FastMCP("test-relationship-and-memory")
    await RelationshipModule().register_tools(
        mcp,
        RelationshipModuleConfig.model_validate(modules["relationship"]),
        db=None,
        butler_name="relationship",
    )
    await MemoryModule().register_tools(
        mcp,
        MemoryModuleConfig.model_validate(modules["memory"]),
        db=None,
        butler_name="relationship",
    )
    names = {tool.name for tool in await mcp.list_tools()}
    assert "memory_entity_create" in names
    assert "entity_create" not in names
    assert _READS <= names
    assert _WRITES.isdisjoint(names)
    assert "relationship_assert_fact" in names


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
