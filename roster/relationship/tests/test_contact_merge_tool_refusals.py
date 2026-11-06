"""The ``contact_merge`` tool turns the merge authority's bare classifications into
messages that name the contacts and the next step, as ``memory_entity_merge`` does."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from butlers.roster_imports import roster_module

relationship = roster_module("relationship", "modules")
contacts = roster_module("relationship", "tools/contacts")
entity_merge = roster_module("relationship", "tools/entity_merge")


async def _call_merge(monkeypatch, error: Exception, source: uuid.UUID, target: uuid.UUID):
    monkeypatch.setattr(contacts, "contact_merge", AsyncMock(side_effect=error))
    mod = relationship.RelationshipModule()
    mod._db = MagicMock()
    mcp = FastMCP("test-relationship")
    cfg = relationship.RelationshipModuleConfig(groups=["contacts_extended"])
    await mod.register_tools(mcp, cfg, db=mod._db, butler_name="relationship")
    return await mcp.call_tool(
        "contact_merge", {"source_id": str(source), "target_id": str(target)}
    )


@pytest.mark.parametrize(
    "classification,names,hint",
    [
        ("source_tombstoned", "source", "already merged"),
        ("target_tombstoned", "target", "already merged"),
        ("source_missing", "source", "contact_search"),
        ("target_missing", "target", "contact_search"),
        ("temporal_occurrence_collision", "both", "Resolve the duplicate"),
        ("plan_drift", "none", "plan_drift"),
    ],
)
async def test_refusal_names_the_contact_and_next_step(monkeypatch, classification, names, hint):
    source, target = uuid.uuid4(), uuid.uuid4()

    with pytest.raises(ToolError) as caught:
        await _call_merge(
            monkeypatch, entity_merge.EntityMergeError(classification), source, target
        )

    message = str(caught.value)
    assert "nothing was merged" in message
    assert hint in message
    assert (str(source) in message) == (names in {"source", "both"})
    assert (str(target) in message) == (names in {"target", "both"})
    assert message != classification


async def test_other_errors_pass_through_unchanged(monkeypatch):
    with pytest.raises(ToolError, match="Source contact x not found."):
        await _call_merge(
            monkeypatch, ValueError("Source contact x not found."), uuid.uuid4(), uuid.uuid4()
        )
