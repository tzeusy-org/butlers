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
    """Fully reset the OpenTelemetry global tracer provider state.

    The OTel SDK uses a ``Once`` guard that prevents ``set_tracer_provider``
    from being called more than once. For test isolation we need to reset both
    the guard and the cached provider reference.
    """
    trace._TRACER_PROVIDER_SET_ONCE = trace.Once()
    trace._TRACER_PROVIDER = None
    # Also reset the module-level guard so init_telemetry behaves as a
    # first-call again in each test.
    _telemetry_mod._tracer_provider_installed = False


@pytest.fixture(autouse=True)
def _clean_tracer_provider():
    """Reset the global tracer provider before and after each test."""
    _reset_otel_global_state()
    yield
    _reset_otel_global_state()


class TestNoopWhenEndpointNotSet:
    """When OTEL_EXPORTER_OTLP_ENDPOINT is not set, init_telemetry returns a no-op tracer."""

    def test_returns_tracer_without_error(self, monkeypatch):
        monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
        tracer = init_telemetry("butler-test")
        assert tracer is not None

    def test_noop_tracer_creates_spans(self, monkeypatch):
        monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
        tracer = init_telemetry("butler-test")
        with tracer.start_as_current_span("test-span") as span:
            # No-op span should not raise
            assert span is not None


class TestTracerReturnsValidTracer:
    """The returned tracer can create spans without error."""

    def test_can_create_spans(self, monkeypatch):
        monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
        tracer = init_telemetry("butler-test")
        with tracer.start_as_current_span("operation") as span:
            span.set_attribute("key", "value")


class TestSharedProviderResourceName:
    """When endpoint IS set, the shared provider uses 'butlers' as resource service.name."""

    def test_resource_has_shared_service_name(self, monkeypatch):
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")

        # Call init_telemetry which sets a real TracerProvider
        init_telemetry("butler-switchboard")

        # Retrieve the provider that was installed globally
        provider = trace.get_tracer_provider()
        assert isinstance(provider, TracerProvider)

        # The shared provider resource uses "butlers" as the process-level service name.
        # Per-butler attribution is done via span attributes (service.name + butler.name).
        attrs = dict(provider.resource.attributes)
        assert attrs["service.name"] == "butlers"

        provider.shutdown()

    def test_spans_record_with_real_provider(self, monkeypatch):
        monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)

        # Create a provider with in-memory exporter (no network needed)
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


class TestMultiButlerNoOverrideWarning:
    """Multiple init_telemetry calls must not trigger provider override warnings."""

    def test_second_init_does_not_reinstall_provider(self, monkeypatch):
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")

        # First butler initializes the provider
        init_telemetry("butler.finance")
        assert _telemetry_mod._tracer_provider_installed is True

        # Capture the installed provider
        provider_after_first = trace.get_tracer_provider()

        # Second butler must NOT reinstall (would trigger override warning)
        init_telemetry("butler.general")

        # Provider identity must not change
        assert trace.get_tracer_provider() is provider_after_first

        provider_after_first.shutdown()

    def test_second_butler_returns_valid_tracer(self, monkeypatch):
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")

        init_telemetry("butler.finance")
        tracer = init_telemetry("butler.general")

        # The returned tracer must be usable (not None)
        assert tracer is not None

        provider = trace.get_tracer_provider()
        if isinstance(provider, TracerProvider):
            provider.shutdown()

    def test_noop_mode_multiple_calls_ok(self, monkeypatch):
        monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)

        # In no-op mode the guard is never set; multiple calls are always safe.
        t1 = init_telemetry("butler.finance")
        t2 = init_telemetry("butler.general")

        assert t1 is not None
        assert t2 is not None
        # Guard should remain False in no-op mode
        assert _telemetry_mod._tracer_provider_installed is False


class TestToolSpanButlerAttribution:
    """tool_span sets both butler.name and service.name on spans."""

    def test_tool_span_sets_butler_and_service_attributes(self):
        exporter = InMemorySpanExporter()
        resource = Resource.create({"service.name": "butlers"})
        provider = TracerProvider(resource=resource)
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
        _telemetry_mod._tracer_provider_installed = True

        with tool_span("state_get", butler_name="general"):
            pass

        spans = exporter.get_finished_spans()
        assert len(spans) == 1
        attrs = dict(spans[0].attributes)
        assert attrs["butler.name"] == "general"
        assert attrs["service.name"] == "butler.general"

        provider.shutdown()

    def test_different_butlers_get_different_service_names(self):
        exporter = InMemorySpanExporter()
        resource = Resource.create({"service.name": "butlers"})
        provider = TracerProvider(resource=resource)
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
        _telemetry_mod._tracer_provider_installed = True

        with tool_span("state_get", butler_name="finance"):
            pass
        with tool_span("state_set", butler_name="general"):
            pass

        spans = exporter.get_finished_spans()
        assert len(spans) == 2

        finance_span = next(s for s in spans if s.name == "butler.tool.state_get")
        general_span = next(s for s in spans if s.name == "butler.tool.state_set")

        assert finance_span.attributes["service.name"] == "butler.finance"
        assert general_span.attributes["service.name"] == "butler.general"

        provider.shutdown()


class TestTagButlerSpan:
    """tag_butler_span sets both butler.name and service.name attributes."""

    def test_sets_both_attributes(self):
        exporter = InMemorySpanExporter()
        resource = Resource.create({"service.name": "butlers"})
        provider = TracerProvider(resource=resource)
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
        _telemetry_mod._tracer_provider_installed = True

        tracer = trace.get_tracer("butlers")
        with tracer.start_as_current_span("route.process") as span:
            tag_butler_span(span, "switchboard")

        spans = exporter.get_finished_spans()
        assert len(spans) == 1
        attrs = dict(spans[0].attributes)
        assert attrs["butler.name"] == "switchboard"
        assert attrs["service.name"] == "butler.switchboard"

        provider.shutdown()


class TestGetTracer:
    """get_tracer() returns a tracer from the current provider."""

    def test_returns_tracer(self):
        tracer = get_tracer("my-module")
        assert tracer is not None

    def test_returns_tracer_after_init(self, monkeypatch):
        monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
        init_telemetry("butler-test")
        tracer = get_tracer("my-module")
        assert tracer is not None


class TestNoopSpanContext:
    """In no-op mode, spans have invalid span context (not recording)."""

    def test_noop_span_not_recording(self, monkeypatch):
        monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
        tracer = init_telemetry("butler-test")
        with tracer.start_as_current_span("noop-span") as span:
            assert not span.is_recording()
            assert not span.get_span_context().is_valid
