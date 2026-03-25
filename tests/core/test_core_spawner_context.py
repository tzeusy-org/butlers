"""Tests for context parameter in trigger (butlers-06j.2)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from butlers.config import ButlerConfig
from butlers.core.runtimes.base import RuntimeAdapter
from butlers.core.spawner import Spawner

pytestmark = pytest.mark.unit


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

    async def test_trigger_without_context(self, tmp_path: Path):
        """When context is not provided, prompt is used as-is."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()

        adapter = _CapturingAdapter()
        spawner = Spawner(
            config=config,
            config_dir=config_dir,
            runtime=adapter,
        )

        await spawner.trigger(prompt="do task", trigger_source="trigger_tool")
        assert adapter.captured_prompts[-1] == "do task"

    async def test_trigger_with_context(self, tmp_path: Path):
        """When context is provided, it is prepended to the prompt."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()

        adapter = _CapturingAdapter()
        spawner = Spawner(
            config=config,
            config_dir=config_dir,
            runtime=adapter,
        )

        await spawner.trigger(
            prompt="do task",
            context="Here is some context.",
            trigger_source="trigger_tool",
        )
        assert adapter.captured_prompts[-1] == "Here is some context.\n\ndo task"

    async def test_trigger_with_empty_context(self, tmp_path: Path):
        """When context is empty string, prompt is used as-is."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()

        adapter = _CapturingAdapter()
        spawner = Spawner(
            config=config,
            config_dir=config_dir,
            runtime=adapter,
        )

        await spawner.trigger(
            prompt="do task",
            context="",
            trigger_source="trigger_tool",
        )
        assert adapter.captured_prompts[-1] == "do task"
