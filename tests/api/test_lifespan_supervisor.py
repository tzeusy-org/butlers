"""Deterministic unit coverage for butlers.api.lifespan_supervisor (bu-27dxl.6.5).

Proves the three restart/shutdown invariants the dashboard app lifespan
depends on, in isolation from the real lifespan and its DB/MCP dependencies:

1. An unexpected ordinary ``return`` from the supervised coroutine restarts
   it with bounded exponential backoff.
2. An unexpected exception restarts it with bounded exponential backoff too.
3. Cancellation (the shutdown path) is the sole termination that does NOT
   restart -- it propagates immediately.

Backoff is driven by an injected fake ``sleep_fn`` (see
``supervise_lifespan_loop``'s ``sleep_fn`` parameter) so these tests never
race real wall-clock time -- restarts happen as fast as the event loop can
schedule them.
"""

from __future__ import annotations

import asyncio
import contextlib

import pytest

from butlers.api.lifespan_supervisor import supervise_lifespan_loop

pytestmark = pytest.mark.asyncio


async def _instant_sleep_fn(recorded: list[float]):
    """Build a fake ``sleep_fn`` that records the requested delay and
    returns immediately (a single ``asyncio.sleep(0)`` yield so the event
    loop still gets a chance to interleave, without ever waiting on the
    real backoff duration).
    """

    async def _sleep(delay: float) -> None:
        recorded.append(delay)
        await asyncio.sleep(0)

    return _sleep


async def _run_until(task: asyncio.Task, predicate, *, max_iterations: int = 10_000) -> None:
    """Yield to the event loop until ``predicate()`` is true or the budget
    is exhausted. Deterministic: each iteration is a bare ``asyncio.sleep(0)``
    yield, not a real-time wait, so this never races wall-clock time.
    """
    for _ in range(max_iterations):
        if predicate():
            return
        if task.done():
            return
        await asyncio.sleep(0)
    raise AssertionError("predicate not satisfied within iteration budget")


async def _cancel_and_await(task: asyncio.Task) -> None:
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


class TestUnexpectedReturnRestarts:
    async def test_ordinary_return_restarts_with_backoff(self, caplog):
        call_count = 0

        async def returns_immediately():
            nonlocal call_count
            call_count += 1

        recorded_backoffs: list[float] = []
        sleep_fn = await _instant_sleep_fn(recorded_backoffs)

        task = supervise_lifespan_loop(
            "return_loop",
            returns_immediately,
            initial_backoff_s=1.0,
            max_backoff_s=8.0,
            backoff_multiplier=2.0,
            sleep_fn=sleep_fn,
        )
        try:
            with caplog.at_level("ERROR"):
                await _run_until(task, lambda: call_count >= 4)
        finally:
            await _cancel_and_await(task)

        assert call_count >= 4, "loop must restart after an unexpected ordinary return"
        # Bounded exponential backoff: 1.0, 2.0, 4.0, ... capped at 8.0.
        assert recorded_backoffs[:3] == [1.0, 2.0, 4.0]
        assert all(b <= 8.0 for b in recorded_backoffs)
        assert "returned unexpectedly" in caplog.text
        assert "return_loop" in caplog.text

    async def test_backoff_caps_at_max(self):
        call_count = 0

        async def returns_immediately():
            nonlocal call_count
            call_count += 1

        recorded_backoffs: list[float] = []
        sleep_fn = await _instant_sleep_fn(recorded_backoffs)

        task = supervise_lifespan_loop(
            "capped_loop",
            returns_immediately,
            initial_backoff_s=1.0,
            max_backoff_s=3.0,
            backoff_multiplier=2.0,
            sleep_fn=sleep_fn,
        )
        try:
            await _run_until(task, lambda: call_count >= 6)
        finally:
            await _cancel_and_await(task)

        # 1.0, 2.0, then capped at 3.0 forever after.
        assert recorded_backoffs[0] == 1.0
        assert recorded_backoffs[1] == 2.0
        assert all(b == 3.0 for b in recorded_backoffs[2:])


class TestUnexpectedExceptionRestarts:
    async def test_exception_restarts_with_backoff(self, caplog):
        call_count = 0

        async def crashes():
            nonlocal call_count
            call_count += 1
            raise ValueError("boom")

        recorded_backoffs: list[float] = []
        sleep_fn = await _instant_sleep_fn(recorded_backoffs)

        task = supervise_lifespan_loop(
            "crash_loop",
            crashes,
            initial_backoff_s=0.5,
            max_backoff_s=4.0,
            backoff_multiplier=2.0,
            sleep_fn=sleep_fn,
        )
        try:
            with caplog.at_level("ERROR"):
                await _run_until(task, lambda: call_count >= 4)
        finally:
            await _cancel_and_await(task)

        assert call_count >= 4, "loop must restart after an unhandled exception"
        assert recorded_backoffs[:3] == [0.5, 1.0, 2.0]
        assert "crashed unexpectedly" in caplog.text
        assert "crash_loop" in caplog.text

    async def test_exception_type_and_message_not_swallowed_silently(self, caplog):
        """The crash must be logged with exc_info (logger.exception), not
        merely a bare message -- otherwise root-causing a real crash from
        production logs is impossible.
        """
        call_count = 0

        async def crashes_once_then_blocks():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("distinctive-failure-marker")
            await asyncio.Event().wait()

        recorded_backoffs: list[float] = []
        sleep_fn = await _instant_sleep_fn(recorded_backoffs)

        task = supervise_lifespan_loop(
            "traceback_loop",
            crashes_once_then_blocks,
            initial_backoff_s=0.1,
            max_backoff_s=1.0,
            sleep_fn=sleep_fn,
        )
        try:
            with caplog.at_level("ERROR"):
                await _run_until(task, lambda: call_count >= 2)
        finally:
            await _cancel_and_await(task)

        assert "distinctive-failure-marker" in caplog.text
        assert "Traceback" in caplog.text or any(r.exc_info for r in caplog.records), (
            "crash must be logged with a traceback (logger.exception)"
        )


