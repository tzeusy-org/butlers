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
        pool = AsyncMock()
        with patch(
            "butlers.modules.memory.tools.writing.memory_store_episode",
            new_callable=AsyncMock,
            return_value={"id": "abc"},
        ) as mock_store:
            result = await store_session_episode(pool, "my-butler", "session output text")

        assert result is True
        mock_store.assert_awaited_once_with(
            pool,
            "session output text",
            "my-butler",
            session_id=None,
        )

    async def test_returns_false_when_tool_raises(self):
        with patch(
            "butlers.modules.memory.tools.writing.memory_store_episode",
            new_callable=AsyncMock,
            side_effect=RuntimeError("boom"),
        ):
            result = await store_session_episode(AsyncMock(), "my-butler", "session output")
        assert result is False

    async def test_returns_false_when_pool_missing(self):
        result = await store_session_episode(None, "my-butler", "session output")
        assert result is False

    async def test_passes_session_id_when_provided(self):
        pool = AsyncMock()
        sid = uuid.UUID("12345678-1234-5678-1234-567812345678")

        with patch(
            "butlers.modules.memory.tools.writing.memory_store_episode",
            new_callable=AsyncMock,
            return_value={"id": "abc"},
        ) as mock_store:
            await store_session_episode(pool, "my-butler", "output text", session_id=sid)

        mock_store.assert_awaited_once_with(
            pool,
            "output text",
            "my-butler",
            session_id="12345678-1234-5678-1234-567812345678",
        )

    async def test_missing_memory_tables_returns_false_without_traceback(
        self, caplog: pytest.LogCaptureFixture
    ):
        with (
            patch(
                "butlers.modules.memory.tools.writing.memory_store_episode",
                new_callable=AsyncMock,
                side_effect=asyncpg.UndefinedTableError('relation "episodes" does not exist'),
            ),
            caplog.at_level(logging.WARNING, logger="butlers.core.spawner"),
        ):
            result = await store_session_episode(AsyncMock(), "my-butler", "session output")

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
    async def test_episode_stored_after_successful_session_when_memory_enabled(
        self, tmp_path: Path
    ):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config(modules={"memory": {}})

        spawner = Spawner(
            config=config,
            config_dir=config_dir,
            runtime=_MockAdapter(result_text="Task completed"),
        )

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
            result = await spawner.trigger(prompt="do task", trigger_source="trigger")

        assert result.success is True
        mock_store.assert_awaited_once_with(
            None,
            "test-butler",
            "Task completed",
            session_id=None,
        )

    async def test_episode_not_stored_when_memory_module_disabled(self, tmp_path: Path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config(modules={})

        spawner = Spawner(
            config=config,
            config_dir=config_dir,
            runtime=_MockAdapter(result_text="Task completed"),
        )

        with patch(
            "butlers.core.spawner.store_session_episode",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_store:
            result = await spawner.trigger(prompt="do task", trigger_source="trigger")

        assert result.success is True
        mock_store.assert_not_called()

    async def test_episode_not_stored_after_failed_session(self, tmp_path: Path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config(modules={"memory": {}})

        spawner = Spawner(
            config=config,
            config_dir=config_dir,
            runtime=_MockAdapter(error="invocation failure"),
        )

        with patch(
            "butlers.core.spawner.store_session_episode",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_store:
            result = await spawner.trigger(prompt="do task", trigger_source="trigger")

        assert result.success is False
        mock_store.assert_not_called()
