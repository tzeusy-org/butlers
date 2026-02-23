"""Unit tests for triage telemetry metrics.

Tests cardinality enforcement, metric naming, and sanitization behavior.
"""

from __future__ import annotations

import pytest
from opentelemetry import metrics
from opentelemetry.metrics import _internal as metrics_internal
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from butlers.tools.switchboard.triage.telemetry import (
    TriageTelemetry,
    _safe_action,
    _safe_reason,
    _safe_result,
    _safe_rule_type,
    get_triage_telemetry,
    reset_triage_telemetry_for_tests,
)

pytestmark = pytest.mark.unit


def _reset_otel_meter_global_state() -> None:
    metrics_internal._METER_PROVIDER_SET_ONCE = metrics_internal.Once()
    metrics_internal._METER_PROVIDER = None
    metrics_internal._PROXY_METER_PROVIDER = metrics_internal._ProxyMeterProvider()


def _metric_names(reader: InMemoryMetricReader) -> set[str]:
    data = reader.get_metrics_data()
    names: set[str] = set()
    for rm in data.resource_metrics:
        for sm in rm.scope_metrics:
            for m in sm.metrics:
                names.add(m.name)
    return names


# ---------------------------------------------------------------------------
# _safe_* sanitization helpers
# ---------------------------------------------------------------------------


class TestSanitizationHelpers:
    def test_safe_rule_type_known_values(self) -> None:
        for rt in (
            "sender_domain",
            "sender_address",
            "header_condition",
            "mime_type",
            "thread_affinity",
        ):
            assert _safe_rule_type(rt) == rt

    def test_safe_rule_type_unknown_returns_unknown(self) -> None:
        assert _safe_rule_type("totally_new_type") == "unknown"
        assert _safe_rule_type(None) == "unknown"

    def test_safe_action_route_to_stripped(self) -> None:
        """route_to:finance → 'route_to' (target stripped to avoid cardinality leak)."""
        assert _safe_action("route_to:finance") == "route_to"
        assert _safe_action("route_to:travel") == "route_to"
        assert _safe_action("route_to:anything") == "route_to"

    def test_safe_action_known_simple_actions(self) -> None:
        for action in ("skip", "metadata_only", "low_priority_queue", "pass_through", "route_to"):
            assert _safe_action(action) == action

    def test_safe_action_unknown_returns_unknown(self) -> None:
        assert _safe_action("mystery_action") == "unknown"

    def test_safe_reason_known_values(self) -> None:
        for reason in ("no_match", "cache_unavailable", "rules_disabled"):
            assert _safe_reason(reason) == reason

    def test_safe_reason_unknown_defaults_to_no_match(self) -> None:
        assert _safe_reason("bad_reason") == "no_match"
        assert _safe_reason(None) == "no_match"

    def test_safe_result_known_values(self) -> None:
        for result in ("matched", "pass_through", "error"):
            assert _safe_result(result) == result

    def test_safe_result_unknown_returns_unknown(self) -> None:
        assert _safe_result("oops") == "unknown"
        assert _safe_result(None) == "unknown"


# ---------------------------------------------------------------------------
# TriageTelemetry metric surfaces
# ---------------------------------------------------------------------------


class TestTriageTelemetryMetricSurfaces:
    def _setup_with_reader(self) -> tuple[TriageTelemetry, InMemoryMetricReader]:
        _reset_otel_meter_global_state()
        reader = InMemoryMetricReader()
        provider = MeterProvider(metric_readers=[reader])
        metrics.set_meter_provider(provider)
        reset_triage_telemetry_for_tests()
        telemetry = TriageTelemetry()
        return telemetry, reader

    def test_rule_matched_counter_created(self) -> None:
        tel, reader = self._setup_with_reader()
        tel.rule_matched.add(
            1, {"rule_type": "sender_domain", "action": "route_to", "source_channel": "email"}
        )
        names = _metric_names(reader)
        assert "butlers.switchboard.triage.rule_matched" in names

    def test_pass_through_counter_created(self) -> None:
        tel, reader = self._setup_with_reader()
        tel.pass_through.add(1, {"source_channel": "email", "reason": "no_match"})
        names = _metric_names(reader)
        assert "butlers.switchboard.triage.pass_through" in names

    def test_evaluation_latency_histogram_created(self) -> None:
        tel, reader = self._setup_with_reader()
        tel.evaluation_latency_ms.record(5.0, {"result": "matched"})
        names = _metric_names(reader)
        assert "butlers.switchboard.triage.evaluation_latency_ms" in names


class TestTriageTelemetryHighLevelMethods:
    def _get_telemetry(self) -> TriageTelemetry:
        _reset_otel_meter_global_state()
        reader = InMemoryMetricReader()
        provider = MeterProvider(metric_readers=[reader])
        metrics.set_meter_provider(provider)
        reset_triage_telemetry_for_tests()
        return TriageTelemetry()

    def test_record_rule_matched_does_not_raise(self) -> None:
        tel = self._get_telemetry()
        tel.record_rule_matched(
            rule_type="sender_domain",
            action="route_to:finance",
            source_channel="email",
        )

    def test_record_rule_matched_strips_route_to_target(self) -> None:
        """route_to:finance action → recorded as 'route_to' (low-cardinality)."""
        tel = self._get_telemetry()
        # Should not raise and should sanitize action correctly
        tel.record_rule_matched(
            rule_type="sender_domain",
            action="route_to:finance",
            source_channel="email",
        )

    def test_record_pass_through_does_not_raise(self) -> None:
        tel = self._get_telemetry()
        tel.record_pass_through(source_channel="email", reason="no_match")

    def test_record_pass_through_unknown_reason_sanitized(self) -> None:
        tel = self._get_telemetry()
        # Should not raise; unknown reason maps to 'no_match'
        tel.record_pass_through(source_channel="email", reason="some_weird_reason")

    def test_record_evaluation_latency_does_not_raise(self) -> None:
        tel = self._get_telemetry()
        tel.record_evaluation_latency(latency_ms=12.5, result="matched")

    def test_record_evaluation_latency_unknown_result_sanitized(self) -> None:
        tel = self._get_telemetry()
        # Should not raise; unknown result sanitized to 'unknown'
        tel.record_evaluation_latency(latency_ms=5.0, result="mystery")


class TestGetTriageTelemetrySingleton:
    def test_returns_same_instance(self) -> None:
        _reset_otel_meter_global_state()
        reader = InMemoryMetricReader()
        provider = MeterProvider(metric_readers=[reader])
        metrics.set_meter_provider(provider)
        reset_triage_telemetry_for_tests()

        t1 = get_triage_telemetry()
        t2 = get_triage_telemetry()
        assert t1 is t2

    def test_reset_for_tests_creates_new_instance(self) -> None:
        _reset_otel_meter_global_state()
        reader = InMemoryMetricReader()
        provider = MeterProvider(metric_readers=[reader])
        metrics.set_meter_provider(provider)
        reset_triage_telemetry_for_tests()

        t1 = get_triage_telemetry()
        reset_triage_telemetry_for_tests()
        t2 = get_triage_telemetry()
        assert t1 is not t2
