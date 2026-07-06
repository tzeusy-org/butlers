"""Unit tests for the chronicler_narrate_daily job wiring (bu-v9y18,
telemetry-distillation bead 6).

Covers:
(a) the job is dispatchable via the deterministic-schedule registry.
(b) job_args validation for run_narrate_daily (unsupported key, bad timezone).
(c) the job computes "yesterday" in the given timezone and delegates to
    narration.narrate_daily_rollup.
(d) ChroniclerModule's module-default schedule registration includes
    chronicler_narrate_daily, following the memory module's
    ``ensure_module_default_schedule`` contract.

Pure-unit tests — no Docker / PostgreSQL required.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest

from butlers.chronicler.jobs import run_narrate_daily
from butlers.modules.registry import default_registry
from butlers.scheduled_jobs import (
    _DETERMINISTIC_SCHEDULE_JOB_REGISTRY,
    _resolve_deterministic_schedule_job_name,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# (a) Registry coverage
# ---------------------------------------------------------------------------


def test_chronicler_narrate_daily_is_registered_callable_and_resolvable() -> None:
    jobs = _DETERMINISTIC_SCHEDULE_JOB_REGISTRY.get("chronicler", {})
    assert "chronicler_narrate_daily" in jobs
    assert callable(jobs["chronicler_narrate_daily"])

    resolved = _resolve_deterministic_schedule_job_name(
        butler_name="chronicler",
        trigger_source="schedule:chronicler_narrate_daily",
        job_name="chronicler_narrate_daily",
    )
    assert resolved == "chronicler_narrate_daily"


# ---------------------------------------------------------------------------
# (b) job_args validation
# ---------------------------------------------------------------------------


async def test_run_narrate_daily_rejects_unsupported_job_args() -> None:
    with pytest.raises(RuntimeError, match="unsupported keys"):
        await run_narrate_daily(AsyncMock(), job_args={"unknown": 1})


async def test_run_narrate_daily_rejects_empty_timezone() -> None:
    with pytest.raises(RuntimeError):
        await run_narrate_daily(AsyncMock(), job_args={"timezone": ""})


async def test_run_narrate_daily_rejects_non_string_timezone() -> None:
    with pytest.raises(RuntimeError):
        await run_narrate_daily(AsyncMock(), job_args={"timezone": 7})


async def test_run_narrate_daily_rejects_unknown_timezone() -> None:
    with pytest.raises(RuntimeError, match="unknown timezone"):
        await run_narrate_daily(AsyncMock(), job_args={"timezone": "Not/AZone"})


# ---------------------------------------------------------------------------
# (c) yesterday-date computation + delegation
# ---------------------------------------------------------------------------


async def test_run_narrate_daily_accepts_no_job_args_and_computes_yesterday(monkeypatch) -> None:
    """No job_args -> default timezone (Asia/Singapore); the closed local
    date passed to narrate_daily_rollup is "yesterday" relative to _now()."""
    captured: dict[str, Any] = {}

    async def _fake_narrate(pool, *, local_date, timezone):
        captured["local_date"] = local_date
        captured["timezone"] = timezone
        return {"local_date": local_date.isoformat(), "status": "labeled"}

    monkeypatch.setattr("butlers.chronicler.narration.narrate_daily_rollup", _fake_narrate)
    # 2026-07-06 01:20 UTC is 2026-07-06 09:20 SGT -> "yesterday" is 07-05.
    monkeypatch.setattr(
        "butlers.chronicler.jobs._now", lambda: datetime(2026, 7, 6, 1, 20, tzinfo=UTC)
    )

    result = await run_narrate_daily(AsyncMock(), job_args=None)

    assert captured["timezone"] == "Asia/Singapore"
    assert captured["local_date"] == date(2026, 7, 5)
    assert result == {"local_date": "2026-07-05", "status": "labeled"}


async def test_run_narrate_daily_honors_explicit_timezone_override(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    async def _fake_narrate(pool, *, local_date, timezone):
        captured["local_date"] = local_date
        captured["timezone"] = timezone
        return {}

    monkeypatch.setattr("butlers.chronicler.narration.narrate_daily_rollup", _fake_narrate)
    monkeypatch.setattr(
        "butlers.chronicler.jobs._now", lambda: datetime(2026, 7, 6, 1, 20, tzinfo=UTC)
    )

    await run_narrate_daily(AsyncMock(), job_args={"timezone": "UTC"})

    assert captured["timezone"] == "UTC"
    assert captured["local_date"] == date(2026, 7, 5)


# ---------------------------------------------------------------------------
# (d) ChroniclerModule default schedule registration
# ---------------------------------------------------------------------------


def _load_chronicler_module():
    registry = default_registry()
    modules = registry.load_all({})
    return next(m for m in modules if m.name == "chronicler")


async def test_chronicler_module_registers_narrate_daily_default_schedule(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []

    async def _fake_ensure(pool, **kwargs):
        calls.append(kwargs)

    monkeypatch.setattr("butlers.core.scheduler.ensure_module_default_schedule", _fake_ensure)

    module = _load_chronicler_module()
    fake_db = AsyncMock()
    fake_db.pool = object()

    await module._register_default_schedules(fake_db)

    # chronicler_rollup_daily/chronicler_routines_mine are also module-default
    # schedules; find this one by name so this test doesn't need updating
    # every time another default schedule is added.
    narrate_call = next(c for c in calls if c["name"] == "chronicler_narrate_daily")
    assert narrate_call["job_name"] == "chronicler_narrate_daily"
