"""Vouching for a held fact is an owner dashboard action, never an LLM tool (bu-jv1moa.14).

A session reads memory written from other people's words; if it could vouch for a
fact, a stranger's message could launder itself into owner-endorsed memory.  So no
registered MCP tool may expose the vouch, and no tool may take an endorsement field.
"""

from __future__ import annotations

import inspect

from butlers.modules.memory import tools as memory_tools
from tests.contracts.test_tool_presentation_inventory import _module_registration_union


async def test_no_registered_tool_vouches_for_a_fact():
    owners = await _module_registration_union()
    vouching = sorted(name for name in owners if "vouch" in name.lower())
    assert vouching == []
    # Rules are endorsed through the dashboard too; the fact path must not be the exception.
    assert not [name for name in owners if name.lower() in {"memory_endorse", "endorse_fact"}]


def test_memory_tools_do_not_import_the_vouch_storage_call():
    source = inspect.getsource(memory_tools)
    assert "endorse_fact" not in source
    assert "fact_vouched" not in source
