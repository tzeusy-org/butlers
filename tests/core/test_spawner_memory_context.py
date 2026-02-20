"""Tests for module-local memory context injection in Spawner."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from butlers.config import ButlerConfig
from butlers.core.spawner import Spawner, fetch_memory_context

pytestmark = pytest.mark.unit


def _make_config(
    name: str = "test-butler",
    port: int = 9100,
    modules: dict[str, dict] | None = None,
) -> ButlerConfig:
    return ButlerConfig(
        name=name,
        port=port,
        modules=modules or {},
        env_required=[],
        env_optional=[],
    )


class TestFetchMemoryContext:
    async def test_returns_context_from_local_memory_tools(self):
        pool = AsyncMock()
        embedding = object()

        with (
            patch(
                "butlers.modules.memory.tools.context.memory_context",
                new_callable=AsyncMock,
                return_value="Remembered context",
            ) as mock_context,
            patch(
                "butlers.modules.memory.tools._helpers.get_embedding_engine",
                return_value=embedding,
            ),
        ):
            result = await fetch_memory_context(pool, "my-butler", "hello", token_budget=4096)

        assert result == "Remembered context"
        mock_context.assert_awaited_once_with(
            pool,
            embedding,
            "hello",
            "my-butler",
            token_budget=4096,
        )

    async def test_returns_none_when_tool_raises(self):
        with patch(
            "butlers.modules.memory.tools.context.memory_context",
            new_callable=AsyncMock,
            side_effect=RuntimeError("boom"),
        ):
            result = await fetch_memory_context(AsyncMock(), "my-butler", "hello")
        assert result is None

    async def test_returns_none_when_pool_missing(self):
        result = await fetch_memory_context(None, "my-butler", "hello")
        assert result is None

    async def test_returns_none_for_empty_context(self):
        with (
            patch(
                "butlers.modules.memory.tools.context.memory_context",
                new_callable=AsyncMock,
                return_value="   ",
            ),
            patch(
                "butlers.modules.memory.tools._helpers.get_embedding_engine",
                return_value=object(),
            ),
        ):
            result = await fetch_memory_context(AsyncMock(), "my-butler", "hello")
        assert result is None


class TestSpawnerMemoryContextInjection:
    async def test_memory_context_injected_when_memory_module_enabled(self, tmp_path: Path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "CLAUDE.md").write_text("Base prompt.")
        config = _make_config(modules={"memory": {"retrieval": {"context_token_budget": 1234}}})

        captured_system_prompt: str | None = None

        async def capturing_sdk(*, prompt: str, options: Any):
            nonlocal captured_system_prompt
            captured_system_prompt = getattr(options, "system_prompt", None)
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

        spawner = Spawner(
            config=config,
            config_dir=config_dir,
            sdk_query=capturing_sdk,
        )

        with patch(
            "butlers.core.spawner.fetch_memory_context",
            new_callable=AsyncMock,
            return_value="Remembered: user prefers concise answers.",
        ) as mock_fetch:
            await spawner.trigger(prompt="do task", trigger_source="trigger")

        assert captured_system_prompt == (
            "Base prompt.\n\nRemembered: user prefers concise answers."
        )
        mock_fetch.assert_awaited_once_with(
            None,
            "test-butler",
            "do task",
            token_budget=1234,
        )

    async def test_memory_context_not_fetched_when_module_disabled(self, tmp_path: Path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "CLAUDE.md").write_text("Base prompt.")
        config = _make_config(modules={})

        async def fake_sdk(*, prompt: str, options: Any):
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

        spawner = Spawner(config=config, config_dir=config_dir, sdk_query=fake_sdk)

        with patch(
            "butlers.core.spawner.fetch_memory_context",
            new_callable=AsyncMock,
        ) as mock_fetch:
            await spawner.trigger(prompt="do task", trigger_source="trigger")

        mock_fetch.assert_not_called()
