"""Tests for module-local episode storage after runtime session completion."""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest

from butlers.config import ButlerConfig
from butlers.core.spawner import Spawner, store_session_episode

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


class TestStoreSessionEpisode:
    async def test_returns_true_on_success(self):
        """True returned on successful episode storage."""
        pool = AsyncMock()
        # The spawner.store_session_episode delegates to core.memory_hooks.store_session_episode.
        # Register a hook that calls the writing module (patched) so the test exercises the
        # end-to-end path without importing modules directly from spawner.
        writing_mock = AsyncMock(return_value={"id": "abc"})
        with patch("butlers.modules.memory.tools.writing.memory_store_episode", writing_mock):
            # Wire the owner runtime so store_session_episode's delegate can reach it.
            import butlers.core.memory_hooks as _hooks

            async def _store_hook(pool, butler_name, session_output, session_id=None):
                await writing_mock(
                    pool,
                    session_output,
                    butler_name,
                    session_id=str(session_id) if session_id is not None else None,
                )
                return True

            async def _unused_context(*_args, **_kwargs):
                return None

            runtime = _hooks.register_memory_session_runtime(
                "my-butler",
                context=_unused_context,
                store_episode=_store_hook,
            )
            try:
                result = await store_session_episode(pool, "my-butler", "session output text")
            finally:
                _hooks.unregister_memory_session_runtime("my-butler", runtime)

        assert result is True

    async def test_returns_false_on_error_no_pool_or_missing_tables(
        self, caplog: pytest.LogCaptureFixture
    ):
        """False on RuntimeError, None pool, whitespace output, or missing table (no traceback)."""
        import butlers.core.memory_hooks as _hooks

        # RuntimeError → False (hook raises, spawner catches)
        async def _raise_runtime(pool, butler_name, session_output, session_id=None):
            raise RuntimeError("boom")

        async def _unused_context(*_args, **_kwargs):
            return None

        runtime = _hooks.register_memory_session_runtime(
            "my-butler",
            context=_unused_context,
            store_episode=_raise_runtime,
        )
        try:
            assert await store_session_episode(AsyncMock(), "my-butler", "session output") is False
        finally:
            _hooks.unregister_memory_session_runtime("my-butler", runtime)

        # pool=None → False (guard before hook call)
        assert await store_session_episode(None, "my-butler", "session output") is False

        # Missing table → False without traceback
        async def _raise_table(pool, butler_name, session_output, session_id=None):
            raise asyncpg.UndefinedTableError('relation "episodes" does not exist')

        runtime = _hooks.register_memory_session_runtime(
            "my-butler",
            context=_unused_context,
            store_episode=_raise_table,
        )
        try:
            with caplog.at_level(logging.WARNING, logger="butlers.core.spawner"):
                result = await store_session_episode(AsyncMock(), "my-butler", "session output")
        finally:
            _hooks.unregister_memory_session_runtime("my-butler", runtime)
        assert result is False
        record = next(r for r in caplog.records if "memory tables are missing" in r.getMessage())
        assert record.exc_info is None


class _MockAdapter:
    """Minimal mock adapter for episode storage tests."""

    def __init__(self, result_text: str | None = None, error: str | None = None) -> None:
        self._result_text = result_text
        self._error = error

    @property
    def binary_name(self) -> str:
        return "mock"

    async def invoke(self, **kwargs):
        if self._error:
            raise RuntimeError(self._error)
        return self._result_text, [], None

    def build_config_file(self, mcp_servers, tmp_dir):
        config_path = tmp_dir / "mock_config.json"
        config_path.write_text("{}")
        return config_path

    def parse_system_prompt_file(self, config_dir):
        return ""

    def create_worker(self):
        return self

    @property
    def last_process_info(self):
        return None

    async def reset(self):
        pass


