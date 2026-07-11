"""Tests for butlers.jobs.external_deadman — external deadman (bu-9r3hd.4).

Covers:
- ping_external_deadman: 2xx -> True; non-2xx / network error -> False, never raises.
- get_last_deadman_success: reads the latest success marker from public.audit_log.
- run_external_deadman_check: success records an audit row; failure records nothing
  (never crashes the tick).
- run_external_deadman_loop: sleeps first, ticks on interval, swallows a bad tick,
  rejects a non-positive interval.

No real database or network required — pool/httpx are faked/mocked.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from butlers.jobs.external_deadman import (
    get_last_deadman_success,
    ping_external_deadman,
    run_external_deadman_check,
    run_external_deadman_loop,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# ping_external_deadman
# ---------------------------------------------------------------------------


async def test_ping_success_on_2xx(monkeypatch):
    resp = MagicMock(status_code=200)

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url):
            return resp

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: _FakeClient())

    assert await ping_external_deadman("https://example.com/ping") is True


async def test_ping_failure_on_non_2xx(monkeypatch):
    resp = MagicMock(status_code=500)

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url):
            return resp

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: _FakeClient())

    assert await ping_external_deadman("https://example.com/ping") is False


async def test_ping_network_error_returns_false_never_raises(monkeypatch):
    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url):
            raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: _FakeClient())

    assert await ping_external_deadman("https://example.com/ping") is False


# ---------------------------------------------------------------------------
# get_last_deadman_success
# ---------------------------------------------------------------------------


class _FakeAuditPool:
    def __init__(self, *, ts: datetime | None) -> None:
        self._ts = ts

    async def fetchrow(self, sql: str, *args):
        assert "external_deadman_ping_success" in args
        return {"ts": self._ts} if self._ts is not None else None


async def test_get_last_deadman_success_never_pinged():
    pool = _FakeAuditPool(ts=None)
    assert await get_last_deadman_success(pool) is None


async def test_get_last_deadman_success_returns_aware_timestamp():
    ts = datetime(2026, 7, 11, 0, 0, tzinfo=UTC)
    pool = _FakeAuditPool(ts=ts)
    result = await get_last_deadman_success(pool)
    assert result == ts


async def test_get_last_deadman_success_normalizes_naive_timestamp():
    naive_ts = datetime(2026, 7, 11, 0, 0)
    pool = _FakeAuditPool(ts=naive_ts)
    result = await get_last_deadman_success(pool)
    assert result is not None
    assert result.tzinfo is not None


# ---------------------------------------------------------------------------
# run_external_deadman_check
# ---------------------------------------------------------------------------


async def test_run_check_success_records_audit_row(monkeypatch):
    monkeypatch.setattr(
        "butlers.jobs.external_deadman.ping_external_deadman", AsyncMock(return_value=True)
    )
    append_mock = AsyncMock()
    monkeypatch.setattr("butlers.jobs.external_deadman.audit_router.append", append_mock)

    result = await run_external_deadman_check(object(), "https://example.com/ping")

    assert result == {"success": True, "recorded": True}
    append_mock.assert_awaited_once()
    assert append_mock.await_args.args[1] == "external_deadman"
    assert append_mock.await_args.args[2] == "external_deadman_ping_success"


async def test_run_check_failure_records_nothing(monkeypatch):
    monkeypatch.setattr(
        "butlers.jobs.external_deadman.ping_external_deadman", AsyncMock(return_value=False)
    )
    append_mock = AsyncMock()
    monkeypatch.setattr("butlers.jobs.external_deadman.audit_router.append", append_mock)

    result = await run_external_deadman_check(object(), "https://example.com/ping")

    assert result == {"success": False}
    append_mock.assert_not_awaited()


async def test_run_check_audit_write_failure_is_reported_not_raised(monkeypatch):
    monkeypatch.setattr(
        "butlers.jobs.external_deadman.ping_external_deadman", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        "butlers.jobs.external_deadman.audit_router.append",
        AsyncMock(side_effect=RuntimeError("db down")),
    )

    result = await run_external_deadman_check(object(), "https://example.com/ping")

    assert result == {"success": True, "recorded": False}


# ---------------------------------------------------------------------------
# run_external_deadman_loop
# ---------------------------------------------------------------------------


class _FakeDatabaseManager:
    def __init__(self, *, pool: object | None = None, missing: bool = False) -> None:
        self._pool = pool
        self._missing = missing

    def pool(self, name: str):
        if self._missing:
            raise KeyError(name)
        return self._pool


async def test_loop_rejects_non_positive_interval():
    with pytest.raises(ValueError):
        await run_external_deadman_loop(
            _FakeDatabaseManager(pool=object()), url="https://example.com/ping", interval_s=0
        )


async def test_loop_sleeps_then_ticks_and_can_be_cancelled(monkeypatch):
    sleep_calls: list[float] = []

    async def _fake_sleep(seconds):
        sleep_calls.append(seconds)
        if len(sleep_calls) >= 2:
            raise asyncio.CancelledError()

    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)
    check_mock = AsyncMock(return_value={"success": True, "recorded": True})
    monkeypatch.setattr("butlers.jobs.external_deadman.run_external_deadman_check", check_mock)

    with pytest.raises(asyncio.CancelledError):
        await run_external_deadman_loop(
            _FakeDatabaseManager(pool=object()), url="https://example.com/ping", interval_s=5
        )

    assert sleep_calls == [5, 5]
    assert check_mock.await_count == 1


async def test_loop_skips_tick_when_pool_unavailable(monkeypatch):
    call_count = 0

    async def _fake_sleep(seconds):
        nonlocal call_count
        call_count += 1
        if call_count >= 1:
            raise asyncio.CancelledError()

    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)
    check_mock = AsyncMock()
    monkeypatch.setattr("butlers.jobs.external_deadman.run_external_deadman_check", check_mock)

    with pytest.raises(asyncio.CancelledError):
        await run_external_deadman_loop(
            _FakeDatabaseManager(missing=True), url="https://example.com/ping", interval_s=5
        )

    check_mock.assert_not_awaited()


async def test_loop_swallows_a_bad_tick(monkeypatch):
    call_count = 0

    async def _fake_sleep(seconds):
        nonlocal call_count
        call_count += 1
        if call_count >= 2:
            raise asyncio.CancelledError()

    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)
    check_mock = AsyncMock(side_effect=RuntimeError("boom"))
    monkeypatch.setattr("butlers.jobs.external_deadman.run_external_deadman_check", check_mock)

    with pytest.raises(asyncio.CancelledError):
        await run_external_deadman_loop(
            _FakeDatabaseManager(pool=object()), url="https://example.com/ping", interval_s=5
        )

    assert check_mock.await_count == 1
