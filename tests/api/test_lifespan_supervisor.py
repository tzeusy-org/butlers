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


@pytest.mark.parametrize(
    ("fault", "expected"),
    [
        ("calendar_partial", "error"),
        ("calendar_no_pool", "error"),
        ("calendar_escalation", "error"),
        ("drift_escalation", "error"),
        ("lifecycle_no_pool", "error"),
        ("lifecycle_butler_pool", "error"),
        ("staleness_no_pool", "error"),
        ("staleness_butler_pool", "error"),
        ("staleness_collection", "error"),
        ("staleness_dispatch", "error"),
        ("staleness_circuit", "error"),
        ("staleness_http500", "error"),
        ("staleness_rate_limited", "ok"),
        ("staleness_missing_credential", "ok"),
    ],
)
async def test_hidden_tick_failures_reach_actual_supervised_work(monkeypatch, fault, expected):
    """Real checks/sweeps keep their old zero/skip summaries without a false ok."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock
    from uuid import UUID

    from fastapi import HTTPException

    from butlers.core.job_health import JobHealthStore
    from butlers.jobs import (
        calendar_sync_deadman,
        deploy_drift,
        secrets_lifecycle,
        secrets_staleness,
    )

    events, summaries = [], []
    phase = {"recovered": False}
    gate = asyncio.Queue()
    shared_pool = MagicMock()

    class RecordingStore(JobHealthStore):
        async def write(self, spec, state, reason, **evidence):
            events.append((state, reason, evidence))

    def pool(_):
        if (
            fault in {"calendar_no_pool", "lifecycle_butler_pool", "staleness_butler_pool"}
            and not phase["recovered"]
        ):
            raise KeyError("switchboard")
        return shared_pool

    def credential_pool():
        if fault.endswith("no_pool") and not phase["recovered"]:
            raise KeyError("shared_pool")
        return shared_pool

    db = SimpleNamespace(
        butler_names=["health"] if fault.endswith("butler_pool") else [],
        pool=pool,
        credential_shared_pool=credential_pool,
    )

    async def sources(_):
        return [], ["relationship"] if fault == "calendar_partial" and not phase[
            "recovered"
        ] else []

    monkeypatch.setattr(calendar_sync_deadman, "query_calendar_sources", sources)
    if fault != "calendar_escalation":
        monkeypatch.setattr(
            calendar_sync_deadman, "reconcile_calendar_conditions", AsyncMock(return_value=[])
        )
    escalation_module = deploy_drift if fault == "drift_escalation" else calendar_sync_deadman
    if fault.endswith("escalation"):
        # Keep the real compute -> reconcile -> L1 producer -> caught I/O failure chain.
        monkeypatch.setattr(deploy_drift, "_expected_chains_by_schema", lambda: {})
        monkeypatch.setattr(
            escalation_module,
            "reconcile_snapshot",
            AsyncMock(
                return_value=[
                    SimpleNamespace(
                        fingerprint="source-fingerprint",
                        transition="escalation_due",
                        escalation_level="L1",
                    )
                ]
            ),
        )

        async def create_attempt(*_, **__):
            if not phase["recovered"]:
                raise RuntimeError("private-hidden-failure-sentinel")
            return UUID(int=1), True

        monkeypatch.setattr(escalation_module, "create_or_join_attempt", create_attempt)
        monkeypatch.setattr(escalation_module, "update_attempt_status", AsyncMock())
        monkeypatch.setattr(escalation_module.audit_router, "append", AsyncMock())
    if fault != "lifecycle_butler_pool":
        monkeypatch.setattr(secrets_lifecycle, "_collect_snapshots", AsyncMock(return_value=[]))
    else:
        monkeypatch.setattr(secrets_lifecycle, "_fetch_system_secrets", AsyncMock(return_value=[]))
        monkeypatch.setattr(secrets_lifecycle, "_fetch_cli_secrets", AsyncMock(return_value=[]))
        monkeypatch.setattr(secrets_lifecycle, "_fetch_user_secrets", AsyncMock(return_value=[]))

    async def system_rows(*_, **__):
        if fault == "staleness_collection" and not phase["recovered"]:
            raise RuntimeError("private-hidden-failure-sentinel")
        return []

    async def user_rows(*_, **__):
        if fault not in {
            "staleness_dispatch",
            "staleness_circuit",
            "staleness_http500",
            "staleness_rate_limited",
            "staleness_missing_credential",
        }:
            return []
        count = (
            secrets_staleness._CIRCUIT_BREAK_THRESHOLD + 1 if fault == "staleness_circuit" else 1
        )
        return [
            SimpleNamespace(
                state="ok",
                type="google_oauth_refresh",
                last_verified=None,
                entity_id=str(UUID(int=index + 1)),
            )
            for index in range(count)
        ]

    async def probe(*_, **__):
        if not phase["recovered"]:
            codes = {
                "staleness_http500": 500,
                "staleness_rate_limited": 429,
                "staleness_missing_credential": 404,
            }
            if fault in codes:
                raise HTTPException(codes[fault], "private-hidden-failure-sentinel")
            raise RuntimeError("private-hidden-failure-sentinel")
        return SimpleNamespace(data=SimpleNamespace(ok=True, message="checked"))

    actual_probe = AsyncMock(side_effect=probe)
    monkeypatch.setattr(secrets_staleness, "_fetch_system_secrets", system_rows)
    monkeypatch.setattr(secrets_staleness, "_fetch_cli_secrets", AsyncMock(return_value=[]))
    monkeypatch.setattr(secrets_staleness, "_fetch_user_secrets", user_rows)
    monkeypatch.setattr(secrets_staleness, "probe_user_credential", actual_probe)

    if fault.startswith("calendar"):
        module, name = calendar_sync_deadman, "calendar_sync_deadman"
        check_name, loop_name = "run_calendar_sync_deadman_check", "run_calendar_sync_deadman_loop"
    elif fault.startswith("drift"):
        module, name = deploy_drift, "migration_drift"
        check_name, loop_name = "run_migration_drift_check", "run_migration_drift_loop"
    elif fault.startswith("lifecycle"):
        module, name = secrets_lifecycle, "secrets_lifecycle"
        check_name, loop_name = "run_secrets_lifecycle_check", "run_secrets_lifecycle_loop"
    else:
        module, name = secrets_staleness, "secrets_staleness"
        check_name, loop_name = "run_secrets_staleness_check", "run_secrets_staleness_loop"
    real_check = getattr(module, check_name)

    async def observed_check(*args, **kwargs):
        summary = await real_check(*args, **kwargs)
        summaries.append(summary)
        return summary

    async def next_tick(_):
        await gate.get()

    monkeypatch.setattr(module, check_name, observed_check)
    monkeypatch.setattr(
        module, "asyncio", SimpleNamespace(sleep=next_tick, CancelledError=asyncio.CancelledError)
    )
    store = RecordingStore(lambda: None)
    task = supervise_lifespan_loop(
        name, lambda: getattr(module, loop_name)(db), health=store.recorder(name, "periodic", 10)
    )
    try:
        gate.put_nowait(None)
        await _run_until(task, lambda: len(events) >= 2)
        assert events[1][0] == expected
        assert not task.done()
        assert len(summaries) == 1  # the real check returned rather than an outer crash
        if fault.startswith("staleness"):
            assert summaries[0]["failed"] == 0
            if fault == "staleness_circuit":
                assert actual_probe.await_count == secrets_staleness._CIRCUIT_BREAK_THRESHOLD
                assert summaries[0]["skipped"] == secrets_staleness._CIRCUIT_BREAK_THRESHOLD + 1
        elif fault.startswith("lifecycle"):
            assert summaries[0] == {
                "scanned": 0,
                "attention": 0,
                "delivered": 0,
                "deferred": 0,
                "suppressed": 0,
                "errors": 0,
            }
        else:
            assert summaries[0]["available"] is True
            if fault == "calendar_no_pool":
                assert summaries[0]["reconciled"] is False
            if fault.endswith("escalation"):
                assert summaries[0]["conditions"][0]["reason"] == "escalation_failed"
                assert summaries[0]["conditions"][0]["escalated"] is False
        phase["recovered"] = True
        gate.put_nowait(None)
        await _run_until(task, lambda: len(events) >= 3)
        assert events[2][0] == "ok"
        assert events[2][2]["success"] is True
        assert len(summaries) == 2
        assert "private-hidden-failure-sentinel" not in repr(events)
    finally:
        await _cancel_and_await(task)


@pytest.mark.parametrize(
    ("fault", "expected"),
    [
        ("fleet", "error"),
        ("qa", "error"),
        ("attention_query", "error"),
        ("attention_episode", "error"),
        ("handoff", "error"),
        ("policy", "error"),
        ("paused", "ok"),
        ("legacy", "ok"),
    ],
)
async def test_actual_shadow_callback_incomplete_work_is_not_a_healthy_probe(
    monkeypatch, fault, expected
):
    from datetime import UTC, datetime, timedelta
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    from butlers.core import control_plane_identity as identity
    from butlers.core import fleet_conditions
    from butlers.core.job_health import JobHealthStore, mark_tick_incomplete

    events, callbacks = [], []
    phase = {"recovered": False}
    gate = asyncio.Queue()
    now = datetime.now(UTC)
    cycle = identity.ShadowCycle(True, 1, 1, 0, 1)

    class RecordingStore(JobHealthStore):
        async def write(self, spec, state, reason, **evidence):
            events.append((state, reason, evidence))

    def failing(which):
        if fault == which and not phase["recovered"]:
            raise RuntimeError("private-controller-failure-sentinel")

    async def fetch(sql, *_):
        if sql == fleet_conditions._ACTIVE_LEGACY_LIVENESS_SQL:
            failing("fleet")
            return []
        assert sql == fleet_conditions._ACTIVE_ATTENTION_CONDITIONS_SQL
        failing("attention_query")
        return [{"id": "condition-1"}]

    async def fetchrow(sql, *_):
        assert sql == fleet_conditions.LATEST_SCHEDULED_HANDOFF_SQL
        failing("handoff")
        return None  # A genuine legacy/missing handoff observation is not a failed query.

    async def fetchval(sql, *_):
        if sql == fleet_conditions.LAST_QUALIFYING_PATROL_SQL:
            failing("qa")
            return now - timedelta(days=1)
        assert sql == "SELECT clock_timestamp()"
        return now

    async def policy_row(sql, *_):
        assert "butler_registry_control_plane" in sql
        failing("policy")
        return {
            "policy_state": "paused" if fault == "paused" else "ready",
            "policy_provenance": "operator",
        }

    async def append_episode(sql, *_):
        assert sql == fleet_conditions._APPEND_ATTENTION_SQL
        failing("attention_episode")
        return "episode-1"

    conn = SimpleNamespace(
        execute=AsyncMock(),
        fetchrow=AsyncMock(side_effect=policy_row),
        fetchval=AsyncMock(side_effect=append_episode),
    )
    acquisition = MagicMock()
    acquisition.__aenter__ = AsyncMock(return_value=conn)
    acquisition.__aexit__ = AsyncMock(return_value=False)
    pool = SimpleNamespace(
        fetch=AsyncMock(side_effect=fetch),
        fetchrow=AsyncMock(side_effect=fetchrow),
        fetchval=AsyncMock(side_effect=fetchval),
        acquire=MagicMock(return_value=acquisition),
    )
    monkeypatch.setattr(fleet_conditions, "get_active_condition", AsyncMock(return_value=None))
    reconcile = AsyncMock(return_value=[])
    monkeypatch.setattr(fleet_conditions, "reconcile_snapshot", reconcile)
    configs = [
        SimpleNamespace(
            name="qa",
            port=41103,
            qa_patrol_contract=SimpleNamespace(digest="source-digest", overdue_after_s=300),
        )
    ]
    actual_callback = fleet_conditions.controller_after_cycle(pool, configs)

    async def after_cycle(role_view, seen):
        result = await actual_callback(role_view, seen)
        assert result is None  # The controller's existing fault-isolation API is retained.
        callbacks.append(seen)

    async def observe(*_):
        await gate.get()
        return cycle

    monkeypatch.setattr(identity, "run_shadow_cycle", observe)
    monkeypatch.setattr(identity, "_effective_observer_interval_s", AsyncMock(return_value=0.001))
    store = RecordingStore(lambda: None)
    mark_tick_incomplete()  # Outside this supervised task there is no recorder to contaminate.
    task = supervise_lifespan_loop(
        "fleet_shadow_observer",
        lambda: identity.run_shadow_observer_loop(pool, configs, after_cycle=after_cycle),
        health=store.recorder("fleet_shadow_observer", "periodic", 0.001),
    )
    try:
        gate.put_nowait(None)
        await _run_until(task, lambda: len(events) >= 2)
        assert events[1][0] == expected
        assert callbacks == [cycle]
        assert not task.done()
        phase["recovered"] = True
        mark_tick_incomplete()  # Also cannot poison another task's recovery tick.
        gate.put_nowait(None)
        await _run_until(task, lambda: len(events) >= 3)
        assert events[2][0] == "ok"
        assert events[2][2]["success"] is True
        assert callbacks == [cycle, cycle]
        assert "private-controller-failure-sentinel" not in repr(events)
        assert conn.execute.await_count >= 4  # Actual role-view acquisition/reset still runs.
        assert any(
            call.args[0] == 'SET ROLE "butler_switchboard_rw"'
            for call in conn.execute.await_args_list
        )
        if fault == "paused":
            assert any(
                observation.fingerprint == fleet_conditions.QA_PATROL_STOPPED_FINGERPRINT
                for call in reconcile.await_args_list
                for observation in call.kwargs["observations"]
            )
    finally:
        await _cancel_and_await(task)
