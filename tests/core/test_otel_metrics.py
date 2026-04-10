"""Unit tests for the OTel metrics instruments (butlers-963.9) — condensed.

Covers:
- init_metrics: no-op when OTEL_EXPORTER_OTLP_ENDPOINT is not set
- ButlerMetrics: all 11 instruments record values with correct attributes
- Spawner: active_sessions, queued_triggers, session_duration_ms updated on trigger()
- DurableBuffer: queue_depth, enqueue_total, backpressure_total,
                  scanner_recovered_total, process_latency_ms
- Multi-butler provider guard
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from opentelemetry import metrics
from opentelemetry.metrics import _internal as _metrics_internal
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.util._once import Once

import butlers.core.metrics as _metrics_mod
from butlers.config import BufferConfig, ButlerConfig, RuntimeConfig
from butlers.core.buffer import DurableBuffer, _MessageRef
from butlers.core.metrics import ButlerMetrics, init_metrics
from butlers.core.runtimes.base import RuntimeAdapter
from butlers.core.spawner import Spawner

pytestmark = pytest.mark.unit


def _reset_metrics_global_state() -> None:
    _metrics_internal._METER_PROVIDER_SET_ONCE = Once()
    _metrics_internal._METER_PROVIDER = None


def _make_in_memory_provider() -> tuple[MeterProvider, InMemoryMetricReader]:
    _reset_metrics_global_state()
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    metrics.set_meter_provider(provider)
    return provider, reader


def _collect_metrics(reader: InMemoryMetricReader) -> dict[str, Any]:
    result: dict[str, Any] = {}
    data = reader.get_metrics_data()
    for rm in data.resource_metrics:
        for sm in rm.scope_metrics:
            for metric in sm.metrics:
                if metric.data.data_points:
                    result[metric.name] = metric.data.data_points
    return result


class MockAdapter(RuntimeAdapter):
    @property
    def binary_name(self) -> str:
        return "mock"

    async def invoke(
        self,
        prompt,
        system_prompt,
        mcp_servers,
        env,
        max_turns=20,
        model=None,
        cwd=None,
        timeout=None,
    ):
        return "result", [], None

    def build_config_file(self, mcp_servers, tmp_dir):
        return tmp_dir / "mock_config.json"

    def parse_system_prompt_file(self, config_dir):
        return ""


def test_init_metrics_and_all_instruments(monkeypatch: pytest.MonkeyPatch) -> None:
    """init_metrics no-op without endpoint; all 11 ButlerMetrics instruments record correctly;
    butler label present; second init does not override provider; no-op mode skips set_provider."""
    # init_metrics no-op: usable meter without endpoint
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    meter = init_metrics("butler-test")
    assert meter is not None
    meter.create_counter("test.counter").add(1, {"key": "val"})  # must not raise

    # All 11 instruments
    _provider, reader = _make_in_memory_provider()
    try:
        m = ButlerMetrics("my-butler")
        m.ensure_registered()

        # spawner: active_sessions zero series
        data = _collect_metrics(reader)
        assert "butlers.spawner.active_sessions" in data
        assert data["butlers.spawner.active_sessions"][0].value == 0

        # spawner: inc/dec and duration
        m.spawner_active_sessions_inc()
        m.spawner_active_sessions_inc()
        m.spawner_active_sessions_dec()
        m.spawner_queued_triggers_inc()
        m.spawner_queued_triggers_inc()
        m.spawner_queued_triggers_dec()
        m.record_session_duration(1234)

        data2 = _collect_metrics(reader)
        assert data2["butlers.spawner.active_sessions"][0].value == 1
        assert data2["butlers.spawner.queued_triggers"][0].value == 1
        assert data2["butlers.spawner.session_duration_ms"][0].sum == 1234

        # buffer: queue_depth, enqueue_total (hot/cold), backpressure, scanner_recovered, latency
        m.buffer_queue_depth_inc()
        m.buffer_queue_depth_inc()
        m.buffer_queue_depth_dec()
        m.buffer_enqueue_hot()
        m.buffer_enqueue_hot()
        m.buffer_enqueue_cold()
        m.buffer_backpressure()
        m.buffer_backpressure()
        m.buffer_scanner_recovered()
        m.record_buffer_process_latency(42.5)

        data3 = _collect_metrics(reader)
        assert data3["butlers.buffer.queue_depth"][0].value == 1
        hot_dps = [
            dp for dp in data3["butlers.buffer.enqueue_total"] if dp.attributes.get("path") == "hot"
        ]
        cold_dps = [
            dp
            for dp in data3["butlers.buffer.enqueue_total"]
            if dp.attributes.get("path") == "cold"
        ]
        assert hot_dps[0].value == 2 and cold_dps[0].value == 1
        assert data3["butlers.buffer.backpressure_total"][0].value == 2
        assert data3["butlers.buffer.scanner_recovered_total"][0].value == 1
        assert data3["butlers.buffer.process_latency_ms"][0].sum == pytest.approx(42.5, rel=1e-3)

        # route metrics
        m.record_route_accept_latency(15.0)
        m.route_queue_depth_inc()
        m.route_queue_depth_inc()
        m.route_queue_depth_dec()
        m.record_route_process_latency(100.0)
        data4 = _collect_metrics(reader)
        assert data4["butlers.route.accept_latency_ms"][0].sum == pytest.approx(15.0, rel=1e-3)
        assert data4["butlers.route.queue_depth"][0].value == 1

        # Butler label in all data points
        for name in [
            "butlers.spawner.active_sessions",
            "butlers.buffer.backpressure_total",
            "butlers.route.queue_depth",
        ]:
            for dp in data4[name]:
                assert dp.attributes.get("butler") == "my-butler"

        # Multi-butler guard: second init does not override provider
        _metrics_mod._meter_provider_installed = True
        set_count = 0
        original_set = metrics.set_meter_provider

        def guarded_set(p):
            nonlocal set_count
            set_count += 1
            original_set(p)

        metrics.set_meter_provider = guarded_set
        try:
            import os as _os

            _os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = "http://localhost:4317"
            try:
                meter2 = init_metrics("butler.general")
            finally:
                _os.environ.pop("OTEL_EXPORTER_OTLP_ENDPOINT", None)
        finally:
            metrics.set_meter_provider = original_set
            _metrics_mod._meter_provider_installed = (
                False  # restore to avoid leaking into later tests
            )
        assert set_count == 0 and meter2 is not None
    finally:
        _reset_metrics_global_state()


async def test_spawner_and_buffer_metrics_integration(tmp_path: Path) -> None:
    """Spawner.trigger() updates active_sessions/queued_triggers/session_duration;
    DurableBuffer updates enqueue_total/queue_depth/backpressure/process_latency_ms."""
    # Spawner metrics
    _provider, reader = _make_in_memory_provider()
    try:
        config = ButlerConfig(
            name="metrics-spawner-test", port=9100, runtime=RuntimeConfig(max_concurrent_sessions=2)
        )
        (tmp_path / "CLAUDE.md").write_text("# test")
        spawner = Spawner(config=config, config_dir=tmp_path, runtime=MockAdapter())
        await spawner.trigger(prompt="hello", trigger_source="tick")

        data = _collect_metrics(reader)
        assert sum(dp.value for dp in data.get("butlers.spawner.active_sessions", [])) == 0
        assert sum(dp.value for dp in data.get("butlers.spawner.queued_triggers", [])) == 0
        assert data["butlers.spawner.session_duration_ms"][0].count == 1
    finally:
        _reset_metrics_global_state()

    # DurableBuffer enqueue and backpressure metrics
    _provider2, reader2 = _make_in_memory_provider()
    try:
        buf = DurableBuffer(
            config=BufferConfig(
                queue_capacity=1,
                worker_count=1,
                scanner_interval_s=30,
                scanner_grace_s=10,
                scanner_batch_size=50,
            ),
            pool=None,
            process_fn=AsyncMock(),
            butler_name="sw",
        )
        buf.enqueue(
            request_id="r1",
            message_inbox_id="r1",
            message_text="first",
            source={},
            event={},
            sender={},
        )
        buf.enqueue(
            request_id="r2",
            message_inbox_id="r2",
            message_text="second",
            source={},
            event={},
            sender={},
        )

        data2 = _collect_metrics(reader2)
        hot_dps = [
            dp
            for dp in data2.get("butlers.buffer.enqueue_total", [])
            if dp.attributes.get("path") == "hot"
        ]
        assert len(hot_dps) == 1 and hot_dps[0].value == 1
        assert sum(dp.value for dp in data2.get("butlers.buffer.backpressure_total", [])) >= 1

    finally:
        _reset_metrics_global_state()

    # Worker decrements queue_depth and records latency (separate provider for isolation)
    _provider3, reader3 = _make_in_memory_provider()
    try:
        processed = asyncio.Event()

        async def _process_fn(ref: _MessageRef) -> None:
            processed.set()

        buf3 = DurableBuffer(
            config=BufferConfig(
                queue_capacity=10,
                worker_count=1,
                scanner_interval_s=30,
                scanner_grace_s=10,
                scanner_batch_size=50,
            ),
            pool=None,
            process_fn=_process_fn,
            butler_name="sw",
        )
        await buf3.start()
        buf3.enqueue(
            request_id="r1",
            message_inbox_id="r1",
            message_text="hello",
            source={},
            event={},
            sender={},
        )
        await asyncio.wait_for(processed.wait(), timeout=2.0)
        await asyncio.sleep(0.05)
        await buf3.stop()

        data3 = _collect_metrics(reader3)
        assert sum(dp.value for dp in data3.get("butlers.buffer.queue_depth", [])) == 0
        assert data3.get("butlers.buffer.process_latency_ms", [{}])[0].count == 1
    finally:
        _reset_metrics_global_state()


def test_recovery_instruments_registered_on_ensure_registered() -> None:
    """ensure_registered() seeds active_workflow series for both healing and qa workflows."""
    _provider, reader = _make_in_memory_provider()
    try:
        m = ButlerMetrics("healer")
        m.ensure_registered()

        data = _collect_metrics(reader)
        # Both healing and qa zero series must be present
        wf_dps = data.get("butlers.recovery.active_workflows", [])
        workflows_seen = {dp.attributes.get("workflow") for dp in wf_dps}
        assert "healing" in workflows_seen, f"healing not in {workflows_seen}"
        assert "qa" in workflows_seen, f"qa not in {workflows_seen}"
        for dp in wf_dps:
            assert dp.attributes.get("butler") == "healer"
            assert dp.value == 0
    finally:
        _reset_metrics_global_state()


def test_recovery_active_workflows_inc_dec() -> None:
    """recovery_workflow_start increments, recovery_workflow_end decrements active_workflows."""
    _provider, reader = _make_in_memory_provider()
    try:
        m = ButlerMetrics("healer")
        m.recovery_workflow_start(workflow="healing")
        m.recovery_workflow_start(workflow="healing")
        m.recovery_workflow_end(workflow="healing")

        data = _collect_metrics(reader)
        healing_dps = [
            dp
            for dp in data.get("butlers.recovery.active_workflows", [])
            if dp.attributes.get("workflow") == "healing"
        ]
        assert len(healing_dps) == 1
        assert healing_dps[0].value == 1
        assert healing_dps[0].attributes.get("butler") == "healer"
    finally:
        _reset_metrics_global_state()


def test_recovery_dispatch_decisions_total() -> None:
    """record_recovery_dispatch_decision increments counter with correct labels."""
    _provider, reader = _make_in_memory_provider()
    try:
        m = ButlerMetrics("patrol")
        m.record_recovery_dispatch_decision(workflow="healing", decision="cooldown")
        m.record_recovery_dispatch_decision(workflow="healing", decision="cooldown")
        m.record_recovery_dispatch_decision(workflow="qa", decision="circuit_breaker")

        data = _collect_metrics(reader)
        dps = data.get("butlers.recovery.dispatch_decisions_total", [])
        assert len(dps) >= 2, f"expected >= 2 data points, got {len(dps)}"

        cooldown_dps = [
            dp
            for dp in dps
            if dp.attributes.get("workflow") == "healing"
            and dp.attributes.get("decision") == "cooldown"
        ]
        assert len(cooldown_dps) == 1 and cooldown_dps[0].value == 2

        cb_dps = [
            dp
            for dp in dps
            if dp.attributes.get("workflow") == "qa"
            and dp.attributes.get("decision") == "circuit_breaker"
        ]
        assert len(cb_dps) == 1 and cb_dps[0].value == 1
        assert cb_dps[0].attributes.get("butler") == "patrol"
    finally:
        _reset_metrics_global_state()


def test_recovery_execution_failures_total() -> None:
    """record_recovery_execution_failure increments counter with phase and error_class labels."""
    _provider, reader = _make_in_memory_provider()
    try:
        m = ButlerMetrics("analyst")
        m.record_recovery_execution_failure(
            workflow="healing", phase="diagnose_and_fix", error_class="agent_failure"
        )
        m.record_recovery_execution_failure(
            workflow="qa", phase="investigate", error_class="anonymization_failed"
        )

        data = _collect_metrics(reader)
        dps = data.get("butlers.recovery.execution_failures_total", [])
        assert len(dps) >= 2

        healing_dps = [
            dp
            for dp in dps
            if dp.attributes.get("workflow") == "healing"
            and dp.attributes.get("phase") == "diagnose_and_fix"
            and dp.attributes.get("error_class") == "agent_failure"
        ]
        assert len(healing_dps) == 1 and healing_dps[0].value == 1
        assert healing_dps[0].attributes.get("butler") == "analyst"

        qa_dps = [
            dp
            for dp in dps
            if dp.attributes.get("workflow") == "qa"
            and dp.attributes.get("phase") == "investigate"
            and dp.attributes.get("error_class") == "anonymization_failed"
        ]
        assert len(qa_dps) == 1 and qa_dps[0].value == 1
    finally:
        _reset_metrics_global_state()


def test_recovery_phase_duration_ms() -> None:
    """record_recovery_phase_duration records histogram with workflow, phase, outcome labels."""
    _provider, reader = _make_in_memory_provider()
    try:
        m = ButlerMetrics("analyst")
        m.record_recovery_phase_duration(
            workflow="healing",
            phase="diagnose_and_fix",
            outcome="success",
            duration_ms=1500.0,
        )
        m.record_recovery_phase_duration(
            workflow="qa",
            phase="investigate",
            outcome="failed",
            duration_ms=500.0,
        )

        data = _collect_metrics(reader)
        dps = data.get("butlers.recovery.phase_duration_ms", [])
        assert len(dps) >= 2

        success_dps = [
            dp
            for dp in dps
            if dp.attributes.get("workflow") == "healing"
            and dp.attributes.get("phase") == "diagnose_and_fix"
            and dp.attributes.get("outcome") == "success"
        ]
        assert len(success_dps) == 1
        assert success_dps[0].sum == pytest.approx(1500.0, rel=1e-3)
        assert success_dps[0].attributes.get("butler") == "analyst"

        failed_dps = [
            dp
            for dp in dps
            if dp.attributes.get("workflow") == "qa"
            and dp.attributes.get("phase") == "investigate"
            and dp.attributes.get("outcome") == "failed"
        ]
        assert len(failed_dps) == 1
        assert failed_dps[0].sum == pytest.approx(500.0, rel=1e-3)
    finally:
        _reset_metrics_global_state()


def test_healing_dispatch_emits_dispatch_decision_metric() -> None:
    """dispatch_healing emits dispatch_decisions_total on gate rejections."""
    import asyncio
    import uuid
    from pathlib import Path
    from unittest.mock import AsyncMock, MagicMock, patch

    from butlers.core.healing.dispatch import HealingConfig, dispatch_healing
    from butlers.core.healing.fingerprint import FingerprintResult

    _provider, reader = _make_in_memory_provider()
    try:
        m = ButlerMetrics("test-butler")

        fp = FingerprintResult(
            fingerprint="a" * 64,
            severity=2,
            exception_type="builtins.ValueError",
            call_site="src/module.py:func",
            sanitized_message="error",
        )
        config = HealingConfig(
            enabled=True,
            severity_threshold=2,
            cooldown_minutes=60,
            max_concurrent=2,
            circuit_breaker_threshold=5,
            timeout_minutes=30,
        )

        pool = MagicMock()

        async def fetchrow(*args, **kwargs):
            return {"status": "pr_open"}  # simulate recent attempt → cooldown

        async def fetchval(*args, **kwargs):
            return 0

        async def fetch(*args, **kwargs):
            return []

        async def execute(*args, **kwargs):
            pass

        pool.fetchrow = AsyncMock(side_effect=fetchrow)
        pool.fetchval = AsyncMock(side_effect=fetchval)
        pool.fetch = AsyncMock(side_effect=fetch)
        pool.execute = AsyncMock(side_effect=execute)

        # Patch create_or_join_attempt to return is_new=True, then patch get_recent_attempt
        # to return a record so the cooldown gate triggers.
        with (
            patch(
                "butlers.core.healing.dispatch.create_or_join_attempt",
                new=AsyncMock(return_value=(uuid.uuid4(), True)),
            ),
            patch(
                "butlers.core.healing.dispatch.get_recent_attempt",
                new=AsyncMock(return_value={"status": "pr_open"}),
            ),
            patch("butlers.core.healing.dispatch.delete_orphaned_attempt", new=AsyncMock()),
            patch("butlers.core.healing.dispatch.create_dispatch_event", new=AsyncMock()),
            patch(
                "butlers.core.healing.dispatch.session_set_healing_fingerprint",
                new=AsyncMock(),
            ),
        ):
            result = asyncio.run(
                dispatch_healing(
                    pool=pool,
                    butler_name="test-butler",
                    session_id=uuid.uuid4(),
                    fingerprint_input=fp,
                    config=config,
                    repo_root=Path("/tmp"),
                    spawner=MagicMock(),
                    metrics=m,
                )
            )

        assert result.reason == "cooldown"
        assert not result.accepted

        data = _collect_metrics(reader)
        dps = data.get("butlers.recovery.dispatch_decisions_total", [])
        cooldown_dps = [
            dp
            for dp in dps
            if dp.attributes.get("workflow") == "healing"
            and dp.attributes.get("decision") == "cooldown"
        ]
        assert len(cooldown_dps) == 1 and cooldown_dps[0].value == 1
        assert cooldown_dps[0].attributes.get("butler") == "test-butler"
    finally:
        _reset_metrics_global_state()
