"""OpenTelemetry metrics instruments for the butler concurrency subsystem.

Emits 11 metrics covering spawner concurrency, durable buffer health, and
route.execute accept/process phases.  Instruments are created lazily from the
global MeterProvider, so callers do not need to pass a Meter instance around.

Initialization
--------------
Call ``init_metrics(service_name)`` once during butler daemon startup (alongside
``init_telemetry``).  When OTEL_EXPORTER_OTLP_ENDPOINT is not set, the SDK
falls back to a no-op MeterProvider and all recordings are silent no-ops.

Instruments
-----------
Spawner (emitted from spawner.py):

  butlers.spawner.active_sessions     UpDownCounter (gauge semantics)
      Current concurrent sessions per butler.

  butlers.spawner.queued_triggers     UpDownCounter (gauge semantics)
      Tasks waiting for the semaphore (i.e. queued behind the concurrency cap).

  butlers.spawner.session_duration_ms Histogram
      End-to-end session duration in milliseconds.

Buffer (emitted from buffer.py):

  butlers.buffer.queue_depth          UpDownCounter (gauge semantics)
      Current in-memory queue depth.

  butlers.buffer.enqueue_total        Counter  (label: path=hot|cold)
      Messages enqueued via the hot path or recovered by the scanner.

  butlers.buffer.backpressure_total   Counter
      Queue-full events (hot path drops).

  butlers.buffer.scanner_recovered_total  Counter
      Messages recovered by the periodic scanner.

  butlers.buffer.process_latency_ms   Histogram
      Time from enqueued_at to processing start (queue wait time).

Route (emitted from daemon.py route.execute):

  butlers.route.accept_latency_ms     Histogram
      Time for target butler to acknowledge receipt (accept phase duration).

  butlers.route.queue_depth           UpDownCounter (gauge semantics)
      Accepted-but-unprocessed route_inbox rows per butler.

  butlers.route.process_latency_ms    Histogram
      Time from acceptance (inbox insert) to processing start.

All instruments carry a ``butler`` label for per-butler drill-down in Grafana.
"""

from __future__ import annotations

import logging
import os

from opentelemetry import metrics

logger = logging.getLogger(__name__)

_METER_NAME = "butlers"

# ---------------------------------------------------------------------------
# MeterProvider initialization
# ---------------------------------------------------------------------------


def init_metrics(service_name: str) -> metrics.Meter:
    """Initialize OpenTelemetry metrics for a butler daemon.

    When OTEL_EXPORTER_OTLP_ENDPOINT is set, configures a real MeterProvider
    with a periodic OTLP gRPC exporter.  Otherwise, the global no-op
    MeterProvider is used and all recordings are silent.

    Call this once on daemon startup alongside ``init_telemetry``.

    Args:
        service_name: The butler's service name (e.g. "butler-switchboard").

    Returns:
        A Meter instance bound to the global MeterProvider.
    """
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")

    if not endpoint:
        logger.info("OTEL_EXPORTER_OTLP_ENDPOINT not set, using no-op meter")
        return metrics.get_meter(_METER_NAME)

    # Import SDK/exporter only when needed to avoid hard dependency at import time
    from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource

    resource = Resource.create({"service.name": service_name})
    exporter = OTLPMetricExporter(endpoint=endpoint)
    reader = PeriodicExportingMetricReader(exporter, export_interval_millis=15_000)
    provider = MeterProvider(resource=resource, metric_readers=[reader])

    metrics.set_meter_provider(provider)
    logger.info("Metrics initialized: service=%s, endpoint=%s", service_name, endpoint)

    return metrics.get_meter(_METER_NAME)


def get_meter() -> metrics.Meter:
    """Return a Meter from the current global provider.

    Usable after ``init_metrics`` has been called.  Safe to call before
    initialization — returns a no-op meter in that case.
    """
    return metrics.get_meter(_METER_NAME)


# ---------------------------------------------------------------------------
# Spawner instruments
# ---------------------------------------------------------------------------


def _spawner_active_sessions() -> metrics.UpDownCounter:
    """UpDownCounter: current concurrent sessions per butler."""
    return get_meter().create_up_down_counter(
        name="butlers.spawner.active_sessions",
        description="Current number of concurrent LLM sessions per butler",
        unit="sessions",
    )


def _spawner_queued_triggers() -> metrics.UpDownCounter:
    """UpDownCounter: tasks waiting for the semaphore."""
    return get_meter().create_up_down_counter(
        name="butlers.spawner.queued_triggers",
        description="Number of triggers waiting for a concurrency slot (semaphore queue)",
        unit="triggers",
    )


def _spawner_session_duration_ms() -> metrics.Histogram:
    """Histogram: per-session end-to-end duration in milliseconds."""
    return get_meter().create_histogram(
        name="butlers.spawner.session_duration_ms",
        description="End-to-end LLM session duration in milliseconds",
        unit="ms",
    )


