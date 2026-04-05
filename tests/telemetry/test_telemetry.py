"""Tests for butlers.core.telemetry — OpenTelemetry initialization."""

import pytest
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import butlers.core.telemetry as _telemetry_mod
from butlers.core.telemetry import get_tracer, init_telemetry, tag_butler_span, tool_span

pytestmark = pytest.mark.unit


def _reset_otel_global_state():
    """Fully reset the OpenTelemetry global tracer provider state."""
    trace._TRACER_PROVIDER_SET_ONCE = trace.Once()
    trace._TRACER_PROVIDER = None
    _telemetry_mod._tracer_provider_installed = False


@pytest.fixture(autouse=True)
def _clean_tracer_provider():
    """Reset the global tracer provider before and after each test."""
    _reset_otel_global_state()
    yield
    _reset_otel_global_state()


class TestNoopMode:
    """When OTEL_EXPORTER_OTLP_ENDPOINT is not set, init_telemetry returns a usable no-op."""

    def test_noop_tracer_works(self, monkeypatch):
        monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
        tracer = init_telemetry("butler-test")
        assert tracer is not None
        with tracer.start_as_current_span("noop-span") as span:
            assert span is not None
            assert not span.is_recording()
            assert not span.get_span_context().is_valid


class TestSharedProvider:
    """When endpoint IS set, provider has 'butlers' as service.name; second init skips."""

    def test_resource_name_and_no_reinstall(self, monkeypatch):
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")

        init_telemetry("butler-switchboard")
        provider = trace.get_tracer_provider()
        assert isinstance(provider, TracerProvider)
        assert dict(provider.resource.attributes)["service.name"] == "butlers"
        provider_after_first = provider

        # Second butler must NOT reinstall (would trigger override warning)
        assert _telemetry_mod._tracer_provider_installed is True
        tracer2 = init_telemetry("butler.general")
        assert trace.get_tracer_provider() is provider_after_first
        assert tracer2 is not None

        provider_after_first.shutdown()

    def test_spans_record_with_real_provider(self, monkeypatch):
        monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
        exporter = InMemorySpanExporter()
        resource = Resource.create({"service.name": "butler-test"})
        provider = TracerProvider(resource=resource)
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        trace.set_tracer_provider(provider)

        tracer = trace.get_tracer("butler-test")
        with tracer.start_as_current_span("test-op") as span:
            span.set_attribute("test.key", "test-value")

        spans = exporter.get_finished_spans()
        assert len(spans) == 1
        assert spans[0].name == "test-op"
        assert spans[0].attributes["test.key"] == "test-value"
        provider.shutdown()


class TestToolSpanAndTagButlerSpan:
    """tool_span and tag_butler_span set butler.name and service.name attributes."""

    def _make_provider(self):
        exporter = InMemorySpanExporter()
        resource = Resource.create({"service.name": "butlers"})
        provider = TracerProvider(resource=resource)
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
        _telemetry_mod._tracer_provider_installed = True
        return provider, exporter

    def test_tool_span_butler_attribution_and_tag(self):
        provider, exporter = self._make_provider()
        try:
            # tool_span sets both butler.name and service.name
            with tool_span("state_get", butler_name="general"):
                pass
            spans = exporter.get_finished_spans()
            assert len(spans) == 1
            attrs = dict(spans[0].attributes)
            assert attrs["butler.name"] == "general"
            assert attrs["service.name"] == "butler.general"

            # tag_butler_span sets both attributes
            tracer = trace.get_tracer("butlers")
            with tracer.start_as_current_span("route.process") as span:
                tag_butler_span(span, "switchboard")
            spans2 = exporter.get_finished_spans()
            tag_span = next(s for s in spans2 if s.name == "route.process")
            tag_attrs = dict(tag_span.attributes)
            assert tag_attrs["butler.name"] == "switchboard"
            assert tag_attrs["service.name"] == "butler.switchboard"
        finally:
            provider.shutdown()


class TestGetTracer:
    """get_tracer() returns a valid tracer."""

    def test_returns_tracer(self, monkeypatch):
        monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
        assert get_tracer("my-module") is not None
        init_telemetry("butler-test")
        assert get_tracer("my-module") is not None
