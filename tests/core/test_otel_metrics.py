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

    def test_returns_meter_when_endpoint_not_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """init_metrics returns a Meter even without OTEL_EXPORTER_OTLP_ENDPOINT."""
        monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
        meter = init_metrics("butler-test")
        assert meter is not None

    def test_meter_is_usable_for_no_op_recording(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Meters returned without an endpoint are no-op and don't raise."""
        monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
        meter = init_metrics("butler-test")
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

    # --- spawner metrics ---

    def test_spawner_active_sessions_inc_dec(self) -> None:
        m = ButlerMetrics("spawner-butler")
        m.spawner_active_sessions_inc()
        m.spawner_active_sessions_inc()
        m.spawner_active_sessions_dec()

        data = self._metrics_map()
        assert "butlers.spawner.active_sessions" in data
        dp = data["butlers.spawner.active_sessions"][0]
        assert dp.value == 1  # 2 inc - 1 dec
        assert dp.attributes == {"butler": "spawner-butler"}

    def test_spawner_queued_triggers_inc_dec(self) -> None:
        m = ButlerMetrics("q-butler")
        m.spawner_queued_triggers_inc()
        m.spawner_queued_triggers_inc()
        m.spawner_queued_triggers_dec()

        data = self._metrics_map()
        assert "butlers.spawner.queued_triggers" in data
        dp = data["butlers.spawner.queued_triggers"][0]
        assert dp.value == 1

    def test_spawner_session_duration_ms(self) -> None:
        m = ButlerMetrics("dur-butler")
        m.record_session_duration(1234)

        data = self._metrics_map()
        assert "butlers.spawner.session_duration_ms" in data
        dp = data["butlers.spawner.session_duration_ms"][0]
        assert dp.count == 1
        assert dp.sum == 1234

    # --- buffer metrics ---

    def test_buffer_queue_depth_inc_dec(self) -> None:
        m = ButlerMetrics("buf-butler")
        m.buffer_queue_depth_inc()
        m.buffer_queue_depth_inc()
        m.buffer_queue_depth_dec()

        data = self._metrics_map()
        assert "butlers.buffer.queue_depth" in data
        dp = data["butlers.buffer.queue_depth"][0]
        assert dp.value == 1

    def test_buffer_enqueue_hot_label(self) -> None:
        m = ButlerMetrics("buf-butler")
        m.buffer_enqueue_hot()
        m.buffer_enqueue_hot()

        data = self._metrics_map()
        assert "butlers.buffer.enqueue_total" in data
        # Find the data point with path=hot
        hot_dps = [
            dp for dp in data["butlers.buffer.enqueue_total"] if dp.attributes.get("path") == "hot"
        ]
        assert len(hot_dps) == 1
        assert hot_dps[0].value == 2

    def test_buffer_enqueue_cold_label(self) -> None:
        m = ButlerMetrics("buf-butler")
        m.buffer_enqueue_cold()

        data = self._metrics_map()
        assert "butlers.buffer.enqueue_total" in data
        cold_dps = [
            dp for dp in data["butlers.buffer.enqueue_total"] if dp.attributes.get("path") == "cold"
        ]
        assert len(cold_dps) == 1
        assert cold_dps[0].value == 1

    def test_buffer_backpressure(self) -> None:
        m = ButlerMetrics("buf-butler")
        m.buffer_backpressure()
        m.buffer_backpressure()

        data = self._metrics_map()
        assert "butlers.buffer.backpressure_total" in data
        dp = data["butlers.buffer.backpressure_total"][0]
        assert dp.value == 2

    def test_buffer_scanner_recovered(self) -> None:
        m = ButlerMetrics("buf-butler")
        m.buffer_scanner_recovered()

        data = self._metrics_map()
        assert "butlers.buffer.scanner_recovered_total" in data
        dp = data["butlers.buffer.scanner_recovered_total"][0]
        assert dp.value == 1

    def test_buffer_process_latency_ms(self) -> None:
        m = ButlerMetrics("buf-butler")
        m.record_buffer_process_latency(42.5)

        data = self._metrics_map()
        assert "butlers.buffer.process_latency_ms" in data
        dp = data["butlers.buffer.process_latency_ms"][0]
        assert dp.count == 1
        assert dp.sum == pytest.approx(42.5, rel=1e-3)

    # --- route metrics ---

    def test_route_accept_latency_ms(self) -> None:
        m = ButlerMetrics("route-butler")
        m.record_route_accept_latency(15.0)

        data = self._metrics_map()
        assert "butlers.route.accept_latency_ms" in data
        dp = data["butlers.route.accept_latency_ms"][0]
        assert dp.count == 1
        assert dp.sum == pytest.approx(15.0, rel=1e-3)

    def test_route_queue_depth_inc_dec(self) -> None:
        m = ButlerMetrics("route-butler")
        m.route_queue_depth_inc()
        m.route_queue_depth_inc()
        m.route_queue_depth_dec()

        data = self._metrics_map()
        assert "butlers.route.queue_depth" in data
        dp = data["butlers.route.queue_depth"][0]
        assert dp.value == 1

    def test_route_process_latency_ms(self) -> None:
        m = ButlerMetrics("route-butler")
        m.record_route_process_latency(100.0)

        data = self._metrics_map()
        assert "butlers.route.process_latency_ms" in data
        dp = data["butlers.route.process_latency_ms"][0]
        assert dp.count == 1

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

    async def test_active_sessions_zero_after_trigger(self, tmp_path: Path) -> None:
        """After a successful trigger, active_sessions returns to 0."""
        config = _make_butler_config(name="metrics-spawner-test")
        (tmp_path / "CLAUDE.md").write_text("# test")
        spawner = Spawner(config=config, config_dir=tmp_path, runtime=MockAdapter())

        await spawner.trigger(prompt="hello", trigger_source="tick")

        data = self._metrics_map()
        active_dps = data.get("butlers.spawner.active_sessions", [])
        total = sum(dp.value for dp in active_dps)
        assert total == 0  # incremented then decremented

    async def test_session_duration_recorded(self, tmp_path: Path) -> None:
        """session_duration_ms histogram has count=1 after a single trigger."""
        config = _make_butler_config(name="duration-test-butler")
        (tmp_path / "CLAUDE.md").write_text("# test")
        spawner = Spawner(config=config, config_dir=tmp_path, runtime=MockAdapter())

        await spawner.trigger(prompt="hello", trigger_source="tick")

        data = self._metrics_map()
        assert "butlers.spawner.session_duration_ms" in data
        dp = data["butlers.spawner.session_duration_ms"][0]
        assert dp.count == 1
        assert dp.sum >= 0

    async def test_queued_triggers_zero_after_completion(self, tmp_path: Path) -> None:
        """queued_triggers decrements back to 0 after trigger completes."""
        config = _make_butler_config(name="queue-test-butler")
        (tmp_path / "CLAUDE.md").write_text("# test")
        spawner = Spawner(config=config, config_dir=tmp_path, runtime=MockAdapter())

        await spawner.trigger(prompt="hello", trigger_source="tick")

        data = self._metrics_map()
        queued_dps = data.get("butlers.spawner.queued_triggers", [])
        total = sum(dp.value for dp in queued_dps)
        assert total == 0


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

    def test_hot_enqueue_increments_enqueue_total_and_depth(self) -> None:
        """Successful hot-path enqueue updates enqueue_total and queue_depth."""
        buf = DurableBuffer(
            config=_make_buffer_config(),
            pool=None,
            process_fn=AsyncMock(),
            butler_name="sw",
        )
        buf.enqueue(
            request_id="r1",
            message_inbox_id="r1",
            message_text="msg",
            source={},
            event={},
            sender={},
        )

        data = self._metrics_map()

        # enqueue_total with path=hot
        hot_dps = [
            dp
            for dp in data.get("butlers.buffer.enqueue_total", [])
            if dp.attributes.get("path") == "hot"
        ]
        assert len(hot_dps) == 1
        assert hot_dps[0].value == 1

        # queue_depth incremented
        depth_dps = data.get("butlers.buffer.queue_depth", [])
        total_depth = sum(dp.value for dp in depth_dps)
        assert total_depth == 1

    def test_backpressure_increments_counter(self) -> None:
        """When queue is full, backpressure_total is incremented."""
        buf = DurableBuffer(
            config=_make_buffer_config(queue_capacity=1),
            pool=None,
            process_fn=AsyncMock(),
            butler_name="sw",
        )
        # Fill the queue
        buf.enqueue(
            request_id="r1",
            message_inbox_id="r1",
            message_text="first",
            source={},
            event={},
            sender={},
        )
        # Trigger backpressure
        buf.enqueue(
            request_id="r2",
            message_inbox_id="r2",
            message_text="second",
            source={},
            event={},
            sender={},
        )

        data = self._metrics_map()
        bp_dps = data.get("butlers.buffer.backpressure_total", [])
        assert sum(dp.value for dp in bp_dps) == 1

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


# ---------------------------------------------------------------------------
# Multi-butler provider guard
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Multi-butler provider guard
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Multi-butler provider guard
# ---------------------------------------------------------------------------


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

    def test_second_init_does_not_call_set_provider_again(self) -> None:
        """Second init_metrics call reuses the existing provider without override.

        Simulates a second butler calling init_metrics by pre-installing a provider
        and setting the guard flag, then verifying init_metrics returns a meter
        without reinstalling.
        """
        import os as _os

        reader = InMemoryMetricReader()
        from opentelemetry.sdk.metrics import MeterProvider as _MP
        from opentelemetry.sdk.resources import Resource as _Res

        # Install a real provider (simulating the first butler's init_metrics)
        provider = _MP(
            resource=_Res.create({"service.name": "butlers"}),
            metric_readers=[reader],
        )
        metrics.set_meter_provider(provider)
        _metrics_mod._meter_provider_installed = True

        # Track whether set_meter_provider is called again
        set_count = 0
        original_set = metrics.set_meter_provider

        def guarded_set(p):
            nonlocal set_count
            set_count += 1
            original_set(p)

        # Replace set_meter_provider to detect if it gets called
        metrics.set_meter_provider = guarded_set
        try:
            _os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = "http://localhost:4317"
            try:
                meter = init_metrics("butler.general")
            finally:
                _os.environ.pop("OTEL_EXPORTER_OTLP_ENDPOINT", None)
        finally:
            metrics.set_meter_provider = original_set

        assert set_count == 0, (
            f"set_meter_provider called {set_count} times on second init; expected 0"
        )
        assert meter is not None

    def test_noop_mode_returns_valid_meter(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """In no-op mode init_metrics returns a valid meter (no endpoint → no provider install)."""
        monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)

        # Track whether set_meter_provider is called at all
        set_count = 0
        original_set = metrics.set_meter_provider

        def guarded_set(p):
            nonlocal set_count
            set_count += 1
            original_set(p)

        metrics.set_meter_provider = guarded_set
        try:
            meter1 = init_metrics("butler.finance")
            meter2 = init_metrics("butler.general")
        finally:
            metrics.set_meter_provider = original_set

        # set_meter_provider must not be called in no-op mode
        assert set_count == 0, (
            f"set_meter_provider called {set_count} times in no-op mode; expected 0"
        )
        assert meter1 is not None
        assert meter2 is not None

    def test_second_call_returns_usable_meter(self) -> None:
        """Meter returned by second init_metrics call is valid and records without error."""
        import os as _os

        reader = InMemoryMetricReader()
        from opentelemetry.sdk.metrics import MeterProvider as _MP
        from opentelemetry.sdk.resources import Resource as _Res

        provider = _MP(
            resource=_Res.create({"service.name": "butlers"}),
            metric_readers=[reader],
        )
        metrics.set_meter_provider(provider)
        _metrics_mod._meter_provider_installed = True

        # Second butler call (endpoint set but guard fires early)
        _os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = "http://localhost:4317"
        try:
            meter = init_metrics("butler.general")
        finally:
            _os.environ.pop("OTEL_EXPORTER_OTLP_ENDPOINT", None)

        assert meter is not None

        # Meter must be usable
        counter = meter.create_counter("test.multi.counter")
        counter.add(5, {"butler": "general"})

        data = _collect_metrics(reader)
        assert "test.multi.counter" in data
        assert data["test.multi.counter"][0].value == 5
