"""Regression tests for Chronicler deterministic schedule registration."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit

_CHRONICLER_JOBS = (
    "chronicler_project_sessions",
    "chronicler_project_calendar",
    "chronicler_project_owntracks",
    "chronicler_project_steam",
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
