"""Tests for Chronicler deterministic scheduled jobs."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.config import ButlerConfig, ButlerType
from butlers.jobs import chronicler as chronicler_jobs

pytestmark = pytest.mark.unit


def _cfg(name: str, *, butler_type: ButlerType = ButlerType.BUTLER) -> ButlerConfig:
    return ButlerConfig(
        name=name,
        port=41000,
        type=butler_type,
        db_name="butlers",
        db_schema=name,
    )


def test_discover_chronicler_source_schemas_excludes_staffers_and_split_db(monkeypatch):
    monkeypatch.setattr(
        chronicler_jobs,
        "list_butlers",
        lambda: [
            _cfg("general"),
            _cfg("chronicler"),
            _cfg("qa", butler_type=ButlerType.STAFFER),
            ButlerConfig(name="legacy", port=41000, db_name="legacy", db_schema=None),
        ],
    )

    assert chronicler_jobs._discover_chronicler_source_schemas() == ("chronicler", "general")


@pytest.mark.asyncio
async def test_run_project_sessions_seeds_registry_and_runs_adapter(monkeypatch):
    pool = MagicMock()
    seed_mock = AsyncMock()
    run_mock = AsyncMock(
        return_value=MagicMock(
            source_name="core.sessions",
            error=None,
            rows_projected=3,
            point_events=6,
            episodes_opened=2,
            episodes_closed=1,
            warnings=["ghost schema skipped"],
        )
    )
    adapter_instance = MagicMock()
    adapter_instance.source_name = "core.sessions"
    adapter_instance.run = run_mock
    adapter_cls = MagicMock(return_value=adapter_instance)

    monkeypatch.setattr(chronicler_jobs, "seed_source_registry", seed_mock)
    monkeypatch.setattr(
        chronicler_jobs,
        "_discover_chronicler_source_schemas",
        lambda: ("chronicler", "general"),
    )
    monkeypatch.setattr(chronicler_jobs, "CoreSessionsAdapter", adapter_cls)

    result = await chronicler_jobs.run_project_sessions(pool, None)

    seed_mock.assert_awaited_once_with(pool)
    adapter_cls.assert_called_once_with(butler_schemas=("chronicler", "general"))
    run_mock.assert_awaited_once_with(pool=pool, chronicler_pool=pool)
    # Contract: discovered schemas are seeded into the result and the adapter's
    # rows_projected is surfaced (full per-field echo is not the contract).
    assert result["source_schemas"] == ["chronicler", "general"]
    assert result["rows_projected"] == 3


@pytest.mark.asyncio
async def test_run_project_calendar_raises_on_adapter_error(monkeypatch):
    pool = MagicMock()
    seed_mock = AsyncMock()
    run_mock = AsyncMock(
        return_value=MagicMock(
            source_name="google_calendar.completed",
            error="boom",
            rows_projected=0,
            point_events=0,
            episodes_opened=0,
            episodes_closed=0,
            warnings=[],
        )
    )
    adapter_instance = MagicMock()
    adapter_instance.source_name = "google_calendar.completed"
    adapter_instance.run = run_mock
    adapter_cls = MagicMock(return_value=adapter_instance)

    monkeypatch.setattr(chronicler_jobs, "seed_source_registry", seed_mock)
    monkeypatch.setattr(
        chronicler_jobs,
        "_discover_chronicler_source_schemas",
        lambda: ("general",),
    )
    monkeypatch.setattr(chronicler_jobs, "CalendarCompletedAdapter", adapter_cls)

    with pytest.raises(
        RuntimeError,
        match="google_calendar.completed projection failed: boom",
    ):
        await chronicler_jobs.run_project_calendar(pool, None)
