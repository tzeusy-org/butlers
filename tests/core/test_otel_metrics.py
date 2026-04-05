"""Unit tests for the OTel metrics instruments (butlers-963.9).

Covers:
- init_metrics: no-op when OTEL_EXPORTER_OTLP_ENDPOINT is not set
- ButlerMetrics: all 11 instruments record values with correct attributes
- Spawner: active_sessions, queued_triggers, session_duration_ms updated on trigger()
- DurableBuffer: queue_depth, enqueue_total, backpressure_total,
                  scanner_recovered_total, process_latency_ms
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
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


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _reset_metrics_global_state() -> None:
    """Reset the OTel global MeterProvider state for test isolation.

    The OTel SDK uses a ``Once`` guard that prevents ``set_meter_provider``
    from being called more than once per process.  For test isolation we
    reset both the guard and the cached provider reference.
    """
    _metrics_internal._METER_PROVIDER_SET_ONCE = Once()
    _metrics_internal._METER_PROVIDER = None


def _make_in_memory_provider() -> tuple[MeterProvider, InMemoryMetricReader]:
    """Create a MeterProvider with an InMemoryMetricReader for test assertions."""
    _reset_metrics_global_state()
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    metrics.set_meter_provider(provider)
    return provider, reader


def _collect_metrics(reader: InMemoryMetricReader) -> dict[str, Any]:
    """Flatten metrics data into {metric_name: data_point} for easy assertions."""
    result: dict[str, Any] = {}
    data = reader.get_metrics_data()
    for rm in data.resource_metrics:
        for sm in rm.scope_metrics:
            for metric in sm.metrics:
                if metric.data.data_points:
                    result[metric.name] = metric.data.data_points
    return result


class MockAdapter(RuntimeAdapter):
    """Minimal adapter returning fixed result for spawner tests."""

    @property
    def binary_name(self) -> str:
        return "mock"

    async def invoke(
        self,
        prompt: str,
        system_prompt: str,
        mcp_servers: dict[str, Any],
        env: dict[str, str],
        max_turns: int = 20,
        model: str | None = None,
        cwd: Path | None = None,
        timeout: int | None = None,
    ) -> tuple[str | None, list[dict[str, Any]], dict[str, Any] | None]:
        return "result", [], None

    def build_config_file(self, mcp_servers: dict[str, Any], tmp_dir: Path) -> Path:
        return tmp_dir / "mock_config.json"

    def parse_system_prompt_file(self, config_dir: Path) -> str:
        return ""


def _make_butler_config(name: str = "test-butler", max_concurrent: int = 2) -> ButlerConfig:
    return ButlerConfig(
        name=name,
        port=9100,
        runtime=RuntimeConfig(max_concurrent_sessions=max_concurrent),
    )


def _make_buffer_config(queue_capacity: int = 10) -> BufferConfig:
    return BufferConfig(
        queue_capacity=queue_capacity,
        worker_count=1,
        scanner_interval_s=30,
        scanner_grace_s=10,
        scanner_batch_size=50,
    )


def _make_message_ref(request_id: str = "r1") -> _MessageRef:
    return _MessageRef(
        request_id=request_id,
        message_inbox_id=request_id,
        message_text="hello",
        source={"channel": "telegram"},
        event={},
        sender={"identity": "u1"},
        enqueued_at=datetime.now(UTC),
    )


# ---------------------------------------------------------------------------
# init_metrics
# ---------------------------------------------------------------------------


class TestInitMetrics:
    """init_metrics behavior with and without OTEL endpoint."""

    def test_meter_returned_and_usable_without_endpoint(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """init_metrics returns a usable no-op meter even without OTEL_EXPORTER_OTLP_ENDPOINT."""
        monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
        meter = init_metrics("butler-test")
        assert meter is not None
        counter = meter.create_counter("test.counter")
        counter.add(1, {"key": "val"})  # must not raise


# ---------------------------------------------------------------------------
# ButlerMetrics — all 11 instruments
# ---------------------------------------------------------------------------


class TestButlerMetrics:
    """ButlerMetrics records all 11 metrics with correct attribute labels."""

    @pytest.fixture(autouse=True)
    def _install_provider(self) -> None:
        """Install a real in-memory MeterProvider for each test."""
        _provider, reader = _make_in_memory_provider()
        self._reader = reader
        yield
        # Reset OTel state so next test gets a fresh provider
        _reset_metrics_global_state()

    def _metrics_map(self) -> dict[str, Any]:
        return _collect_metrics(self._reader)

    def test_ensure_registered_creates_zero_series(self) -> None:
        m = ButlerMetrics("idle-butler")
        m.ensure_registered()

        data = self._metrics_map()
        assert "butlers.spawner.active_sessions" in data
        dp = data["butlers.spawner.active_sessions"][0]
        assert dp.value == 0
        assert dp.attributes == {"butler": "idle-butler"}

        assert "butlers.spawner.queued_triggers" in data
        dp2 = data["butlers.spawner.queued_triggers"][0]
        assert dp2.value == 0

    def test_spawner_metrics_inc_dec_and_duration(self) -> None:
        """active_sessions, queued_triggers, session_duration_ms all record correctly."""
        m = ButlerMetrics("spawner-butler")

        # active_sessions: 2 inc - 1 dec = 1
        m.spawner_active_sessions_inc()
        m.spawner_active_sessions_inc()
        m.spawner_active_sessions_dec()

        # queued_triggers: 2 inc - 1 dec = 1
        m.spawner_queued_triggers_inc()
        m.spawner_queued_triggers_inc()
        m.spawner_queued_triggers_dec()

        # session_duration histogram
        m.record_session_duration(1234)

        data = self._metrics_map()
        assert data["butlers.spawner.active_sessions"][0].value == 1
        assert data["butlers.spawner.active_sessions"][0].attributes == {"butler": "spawner-butler"}
        assert data["butlers.spawner.queued_triggers"][0].value == 1
        assert data["butlers.spawner.session_duration_ms"][0].count == 1
        assert data["butlers.spawner.session_duration_ms"][0].sum == 1234

    def test_buffer_metrics(self) -> None:
        """queue_depth, enqueue_total (hot/cold), backpressure, scanner_recovered all correct."""
        m = ButlerMetrics("buf-butler")

        # queue_depth: 2 inc - 1 dec = 1
        m.buffer_queue_depth_inc()
        m.buffer_queue_depth_inc()
        m.buffer_queue_depth_dec()

        # enqueue_total: 2 hot + 1 cold
        m.buffer_enqueue_hot()
        m.buffer_enqueue_hot()
        m.buffer_enqueue_cold()

        # counters
        m.buffer_backpressure()
        m.buffer_backpressure()
        m.buffer_scanner_recovered()

        # histogram
        m.record_buffer_process_latency(42.5)

        data = self._metrics_map()
        assert data["butlers.buffer.queue_depth"][0].value == 1

        hot_dps = [
            dp for dp in data["butlers.buffer.enqueue_total"] if dp.attributes.get("path") == "hot"
        ]
        cold_dps = [
            dp for dp in data["butlers.buffer.enqueue_total"] if dp.attributes.get("path") == "cold"
        ]
        assert hot_dps[0].value == 2
        assert cold_dps[0].value == 1

        assert data["butlers.buffer.backpressure_total"][0].value == 2
        assert data["butlers.buffer.scanner_recovered_total"][0].value == 1
        dp_lat = data["butlers.buffer.process_latency_ms"][0]
        assert dp_lat.count == 1
        assert dp_lat.sum == pytest.approx(42.5, rel=1e-3)

    def test_route_metrics(self) -> None:
        """accept_latency_ms, queue_depth, process_latency_ms record correctly."""
        m = ButlerMetrics("route-butler")

        m.record_route_accept_latency(15.0)
        m.route_queue_depth_inc()
        m.route_queue_depth_inc()
        m.route_queue_depth_dec()
        m.record_route_process_latency(100.0)

        data = self._metrics_map()
        dp_accept = data["butlers.route.accept_latency_ms"][0]
        assert dp_accept.count == 1
        assert dp_accept.sum == pytest.approx(15.0, rel=1e-3)

        assert data["butlers.route.queue_depth"][0].value == 1

        dp_proc = data["butlers.route.process_latency_ms"][0]
        assert dp_proc.count == 1

    def test_butler_label_in_attributes(self) -> None:
        """All data points carry the correct butler label."""
        m = ButlerMetrics("my-butler")
        m.spawner_active_sessions_inc()
        m.buffer_backpressure()
        m.route_queue_depth_inc()

        data = self._metrics_map()
        for name in [
            "butlers.spawner.active_sessions",
            "butlers.buffer.backpressure_total",
            "butlers.route.queue_depth",
        ]:
            assert name in data
            for dp in data[name]:
                assert dp.attributes.get("butler") == "my-butler", (
                    f"{name} missing butler=my-butler attribute"
                )


# ---------------------------------------------------------------------------
# Spawner metrics integration
# ---------------------------------------------------------------------------


class TestSpawnerMetrics:
    """Spawner.trigger() updates active_sessions, queued_triggers, session_duration."""

    @pytest.fixture(autouse=True)
    def _install_provider(self) -> None:
        _provider, reader = _make_in_memory_provider()
        self._reader = reader
        yield
        _reset_metrics_global_state()

    def _metrics_map(self) -> dict[str, Any]:
        return _collect_metrics(self._reader)

    async def test_spawner_trigger_updates_all_metrics(self, tmp_path: Path) -> None:
        """After trigger: active_sessions=0, queued_triggers=0, session_duration recorded."""
        config = _make_butler_config(name="metrics-spawner-test")
        (tmp_path / "CLAUDE.md").write_text("# test")
        spawner = Spawner(config=config, config_dir=tmp_path, runtime=MockAdapter())

        await spawner.trigger(prompt="hello", trigger_source="tick")

        data = self._metrics_map()

        active_total = sum(dp.value for dp in data.get("butlers.spawner.active_sessions", []))
        assert active_total == 0

        queued_total = sum(dp.value for dp in data.get("butlers.spawner.queued_triggers", []))
        assert queued_total == 0

        assert "butlers.spawner.session_duration_ms" in data
        dp = data["butlers.spawner.session_duration_ms"][0]
        assert dp.count == 1
        assert dp.sum >= 0


# ---------------------------------------------------------------------------
# DurableBuffer metrics integration
# ---------------------------------------------------------------------------


class TestDurableBufferMetrics:
    """DurableBuffer updates OTel metrics on enqueue, backpressure, worker, scanner."""

    @pytest.fixture(autouse=True)
    def _install_provider(self) -> None:
        _provider, reader = _make_in_memory_provider()
        self._reader = reader
        yield
        _reset_metrics_global_state()

    def _metrics_map(self) -> dict[str, Any]:
        return _collect_metrics(self._reader)

    def test_enqueue_metrics_and_backpressure(self) -> None:
        """Hot-path enqueue updates enqueue_total and queue_depth; full queue increments backpressure."""
        # Hot enqueue increments enqueue_total and queue_depth
        buf = DurableBuffer(
            config=_make_buffer_config(),
            pool=None,
            process_fn=AsyncMock(),
            butler_name="sw",
        )
        buf.enqueue(request_id="r1", message_inbox_id="r1", message_text="msg",
                    source={}, event={}, sender={})

        data = self._metrics_map()
        hot_dps = [dp for dp in data.get("butlers.buffer.enqueue_total", [])
                   if dp.attributes.get("path") == "hot"]
        assert len(hot_dps) == 1 and hot_dps[0].value == 1
        total_depth = sum(dp.value for dp in data.get("butlers.buffer.queue_depth", []))
        assert total_depth == 1

        # Backpressure: fill queue then overflow increments backpressure_total
        buf2 = DurableBuffer(
            config=_make_buffer_config(queue_capacity=1),
            pool=None,
            process_fn=AsyncMock(),
            butler_name="sw",
        )
        buf2.enqueue(request_id="r1", message_inbox_id="r1", message_text="first",
                     source={}, event={}, sender={})
        buf2.enqueue(request_id="r2", message_inbox_id="r2", message_text="second",
                     source={}, event={}, sender={})

        data2 = self._metrics_map()
        bp_dps = data2.get("butlers.buffer.backpressure_total", [])
        assert sum(dp.value for dp in bp_dps) >= 1

    async def test_worker_decrements_queue_depth_and_records_latency(self) -> None:
        """Worker loop decrements queue_depth and records process_latency_ms."""
        processed = asyncio.Event()

        async def _process_fn(ref: _MessageRef) -> None:
            processed.set()

        buf = DurableBuffer(
            config=_make_buffer_config(),
            pool=None,
            process_fn=_process_fn,
            butler_name="sw",
        )
        await buf.start()

        buf.enqueue(
            request_id="r1",
            message_inbox_id="r1",
            message_text="hello",
            source={},
            event={},
            sender={},
        )

        await asyncio.wait_for(processed.wait(), timeout=2.0)
        # Give the worker a moment to finish recording metrics
        await asyncio.sleep(0.05)

        await buf.stop()

        data = self._metrics_map()

        # queue_depth should be net 0 (inc from enqueue, dec from worker)
        depth_dps = data.get("butlers.buffer.queue_depth", [])
        total_depth = sum(dp.value for dp in depth_dps)
        assert total_depth == 0

        # process_latency_ms should have one observation
        latency_dps = data.get("butlers.buffer.process_latency_ms", [])
        assert latency_dps, "Expected process_latency_ms to be recorded"
        assert latency_dps[0].count == 1


# ---------------------------------------------------------------------------
# Multi-butler provider guard
# ---------------------------------------------------------------------------


class TestMultiButlerMeterProvider:
    """Multiple init_metrics calls must not trigger provider override warnings."""

    @pytest.fixture(autouse=True)
    def _reset(self) -> None:
        _reset_metrics_global_state()
        yield
        _reset_metrics_global_state()

    def test_provider_guard_and_noop_mode(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Second init_metrics does not override provider; no-op mode never calls set_provider."""
        import os as _os

        # Part 1: second init with installed provider → no override
        reader = InMemoryMetricReader()
        from opentelemetry.sdk.metrics import MeterProvider as _MP
        from opentelemetry.sdk.resources import Resource as _Res

        provider = _MP(
            resource=_Res.create({"service.name": "butlers"}),
            metric_readers=[reader],
        )
        metrics.set_meter_provider(provider)
        _metrics_mod._meter_provider_installed = True

        set_count = 0
        original_set = metrics.set_meter_provider

        def guarded_set(p):
            nonlocal set_count
            set_count += 1
            original_set(p)

        metrics.set_meter_provider = guarded_set
        try:
            _os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = "http://localhost:4317"
            try:
                meter = init_metrics("butler.general")
            finally:
                _os.environ.pop("OTEL_EXPORTER_OTLP_ENDPOINT", None)
        finally:
            metrics.set_meter_provider = original_set

        assert set_count == 0
        assert meter is not None
        counter = meter.create_counter("test.multi.counter")
        counter.add(5, {"butler": "general"})
        data = _collect_metrics(reader)
        assert "test.multi.counter" in data
        assert data["test.multi.counter"][0].value == 5

        # Part 2: no-op mode (no endpoint) — set_provider never called
        _reset_metrics_global_state()
        monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)

        set_count2 = 0
        original_set2 = metrics.set_meter_provider

        def guarded_set2(p):
            nonlocal set_count2
            set_count2 += 1
            original_set2(p)

        metrics.set_meter_provider = guarded_set2
        try:
            meter1 = init_metrics("butler.finance")
            meter2 = init_metrics("butler.general")
        finally:
            metrics.set_meter_provider = original_set2

        assert set_count2 == 0
        assert meter1 is not None and meter2 is not None
