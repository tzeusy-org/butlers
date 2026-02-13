"""Switchboard telemetry helpers for metrics, attributes, and request correlation."""

from __future__ import annotations

import re
import threading
from collections.abc import Iterable
from contextlib import contextmanager
from typing import Any

from opentelemetry import metrics
from opentelemetry.metrics import CallbackOptions, Observation

_METER_NAME = "butlers.switchboard"
_SCHEMA_VERSION_DEFAULT = "route.v1"

_ALLOWED_ATTRIBUTE_KEYS = frozenset(
    {
        "source",
        "destination_butler",
        "outcome",
        "lifecycle_state",
        "error_class",
        "policy_tier",
        "fanout_mode",
        "model_family",
        "prompt_version",
        "schema_version",
    }
)

_ERROR_CLASS_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)(?::|$)")


def normalize_error_class(value: Exception | str | None) -> str:
    """Return a bounded, low-cardinality error class."""
    if value is None:
        return "none"
    if isinstance(value, Exception):
        return type(value).__name__
    text = str(value).strip()
    if not text:
        return "none"
    match = _ERROR_CLASS_RE.match(text)
    if match:
        return match.group(1)
    return "unknown"


def _sanitize_attribute_value(value: Any) -> str:
    cleaned = str(value).strip().lower()
    if not cleaned:
        return "unknown"
    if len(cleaned) > 64:
        return cleaned[:64]
    return cleaned


def _metric_attrs(**attributes: Any) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for key, value in attributes.items():
        if key not in _ALLOWED_ATTRIBUTE_KEYS:
            continue
        if value in (None, ""):
            continue
        attrs[key] = _sanitize_attribute_value(value)
    attrs.setdefault("schema_version", _SCHEMA_VERSION_DEFAULT)
    return attrs


class SwitchboardTelemetry:
    """Container for switchboard metrics with low-cardinality helper APIs."""

    def __init__(self) -> None:
        meter = metrics.get_meter(_METER_NAME)

        self.message_received = meter.create_counter(
            "butlers.switchboard.message_received",
            unit="1",
            description="Accepted switchboard messages.",
        )
        self.message_deduplicated = meter.create_counter(
            "butlers.switchboard.message_deduplicated",
            unit="1",
            description="Messages deduplicated at ingress.",
        )
        self.message_overload_rejected = meter.create_counter(
            "butlers.switchboard.message_overload_rejected",
            unit="1",
            description="Messages rejected due to overload policy.",
        )
        self.fallback_to_general = meter.create_counter(
            "butlers.switchboard.fallback_to_general",
            unit="1",
            description="Fallback decisions routed to general.",
        )
        self.ambiguity_to_general = meter.create_counter(
            "butlers.switchboard.ambiguity_to_general",
            unit="1",
            description="Ambiguous messages routed to general.",
        )
        self.router_parse_failure = meter.create_counter(
            "butlers.switchboard.router_parse_failure",
            unit="1",
            description="Router parse/validation failures.",
        )
        self.subroute_dispatched = meter.create_counter(
            "butlers.switchboard.subroute_dispatched",
            unit="1",
            description="Subroutes dispatched to downstream butlers.",
        )
        self.subroute_result = meter.create_counter(
            "butlers.switchboard.subroute_result",
            unit="1",
            description="Subroute outcomes.",
        )
        self.lifecycle_transition = meter.create_counter(
            "butlers.switchboard.lifecycle_transition",
            unit="1",
            description="Lifecycle transition events.",
        )
        self.retry_attempt = meter.create_counter(
            "butlers.switchboard.retry_attempt",
            unit="1",
            description="Retry attempts for route operations.",
        )
        self.circuit_transition = meter.create_counter(
            "butlers.switchboard.circuit_transition",
            unit="1",
            description="Circuit-breaker state transitions.",
        )

        self.ingress_accept_latency_ms = meter.create_histogram(
            "butlers.switchboard.ingress_accept_latency_ms",
            unit="ms",
            description="Latency from request acceptance to normalized ingress context.",
        )
        self.routing_decision_latency_ms = meter.create_histogram(
            "butlers.switchboard.routing_decision_latency_ms",
            unit="ms",
            description="Latency for routing decision/classification.",
        )
        self.subroute_latency_ms = meter.create_histogram(
            "butlers.switchboard.subroute_latency_ms",
            unit="ms",
            description="Latency for a single subroute dispatch.",
        )
        self.fanout_completion_latency_ms = meter.create_histogram(
            "butlers.switchboard.fanout_completion_latency_ms",
            unit="ms",
            description="Latency to complete fanout dispatch and aggregation.",
        )
        self.end_to_end_latency_ms = meter.create_histogram(
            "butlers.switchboard.end_to_end_latency_ms",
            unit="ms",
            description="End-to-end latency from ingress acceptance to completion.",
        )

        self._lock = threading.Lock()
        self._queue_depth = 0
        self._inflight_requests = 0
        self._circuit_open_targets = 0

        meter.create_observable_gauge(
            "butlers.switchboard.queue_depth",
            callbacks=[self._observe_queue_depth],
            unit="1",
            description="Queue depth for switchboard ingress/routing work.",
        )
        meter.create_observable_gauge(
            "butlers.switchboard.inflight_requests",
            callbacks=[self._observe_inflight_requests],
            unit="1",
            description="Current number of inflight switchboard requests.",
        )
        meter.create_observable_gauge(
            "butlers.switchboard.circuit_open_targets",
            callbacks=[self._observe_circuit_open_targets],
            unit="1",
            description="Current number of circuit-open downstream targets.",
        )

    def _observe_queue_depth(self, _options: CallbackOptions) -> Iterable[Observation]:
        with self._lock:
            value = self._queue_depth
        return [Observation(value, _metric_attrs())]

    def _observe_inflight_requests(self, _options: CallbackOptions) -> Iterable[Observation]:
        with self._lock:
            value = self._inflight_requests
        return [Observation(value, _metric_attrs())]

    def _observe_circuit_open_targets(self, _options: CallbackOptions) -> Iterable[Observation]:
        with self._lock:
            value = self._circuit_open_targets
        return [Observation(value, _metric_attrs())]

    @contextmanager
    def track_inflight_requests(self) -> Any:
        with self._lock:
            self._inflight_requests += 1
        try:
            yield
        finally:
            with self._lock:
                self._inflight_requests = max(0, self._inflight_requests - 1)

    def set_queue_depth(self, value: int) -> None:
        with self._lock:
            self._queue_depth = max(0, int(value))

    def set_circuit_open_targets(self, value: int) -> None:
        with self._lock:
            self._circuit_open_targets = max(0, int(value))


_SWITCHBOARD_TELEMETRY: SwitchboardTelemetry | None = None


def get_switchboard_telemetry() -> SwitchboardTelemetry:
    """Return the process-level switchboard telemetry singleton."""
    global _SWITCHBOARD_TELEMETRY
    if _SWITCHBOARD_TELEMETRY is None:
        _SWITCHBOARD_TELEMETRY = SwitchboardTelemetry()
    return _SWITCHBOARD_TELEMETRY


def reset_switchboard_telemetry_for_tests() -> None:
    """Test helper to reset cached telemetry after meter-provider changes."""
    global _SWITCHBOARD_TELEMETRY
    _SWITCHBOARD_TELEMETRY = None


__all__ = [
    "SwitchboardTelemetry",
    "get_switchboard_telemetry",
    "normalize_error_class",
    "reset_switchboard_telemetry_for_tests",
]
