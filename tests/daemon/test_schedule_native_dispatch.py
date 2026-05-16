"""Tests for native scheduled-task dispatch in ButlerDaemon."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.config import ButlerConfig
from butlers.core.model_routing import Complexity
from butlers.daemon import ButlerDaemon

pytestmark = pytest.mark.unit


class TestNativeScheduleDispatch:
    def _make_daemon(self, tmp_path, butler_name="switchboard", port=41100):
        daemon = ButlerDaemon(tmp_path)
        daemon.config = ButlerConfig(name=butler_name, port=port)
        mock_pool = AsyncMock()
        mock_db = MagicMock()
        mock_db.pool = mock_pool
        daemon.db = mock_db
        mock_spawner = MagicMock()
        mock_spawner.trigger = AsyncMock()
        daemon.spawner = mock_spawner
        return daemon, mock_spawner

    async def test_registry_and_job_dispatch_and_errors(self, tmp_path):
        """Registry has memory jobs + eligibility_sweep; rollup jobs removed; job-mode dispatches; unknown/blank raise."""
        from butlers.daemon import _DETERMINISTIC_SCHEDULE_JOB_REGISTRY

        expected_memory_jobs = {"memory_consolidation", "memory_episode_cleanup"}
        for butler_name in ("general", "health", "home", "relationship", "switchboard"):
            jobs = _DETERMINISTIC_SCHEDULE_JOB_REGISTRY.get(butler_name, {})
            missing = expected_memory_jobs - set(jobs)
            assert not missing, f"Missing memory jobs for {butler_name}: {sorted(missing)}"
        switchboard_jobs = _DETERMINISTIC_SCHEDULE_JOB_REGISTRY.get("switchboard", {})
        assert {"eligibility_sweep"} <= set(switchboard_jobs)
        removed = {
            "connector_stats_hourly_rollup",
            "connector_stats_daily_rollup",
            "connector_stats_pruning",
        }
        assert not (removed & set(switchboard_jobs)), (
            f"Removed jobs still present: {sorted(removed & set(switchboard_jobs))}"
        )

        daemon, mock_spawner = self._make_daemon(tmp_path)
        native_result = {"evaluated": 1, "skipped": 0, "transitioned": 0, "transitions": []}
        mock_handler = AsyncMock(return_value=native_result)

        # Job-mode dispatches via registry; spawner not called
        with patch.dict(
            "butlers.daemon._DETERMINISTIC_SCHEDULE_JOB_REGISTRY",
            {"switchboard": {"eligibility_sweep": mock_handler}},
            clear=True,
        ):
            result = await daemon._dispatch_scheduled_task(
                trigger_source="schedule:eligibility_sweep",
                job_name="eligibility_sweep",
                job_args={"dry_run": True},
            )
        assert result == native_result
        mock_handler.assert_awaited_once_with(daemon.db.pool, {"dry_run": True})
        mock_spawner.trigger.assert_not_awaited()

        # Job-mode with complexity param: handler doesn't get complexity
        mock_handler2 = AsyncMock(return_value={"evaluated": 1})
        mock_spawner.trigger.reset_mock()
        with patch.dict(
            "butlers.daemon._DETERMINISTIC_SCHEDULE_JOB_REGISTRY",
            {"switchboard": {"eligibility_sweep": mock_handler2}},
            clear=True,
        ):
            await daemon._dispatch_scheduled_task(
                trigger_source="schedule:eligibility_sweep",
                job_name="eligibility_sweep",
                complexity=Complexity.REASONING,
            )
        mock_spawner.trigger.assert_not_awaited()

        # Unknown job raises
        with pytest.raises(RuntimeError, match="Unknown deterministic scheduler job"):
            await daemon._dispatch_scheduled_task(
                trigger_source="schedule:some-job", job_name="unregistered_job"
            )

        # Blank job name raises
        with pytest.raises(RuntimeError, match="must be a non-empty string"):
            await daemon._dispatch_scheduled_task(
                trigger_source="schedule:eligibility_sweep", job_name="  "
            )

    async def test_prompt_dispatch_complexity_and_fallback(self, tmp_path):
        """Non-native schedule falls back to spawner; complexity forwarded; defaults to WORKHORSE."""
        daemon, mock_spawner = self._make_daemon(tmp_path, "general", 41101)

        # Non-native falls back to spawner
        spawner_result = {"ok": True}
        mock_spawner.trigger.return_value = spawner_result
        result = await daemon._dispatch_scheduled_task(
            prompt="run memory cleanup", trigger_source="schedule:non-native-prompt-task"
        )
        assert result == spawner_result
        mock_spawner.trigger.assert_awaited_once_with(
            prompt="run memory cleanup",
            trigger_source="schedule:non-native-prompt-task",
            complexity=Complexity.WORKHORSE,
            max_token_budget=None,
        )

        # Complexity forwarded
        mock_spawner.trigger.reset_mock()
        mock_spawner.trigger.return_value = spawner_result
        await daemon._dispatch_scheduled_task(
            prompt="complex task",
            trigger_source="schedule:complex-task",
            complexity=Complexity.REASONING,
        )
        call_kwargs = mock_spawner.trigger.call_args.kwargs
        assert call_kwargs["complexity"] is Complexity.REASONING

        # Default complexity is WORKHORSE
        mock_spawner.trigger.reset_mock()
        mock_spawner.trigger.return_value = spawner_result
        await daemon._dispatch_scheduled_task(
            prompt="routine task", trigger_source="schedule:routine"
        )
        assert mock_spawner.trigger.call_args.kwargs["complexity"] is Complexity.WORKHORSE
