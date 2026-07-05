"""Unit tests for the chronicler_rollup_daily job wiring (bu-u30as).

Covers:
(a) the job is dispatchable via the deterministic-schedule registry.
(b) job_args validation for run_rollup_daily (unsupported key, bad
    lookback_days, bad timezone).
(c) ChroniclerModule's module-default schedule registration includes
    chronicler_rollup_daily alongside chronicler_routines_mine, following
    the memory module's ``ensure_module_default_schedule`` contract.

Pure-unit tests — no Docker / PostgreSQL required.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from butlers.chronicler.jobs import run_rollup_daily
from butlers.modules.registry import default_registry
from butlers.scheduled_jobs import (
    _DETERMINISTIC_SCHEDULE_JOB_REGISTRY,
    _resolve_deterministic_schedule_job_name,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# (a) Registry coverage
# ---------------------------------------------------------------------------


def test_chronicler_rollup_daily_is_registered_callable_and_resolvable() -> None:
    jobs = _DETERMINISTIC_SCHEDULE_JOB_REGISTRY.get("chronicler", {})
    assert "chronicler_rollup_daily" in jobs
    assert callable(jobs["chronicler_rollup_daily"])

    resolved = _resolve_deterministic_schedule_job_name(
        butler_name="chronicler",
        trigger_source="schedule:chronicler_rollup_daily",
        job_name="chronicler_rollup_daily",
    )
    assert resolved == "chronicler_rollup_daily"


# ---------------------------------------------------------------------------
# (b) job_args validation
# ---------------------------------------------------------------------------


async def test_run_rollup_daily_rejects_unsupported_job_args() -> None:
    with pytest.raises(RuntimeError, match="unsupported keys"):
        await run_rollup_daily(AsyncMock(), job_args={"unknown": 1})


async def test_run_rollup_daily_rejects_non_positive_lookback_days() -> None:
    with pytest.raises(RuntimeError):
        await run_rollup_daily(AsyncMock(), job_args={"lookback_days": 0})


async def test_run_rollup_daily_rejects_non_int_lookback_days() -> None:
    with pytest.raises(RuntimeError):
        await run_rollup_daily(AsyncMock(), job_args={"lookback_days": "7"})


async def test_run_rollup_daily_rejects_empty_timezone() -> None:
    with pytest.raises(RuntimeError):
        await run_rollup_daily(AsyncMock(), job_args={"timezone": ""})


async def test_run_rollup_daily_accepts_no_job_args(monkeypatch) -> None:
    """No job_args -> defaults (lookback_days=7, timezone=Asia/Singapore)
    reach materialize_daily_rollups."""
    captured: dict[str, Any] = {}

    async def _fake_materialize(pool, *, timezone, lookback_days):
        captured["timezone"] = timezone
        captured["lookback_days"] = lookback_days
        return {"timezone": timezone, "lookback_days": lookback_days}

    monkeypatch.setattr("butlers.chronicler.rollups.materialize_daily_rollups", _fake_materialize)

    result = await run_rollup_daily(AsyncMock(), job_args=None)

    assert captured == {"timezone": "Asia/Singapore", "lookback_days": 7}
    # No days_processed in the fake materializer result -> no flag evaluation
    # runs, but run_rollup_daily always adds the (possibly empty) "flags" key
    # (bu-v76a7, chained anomaly-flag step).
    assert result == {"timezone": "Asia/Singapore", "lookback_days": 7, "flags": {}}


async def test_run_rollup_daily_honors_explicit_overrides(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    async def _fake_materialize(pool, *, timezone, lookback_days):
        captured["timezone"] = timezone
        captured["lookback_days"] = lookback_days
        return {}

    monkeypatch.setattr("butlers.chronicler.rollups.materialize_daily_rollups", _fake_materialize)

    await run_rollup_daily(AsyncMock(), job_args={"lookback_days": 14, "timezone": "UTC"})

    assert captured == {"timezone": "UTC", "lookback_days": 14}


# ---------------------------------------------------------------------------
# (c) ChroniclerModule default schedule registration
# ---------------------------------------------------------------------------


def _load_chronicler_module():
    registry = default_registry()
    modules = registry.load_all({})
    return next(m for m in modules if m.name == "chronicler")


async def test_chronicler_module_registers_rollup_daily_default_schedule(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []

    async def _fake_ensure(pool, **kwargs):
        calls.append(kwargs)

    monkeypatch.setattr("butlers.core.scheduler.ensure_module_default_schedule", _fake_ensure)

    module = _load_chronicler_module()
    fake_db = AsyncMock()
    fake_db.pool = object()

    await module._register_default_schedules(fake_db)

    names = {c["name"] for c in calls}
    assert "chronicler_rollup_daily" in names
    rollup_call = next(c for c in calls if c["name"] == "chronicler_rollup_daily")
    assert rollup_call["job_name"] == "chronicler_rollup_daily"
