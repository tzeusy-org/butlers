"""Tests for native scheduled-task dispatch in ButlerDaemon."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.config import ButlerConfig
from butlers.daemon import ButlerDaemon

pytestmark = pytest.mark.unit


class TestNativeScheduleDispatch:
    async def test_switchboard_eligibility_sweep_uses_native_job(self, tmp_path):
        """Switchboard eligibility sweep should bypass spawner/LLM dispatch."""
        daemon = ButlerDaemon(tmp_path)
        daemon.config = ButlerConfig(name="switchboard", port=40100)

        mock_pool = AsyncMock()
        mock_db = MagicMock()
        mock_db.pool = mock_pool
        daemon.db = mock_db

        mock_spawner = MagicMock()
        mock_spawner.trigger = AsyncMock()
        daemon.spawner = mock_spawner

        native_result = {"evaluated": 4, "skipped": 1, "transitioned": 2, "transitions": []}
        mock_native_job = AsyncMock(return_value=native_result)
        with patch(
            "butlers.daemon._load_switchboard_eligibility_sweep_job",
            return_value=mock_native_job,
        ):
            result = await daemon._dispatch_scheduled_task(
                prompt="ignored",
                trigger_source="schedule:eligibility-sweep",
            )

        assert result == native_result
        mock_native_job.assert_awaited_once_with(mock_pool)
        mock_spawner.trigger.assert_not_awaited()

    async def test_non_native_schedule_falls_back_to_spawner(self, tmp_path):
        """Schedules without native handlers should continue using spawner.trigger."""
        daemon = ButlerDaemon(tmp_path)
        daemon.config = ButlerConfig(name="switchboard", port=40100)

        mock_pool = AsyncMock()
        mock_db = MagicMock()
        mock_db.pool = mock_pool
        daemon.db = mock_db

        spawner_result = {"ok": True}
        mock_spawner = MagicMock()
        mock_spawner.trigger = AsyncMock(return_value=spawner_result)
        daemon.spawner = mock_spawner

        result = await daemon._dispatch_scheduled_task(
            prompt="run memory cleanup",
            trigger_source="schedule:memory-episode-cleanup",
        )

        assert result == spawner_result
        mock_spawner.trigger.assert_awaited_once_with(
            prompt="run memory cleanup",
            trigger_source="schedule:memory-episode-cleanup",
        )
