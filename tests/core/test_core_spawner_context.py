"""Tests for context parameter in trigger (butlers-06j.2)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from butlers.config import ButlerConfig
from butlers.core.spawner import CCSpawner

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


class TestContextParameter:
    """Test that context parameter is properly prepended to prompt."""

    async def test_trigger_without_context(self, tmp_path: Path):
        """When context is not provided, prompt is used as-is."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()

        captured_prompt: str | None = None

        async def capturing_sdk(*, prompt: str, options: Any):
            nonlocal captured_prompt
            captured_prompt = prompt
            from claude_agent_sdk import ResultMessage

            yield ResultMessage(
                subtype="result",
                duration_ms=10,
                duration_api_ms=8,
                is_error=False,
                num_turns=1,
                session_id="test-session",
                total_cost_usd=0.005,
                usage={},
                result="Done",
            )

        spawner = CCSpawner(
            config=config,
            config_dir=config_dir,
            sdk_query=capturing_sdk,
        )

        await spawner.trigger(prompt="do task", trigger_source="trigger_tool")
        assert captured_prompt == "do task"

    async def test_trigger_with_context(self, tmp_path: Path):
        """When context is provided, it is prepended to the prompt."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()

        captured_prompt: str | None = None

        async def capturing_sdk(*, prompt: str, options: Any):
            nonlocal captured_prompt
            captured_prompt = prompt
            from claude_agent_sdk import ResultMessage

            yield ResultMessage(
                subtype="result",
                duration_ms=10,
                duration_api_ms=8,
                is_error=False,
                num_turns=1,
                session_id="test-session",
                total_cost_usd=0.005,
                usage={},
                result="Done",
            )

        spawner = CCSpawner(
            config=config,
            config_dir=config_dir,
            sdk_query=capturing_sdk,
        )

        await spawner.trigger(
            prompt="do task",
            context="Here is some context.",
            trigger_source="trigger_tool",
        )
        assert captured_prompt == "Here is some context.\n\ndo task"

    async def test_trigger_with_empty_context(self, tmp_path: Path):
        """When context is empty string, prompt is used as-is."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()

        captured_prompt: str | None = None

        async def capturing_sdk(*, prompt: str, options: Any):
            nonlocal captured_prompt
            captured_prompt = prompt
            from claude_agent_sdk import ResultMessage

            yield ResultMessage(
                subtype="result",
                duration_ms=10,
                duration_api_ms=8,
                is_error=False,
                num_turns=1,
                session_id="test-session",
                total_cost_usd=0.005,
                usage={},
                result="Done",
            )

        spawner = CCSpawner(
            config=config,
            config_dir=config_dir,
            sdk_query=capturing_sdk,
        )

        await spawner.trigger(
            prompt="do task",
            context="",
            trigger_source="trigger_tool",
        )
        assert captured_prompt == "do task"
