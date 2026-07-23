"""Unit tests for the chronicler_routines_mine job wiring (bu-whhll.9).

Covers:
(a) the job is dispatchable via the deterministic-schedule registry.
(b) job_args validation for run_routines_mine (unsupported key, bad weeks,
    bad timezone).
(c) ChroniclerModule's module-default schedule registration follows the
    memory module's ``ensure_module_default_schedule`` contract: called once
    per default entry, best-effort (a failure does not raise or block
    startup).

Pure-unit tests — no Docker / PostgreSQL required.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from butlers.chronicler.jobs import run_routines_mine
from butlers.modules.registry import default_registry
from butlers.scheduled_jobs import (
    _DETERMINISTIC_SCHEDULE_JOB_REGISTRY,
    _resolve_deterministic_schedule_job_name,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# (a) Registry coverage
# ---------------------------------------------------------------------------


def test_chronicler_routines_mine_is_registered_callable_and_resolvable() -> None:
    jobs = _DETERMINISTIC_SCHEDULE_JOB_REGISTRY.get("chronicler", {})
    assert "chronicler_routines_mine" in jobs
    assert callable(jobs["chronicler_routines_mine"])

    resolved = _resolve_deterministic_schedule_job_name(
        butler_name="chronicler",
        trigger_source="schedule:chronicler_routines_mine",
        job_name="chronicler_routines_mine",
    )
    assert resolved == "chronicler_routines_mine"


# ---------------------------------------------------------------------------
# (b) job_args validation
# ---------------------------------------------------------------------------


async def test_run_routines_mine_rejects_unsupported_job_args() -> None:
    with pytest.raises(RuntimeError, match="unsupported keys"):
        await run_routines_mine(AsyncMock(), job_args={"unknown": 1})


async def test_run_routines_mine_rejects_non_positive_weeks() -> None:
    with pytest.raises(RuntimeError):
        await run_routines_mine(AsyncMock(), job_args={"weeks": 0})


async def test_run_routines_mine_rejects_non_int_weeks() -> None:
    with pytest.raises(RuntimeError):
        await run_routines_mine(AsyncMock(), job_args={"weeks": "6"})


async def test_run_routines_mine_rejects_empty_timezone() -> None:
    with pytest.raises(RuntimeError):
        await run_routines_mine(AsyncMock(), job_args={"timezone": ""})


async def test_run_routines_mine_accepts_no_job_args(monkeypatch) -> None:
    """No job_args -> defaults (weeks=6, timezone=Asia/Singapore) reach mine_routines."""
    captured: dict[str, Any] = {}

    async def _fake_mine_routines(pool, *, weeks, timezone):
        captured["weeks"] = weeks
        captured["timezone"] = timezone
        return {"weeks": weeks, "timezone": timezone}

    monkeypatch.setattr("butlers.chronicler.routines.mine_routines", _fake_mine_routines)

    result = await run_routines_mine(AsyncMock(), job_args=None)

    assert captured == {"weeks": 6, "timezone": "Asia/Singapore"}
    assert result == {"weeks": 6, "timezone": "Asia/Singapore"}


async def test_run_routines_mine_honors_explicit_overrides(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    async def _fake_mine_routines(pool, *, weeks, timezone):
        captured["weeks"] = weeks
        captured["timezone"] = timezone
        return {}

    monkeypatch.setattr("butlers.chronicler.routines.mine_routines", _fake_mine_routines)

    await run_routines_mine(AsyncMock(), job_args={"weeks": 4, "timezone": "UTC"})

    assert captured == {"weeks": 4, "timezone": "UTC"}


# ---------------------------------------------------------------------------
# (c) ChroniclerModule default schedule registration
# ---------------------------------------------------------------------------


def _load_chronicler_module():
    registry = default_registry()
    modules = registry.load_all({})
    return next(m for m in modules if m.name == "chronicler")


async def test_chronicler_module_registers_routines_mine_default_schedule(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []

    async def _fake_ensure(pool, **kwargs):
        calls.append(kwargs)

    monkeypatch.setattr("butlers.core.scheduler.ensure_module_default_schedule", _fake_ensure)

    module = _load_chronicler_module()
    fake_db = AsyncMock()
    fake_db.pool = object()

    await module._register_default_schedules(fake_db)

    # chronicler_rollup_daily (bu-u30as) is also a module-default schedule;
    # find this one by name rather than asserting an exact count so this test
    # doesn't need updating every time another default schedule is added.
    routines_call = next(c for c in calls if c["name"] == "chronicler_routines_mine")
    assert routines_call["job_name"] == "chronicler_routines_mine"


async def test_chronicler_module_uses_public_schema_for_legacy_database(monkeypatch) -> None:
    """Legacy per-database schedules remain auditable through their public schema."""
    calls: list[dict[str, Any]] = []

    async def _fake_ensure(pool, **kwargs):
        calls.append(kwargs)

    monkeypatch.setattr("butlers.core.scheduler.ensure_module_default_schedule", _fake_ensure)

    module = _load_chronicler_module()
    legacy_db = SimpleNamespace(
        pool=object(),
        owner_butler="legacy-chronicler",
        schema=None,
    )
    await module._register_default_schedules(legacy_db)

    assert {(call["owner_butler"], call["owner_schema"]) for call in calls} == {
        ("legacy-chronicler", "public")
    }


async def test_chronicler_module_none_db_is_a_noop() -> None:
    module = _load_chronicler_module()
    # Should not raise even when db is None (e.g. some test harnesses).
    await module._register_default_schedules(None)


async def test_chronicler_module_schedule_failure_is_best_effort(monkeypatch) -> None:
    async def _flaky_ensure(pool, **kwargs):
        raise RuntimeError("scheduled_tasks not migrated yet")

    monkeypatch.setattr("butlers.core.scheduler.ensure_module_default_schedule", _flaky_ensure)

    module = _load_chronicler_module()
    fake_db = AsyncMock()
    fake_db.pool = object()

    # Must not raise despite the schedule call failing.
    await module._register_default_schedules(fake_db)


async def test_chronicler_module_on_startup_registers_schedule(monkeypatch) -> None:
    """on_startup wires _register_default_schedules in (not just a method
    that exists but is never called)."""
    calls: list[dict[str, Any]] = []

    async def _fake_ensure(pool, **kwargs):
        calls.append(kwargs)

    monkeypatch.setattr("butlers.core.scheduler.ensure_module_default_schedule", _fake_ensure)

    module = _load_chronicler_module()
    fake_db = AsyncMock()
    fake_db.pool = object()

    await module.on_startup(config=None, db=fake_db)

    routines_call = next(c for c in calls if c["name"] == "chronicler_routines_mine")
    assert routines_call["job_name"] == "chronicler_routines_mine"
