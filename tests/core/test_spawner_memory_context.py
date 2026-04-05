"""Tests for module-local memory context injection in Spawner."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest

from butlers.config import ButlerConfig
from butlers.core.runtimes.base import RuntimeAdapter
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

    async def test_returns_none_for_failure_empty_pool_or_missing_tables(
        self, caplog: pytest.LogCaptureFixture
    ):
        """None on RuntimeError; None when pool=None; None for whitespace; None for missing table (no traceback)."""
        # RuntimeError → None
        with patch(
            "butlers.modules.memory.tools.context.memory_context",
            new_callable=AsyncMock,
            side_effect=RuntimeError("boom"),
        ):
            assert await fetch_memory_context(AsyncMock(), "my-butler", "hello") is None

        # pool=None → None (no call at all)
        assert await fetch_memory_context(None, "my-butler", "hello") is None

        # Empty / whitespace context → None
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
            assert await fetch_memory_context(AsyncMock(), "my-butler", "hello") is None

        # Missing table → None without traceback
        with (
            patch(
                "butlers.modules.memory.tools.context.memory_context",
                new_callable=AsyncMock,
                side_effect=asyncpg.UndefinedTableError('relation "facts" does not exist'),
            ),
            caplog.at_level(logging.WARNING, logger="butlers.core.spawner"),
        ):
            result = await fetch_memory_context(AsyncMock(), "my-butler", "hello")
        assert result is None
        record = next(r for r in caplog.records if "memory tables are missing" in r.getMessage())
        assert record.exc_info is None


class _CapturingAdapter(RuntimeAdapter):
    """Minimal capturing adapter for system_prompt injection tests."""

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
        claude_md = config_dir / "CLAUDE.md"
        if claude_md.exists():
            return claude_md.read_text().strip()
        return ""


class TestSpawnerMemoryContextInjection:
    async def test_memory_context_injected_and_skipped_when_disabled(self, tmp_path: Path):
        """Memory context injected with custom budget when enabled; not fetched when disabled."""
        # Enabled: context injected with custom budget
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "CLAUDE.md").write_text("Base prompt.")
        config = _make_config(modules={"memory": {"retrieval": {"context_token_budget": 1234}}})
        adapter = _CapturingAdapter()
        spawner = Spawner(config=config, config_dir=config_dir, runtime=adapter)
        with patch(
            "butlers.core.spawner.fetch_memory_context",
            new_callable=AsyncMock,
            return_value="Remembered: user prefers concise answers.",
        ) as mock_fetch:
            await spawner.trigger(prompt="do task", trigger_source="trigger")
        assert adapter.captured_system_prompts[-1] == "Base prompt.\n\nRemembered: user prefers concise answers."
        mock_fetch.assert_awaited_once_with(None, "test-butler", "do task", token_budget=1234)

        # Disabled: fetch not called
        config_dir2 = tmp_path / "config2"
        config_dir2.mkdir()
        (config_dir2 / "CLAUDE.md").write_text("Base prompt.")
        config2 = _make_config(modules={})
        adapter2 = _CapturingAdapter()
        spawner2 = Spawner(config=config2, config_dir=config_dir2, runtime=adapter2)
        with patch("butlers.core.spawner.fetch_memory_context", new_callable=AsyncMock) as mock_fetch2:
            await spawner2.trigger(prompt="do task", trigger_source="trigger")
        mock_fetch2.assert_not_called()