class TestShutdownCancellationDoesNotRestart:
    async def test_cancellation_while_running_does_not_restart(self, caplog):
        call_count = 0
        started = asyncio.Event()

        async def steady_forever():
            nonlocal call_count
            call_count += 1
            started.set()
            await asyncio.Event().wait()  # blocks until cancelled

        recorded_backoffs: list[float] = []
        sleep_fn = await _instant_sleep_fn(recorded_backoffs)

        task = supervise_lifespan_loop(
            "steady_loop",
            steady_forever,
            initial_backoff_s=0.1,
            sleep_fn=sleep_fn,
        )
        await started.wait()

        with caplog.at_level("INFO"):
            await _cancel_and_await(task)

        assert call_count == 1, "a cancelled-while-running loop must never restart"
        assert recorded_backoffs == [], "cancellation must not sleep for backoff"
        assert task.cancelled()
        assert "cancelled" in caplog.text.lower()
        # "not restarting" is the expected cancellation log; "restarting in"
        # (the restart-path phrasing) must never appear.
        assert "restarting in" not in caplog.text

    async def test_cancellation_during_backoff_sleep_does_not_restart(self, caplog):
        call_count = 0
        backoff_reached = asyncio.Event()

        async def crashes_then_would_restart():
            nonlocal call_count
            call_count += 1
            raise ValueError("boom")

        async def sleep_fn(delay: float) -> None:
            backoff_reached.set()
            # Block "in the backoff sleep" until the test cancels the task --
            # proves cancellation mid-backoff also short-circuits, not just
            # cancellation while the wrapped loop itself is running.
            await asyncio.Event().wait()

        task = supervise_lifespan_loop(
            "backoff_cancel_loop",
            crashes_then_would_restart,
            initial_backoff_s=1.0,
            sleep_fn=sleep_fn,
        )
        await backoff_reached.wait()

        with caplog.at_level("INFO"):
            await _cancel_and_await(task)

        assert call_count == 1, "must not restart once cancelled mid-backoff"
        assert task.cancelled()
        assert "cancelled during restart backoff" in caplog.text


class TestTaskIdentity:
    async def test_task_is_named(self):
        async def blocks_forever():
            await asyncio.Event().wait()

        task = supervise_lifespan_loop("my_named_loop", blocks_forever)
        try:
            assert task.get_name() == "my_named_loop"
        finally:
            await _cancel_and_await(task)

    async def test_coro_factory_called_fresh_each_restart(self):
        """A coroutine object can only be awaited once -- the supervisor
        must call ``coro_factory()`` again for every restart rather than
        reusing the same coroutine object (which would raise
        RuntimeError: cannot reuse already awaited coroutine).
        """
        factory_calls = 0

        async def returns_immediately():
            pass

        def coro_factory():
            nonlocal factory_calls
            factory_calls += 1
            return returns_immediately()

        recorded_backoffs: list[float] = []
        sleep_fn = await _instant_sleep_fn(recorded_backoffs)

        task = supervise_lifespan_loop(
            "fresh_coro_loop",
            coro_factory,
            initial_backoff_s=0.01,
            max_backoff_s=0.02,
            sleep_fn=sleep_fn,
        )
        try:
            await _run_until(task, lambda: factory_calls >= 3)
        finally:
            await _cancel_and_await(task)

        assert factory_calls >= 3


