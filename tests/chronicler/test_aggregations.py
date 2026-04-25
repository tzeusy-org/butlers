"""Tests for butlers.chronicler.aggregations.

Covers:
- category_for() returns the correct non-'other' category for every active
  SUPPORTED source/episode_type pair declared in contracts.py.
- category_for() returns 'other' for unknown pairs.
- Guardrail: aggregations.py imports nothing from anthropic, openai, or
  claude_agent_sdk.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from butlers.chronicler.aggregations import CATEGORIES, category_for
from butlers.chronicler.contracts import INITIAL_SOURCES
from butlers.chronicler.models import Compatibility

# ── Mapping fixture: all active SUPPORTED source/episode_type pairs ────────

# The D1 table from design.md — expected (source_name, episode_type, category).
# This is the normative source of truth for the unit tests.
_D1_PAIRS: list[tuple[str, str, str]] = [
    ("core.sessions", "work", "work"),
    ("google_calendar.completed", "scheduled_block", "calendar"),
    ("spotify.session_summary", "listening_episode", "music"),
    ("steam.play_history", "play_episode", "gaming"),
    ("owntracks.points", "movement_episode", "travel"),
    ("google_health.measurements", "sleep_episode", "sleep"),
    ("health.meals", "eating_event", "meal"),
    ("home_assistant.history", "presence_episode", "home"),
]


@pytest.mark.parametrize("source_name,episode_type,expected", _D1_PAIRS)
def test_category_for_known_pairs(source_name: str, episode_type: str, expected: str) -> None:
    """Every D1 mapping must return a non-'other' category."""
    result = category_for(source_name, episode_type)
    assert result == expected, (
        f"category_for({source_name!r}, {episode_type!r}) → {result!r}; expected {expected!r}"
    )
    assert result != "other", f"Mapping for ({source_name!r}, {episode_type!r}) must not be 'other'"


def test_category_for_unknown_pair_returns_other() -> None:
    """Unmapped (source_name, episode_type) pairs must return 'other'."""
    assert category_for("unknown.source", "unknown_type") == "other"
    assert category_for("core.sessions", "nonexistent_type") == "other"
    assert category_for("", "") == "other"


def test_category_for_result_is_always_in_taxonomy() -> None:
    """category_for() must always return a value from the stable taxonomy."""
    for source_name, episode_type, _ in _D1_PAIRS:
        result = category_for(source_name, episode_type)
        assert result in CATEGORIES, (
            f"category_for({source_name!r}, {episode_type!r}) returned "
            f"{result!r} which is not in CATEGORIES"
        )
    # Unknown pair
    assert category_for("x", "y") in CATEGORIES


def test_all_supported_sources_have_non_other_category() -> None:
    """Every SUPPORTED source in contracts.py must map to a non-'other' category.

    This test enforces that adding a new SUPPORTED source requires also
    wiring it in the D1 mapping table. The episode_type is derived from
    the adapter constants collected in _D1_PAIRS source names.
    """
    supported_source_names = {
        s.source_name
        for s in INITIAL_SOURCES
        if s.chronicler_compatibility == Compatibility.SUPPORTED
    }
    d1_source_names = {pair[0] for pair in _D1_PAIRS}

    # Every SUPPORTED source must have at least one D1 entry.
    missing = supported_source_names - d1_source_names
    assert not missing, (
        f"SUPPORTED sources without D1 mapping entries: {sorted(missing)}. "
        "Add the (source_name, episode_type) → category mapping to "
        "aggregations._CATEGORY_MAP and a test row to _D1_PAIRS."
    )


# ── Guardrail: no LLM imports ──────────────────────────────────────────────

_AGGREGATIONS_MODULE = (
    Path(__file__).parent.parent.parent / "src" / "butlers" / "chronicler" / "aggregations.py"
)

_FORBIDDEN_IMPORTS = frozenset({"anthropic", "openai", "claude_agent_sdk"})


def test_aggregations_no_llm_imports() -> None:
    """aggregations.py must not import any LLM provider package."""
    source = _AGGREGATIONS_MODULE.read_text()
    tree = ast.parse(source)

    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in _FORBIDDEN_IMPORTS:
                    violations.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                root = node.module.split(".")[0]
                if root in _FORBIDDEN_IMPORTS:
                    violations.append(node.module)

    assert not violations, f"aggregations.py must not import LLM packages; found: {violations}"