class TestSpawnerEpisodeStorageIntegration:
    async def test_consolidation_schedule_keeps_session_record_without_episode(
        self, tmp_path: Path
    ) -> None:
        """The consolidation runtime session is logged but not fed back into Eden."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        session_id = uuid.uuid4()
        pool = AsyncMock()

        with (
            patch(
                "butlers.core.spawner.fetch_memory_context",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "butlers.core.spawner.session_create",
                new_callable=AsyncMock,
                return_value=session_id,
            ) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock) as mock_complete,
            patch(
                "butlers.core.spawner.store_session_episode",
                new_callable=AsyncMock,
                return_value=True,
            ) as mock_store,
        ):
            result = await Spawner(
                config=_make_config(modules={"memory": {}}),
                config_dir=config_dir,
                pool=pool,
                runtime=_MockAdapter(result_text="Consolidation completed"),
            ).trigger(prompt="consolidate", trigger_source="schedule:consolidation")

        assert result.success is True
        assert result.session_id == session_id
        mock_create.assert_awaited_once()
        mock_complete.assert_awaited_once()
        mock_store.assert_not_awaited()

    async def test_other_scheduled_session_stores_episode(self, tmp_path: Path) -> None:
        """Ordinary scheduled work remains eligible for automatic episode storage."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()

        with (
            patch(
                "butlers.core.spawner.fetch_memory_context",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "butlers.core.spawner.store_session_episode",
                new_callable=AsyncMock,
                return_value=True,
            ) as mock_store,
        ):
            result = await Spawner(
                config=_make_config(modules={"memory": {}}),
                config_dir=config_dir,
                runtime=_MockAdapter(result_text="Daily digest completed"),
            ).trigger(prompt="write digest", trigger_source="schedule:daily_digest")

        assert result.success is True
        mock_store.assert_awaited_once_with(
            None,
            "test-butler",
            "Daily digest completed",
            session_id=None,
        )

    async def test_consolidation_prefix_schedule_stores_episode(self, tmp_path: Path) -> None:
        """Only the exact consolidation trigger skips automatic episode storage."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()

        with (
            patch(
                "butlers.core.spawner.fetch_memory_context",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "butlers.core.spawner.store_session_episode",
                new_callable=AsyncMock,
                return_value=True,
            ) as mock_store,
        ):
            result = await Spawner(
                config=_make_config(modules={"memory": {}}),
                config_dir=config_dir,
                runtime=_MockAdapter(result_text="Consolidation retry completed"),
            ).trigger(
                prompt="retry consolidation",
                trigger_source="schedule:consolidation:retry",
            )

        assert result.success is True
        mock_store.assert_awaited_once_with(
            None,
            "test-butler",
            "Consolidation retry completed",
            session_id=None,
        )

    async def test_episode_stored_when_memory_enabled_and_success_only(self, tmp_path: Path):
        """Episode stored on success with memory enabled; not stored when disabled or on failure."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()

        # Memory enabled + success → stored
        config = _make_config(modules={"memory": {}})
        with (
            patch(
                "butlers.core.spawner.fetch_memory_context",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "butlers.core.spawner.store_session_episode",
                new_callable=AsyncMock,
                return_value=True,
            ) as mock_store,
        ):
            result = await Spawner(
                config=config,
                config_dir=config_dir,
                runtime=_MockAdapter(result_text="Task completed"),
            ).trigger(prompt="do task", trigger_source="trigger")
        assert result.success is True
        mock_store.assert_awaited_once_with(None, "test-butler", "Task completed", session_id=None)

        # Memory disabled → not stored
        config2 = _make_config(modules={})
        with patch(
            "butlers.core.spawner.store_session_episode", new_callable=AsyncMock, return_value=True
        ) as mock_store2:
            result2 = await Spawner(
                config=config2,
                config_dir=config_dir,
                runtime=_MockAdapter(result_text="Task completed"),
            ).trigger(prompt="do task", trigger_source="trigger")
        assert result2.success is True
        mock_store2.assert_not_called()

        # Memory enabled + failure → not stored
        with patch(
            "butlers.core.spawner.store_session_episode", new_callable=AsyncMock, return_value=True
        ) as mock_store3:
            result3 = await Spawner(
                config=config,
                config_dir=config_dir,
                runtime=_MockAdapter(error="invocation failure"),
            ).trigger(prompt="do task", trigger_source="trigger")
        assert result3.success is False
        mock_store3.assert_not_called()