# ---------------------------------------------------------------------------
# Buffer instruments
# ---------------------------------------------------------------------------


def _buffer_queue_depth() -> metrics.UpDownCounter:
    """UpDownCounter: current in-memory queue depth."""
    return get_meter().create_up_down_counter(
        name="butlers.buffer.queue_depth",
        description="Current number of messages in the durable buffer in-memory queue",
        unit="messages",
    )


def _buffer_enqueue_total() -> metrics.Counter:
    """Counter: messages enqueued (label: path=hot|cold)."""
    return get_meter().create_counter(
        name="butlers.buffer.enqueue_total",
        description="Total messages enqueued via hot path or recovered by scanner",
        unit="messages",
    )


def _buffer_backpressure_total() -> metrics.Counter:
    """Counter: queue-full events (hot path drops)."""
    return get_meter().create_counter(
        name="butlers.buffer.backpressure_total",
        description="Total queue-full backpressure events on the hot enqueue path",
        unit="events",
    )


def _buffer_scanner_recovered_total() -> metrics.Counter:
    """Counter: messages recovered by the periodic scanner."""
    return get_meter().create_counter(
        name="butlers.buffer.scanner_recovered_total",
        description="Total messages recovered by the periodic buffer scanner",
        unit="messages",
    )


def _buffer_process_latency_ms() -> metrics.Histogram:
    """Histogram: queue wait time from enqueue to processing start (ms)."""
    return get_meter().create_histogram(
        name="butlers.buffer.process_latency_ms",
        description="Time from message enqueue to processing start in milliseconds",
        unit="ms",
    )


# ---------------------------------------------------------------------------
# Route instruments
# ---------------------------------------------------------------------------


def _route_accept_latency_ms() -> metrics.Histogram:
    """Histogram: accept phase duration (inbox insert + response) in ms."""
    return get_meter().create_histogram(
        name="butlers.route.accept_latency_ms",
        description="Time for target butler to acknowledge route.execute receipt",
        unit="ms",
    )


def _route_queue_depth() -> metrics.UpDownCounter:
    """UpDownCounter: accepted-but-unprocessed route_inbox rows per butler."""
    return get_meter().create_up_down_counter(
        name="butlers.route.queue_depth",
        description="Accepted-but-unprocessed route requests per butler",
        unit="requests",
    )


def _route_process_latency_ms() -> metrics.Histogram:
    """Histogram: time from route_inbox insert to processing start (ms)."""
    return get_meter().create_histogram(
        name="butlers.route.process_latency_ms",
        description="Time from route_inbox acceptance to processing start in milliseconds",
        unit="ms",
    )


# ---------------------------------------------------------------------------
# ButlerMetrics — convenience wrapper that caches instruments per butler
# ---------------------------------------------------------------------------


