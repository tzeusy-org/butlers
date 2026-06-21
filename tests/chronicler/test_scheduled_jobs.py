"""Unit tests for Chronicler deterministic scheduled jobs."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.chronicler.adapters.base import AdapterResult
from butlers.scheduled_jobs import (
    _DETERMINISTIC_SCHEDULE_JOB_REGISTRY,
    _discover_chronicler_projection_schemas,
    _resolve_deterministic_schedule_job_name,
    _run_chronicler_project_calendar_job,
    _run_chronicler_project_sessions_job,
)

pytestmark = pytest.mark.unit


class _AsyncCtx:
    def __init__(self, obj: object) -> None:
        self._obj = obj

    async def __aenter__(self) -> object:
        return self._obj

    async def __aexit__(self, *_: object) -> None:
        pass


def _pool_with_schema_rows(*schemas: str):
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=[{"table_schema": schema} for schema in schemas])

    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))
    return pool, conn


def test_chronicler_jobs_registered_callable_and_resolvable() -> None:
    jobs = _DETERMINISTIC_SCHEDULE_JOB_REGISTRY.get("chronicler", {})
    expected = {
        "chronicler_project_sessions",
        "chronicler_project_calendar",
        "chronicler_project_google_health_workout",
        "chronicler_project_google_health_steps",
        "chronicler_project_google_health_heart_rate",
        "chronicler_project_focus_inferred",
        "chronicler_project_reading_inferred",
    }
    assert expected <= jobs.keys()
    assert all(callable(jobs[name]) for name in expected)

    for job_name in expected:
        resolved = _resolve_deterministic_schedule_job_name(
            butler_name="chronicler",
            trigger_source=f"schedule:{job_name}",
            job_name=job_name,
        )
        assert resolved == job_name


@pytest.mark.asyncio
async def test_discover_chronicler_projection_schemas_filters_internal_names() -> None:
    pool, conn = _pool_with_schema_rows(
        "general",
        "relationship",
    )

    schemas = await _discover_chronicler_projection_schemas(pool, table_name="sessions")

    conn.fetch.assert_awaited_once()
    assert schemas == ("general", "relationship")


@pytest.mark.asyncio
async def test_run_chronicler_project_sessions_job_runs_adapter() -> None:
    pool = object()
    adapter_result = AdapterResult(
        source_name="core.sessions",
        rows_projected=4,
        episodes_opened=1,
        episodes_closed=3,
        point_events=8,
    )
    seed_registry = AsyncMock()
    configs = [
        MagicMock(db_schema="chronicler", modules={}),
        MagicMock(db_schema="general", modules={}),
        MagicMock(db_schema="health", modules={}),
    ]

    with (
        patch("butlers.chronicler.jobs.seed_source_registry", seed_registry),
        patch("butlers.chronicler.jobs.list_butlers", return_value=configs),
        patch("butlers.chronicler.jobs.CoreSessionsAdapter") as adapter_cls,
    ):
        adapter = MagicMock()
        adapter.run = AsyncMock(return_value=adapter_result)
        adapter_cls.return_value = adapter

        result = await _run_chronicler_project_sessions_job(pool, None)

    # Behavioral: the sessions job fans the adapter across ALL butler schemas.
    adapter_cls.assert_called_once_with(butler_schemas=("chronicler", "general", "health"))
    assert result["source_name"] == "core.sessions"
    assert result["rows_projected"] == 4
    assert result["point_events"] == 8


@pytest.mark.asyncio
async def test_run_chronicler_project_calendar_job_runs_adapter() -> None:
    pool = object()
    adapter_result = AdapterResult(
        source_name="google_calendar.completed",
        rows_projected=2,
        episodes_closed=2,
    )
    seed_registry = AsyncMock()
    configs = [
        MagicMock(db_schema="general", modules={"calendar": {}}),
        MagicMock(db_schema="relationship", modules={"calendar": {"provider": "google"}}),
        MagicMock(db_schema="health", modules={"contacts": {}}),
    ]

    with (
        patch("butlers.chronicler.jobs.seed_source_registry", seed_registry),
        patch("butlers.chronicler.jobs.list_butlers", return_value=configs),
        patch("butlers.chronicler.jobs.CalendarCompletedAdapter") as adapter_cls,
    ):
        adapter = MagicMock()
        adapter.run = AsyncMock(return_value=adapter_result)
        adapter_cls.return_value = adapter

        result = await _run_chronicler_project_calendar_job(pool, None)

    # Behavioral: the calendar job is module-gated — only butlers with the calendar
    # module (general, relationship) get the adapter; health (contacts only) is excluded.
    adapter_cls.assert_called_once_with(butler_schemas=("general", "relationship"))
    assert result["source_name"] == "google_calendar.completed"
    assert result["rows_projected"] == 2
    assert result["episodes_closed"] == 2
