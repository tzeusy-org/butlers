"""Regression tests for Chronicler deterministic schedule registration."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.config import ButlerConfig
from butlers.daemon import ButlerDaemon

pytestmark = pytest.mark.unit

_CHRONICLER_JOBS = (
    "chronicler_project_sessions",
    "chronicler_project_calendar",
    "chronicler_project_owntracks",
    "chronicler_project_steam",
    "chronicler_project_exercise_inferred",
)


def test_chronicler_jobs_registered_callable_and_resolve() -> None:
    from butlers.daemon import (
        _DETERMINISTIC_SCHEDULE_JOB_REGISTRY,
        _resolve_deterministic_schedule_job_name,
    )

    chronicler_jobs = _DETERMINISTIC_SCHEDULE_JOB_REGISTRY.get("chronicler", {})
    missing = [job_name for job_name in _CHRONICLER_JOBS if job_name not in chronicler_jobs]
    assert not missing, f"Chronicler deterministic jobs not registered: {missing}"

    not_callable = [
        job_name for job_name in _CHRONICLER_JOBS if not callable(chronicler_jobs[job_name])
    ]
    assert not not_callable, f"Chronicler handlers not callable: {not_callable}"

    for job_name in _CHRONICLER_JOBS:
        resolved = _resolve_deterministic_schedule_job_name(
            butler_name="chronicler",
            trigger_source=f"schedule:{job_name}",
            job_name=job_name,
        )
        assert resolved == job_name


@pytest.mark.asyncio
async def test_chronicler_dispatch_recovers_when_registry_entry_is_missing(tmp_path) -> None:
    daemon = ButlerDaemon(tmp_path)
    daemon.config = ButlerConfig(name="chronicler", port=41111)
    daemon.db = MagicMock()
    daemon.db.pool = AsyncMock()
    daemon.spawner = MagicMock()
    daemon.spawner.trigger = AsyncMock()

    expected = {"source_name": "steam.play_history", "rows_projected": 1}
    mock_handler = AsyncMock(return_value=expected)

    with (
        patch("butlers.scheduled_jobs._run_chronicler_project_steam_job", mock_handler),
        patch.dict("butlers.background._DETERMINISTIC_SCHEDULE_JOB_REGISTRY", {}, clear=True),
    ):
        result = await daemon._dispatch_scheduled_task(
            trigger_source="schedule:chronicler_project_steam",
            job_name="chronicler_project_steam",
        )

    assert result == expected
    mock_handler.assert_awaited_once_with(daemon.db.pool, None)
    daemon.spawner.trigger.assert_not_awaited()
