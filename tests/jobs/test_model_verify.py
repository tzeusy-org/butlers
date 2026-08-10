"""Tests for butlers.jobs.model_verify (bu-hmdqz.2).

Covers:
- run_model_verify_sweep: delegates to run_verify_all_models with explicit
  Codex authority and audit_actor="model_verify_sweep"; returns None (no
  raise) when no shared pool is configured.
- run_model_verify_loop: sleeps first, ticks on interval, can be cancelled,
  skips a tick when no pool is available, swallows a bad tick.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.jobs.model_verify import (
    DEFAULT_MODEL_VERIFY_INTERVAL_S,
    run_model_verify_loop,
    run_model_verify_sweep,
)

pytestmark = pytest.mark.unit


class _FakeDatabaseManager:
    def __init__(self, *, pool: object | None = None, missing: bool = False) -> None:
        self._pool = pool
        self._missing = missing

    def credential_shared_pool(self):
        if self._missing:
            raise KeyError("no shared pool")
        return self._pool


# ---------------------------------------------------------------------------
# run_model_verify_sweep
# ---------------------------------------------------------------------------


async def test_sweep_returns_none_when_pool_missing():
    result = await run_model_verify_sweep(_FakeDatabaseManager(missing=True))
    assert result is None


async def test_sweep_delegates_to_run_verify_all_models(monkeypatch):
    verify_result = AsyncMock(
        return_value=type("R", (), {"total": 3, "ok": 2, "failed": 1, "skipped": 0})()
    )
    monkeypatch.setattr("butlers.api.routers.model_settings.run_verify_all_models", verify_result)
    pool = object()
    authority = MagicMock()
    store_cls = MagicMock(return_value=authority)
    monkeypatch.setattr("butlers.jobs.model_verify.CredentialStore", store_cls, raising=False)
    result = await run_model_verify_sweep(_FakeDatabaseManager(pool=pool))

    store_cls.assert_called_once_with(pool, system_global_pool=pool)
    verify_result.assert_awaited_once_with(
        pool,
        audit_actor="model_verify_sweep",
        codex_auth_authority=authority,
    )
    assert result == {"total": 3, "ok": 2, "failed": 1, "skipped": 0}


# ---------------------------------------------------------------------------
# run_model_verify_loop
# ---------------------------------------------------------------------------


async def test_loop_rejects_non_positive_interval():
    with pytest.raises(ValueError):
        await run_model_verify_loop(_FakeDatabaseManager(pool=object()), interval_s=0)


async def test_default_interval_is_one_hour():
    assert DEFAULT_MODEL_VERIFY_INTERVAL_S == 3600.0


async def test_loop_sleeps_then_ticks_and_can_be_cancelled(monkeypatch):
    sleep_calls: list[float] = []

    async def _fake_sleep(seconds):
        sleep_calls.append(seconds)
        if len(sleep_calls) >= 2:
            raise asyncio.CancelledError()

    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)
    sweep_mock = AsyncMock(return_value={"total": 0, "ok": 0, "failed": 0})
    monkeypatch.setattr("butlers.jobs.model_verify.run_model_verify_sweep", sweep_mock)

    with pytest.raises(asyncio.CancelledError):
        await run_model_verify_loop(_FakeDatabaseManager(pool=object()), interval_s=5)

    assert sleep_calls == [5, 5]
    assert sweep_mock.await_count == 1


async def test_loop_swallows_a_bad_tick(monkeypatch):
    call_count = 0

    async def _fake_sleep(seconds):
        nonlocal call_count
        call_count += 1
        if call_count >= 2:
            raise asyncio.CancelledError()

    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)
    sweep_mock = AsyncMock(side_effect=RuntimeError("boom"))
    monkeypatch.setattr("butlers.jobs.model_verify.run_model_verify_sweep", sweep_mock)

    with pytest.raises(asyncio.CancelledError):
        await run_model_verify_loop(_FakeDatabaseManager(pool=object()), interval_s=5)

    assert sweep_mock.await_count == 1