@pytest.mark.parametrize(
    ("stored", "age", "same_process", "expected", "job_class"),
    [
        ("starting", 0, True, "starting", "periodic"),
        ("ok", 0, True, "ok", "periodic"),
        ("degraded", 0, True, "degraded", "periodic"),
        ("error", 0, True, "error", "periodic"),
        ("disabled", 1000, True, "disabled", "periodic"),
        ("ok", 0, False, "unknown", "periodic"),
        ("ok", 1000, True, "stale", "periodic"),
        (None, 0, True, "unknown", "periodic"),
        ("ok", 0, True, "ok", "continuous"),
        ("ok", 1000, True, "stale", "continuous"),
        ("degraded", 0, True, "degraded", "continuous"),
        ("unavailable", 0, True, "unavailable", "periodic"),
    ],
)
async def test_job_health_freshness_never_uses_an_old_process_success(
    stored,
    age,
    same_process,
    expected,
    job_class,
):
    from datetime import UTC, datetime, timedelta
    from uuid import uuid4

    from butlers.core.job_health import JobSpec, present_job

    if stored == "unavailable":
        from butlers.core.job_health import JobHealthStore

        def unavailable_pool():
            raise RuntimeError("storage-unavailable-sentinel")

        assert await JobHealthStore(unavailable_pool).read() == {
            "jobs_available": False,
            "jobs": [],
        }
        assert expected == "unavailable"
        return

    now = datetime.now(UTC)
    process_id = uuid4()
    row = (
        None
        if stored is None
        else {
            "process_id": process_id if same_process else uuid4(),
            "state": stored,
            "reason": "started" if stored == "starting" else "tick_ok",
            "last_success_at": now - timedelta(seconds=age if job_class == "periodic" else 1000),
            "last_progress_at": now - timedelta(seconds=age if job_class == "continuous" else 0),
            "updated_at": now - timedelta(seconds=age),
            "error_count": 0,
            "consecutive_failures": 0,
            "interval_s": 10.0,
            "grace_s": 5.0,
        }
    )
    name = "secrets_lifecycle" if job_class == "periodic" else "fleet_events_bridge"
    result = present_job(JobSpec(name, job_class, 10), row, process_id, now)
    assert result["job_class"] == job_class
    assert result["state"] == expected
    assert "process_id" not in result


async def test_caught_tick_and_partial_failure_are_recorded_without_private_text(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from butlers.core.job_health import JobHealthStore
    from butlers.jobs import secrets_lifecycle

    events = []

    class RecordingStore(JobHealthStore):
        async def write(self, spec, state, reason, **evidence):
            events.append((spec.name, state, reason, evidence))

    store = RecordingStore(lambda: None)
    real_sleep = asyncio.sleep
    sleeps = 0

    async def next_tick(_):
        nonlocal sleeps
        sleeps += 1
        if sleeps > 3:
            await asyncio.Event().wait()
        await real_sleep(0)

    monkeypatch.setattr(
        secrets_lifecycle,
        "asyncio",
        SimpleNamespace(
            sleep=next_tick,
            CancelledError=asyncio.CancelledError,
        ),
    )
    check = AsyncMock(
        side_effect=[RuntimeError("private-token-sentinel"), {"errors": 2}, {"errors": 0}]
    )
    monkeypatch.setattr(secrets_lifecycle, "run_secrets_lifecycle_check", check)
    task = supervise_lifespan_loop(
        "secrets_lifecycle",
        lambda: secrets_lifecycle.run_secrets_lifecycle_loop(None),
        health=store.recorder("secrets_lifecycle", "periodic", 10),
    )
    try:
        await _run_until(task, lambda: any(e[1] == "ok" for e in events))
        assert not task.done(), "caught tick failures must not crash the supervised job"
        assert [e[1] for e in events] == ["starting", "error", "error", "ok"]
        assert check.await_count == 3
        assert "private-token-sentinel" not in repr(events)
        assert events[-1][3]["success"] is True
    finally:
        await _cancel_and_await(task)
    assert events[-1][1:] == ("unknown", "stopped", {})


async def test_continuous_connection_evidence_counts_reconnect_failures_and_recovers(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock

    from butlers.api import fleet_events_bridge
    from butlers.core.job_health import JobHealthStore

    events = []

    class RecordingStore(JobHealthStore):
        async def write(self, spec, state, reason, **evidence):
            events.append((state, reason))

    store = RecordingStore(lambda: None)
    conn = MagicMock()
    conn.add_listener = AsyncMock()
    conn.close = AsyncMock()
    conn.is_closed.return_value = False
    connect = AsyncMock(side_effect=[RuntimeError("private-connect-sentinel")] * 3 + [conn])
    task = supervise_lifespan_loop(
        "fleet_events_bridge",
        lambda: fleet_events_bridge.run_fleet_events_listener(
            connect,
            reconnect_backoff_s=0,
            health_poll_interval_s=0.001,
        ),
        health=store.recorder("fleet_events_bridge", "continuous", 5),
    )
    try:
        await _run_until(task, lambda: ("ok", "connected") in events)
        assert events[:5] == [
            ("starting", "started"),
            ("degraded", "reconnecting"),
            ("degraded", "reconnecting"),
            ("error", "reconnecting"),
            ("ok", "connected"),
        ]
        assert "private-connect-sentinel" not in repr(events)
        assert connect.await_count == 4
    finally:
        await _cancel_and_await(task)
    conn.close.assert_awaited_once()


async def test_health_storage_failure_is_unavailable_and_does_not_stop_work():
    from butlers.core.job_health import JobHealthStore

    def unavailable_pool():
        raise RuntimeError("private-storage-sentinel")

    store = JobHealthStore(unavailable_pool)
    recorder = store.recorder("model_verify", "periodic", 10)
    await recorder.starting()
    await recorder.tick_failed("https://private-token-sentinel")
    await recorder.tick_ok()
    assert await store.read() == {"jobs_available": False, "jobs": []}
    assert store.unavailable_jobs == {"model_verify"}
