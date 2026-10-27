"""Tests for butlers.jobs.external_deadman — external deadman (bu-9r3hd.4).

Covers:
- ping_external_deadman: 2xx -> True; non-2xx / network error -> False, never raises.
- get_last_deadman_success: reads the latest success marker from public.audit_log.
- run_external_deadman_check: a committed DB/fleet gate precedes HTTP; only
  success records a success marker. Claims prevent uncertain tick resends.
- REQ-infrastructure-reliability-002: failed round trips and fleet observations
  withhold the heartbeat without leaking endpoint/error content.
- run_external_deadman_loop: sleeps first, ticks on interval, swallows a bad tick,
  rejects a non-positive interval.

No real database or network required — pool/httpx are faked/mocked.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
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
from butlers.testing.fake_pool import AsyncCtx

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

        def stream(self, method, url):

            return AsyncCtx(resp)

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: _FakeClient())

    assert await ping_external_deadman("http://example.com/ping") is True


async def test_ping_failure_on_non_2xx(monkeypatch):
    resp = MagicMock(status_code=500)

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def stream(self, method, url):

            return AsyncCtx(resp)

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: _FakeClient())

    assert await ping_external_deadman("https://example.com/ping") is False


async def test_ping_network_error_returns_false_never_raises(monkeypatch):
    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def stream(self, method, url):
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


class HealthyPool:
    def __init__(self):
        self.released = 0
        self.committed = False
        self.existing = None
        self.claim = 1
        self.rows = [
            {
                "name": "home",
                "policy_state": "active",
                "observed_state": "healthy",
                "boot_epoch": 2,
                "observed_boot_epoch": 2,
                "route_compatible": True,
                "accepting_routes": True,
                "healthy_observed_at": datetime.now(UTC),
                "liveness_ttl_seconds": 60,
                "checked_at": datetime.now(UTC),
            }
        ]

    def acquire(self, **kwargs):
        pool = self

        class Acquisition:
            async def __aenter__(self):
                return pool

            async def __aexit__(self, *exc):
                pool.released += 1

        return Acquisition()

    def transaction(self):
        pool = self

        class Transaction:
            async def __aenter__(self):
                return self

            async def __aexit__(self, kind, *exc):
                pool.committed = kind is None

        return Transaction()

    async def fetch(self, sql, *args):
        return self.rows

    async def fetchval(self, sql, *args, **kwargs):
        if "pg_try_advisory_xact_lock" in sql:
            return True
        if "LIMIT 1" in sql:
            return self.existing
        assert self.committed and self.released
        return self.claim


# ---------------------------------------------------------------------------
# run_external_deadman_check
# ---------------------------------------------------------------------------


async def test_run_check_success_records_audit_row(monkeypatch):
    monkeypatch.setattr(
        "butlers.jobs.external_deadman.ping_external_deadman", AsyncMock(return_value=True)
    )
    append_mock = AsyncMock(return_value=1)
    monkeypatch.setattr("butlers.jobs.external_deadman.audit_router.append", append_mock)

    result = await run_external_deadman_check(
        HealthyPool(), "https://example.com/ping", expected_names=("home",)
    )

    assert result == {"success": True, "recorded": True}
    assert append_mock.await_count == 2
    assert append_mock.await_args.args[1] == "external_deadman"
    assert append_mock.await_args.args[2] == "external_deadman_ping_success"
    assert [(call.args[2], call.kwargs["result"]) for call in append_mock.await_args_list] == [
        ("external_deadman_tick", "claimed"),
        ("external_deadman_ping_success", "success"),
    ]


async def test_run_check_failure_records_nothing(monkeypatch):
    monkeypatch.setattr(
        "butlers.jobs.external_deadman.ping_external_deadman", AsyncMock(return_value=False)
    )
    append_mock = AsyncMock(return_value=1)
    monkeypatch.setattr("butlers.jobs.external_deadman.audit_router.append", append_mock)

    result = await run_external_deadman_check(
        HealthyPool(), "https://example.com/ping", expected_names=("home",)
    )

    assert result == {"success": False, "cause": "ping_failed"}
    append_mock.assert_awaited_once()  # Claim committed, no success receipt.


async def test_run_check_audit_write_failure_is_reported_not_raised(monkeypatch):
    monkeypatch.setattr(
        "butlers.jobs.external_deadman.ping_external_deadman", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        "butlers.jobs.external_deadman.audit_router.append",
        AsyncMock(side_effect=RuntimeError("db down")),
    )

    result = await run_external_deadman_check(
        HealthyPool(), "https://example.com/ping", expected_names=("home",)
    )

    assert result == {"success": False, "cause": "database_unavailable"}


# ---------------------------------------------------------------------------
# run_external_deadman_loop
# ---------------------------------------------------------------------------


class _FakeDatabaseManager:
    def __init__(self, *, pool: object | None = None, missing: bool = False) -> None:
        self._pool = pool
        self._missing = missing
        self.configured_butler_names = ["home"]

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
    assert check_mock.await_args.kwargs["expected_names"] == ("home",)
    assert isinstance(check_mock.await_args.kwargs["tick_id"], uuid.UUID)


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


async def test_database_failure_cannot_emit_success(monkeypatch):
    class FailedPool:
        def acquire(self, **kwargs):
            raise RuntimeError("private database diagnostic")

    ping = AsyncMock(return_value=True)
    monkeypatch.setattr("butlers.jobs.external_deadman.ping_external_deadman", ping)
    monkeypatch.setattr(
        "butlers.jobs.external_deadman.audit_router.append",
        AsyncMock(side_effect=RuntimeError("database unavailable")),
    )
    result = await run_external_deadman_check(FailedPool(), "https://deadman.example.test/ping")
    assert result["success"] is False
    ping.assert_not_awaited()


@pytest.fixture
def gated_consumer(monkeypatch):
    pool = HealthyPool()
    records = []

    async def append(connection, actor, action, **kwargs):
        assert kwargs["target"] == "external_deadman"
        records.append((action, kwargs["request_id"]))
        if action == "external_deadman_tick":
            assert connection is pool
            pool.existing = 1
        return 1

    monkeypatch.setattr("butlers.jobs.external_deadman.audit_router.append", append)
    ping = AsyncMock(return_value=True)
    monkeypatch.setattr("butlers.jobs.external_deadman.ping_external_deadman", ping)
    return pool, records, ping


async def test_committed_roundtrip_precedes_http_and_duplicate_tick_is_not_resent(gated_consumer):
    pool, records, ping = gated_consumer
    tick = uuid.uuid4()

    async def checked_ping(url):
        assert pool.committed and pool.released == 1
        assert records == [("external_deadman_tick", tick)]
        return True

    ping.side_effect = checked_ping
    result = await run_external_deadman_check(
        pool, "https://deadman.example.test/ping", expected_names=("home",), tick_id=tick
    )
    assert result == {"success": True, "recorded": True}
    assert records == [("external_deadman_tick", tick), ("external_deadman_ping_success", tick)]
    duplicate = await run_external_deadman_check(
        pool, "https://deadman.example.test/ping", expected_names=("home",), tick_id=tick
    )
    assert duplicate == {"success": False, "cause": "duplicate_tick"}
    ping.assert_awaited_once()
    assert len(records) == 2


@pytest.mark.parametrize("fault", ["missing", "malformed", "stale", "old_boot", "quarantined"])
async def test_fleet_adversity_never_emits_a_success(gated_consumer, fault):
    pool, records, ping = gated_consumer
    if fault == "missing":
        pool.rows.clear()
    elif fault == "malformed":
        pool.rows[0]["accepting_routes"] = "true"
    elif fault == "stale":
        pool.rows[0]["liveness_ttl_seconds"] = 0
    elif fault == "old_boot":
        pool.rows[0]["observed_boot_epoch"] = 1
    else:
        pool.rows[0]["policy_state"] = "quarantined"
    result = await run_external_deadman_check(
        pool, "https://deadman.example.test/ping", expected_names=("home",)
    )
    assert result["success"] is False
    assert result["cause"] == "fleet_unhealthy"
    assert pool.released == 1
    assert records == []
    ping.assert_not_awaited()


@pytest.mark.parametrize("phase", ["commit", "readback", "fleet_query"])
async def test_roundtrip_faults_withhold_http_and_release_connection(gated_consumer, phase):
    pool, records, ping = gated_consumer
    if phase == "commit":
        original_transaction = pool.transaction

        def transaction():
            base = original_transaction()

            class BrokenCommit:
                async def __aenter__(self):
                    await base.__aenter__()

                async def __aexit__(self, *exc):
                    raise RuntimeError("private DB error")

            return BrokenCommit()

        pool.transaction = transaction
    elif phase == "readback":
        pool.claim = None
    else:
        pool.fetch = AsyncMock(side_effect=RuntimeError("private fleet error"))
    result = await run_external_deadman_check(
        pool, "https://deadman.example.test/ping", expected_names=("home",)
    )
    assert result["success"] is False
    assert pool.released == 1
    assert not any(action == "external_deadman_ping_success" for action, _ in records)
    ping.assert_not_awaited()


@pytest.mark.parametrize("expire", [False, True])
async def test_inflight_cancellation_or_timeout_never_retries_tick(gated_consumer, expire):
    pool, records, ping = gated_consumer
    started = asyncio.Event()
    finished = asyncio.Event()

    async def blocked_ping(url):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            finished.set()

    ping.side_effect = blocked_ping
    tick = uuid.uuid4()
    task = asyncio.create_task(
        run_external_deadman_check(
            pool,
            "https://deadman.example.test/ping",
            expected_names=("home",),
            tick_id=tick,
            timeout_s=0.05 if expire else 1,
        )
    )
    await asyncio.wait_for(started.wait(), 1)
    if not expire:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1)
    else:
        assert await asyncio.wait_for(task, 1) == {"success": False, "cause": "ping_failed"}
    assert finished.is_set() and task.done()
    assert records == [("external_deadman_tick", tick)]
    assert pool.released == 1
    duplicate = await run_external_deadman_check(
        pool, "https://deadman.example.test/ping", expected_names=("home",), tick_id=tick
    )
    assert duplicate["cause"] == "duplicate_tick"
    ping.assert_awaited_once()


async def test_unconfigured_has_no_http_or_database_io(gated_consumer):
    pool, records, ping = gated_consumer
    result = await run_external_deadman_check(pool, "")
    assert result == {"success": False, "cause": "unconfigured"}
    assert not records and pool.released == 0
    ping.assert_not_awaited()


async def test_http_is_bodyless_nonredirecting_and_diagnostics_are_content_blind(
    monkeypatch, caplog
):
    requests = []
    caplog.set_level(logging.INFO, logger="httpx")
    real_client = httpx.AsyncClient

    async def handle(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(200)
        raise httpx.ConnectError("private token and endpoint", request=request)

    def client(**kwargs):
        assert kwargs["follow_redirects"] is False and kwargs["trust_env"] is False
        return real_client(**kwargs, transport=httpx.MockTransport(handle))

    monkeypatch.setattr(httpx, "AsyncClient", client)
    assert await ping_external_deadman("https://deadman.example.test/private-token") is True
    assert await ping_external_deadman("https://deadman.example.test/private-token") is False
    assert len(requests) == 2 and requests[0].method == "GET" and requests[0].content == b""
    assert "private-token" not in caplog.text and "private token" not in caplog.text
    assert "deadman.example.test" not in caplog.text
