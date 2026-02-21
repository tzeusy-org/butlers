"""Tests for native scheduled-task dispatch in ButlerDaemon."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.config import ButlerConfig
from butlers.daemon import ButlerDaemon

pytestmark = pytest.mark.unit


class TestNativeScheduleDispatch:
    async def test_switchboard_job_mode_dispatches_via_registry(self, tmp_path):
        """Job-mode deterministic schedules should dispatch via registry handler."""
        daemon = ButlerDaemon(tmp_path)
        daemon.config = ButlerConfig(name="switchboard", port=40100)

        mock_pool = AsyncMock()
        mock_db = MagicMock()
        mock_db.pool = mock_pool
        daemon.db = mock_db

        mock_spawner = MagicMock()
        mock_spawner.trigger = AsyncMock()
        daemon.spawner = mock_spawner

        native_result = {"evaluated": 1, "skipped": 0, "transitioned": 0, "transitions": []}
        mock_handler = AsyncMock(return_value=native_result)
        with patch.dict(
            "butlers.daemon._DETERMINISTIC_SCHEDULE_JOB_REGISTRY",
            {"switchboard": {"eligibility_sweep": mock_handler}},
            clear=True,
        ):
            result = await daemon._dispatch_scheduled_task(
                trigger_source="schedule:eligibility-sweep",
                job_name="eligibility_sweep",
                job_args={"dry_run": True},
            )

        assert result == native_result
        mock_handler.assert_awaited_once_with(mock_pool, {"dry_run": True})
        mock_spawner.trigger.assert_not_awaited()

    async def test_switchboard_legacy_schedule_name_alias_uses_registry(self, tmp_path):
        """Legacy schedule names should map to deterministic registry entries."""
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
        mock_handler = AsyncMock(return_value=native_result)
        with (
            patch.dict(
                "butlers.daemon._DETERMINISTIC_SCHEDULE_JOB_REGISTRY",
                {"switchboard": {"eligibility_sweep": mock_handler}},
                clear=True,
            ),
            patch.dict(
                "butlers.daemon._DETERMINISTIC_SCHEDULE_LEGACY_ALIASES",
                {"switchboard": {"eligibility-sweep": "eligibility_sweep"}},
                clear=True,
            ),
        ):
            result = await daemon._dispatch_scheduled_task(
                prompt="ignored",
                trigger_source="schedule:eligibility-sweep",
            )

        assert result == native_result
        mock_handler.assert_awaited_once_with(mock_pool, None)
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

    async def test_unknown_job_mode_raises(self, tmp_path):
        """Unknown deterministic job names fail explicitly."""
        daemon = ButlerDaemon(tmp_path)
        daemon.config = ButlerConfig(name="switchboard", port=40100)

        mock_pool = AsyncMock()
        mock_db = MagicMock()
        mock_db.pool = mock_pool
        daemon.db = mock_db

        mock_spawner = MagicMock()
        mock_spawner.trigger = AsyncMock()
        daemon.spawner = mock_spawner

        with pytest.raises(RuntimeError, match="Unknown deterministic scheduler job"):
            await daemon._dispatch_scheduled_task(
                trigger_source="schedule:some-job",
                job_name="unregistered_job",
            )
        mock_spawner.trigger.assert_not_awaited()

    async def test_blank_job_name_rejected(self, tmp_path):
        """Blank deterministic job names should fail with actionable error."""
        daemon = ButlerDaemon(tmp_path)
        daemon.config = ButlerConfig(name="switchboard", port=40100)

        mock_pool = AsyncMock()
        mock_db = MagicMock()
        mock_db.pool = mock_pool
        daemon.db = mock_db

        mock_spawner = MagicMock()
        mock_spawner.trigger = AsyncMock()
        daemon.spawner = mock_spawner

        with pytest.raises(RuntimeError, match="must be a non-empty string"):
            await daemon._dispatch_scheduled_task(
                trigger_source="schedule:eligibility-sweep",
                job_name="  ",
            )
        mock_spawner.trigger.assert_not_awaited()
