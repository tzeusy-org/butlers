"""Tests verifying the 'correct' MCP tool is registered as a core tool (task 3.1).

These tests validate:
- `correct` appears in CORE_TOOL_NAMES

Tests for MCP server startup registration and tool description text are covered in
tests/core/test_corrections.py (tasks 7.9) once butlers.core.corrections is implemented.

These tests will fail (correctly) until the implementation in daemon.py adds 'correct'
to CORE_TOOL_NAMES as part of the error-recovery-corrections implementation.
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
