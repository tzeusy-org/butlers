"""The ``contact_merge`` tool turns the merge authority's bare classifications into
messages that name the contacts and the next step, as ``memory_entity_merge`` does."""

from __future__ import annotations

import importlib
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastmcp import FastMCP

from butlers.modules._roster_relationship import (
    RelationshipModule,
    RelationshipModuleConfig,
)


async def _tool(monkeypatch, error: Exception):
    contacts = importlib.import_module("butlers.tools.relationship.contacts")
    monkeypatch.setattr(contacts, "contact_merge", AsyncMock(side_effect=error))
    mod = RelationshipModule()
    mod._db = MagicMock()
    mcp = FastMCP("test-relationship")
    cfg = RelationshipModuleConfig(groups=["contacts_extended"])
    await mod.register_tools(mcp, cfg, db=mod._db, butler_name="relationship")
    return await mcp.get_tool("contact_merge")


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
    entity_merge = importlib.import_module("butlers.tools.relationship.entity_merge")
    tool = await _tool(monkeypatch, entity_merge.EntityMergeError(classification))
    source, target = uuid.uuid4(), uuid.uuid4()

    with pytest.raises(ValueError) as caught:
        await tool.fn(source_id=source, target_id=target)

    message = str(caught.value)
    assert "nothing was merged" in message
    assert hint in message
    assert (str(source) in message) == (names in {"source", "both"})
    assert (str(target) in message) == (names in {"target", "both"})
    assert message != classification


async def test_other_errors_pass_through_unchanged(monkeypatch):
    tool = await _tool(monkeypatch, ValueError("Source contact x not found."))

    with pytest.raises(ValueError, match="Source contact x not found."):
        await tool.fn(source_id=uuid.uuid4(), target_id=uuid.uuid4())
