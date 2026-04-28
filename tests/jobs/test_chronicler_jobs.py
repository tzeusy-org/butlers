"""Tests for Chronicler deterministic projection job handlers."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from butlers.chronicler.adapters.base import AdapterResult
from butlers.chronicler.jobs import _DEFAULT_CALENDAR_SCHEMAS, _DEFAULT_SESSION_SCHEMAS

pytestmark = pytest.mark.unit


async def test_project_sessions_discovers_butler_schemas_and_runs_adapter() -> None:
    pool = object()
    adapter = AsyncMock()
    adapter.run.return_value = AdapterResult(source_name="core.sessions", rows_projected=2)
    seed_registry = AsyncMock()
    configs = [
        SimpleNamespace(db_schema="general", modules={}),
        SimpleNamespace(db_schema="chronicler", modules={}),
        SimpleNamespace(db_schema="general", modules={}),
    ]

    with (
        patch("butlers.chronicler.jobs.seed_source_registry", seed_registry),
        patch("butlers.chronicler.jobs.list_butlers", return_value=configs),
        patch("butlers.chronicler.jobs.CoreSessionsAdapter", return_value=adapter) as adapter_cls,
    ):
        from butlers.chronicler.jobs import run_project_sessions

        result = await run_project_sessions(pool, {"batch_limit": 25})

    seed_registry.assert_awaited_once_with(pool)
    adapter_cls.assert_called_once_with(
        butler_schemas=("general", "chronicler"),
        batch_limit=25,
    )
    adapter.run.assert_awaited_once_with(pool=pool, chronicler_pool=pool)
    assert result["source_name"] == "core.sessions"
    assert result["rows_projected"] == 2


async def test_project_calendar_filters_to_calendar_enabled_butlers() -> None:
    pool = object()
    adapter = AsyncMock()
    adapter.run.return_value = AdapterResult(
        source_name="google_calendar.completed",
        rows_projected=3,
    )
    seed_registry = AsyncMock()
    configs = [
        SimpleNamespace(db_schema="general", modules={"calendar": {}}),
        SimpleNamespace(db_schema="travel", modules={"calendar": {"provider": "google"}}),
        SimpleNamespace(db_schema="health", modules={"contacts": {}}),
    ]

    with (
        patch("butlers.chronicler.jobs.seed_source_registry", seed_registry),
        patch("butlers.chronicler.jobs.list_butlers", return_value=configs),
        patch(
            "butlers.chronicler.jobs.CalendarCompletedAdapter",
            return_value=adapter,
        ) as adapter_cls,
    ):
        from butlers.chronicler.jobs import run_project_calendar

        result = await run_project_calendar(pool, None)

    seed_registry.assert_awaited_once_with(pool)
    adapter_cls.assert_called_once_with(
        butler_schemas=("general", "travel"),
    )
    adapter.run.assert_awaited_once_with(pool=pool, chronicler_pool=pool)
    assert result["source_name"] == "google_calendar.completed"
    assert result["rows_projected"] == 3


async def test_project_sessions_falls_back_to_static_schema_set_when_roster_scan_fails() -> None:
    pool = object()
    adapter = AsyncMock()
    adapter.run.return_value = AdapterResult(source_name="core.sessions")
    seed_registry = AsyncMock()

    with (
        patch("butlers.chronicler.jobs.seed_source_registry", seed_registry),
        patch("butlers.chronicler.jobs.list_butlers", side_effect=RuntimeError("boom")),
        patch("butlers.chronicler.jobs.CoreSessionsAdapter", return_value=adapter) as adapter_cls,
    ):
        from butlers.chronicler.jobs import run_project_sessions

        await run_project_sessions(pool, None)

    seed_registry.assert_awaited_once_with(pool)
    adapter_cls.assert_called_once_with(butler_schemas=_DEFAULT_SESSION_SCHEMAS)


async def test_project_calendar_falls_back_to_static_schema_set_when_none_have_calendar() -> None:
    pool = object()
    adapter = AsyncMock()
    adapter.run.return_value = AdapterResult(source_name="google_calendar.completed")
    seed_registry = AsyncMock()
    configs = [SimpleNamespace(db_schema="general", modules={})]

    with (
        patch("butlers.chronicler.jobs.seed_source_registry", seed_registry),
        patch("butlers.chronicler.jobs.list_butlers", return_value=configs),
        patch(
            "butlers.chronicler.jobs.CalendarCompletedAdapter",
            return_value=adapter,
        ) as adapter_cls,
    ):
        from butlers.chronicler.jobs import run_project_calendar

        await run_project_calendar(pool, None)

    seed_registry.assert_awaited_once_with(pool)
    adapter_cls.assert_called_once_with(butler_schemas=_DEFAULT_CALENDAR_SCHEMAS)


async def test_project_owntracks_rejects_unsupported_job_args() -> None:
    from butlers.chronicler.jobs import run_project_owntracks

    with pytest.raises(RuntimeError, match="unsupported keys"):
        await run_project_owntracks(object(), {"oops": 1})
