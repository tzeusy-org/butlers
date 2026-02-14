"""Unit tests for switchboard telemetry metric surfaces."""

from __future__ import annotations

import pytest
from opentelemetry import metrics
from opentelemetry.metrics import _internal as metrics_internal
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from butlers.tools.switchboard import (
    get_switchboard_telemetry,
    reset_switchboard_telemetry_for_tests,
)

pytestmark = pytest.mark.unit


def _reset_otel_meter_global_state() -> None:
    metrics_internal._METER_PROVIDER_SET_ONCE = metrics_internal.Once()
    metrics_internal._METER_PROVIDER = None
    metrics_internal._PROXY_METER_PROVIDER = metrics_internal._ProxyMeterProvider()


def _metric_names(metric_reader: InMemoryMetricReader) -> set[str]:
    data = metric_reader.get_metrics_data()
    names: set[str] = set()
    for resource_metric in data.resource_metrics:
        for scope_metric in resource_metric.scope_metrics:
            for metric in scope_metric.metrics:
                names.add(metric.name)
    return names


def test_switchboard_metric_namespace_contract() -> None:
    _reset_otel_meter_global_state()
    metric_reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[metric_reader])
    metrics.set_meter_provider(provider)
    reset_switchboard_telemetry_for_tests()
    telemetry = get_switchboard_telemetry()

    attrs = telemetry.attrs(
        source="telegram",
        destination_butler="general",
        outcome="success",
        lifecycle_state="parsed",
        error_class="none",
        policy_tier="default",
        fanout_mode="ordered",
        model_family="claude",
        prompt_version="switchboard.v1",
        schema_version="route.v1",
    )

    telemetry.message_received.add(1, attrs)
    telemetry.message_deduplicated.add(1, attrs)
    telemetry.message_overload_rejected.add(1, attrs)
    telemetry.fallback_to_general.add(1, attrs)
    telemetry.ambiguity_to_general.add(1, attrs)
    telemetry.router_parse_failure.add(1, attrs)
    telemetry.subroute_dispatched.add(1, attrs)
    telemetry.subroute_result.add(1, attrs)
    telemetry.lifecycle_transition.add(1, attrs)
    telemetry.retry_attempt.add(1, attrs)
    telemetry.circuit_transition.add(1, attrs)

    telemetry.ingress_accept_latency_ms.record(1.0, attrs)
    telemetry.routing_decision_latency_ms.record(2.0, attrs)
    telemetry.subroute_latency_ms.record(3.0, attrs)
    telemetry.fanout_completion_latency_ms.record(4.0, attrs)
    telemetry.end_to_end_latency_ms.record(5.0, attrs)

    telemetry.set_queue_depth(1)
    telemetry.set_circuit_open_targets(2)
    with telemetry.track_inflight_requests():
        names = _metric_names(metric_reader)
    provider.shutdown()
    _reset_otel_meter_global_state()
    reset_switchboard_telemetry_for_tests()

    assert names >= {
        "butlers.switchboard.message_received",
        "butlers.switchboard.message_deduplicated",
        "butlers.switchboard.message_overload_rejected",
        "butlers.switchboard.fallback_to_general",
        "butlers.switchboard.ambiguity_to_general",
        "butlers.switchboard.router_parse_failure",
        "butlers.switchboard.subroute_dispatched",
        "butlers.switchboard.subroute_result",
        "butlers.switchboard.lifecycle_transition",
        "butlers.switchboard.retry_attempt",
        "butlers.switchboard.circuit_transition",
        "butlers.switchboard.ingress_accept_latency_ms",
        "butlers.switchboard.routing_decision_latency_ms",
        "butlers.switchboard.subroute_latency_ms",
        "butlers.switchboard.fanout_completion_latency_ms",
        "butlers.switchboard.end_to_end_latency_ms",
        "butlers.switchboard.queue_depth",
        "butlers.switchboard.inflight_requests",
        "butlers.switchboard.circuit_open_targets",
    }
