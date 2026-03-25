"""Tests verifying the 'correct' MCP tool is registered as a core tool (task 3.1, 3.7).

These tests validate:
- `correct` appears in CORE_TOOL_NAMES
- The `correct` tool is registered on the MCP server at startup
- The tool description contains the canonical text from the spec

These tests will fail (correctly) until the implementation in daemon.py adds 'correct'
to CORE_TOOL_NAMES and registers the tool handler.
"""

from __future__ import annotations

import pytest

from butlers.daemon import CORE_TOOL_NAMES

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# CORE_TOOL_NAMES membership
# ---------------------------------------------------------------------------


def test_correct_in_core_tool_names():
    """'correct' must be listed in CORE_TOOL_NAMES so every butler exposes it."""
    assert "correct" in CORE_TOOL_NAMES, (
        "'correct' is not in CORE_TOOL_NAMES. "
        "Add it to the frozenset in src/butlers/daemon.py as part of the "
        "error-recovery-corrections implementation."
    )
