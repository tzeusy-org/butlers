"""Tests for the consolidation prompt template builder."""

from __future__ import annotations

import importlib.util

import pytest
from ._test_helpers import MEMORY_MODULE_PATH

# ---------------------------------------------------------------------------
# Load prompt_template module from disk (roster/ is not a Python package).
# ---------------------------------------------------------------------------

_MODULE_PATH = MEMORY_MODULE_PATH / "prompt_template.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("prompt_template", _MODULE_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_mod = _load_module()
build_consolidation_prompt = _mod.build_consolidation_prompt

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestBuildConsolidationPrompt:
    """Tests for build_consolidation_prompt()."""

    def test_includes_skill_md_content(self):
        """The prompt should include the SKILL.md template content."""
        prompt = build_consolidation_prompt(
            episodes=[],
            existing_facts=[],
            existing_rules=[],
            butler_name="test-butler",
        )
        # Key phrases from SKILL.md
        assert "Memory Consolidation" in prompt
        assert "permanence" in prompt
        assert "Output Format" in prompt
        assert "Guidelines" in prompt

    def test_episodes_section_formatted(self):
        """Episodes should appear in the Episodes to Process section."""
        episodes = [
            {
                "content": "User prefers dark mode",
                "butler": "ui-butler",
                "created_at": "2025-01-15T10:00:00Z",
                "importance": 5,
            },
            {
                "content": "User asked about Python packaging",
                "butler": "dev-butler",
                "created_at": "2025-01-15T11:00:00Z",
            },
        ]
        prompt = build_consolidation_prompt(
            episodes=episodes,
            existing_facts=[],
            existing_rules=[],
            butler_name="memory",
        )
        assert "## Episodes to Process" in prompt
        assert "User prefers dark mode" in prompt
        assert "User asked about Python packaging" in prompt
        assert "ui-butler" in prompt
        assert "2025-01-15T10:00:00Z" in prompt
        assert "[importance=5]" in prompt

    def test_existing_facts_show_ids(self):
        """Existing facts should display their IDs for dedup reference."""
        facts = [
            {
                "id": "aaaa-bbbb-cccc",
                "subject": "user",
                "predicate": "prefers",
                "content": "dark mode for all UIs",
                "permanence": "stable",
            },
            {
                "id": "dddd-eeee-ffff",
                "subject": "user",
                "predicate": "works_at",
                "content": "Acme Corp",
                "permanence": "standard",
            },
        ]
        prompt = build_consolidation_prompt(
            episodes=[],
            existing_facts=facts,
            existing_rules=[],
            butler_name="memory",
        )
        assert "## Existing Facts (for dedup)" in prompt
        assert "aaaa-bbbb-cccc" in prompt
        assert "dddd-eeee-ffff" in prompt
        assert "dark mode for all UIs" in prompt
        assert "Acme Corp" in prompt
        assert "[stable]" in prompt
        assert "[standard]" in prompt

    def test_existing_rules_show_ids(self):
        """Existing rules should display their IDs for dedup reference."""
        rules = [
            {
                "id": "rule-1111",
                "content": "Always greet user by first name",
                "status": "established",
            },
            {
                "id": "rule-2222",
                "content": "Use metric units unless user specifies otherwise",
                "status": "candidate",
            },
        ]
        prompt = build_consolidation_prompt(
            episodes=[],
            existing_facts=[],
            existing_rules=rules,
            butler_name="memory",
        )
        assert "## Existing Rules (for dedup)" in prompt
        assert "rule-1111" in prompt
        assert "rule-2222" in prompt
        assert "Always greet user by first name" in prompt
        assert "[established]" in prompt
        assert "[candidate]" in prompt

    def test_empty_episodes_returns_minimal_prompt(self):
        """With no episodes, the prompt should still be well-formed."""
        prompt = build_consolidation_prompt(
            episodes=[],
            existing_facts=[],
            existing_rules=[],
            butler_name="memory",
        )
        assert "## Episodes to Process" in prompt
        assert "_No episodes to process._" in prompt
        assert "_No existing facts._" in prompt
        assert "_No existing rules._" in prompt
        # Should still include the template
        assert "Memory Consolidation" in prompt

    def test_butler_name_appears_in_prompt(self):
        """The butler name should be present in the prompt."""
        prompt = build_consolidation_prompt(
            episodes=[],
            existing_facts=[],
            existing_rules=[],
            butler_name="calendar-butler",
        )
        assert "calendar-butler" in prompt
