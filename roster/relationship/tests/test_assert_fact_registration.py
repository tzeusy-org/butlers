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

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastmcp import FastMCP

from butlers.modules._roster_relationship import (
    RelationshipModule,
    RelationshipModuleConfig,
)

# Groups the production Relationship butler currently enables. The mixed entity
# group stays pruned until its adopted six-read/two-write split is implemented.
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


async def test_approved_groups_register_expected_relationship_surface(monkeypatch):
    """Approved groups retain the fact writer and the owning channel resolver."""
    names = await _register(_PRODUCTION_GROUPS)
    assert len(names) == 62
    assert "identity_resolve_channels" in names
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

    # REQ-endpoint-custody-holds-002: actual registered callback selection, not
    # SQL/source/currentness evidence. Both external reads are explicit doubles.
    from types import SimpleNamespace

    from fastmcp import Client

    import butlers.identity as identity_module
    from butlers.core.custody_admission import CustodyAdmission, CustodyProfile
    from butlers.core.custody_bindings import (
        CustodyChannelBindings,
        install_binding_publisher,
        remove_binding_publisher,
    )
    from butlers.identity import ResolvedContact

    pool = object()
    entity_id = uuid.uuid4()
    events = []
    payload = {
        "name": "synthetic",
        "roles": ["owner"],
        "entity_id": str(entity_id),
        "is_unidentified": False,
    }
    admission = CustodyAdmission(
        CustodyProfile(
            "relationship",
            "butler_relationship_rw",
            ("domain_evidence",),
            ("write",),
            ("relationship",),
            "a" * 64,
        ),
        pool,
        None,
        host_enroll=None,
    )
    admission._ready = True  # SOFTWARE-only constructor allocation.
    publisher = CustodyChannelBindings(admission)

    async def observe(channel_type, values):
        events.append(("publisher", channel_type, values))
        return {value: dict(payload) for value in values}

    async def legacy(actual, pairs, *, raise_on_error):
        assert actual is pool and raise_on_error
        events.append(("legacy", pairs))
        return {pair: ResolvedContact("synthetic", ["owner"], entity_id) for pair in pairs}

    monkeypatch.setattr(publisher, "observe_channels", observe)
    monkeypatch.setattr(identity_module, "resolve_contacts_by_channel_bulk", legacy)
    mcp = FastMCP("owning-resolver-callback-control")
    mod = RelationshipModule()
    await mod.register_tools(
        mcp,
        RelationshipModuleConfig(groups=_PRODUCTION_GROUPS),
        db=SimpleNamespace(pool=pool),
        butler_name="relationship",
    )
    install_binding_publisher(pool, publisher)
    try:
        async with Client(mcp) as client:
            result = await client.call_tool(
                "identity_resolve_channels",
                {
                    "channel_type": "email",
                    "channel_values": ["synthetic@example.test"],
                },
            )
            assert result.structured_content == {"synthetic@example.test": payload}
            assert events == [("publisher", "email", ["synthetic@example.test"])]
            events.clear()
            empty = await client.call_tool(
                "identity_resolve_channels",
                {
                    "channel_type": "email",
                    "channel_values": [],
                },
            )
            assert empty.structured_content == {} and events == []
    finally:
        remove_binding_publisher(pool, publisher)
    async with Client(mcp) as client:
        result = await client.call_tool(
            "identity_resolve_channels",
            {
                "channel_type": "email",
                "channel_values": ["synthetic@example.test"],
            },
        )
    assert result.structured_content == {"synthetic@example.test": payload}
    assert events == [("legacy", [("email", "synthetic@example.test")])]
    assert set(payload) == {"name", "roles", "entity_id", "is_unidentified"}


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


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
