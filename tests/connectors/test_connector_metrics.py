"""Tests for connector metrics instrumentation."""

from __future__ import annotations

import time

import httpx
import pytest

from butlers.connectors.metrics import (
    ConnectorMetrics,
    checkpoint_saves_total,
    errors_total,
    get_error_type,
    ingest_latency_seconds,
    ingest_submissions_total,
    source_api_calls_total,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def metrics() -> ConnectorMetrics:
    """Create a metrics collector instance."""
    return ConnectorMetrics(
        connector_type="test_connector",
        endpoint_identity="test_endpoint",
    )


@pytest.fixture(autouse=True)
def clear_metrics() -> None:
    """Clear metrics before each test to avoid interference."""
    # Prometheus metrics are cumulative, so we need to clear the underlying data
    # by accessing the internal _metrics dict
    for collector in [
        ingest_submissions_total,
        ingest_latency_seconds,
        source_api_calls_total,
        checkpoint_saves_total,
        errors_total,
    ]:
        collector._metrics.clear()


# -----------------------------------------------------------------------------
# Ingest submission metrics tests
# -----------------------------------------------------------------------------


def test_record_ingest_submission_success(metrics: ConnectorMetrics) -> None:
    """Test recording successful ingest submission."""
    metrics.record_ingest_submission(status="success", latency=0.123)

    # Verify counter incremented
    counter_value = ingest_submissions_total.labels(
        connector_type="test_connector",
        endpoint_identity="test_endpoint",
        status="success",
    )._value.get()
    assert counter_value == 1.0


def test_record_ingest_submission_duplicate(metrics: ConnectorMetrics) -> None:
    """Test recording duplicate ingest submission."""
    metrics.record_ingest_submission(status="duplicate", latency=0.050)

    counter_value = ingest_submissions_total.labels(
        connector_type="test_connector",
        endpoint_identity="test_endpoint",
        status="duplicate",
    )._value.get()
    assert counter_value == 1.0


def test_record_ingest_submission_error(metrics: ConnectorMetrics) -> None:
    """Test recording failed ingest submission."""
    metrics.record_ingest_submission(status="error")

    counter_value = ingest_submissions_total.labels(
        connector_type="test_connector",
        endpoint_identity="test_endpoint",
        status="error",
    )._value.get()
    assert counter_value == 1.0


def test_record_ingest_submission_latency(metrics: ConnectorMetrics) -> None:
    """Test that latency is recorded in histogram."""
    metrics.record_ingest_submission(status="success", latency=0.456)

    # Verify histogram has recorded the latency by collecting samples
    samples = list(
        ingest_latency_seconds.collect()[0].samples  # Get first metric family, then samples
    )

    # Find the sum and count samples for our labels
    target_labels = {
        "connector_type": "test_connector",
        "endpoint_identity": "test_endpoint",
        "status": "success",
    }

    sum_sample = next(
        (s for s in samples if s.name.endswith("_sum") and dict(s.labels) == target_labels), None
    )
    count_sample = next(
        (s for s in samples if s.name.endswith("_count") and dict(s.labels) == target_labels),
        None,
    )

    assert sum_sample is not None, f"Sum sample not found. Available samples: {samples}"
    assert count_sample is not None, f"Count sample not found. Available samples: {samples}"
    assert sum_sample.value == pytest.approx(0.456, rel=1e-6)
    assert count_sample.value == 1.0


def test_track_ingest_submission_context_manager(metrics: ConnectorMetrics) -> None:
    """Test context manager for tracking ingest submission."""
    start = time.perf_counter()

    with metrics.track_ingest_submission(lambda: "success"):
        time.sleep(0.01)

    elapsed = time.perf_counter() - start

    # Verify counter incremented
    counter_value = ingest_submissions_total.labels(
        connector_type="test_connector",
        endpoint_identity="test_endpoint",
        status="success",
    )._value.get()
    assert counter_value == 1.0

    # Verify latency recorded (should be at least 0.01s)
    samples = list(ingest_latency_seconds.collect()[0].samples)
    target_labels = {
        "connector_type": "test_connector",
        "endpoint_identity": "test_endpoint",
        "status": "success",
    }
    sum_sample = next(
        (s for s in samples if s.name.endswith("_sum") and dict(s.labels) == target_labels), None
    )

    assert sum_sample is not None, f"Sum sample not found. Available samples: {samples}"
    recorded_latency = sum_sample.value
    assert recorded_latency >= 0.01
    assert recorded_latency <= elapsed + 0.01  # Allow some overhead


def test_track_ingest_submission_on_exception(metrics: ConnectorMetrics) -> None:
    """Test that metrics are still recorded when an exception occurs."""

    class TestError(Exception):
        pass

    with pytest.raises(TestError):
        with metrics.track_ingest_submission(lambda: "error"):
            raise TestError("test error")

    # Verify counter incremented even after exception
    counter_value = ingest_submissions_total.labels(
        connector_type="test_connector",
        endpoint_identity="test_endpoint",
        status="error",
    )._value.get()
    assert counter_value == 1.0


# -----------------------------------------------------------------------------
# Source API call metrics tests
# -----------------------------------------------------------------------------


def test_record_source_api_call_success(metrics: ConnectorMetrics) -> None:
    """Test recording successful source API call."""
    metrics.record_source_api_call(api_method="getUpdates", status="success")

    counter_value = source_api_calls_total.labels(
        connector_type="test_connector",
        endpoint_identity="test_endpoint",
        api_method="getUpdates",
        status="success",
    )._value.get()
    assert counter_value == 1.0


def test_record_source_api_call_error(metrics: ConnectorMetrics) -> None:
    """Test recording failed source API call."""
    metrics.record_source_api_call(api_method="history.list", status="error")

    counter_value = source_api_calls_total.labels(
        connector_type="test_connector",
        endpoint_identity="test_endpoint",
        api_method="history.list",
        status="error",
    )._value.get()
    assert counter_value == 1.0


def test_record_source_api_call_rate_limited(metrics: ConnectorMetrics) -> None:
    """Test recording rate-limited source API call."""
    metrics.record_source_api_call(api_method="messages.get", status="rate_limited")

    counter_value = source_api_calls_total.labels(
        connector_type="test_connector",
        endpoint_identity="test_endpoint",
        api_method="messages.get",
        status="rate_limited",
    )._value.get()
    assert counter_value == 1.0


def test_record_multiple_api_calls(metrics: ConnectorMetrics) -> None:
    """Test recording multiple API calls with different methods."""
    metrics.record_source_api_call(api_method="getUpdates", status="success")
    metrics.record_source_api_call(api_method="getUpdates", status="success")
    metrics.record_source_api_call(api_method="sendMessage", status="success")

    get_updates_count = source_api_calls_total.labels(
        connector_type="test_connector",
        endpoint_identity="test_endpoint",
        api_method="getUpdates",
        status="success",
    )._value.get()
    assert get_updates_count == 2.0

    send_message_count = source_api_calls_total.labels(
        connector_type="test_connector",
        endpoint_identity="test_endpoint",
        api_method="sendMessage",
        status="success",
    )._value.get()
    assert send_message_count == 1.0


# -----------------------------------------------------------------------------
# Checkpoint save metrics tests
# -----------------------------------------------------------------------------


def test_record_checkpoint_save_success(metrics: ConnectorMetrics) -> None:
    """Test recording successful checkpoint save."""
    metrics.record_checkpoint_save(status="success")

    counter_value = checkpoint_saves_total.labels(
        connector_type="test_connector",
        endpoint_identity="test_endpoint",
        status="success",
    )._value.get()
    assert counter_value == 1.0


def test_record_checkpoint_save_error(metrics: ConnectorMetrics) -> None:
    """Test recording failed checkpoint save."""
    metrics.record_checkpoint_save(status="error")

    counter_value = checkpoint_saves_total.labels(
        connector_type="test_connector",
        endpoint_identity="test_endpoint",
        status="error",
    )._value.get()
    assert counter_value == 1.0


def test_record_multiple_checkpoint_saves(metrics: ConnectorMetrics) -> None:
    """Test recording multiple checkpoint saves."""
    for _ in range(5):
        metrics.record_checkpoint_save(status="success")

    counter_value = checkpoint_saves_total.labels(
        connector_type="test_connector",
        endpoint_identity="test_endpoint",
        status="success",
    )._value.get()
    assert counter_value == 5.0


# -----------------------------------------------------------------------------
# Error metrics tests
# -----------------------------------------------------------------------------


def test_record_error(metrics: ConnectorMetrics) -> None:
    """Test recording error occurrence."""
    metrics.record_error(error_type="http_error", operation="ingest_submit")

    counter_value = errors_total.labels(
        connector_type="test_connector",
        endpoint_identity="test_endpoint",
        error_type="http_error",
        operation="ingest_submit",
    )._value.get()
    assert counter_value == 1.0


def test_record_different_error_types(metrics: ConnectorMetrics) -> None:
    """Test recording different error types."""
    metrics.record_error(error_type="timeout", operation="fetch_updates")
    metrics.record_error(error_type="connection_error", operation="fetch_updates")
    metrics.record_error(error_type="parse_error", operation="ingest_submit")

    timeout_count = errors_total.labels(
        connector_type="test_connector",
        endpoint_identity="test_endpoint",
        error_type="timeout",
        operation="fetch_updates",
    )._value.get()
    assert timeout_count == 1.0

    connection_count = errors_total.labels(
        connector_type="test_connector",
        endpoint_identity="test_endpoint",
        error_type="connection_error",
        operation="fetch_updates",
    )._value.get()
    assert connection_count == 1.0


# -----------------------------------------------------------------------------
# Error type extraction tests
# -----------------------------------------------------------------------------


def test_get_error_type_http_error() -> None:
    """Test extracting error type from HTTP exceptions."""
    exc = httpx.HTTPStatusError("test", request=None, response=None)
    assert get_error_type(exc) == "http_error"


def test_get_error_type_timeout() -> None:
    """Test extracting error type from timeout exceptions."""
    exc = TimeoutError("timeout")
    assert get_error_type(exc) == "timeout"


def test_get_error_type_connection_error() -> None:
    """Test extracting error type from connection exceptions."""
    exc = ConnectionError("connection failed")
    assert get_error_type(exc) == "connection_error"


def test_get_error_type_validation_error() -> None:
    """Test extracting error type from validation exceptions."""
    exc = ValueError("invalid value")
    assert get_error_type(exc) == "validation_error"


def test_get_error_type_generic() -> None:
    """Test extracting error type from generic exceptions."""
    exc = RuntimeError("runtime error")
    assert get_error_type(exc) == "runtimeerror"


# -----------------------------------------------------------------------------
# Integration test with multiple connectors
# -----------------------------------------------------------------------------


def test_multiple_connectors_isolated_metrics() -> None:
    """Test that metrics from different connectors are isolated by labels."""
    metrics1 = ConnectorMetrics(
        connector_type="telegram_bot",
        endpoint_identity="bot1",
    )
    metrics2 = ConnectorMetrics(
        connector_type="gmail",
        endpoint_identity="user@example.com",
    )

    # Record metrics from both connectors
    metrics1.record_ingest_submission(status="success", latency=0.1)
    metrics2.record_ingest_submission(status="success", latency=0.2)

    # Verify metrics are isolated by labels
    bot_count = ingest_submissions_total.labels(
        connector_type="telegram_bot",
        endpoint_identity="bot1",
        status="success",
    )._value.get()
    assert bot_count == 1.0

    gmail_count = ingest_submissions_total.labels(
        connector_type="gmail",
        endpoint_identity="user@example.com",
        status="success",
    )._value.get()
    assert gmail_count == 1.0