class ButlerMetrics:
    """Convenience wrapper around all butler concurrency metrics.

    Create one instance per butler name.  Instruments are lazily created from
    the global MeterProvider on first use, so it is safe to construct this
    object before ``init_metrics`` is called (all recordings will be no-ops
    until a real provider is installed).

    Typical usage::

        _metrics = ButlerMetrics(butler_name="analyst")

        # In spawner.trigger():
        _metrics.spawner_queued_triggers_inc()
        async with semaphore:
            _metrics.spawner_queued_triggers_dec()
            _metrics.spawner_active_sessions_inc()
            try:
                result = await _run(...)
                _metrics.record_session_duration(result.duration_ms)
            finally:
                _metrics.spawner_active_sessions_dec()
    """

    def __init__(self, butler_name: str) -> None:
        self._butler = butler_name
        self._attrs = {"butler": butler_name}

        # Instruments are created lazily; store factory lambdas here so that
        # the class body does not eagerly call get_meter() at module import time
        # (the provider may not be set up yet).
        self.__spawner_active: metrics.UpDownCounter | None = None
        self.__spawner_queued: metrics.UpDownCounter | None = None
        self.__spawner_duration: metrics.Histogram | None = None
        self.__buf_depth: metrics.UpDownCounter | None = None
        self.__buf_enqueue: metrics.Counter | None = None
        self.__buf_backpressure: metrics.Counter | None = None
        self.__buf_scanner: metrics.Counter | None = None
        self.__buf_latency: metrics.Histogram | None = None
        self.__route_accept: metrics.Histogram | None = None
        self.__route_depth: metrics.UpDownCounter | None = None
        self.__route_process: metrics.Histogram | None = None

    # -- instrument accessors (lazy init) ------------------------------------

    @property
    def _spawner_active(self) -> metrics.UpDownCounter:
        if self.__spawner_active is None:
            self.__spawner_active = _spawner_active_sessions()
        return self.__spawner_active

    @property
    def _spawner_queued(self) -> metrics.UpDownCounter:
        if self.__spawner_queued is None:
            self.__spawner_queued = _spawner_queued_triggers()
        return self.__spawner_queued

    @property
    def _spawner_duration(self) -> metrics.Histogram:
        if self.__spawner_duration is None:
            self.__spawner_duration = _spawner_session_duration_ms()
        return self.__spawner_duration

    @property
    def _buf_depth(self) -> metrics.UpDownCounter:
        if self.__buf_depth is None:
            self.__buf_depth = _buffer_queue_depth()
        return self.__buf_depth

    @property
    def _buf_enqueue(self) -> metrics.Counter:
        if self.__buf_enqueue is None:
            self.__buf_enqueue = _buffer_enqueue_total()
        return self.__buf_enqueue

    @property
    def _buf_backpressure(self) -> metrics.Counter:
        if self.__buf_backpressure is None:
            self.__buf_backpressure = _buffer_backpressure_total()
        return self.__buf_backpressure

    @property
    def _buf_scanner(self) -> metrics.Counter:
        if self.__buf_scanner is None:
            self.__buf_scanner = _buffer_scanner_recovered_total()
        return self.__buf_scanner

    @property
    def _buf_latency(self) -> metrics.Histogram:
        if self.__buf_latency is None:
            self.__buf_latency = _buffer_process_latency_ms()
        return self.__buf_latency

    @property
    def _route_accept(self) -> metrics.Histogram:
        if self.__route_accept is None:
            self.__route_accept = _route_accept_latency_ms()
        return self.__route_accept

    @property
    def _route_depth(self) -> metrics.UpDownCounter:
        if self.__route_depth is None:
            self.__route_depth = _route_queue_depth()
        return self.__route_depth

    @property
    def _route_process(self) -> metrics.Histogram:
        if self.__route_process is None:
            self.__route_process = _route_process_latency_ms()
        return self.__route_process

    # -- spawner recording helpers ------------------------------------------

    def spawner_active_sessions_inc(self) -> None:
        """Record that an LLM session has started (semaphore acquired)."""
        self._spawner_active.add(1, self._attrs)

    def spawner_active_sessions_dec(self) -> None:
        """Record that an LLM session has ended (semaphore released)."""
        self._spawner_active.add(-1, self._attrs)

    def spawner_queued_triggers_inc(self) -> None:
        """Record that a trigger is waiting for a concurrency slot."""
        self._spawner_queued.add(1, self._attrs)

    def spawner_queued_triggers_dec(self) -> None:
        """Record that a trigger has acquired its concurrency slot."""
        self._spawner_queued.add(-1, self._attrs)

    def record_session_duration(self, duration_ms: int) -> None:
        """Record the end-to-end duration of a completed session."""
        self._spawner_duration.record(duration_ms, self._attrs)

    # -- buffer recording helpers -------------------------------------------

    def buffer_queue_depth_inc(self) -> None:
        """Record that one message was added to the in-memory queue."""
        self._buf_depth.add(1, self._attrs)

    def buffer_queue_depth_dec(self) -> None:
        """Record that one message was removed from the in-memory queue."""
        self._buf_depth.add(-1, self._attrs)

    def buffer_enqueue_hot(self) -> None:
        """Record a successful hot-path enqueue."""
        self._buf_enqueue.add(1, {**self._attrs, "path": "hot"})

    def buffer_enqueue_cold(self) -> None:
        """Record a cold-path enqueue (scanner recovery)."""
        self._buf_enqueue.add(1, {**self._attrs, "path": "cold"})

    def buffer_backpressure(self) -> None:
        """Record a queue-full backpressure event."""
        self._buf_backpressure.add(1, self._attrs)

    def buffer_scanner_recovered(self) -> None:
        """Record one message recovered by the scanner."""
        self._buf_scanner.add(1, self._attrs)

    def record_buffer_process_latency(self, latency_ms: float) -> None:
        """Record buffer process latency (queue wait time) in ms."""
        self._buf_latency.record(latency_ms, self._attrs)

    # -- route recording helpers --------------------------------------------

    def record_route_accept_latency(self, latency_ms: float) -> None:
        """Record the accept phase duration for a route.execute call."""
        self._route_accept.record(latency_ms, self._attrs)

    def route_queue_depth_inc(self) -> None:
        """Record that one route request entered the inbox (accepted)."""
        self._route_depth.add(1, self._attrs)

    def route_queue_depth_dec(self) -> None:
        """Record that one route request left the inbox (processing started)."""
        self._route_depth.add(-1, self._attrs)

    def record_route_process_latency(self, latency_ms: float) -> None:
        """Record time from route_inbox acceptance to processing start in ms."""
        self._route_process.record(latency_ms, self._attrs)
