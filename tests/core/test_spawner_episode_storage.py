"""Tests for module-local episode storage after CC session completion."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

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
            "butlers.tools.memory.writing.memory_store_episode",
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
            "butlers.tools.memory.writing.memory_store_episode",
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
            "butlers.tools.memory.writing.memory_store_episode",
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


class TestSpawnerEpisodeStorageIntegration:
    async def test_episode_stored_after_successful_session_when_memory_enabled(
        self, tmp_path: Path
    ):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config(modules={"memory": {}})

        async def fake_sdk(*, prompt: str, options: Any):
            from claude_code_sdk import ResultMessage

            yield ResultMessage(
                subtype="result",
                duration_ms=10,
                duration_api_ms=8,
                is_error=False,
                num_turns=1,
                session_id="test-session",
                total_cost_usd=0.005,
                usage={},
                result="Task completed",
            )

        spawner = Spawner(config=config, config_dir=config_dir, sdk_query=fake_sdk)

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

        async def fake_sdk(*, prompt: str, options: Any):
            from claude_code_sdk import ResultMessage

            yield ResultMessage(
                subtype="result",
                duration_ms=10,
                duration_api_ms=8,
                is_error=False,
                num_turns=1,
                session_id="test-session",
                total_cost_usd=0.005,
                usage={},
                result="Task completed",
            )

        spawner = Spawner(config=config, config_dir=config_dir, sdk_query=fake_sdk)

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

        async def failing_sdk(*, prompt: str, options: Any):
            raise RuntimeError("SDK failure")
            yield  # pragma: no cover

        spawner = Spawner(config=config, config_dir=config_dir, sdk_query=failing_sdk)

        with patch(
            "butlers.core.spawner.store_session_episode",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_store:
            result = await spawner.trigger(prompt="do task", trigger_source="trigger")

        assert result.success is False
        mock_store.assert_not_called()
