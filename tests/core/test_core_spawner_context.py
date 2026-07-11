"""Tests for context parameter in trigger (butlers-06j.2).

Also covers spawned-prompt parity across runtime adapters (bu-1mq1d.3).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from butlers.config import ButlerConfig
from butlers.core.runtimes.base import RuntimeAdapter
from butlers.core.spawner import Spawner

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
RELATIONSHIP_CONFIG_DIR = REPO_ROOT / "roster" / "relationship"

# Distinctive substring of the "Scope Filter — MANDATORY for All facts Table
# Queries" section in roster/relationship/AGENTS.md. Before bu-1mq1d.1
# (#3110), this section lived only in AGENTS.md while CLAUDE.md carried a
# separately-drifted body, so a Claude-runtime relationship session never
# received the mandate.
SCOPE_FILTER_MARKER = "Scope Filter — MANDATORY for All facts Table Queries"


def _make_config(
    name: str = "test-butler",
    port: int = 9100,
) -> ButlerConfig:
    return ButlerConfig(
        name=name,
        port=port,
        env_required=[],
        env_optional=[],
    )


class _CapturingAdapter(RuntimeAdapter):
    """Minimal capturing adapter for prompt context tests."""

    def __init__(self) -> None:
        self.captured_prompts: list[str] = []

    @property
    def binary_name(self) -> str:
        return "mock"

    async def invoke(
        self,
        prompt: str,
        system_prompt: str,
        mcp_servers: dict,
        env: dict,
        **kwargs: Any,
    ) -> tuple:
        self.captured_prompts.append(prompt)
        return "Done", [], None

    def build_config_file(self, mcp_servers: dict, tmp_dir: Any) -> Any:
        config_path = tmp_dir / "mock.json"
        config_path.write_text("{}")
        return config_path

    def parse_system_prompt_file(self, config_dir: Any) -> str:
        return ""


class TestContextParameter:
    """Test that context parameter is properly prepended to prompt."""

    async def test_trigger_context_prepend(self, tmp_path: Path):
        """No context → prompt as-is; with context → prepended; empty context → prompt as-is."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()
        adapter = _CapturingAdapter()
        spawner = Spawner(config=config, config_dir=config_dir, runtime=adapter)

        await spawner.trigger(prompt="do task", trigger_source="trigger_tool")
        assert adapter.captured_prompts[-1] == "do task"

        await spawner.trigger(
            prompt="do task", context="Here is some context.", trigger_source="trigger_tool"
        )
        assert adapter.captured_prompts[-1] == "Here is some context.\n\ndo task"

        await spawner.trigger(prompt="do task", context="", trigger_source="trigger_tool")
        assert adapter.captured_prompts[-1] == "do task"


class _SystemPromptCapturingAdapter(RuntimeAdapter):
    """Minimal adapter that records the composed ``system_prompt`` it is invoked with.

    Stands in for a real runtime adapter (Claude Code, Codex, ...): the
    Spawner composes exactly one system-prompt string in ``trigger()`` before
    calling ``invoke()``, regardless of which adapter is plugged in. Swapping
    this adapter in place of another therefore isolates whether that composed
    string differs by runtime.
    """

    def __init__(self) -> None:
        self.captured_system_prompts: list[str] = []

    @property
    def binary_name(self) -> str:
        return "mock"

    async def invoke(
        self,
        prompt: str,
        system_prompt: str,
        mcp_servers: dict,
        env: dict,
        **kwargs: Any,
    ) -> tuple:
        self.captured_system_prompts.append(system_prompt)
        return "Done", [], None

    def build_config_file(self, mcp_servers: dict, tmp_dir: Any) -> Any:
        config_path = tmp_dir / "mock.json"
        config_path.write_text("{}")
        return config_path

    def parse_system_prompt_file(self, config_dir: Any) -> str:
        return ""


class TestSpawnedPromptParityAcrossRuntimes:
    """bu-1mq1d.3: every runtime adapter must receive the identical composed prompt.

    ``Spawner.trigger()`` reads ``roster/<butler>/CLAUDE.md`` once via
    ``read_system_prompt`` (resolving the bare ``@AGENTS.md`` include) and
    composes it with routing/memory/context layers via
    ``spawner_context._compose_system_prompt`` — a single string that is then
    handed to whichever runtime adapter is plugged into the spawner. This
    locks in that no runtime-specific branch re-derives or truncates that
    string, and that the relationship butler's mandatory scope filter
    survives composition intact for every runtime.
    """

    async def test_both_runtime_adapters_receive_identical_composed_prompt(self) -> None:
        assert RELATIONSHIP_CONFIG_DIR.is_dir(), (
            f"expected roster/relationship at {RELATIONSHIP_CONFIG_DIR}"
        )
        config = _make_config(name="relationship", port=9101)

        # Two independently-instantiated adapters standing in for two distinct
        # runtimes (e.g. Claude Code vs. Codex). Each is wired into its own
        # Spawner over the SAME on-disk config dir.
        claude_like_adapter = _SystemPromptCapturingAdapter()
        codex_like_adapter = _SystemPromptCapturingAdapter()

        claude_spawner = Spawner(
            config=config, config_dir=RELATIONSHIP_CONFIG_DIR, runtime=claude_like_adapter
        )
        codex_spawner = Spawner(
            config=config, config_dir=RELATIONSHIP_CONFIG_DIR, runtime=codex_like_adapter
        )

        await claude_spawner.trigger(prompt="do task", trigger_source="trigger_tool")
        await codex_spawner.trigger(prompt="do task", trigger_source="trigger_tool")

        claude_prompt = claude_like_adapter.captured_system_prompts[-1]
        codex_prompt = codex_like_adapter.captured_system_prompts[-1]

        assert claude_prompt == codex_prompt, (
            "Spawner composed different system prompts for two runtime adapters over the "
            "same butler config dir — the merged body must be identical regardless of "
            "which runtime spawns the session."
        )
        assert SCOPE_FILTER_MARKER in claude_prompt, (
            "relationship's spawned system prompt is missing the mandatory scope filter "
            "section (roster/relationship/AGENTS.md 'Scope Filter' heading) — a Claude-runtime "
            "session would silently skip the scope='relationship' facts-table guard."
        )
