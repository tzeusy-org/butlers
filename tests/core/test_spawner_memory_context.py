"""Tests for module-local memory context injection in Spawner."""

from __future__ import annotations

import asyncio
import logging
import time
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
    async def test_embedding_engine_load_does_not_block_event_loop(self):
        """Slow embedding engine construction should not stall unrelated async work."""
        marker_started_at = time.monotonic()

        def slow_engine():
            time.sleep(0.25)
            return object()

        # The new dependency-inversion path goes through core.memory_hooks._memory_context_hook.
        # Register a hook that exercises the embedding-engine-in-thread pattern (delegating to
        # the patched module internals) to verify the slow engine call doesn't block the loop.
        import butlers.core.memory_hooks as _hooks

        context_mock = AsyncMock(return_value="# Memory Context\n")
        with (
            patch("butlers.modules.memory.tools.context.memory_context", context_mock),
            patch(
                "butlers.modules.memory.tools._helpers.get_embedding_engine",
                side_effect=slow_engine,
            ),
        ):

            async def _context_hook(
                pool,
                butler_name: str,
                prompt: str,
                *,
                token_budget: int = 3000,
            ):
                from butlers.modules.memory.tools import _helpers

                embedding_engine = await asyncio.to_thread(_helpers.get_embedding_engine)
                from butlers.modules.memory.tools import context as _context

                result = await _context.memory_context(
                    pool, embedding_engine, prompt, butler_name, token_budget=token_budget
                )
                if isinstance(result, str) and result.strip():
                    return result
                return None

            orig = _hooks._memory_context_hook
            _hooks._memory_context_hook = _context_hook
            try:
                fetch_task = asyncio.create_task(
                    fetch_memory_context(AsyncMock(), "my-butler", "hello")
                )
                await asyncio.sleep(0.05)
                marker_elapsed = time.monotonic() - marker_started_at
                result = await fetch_task
            finally:
                _hooks._memory_context_hook = orig

        assert marker_elapsed < 0.15
        assert result == "# Memory Context\n"

    async def test_returns_none_for_failure_empty_pool_or_missing_tables(
        self, caplog: pytest.LogCaptureFixture
    ):
        """None on RuntimeError; None when pool=None; None for whitespace; None for missing table (no traceback)."""
        import butlers.core.memory_hooks as _hooks

        orig = _hooks._memory_context_hook

        # RuntimeError → None (hook raises, spawner catches)
        async def _raise_runtime(pool, butler_name, prompt, *, token_budget=3000):
            raise RuntimeError("boom")

        _hooks._memory_context_hook = _raise_runtime
        try:
            assert await fetch_memory_context(AsyncMock(), "my-butler", "hello") is None
        finally:
            _hooks._memory_context_hook = orig

        # pool=None → None (guard before hook call)
        assert await fetch_memory_context(None, "my-butler", "hello") is None

        # Empty / whitespace context → None (hook returns None)
        async def _return_whitespace(pool, butler_name, prompt, *, token_budget=3000):
            return None  # hook returns None for whitespace (handled inside hook)

        _hooks._memory_context_hook = _return_whitespace
        try:
            assert await fetch_memory_context(AsyncMock(), "my-butler", "hello") is None
        finally:
            _hooks._memory_context_hook = orig

        # Missing table → None without traceback
        async def _raise_table(pool, butler_name, prompt, *, token_budget=3000):
            raise asyncpg.UndefinedTableError('relation "facts" does not exist')

        _hooks._memory_context_hook = _raise_table
        try:
            with caplog.at_level(logging.WARNING, logger="butlers.core.spawner"):
                result = await fetch_memory_context(AsyncMock(), "my-butler", "hello")
        finally:
            _hooks._memory_context_hook = orig
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
        assert (
            adapter.captured_system_prompts[-1]
            == "Base prompt.\n\nRemembered: user prefers concise answers."
        )
        mock_fetch.assert_awaited_once_with(None, "test-butler", "do task", token_budget=1234)

        # Disabled: fetch not called
        config_dir2 = tmp_path / "config2"
        config_dir2.mkdir()
        (config_dir2 / "CLAUDE.md").write_text("Base prompt.")
        config2 = _make_config(modules={})
        adapter2 = _CapturingAdapter()
        spawner2 = Spawner(config=config2, config_dir=config_dir2, runtime=adapter2)
        with patch(
            "butlers.core.spawner.fetch_memory_context", new_callable=AsyncMock
        ) as mock_fetch2:
            await spawner2.trigger(prompt="do task", trigger_source="trigger")
        mock_fetch2.assert_not_called()
