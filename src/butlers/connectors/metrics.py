"""Prometheus metrics instrumentation for connector runtimes.

This module provides standardized metrics export for connector observability at scale.

Metrics exported:
- connector_ingest_submissions_total: Counter of ingest API submission attempts
- connector_ingest_latency_seconds: Histogram of ingest API latency
- connector_source_api_calls_total: Counter of source API calls
- connector_checkpoint_saves_total: Counter of checkpoint save operations
- connector_errors_total: Counter of errors by type

All metrics include standard labels:
- connector_type: telegram_bot, gmail, telegram_user_client, etc.
- endpoint_identity: Bot/mailbox/client identity
- Additional metric-specific labels as needed
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING

from prometheus_client import Counter, Histogram

if TYPE_CHECKING:
    from collections.abc import Callable

# Ingest submission metrics
ingest_submissions_total = Counter(
    "connector_ingest_submissions_total",
    "Total number of ingest API submission attempts",
    labelnames=["connector_type", "endpoint_identity", "status"],
)

ingest_latency_seconds = Histogram(
    "connector_ingest_latency_seconds",
    "Latency of ingest API submissions in seconds",
    labelnames=["connector_type", "endpoint_identity", "status"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.075, 0.1, 0.25, 0.5, 0.75, 1.0, 2.5, 5.0, 10.0),
)

# Source API call metrics
source_api_calls_total = Counter(
    "connector_source_api_calls_total",
    "Total number of source API calls",
    labelnames=["connector_type", "endpoint_identity", "api_method", "status"],
)

# Checkpoint save metrics
checkpoint_saves_total = Counter(
    "connector_checkpoint_saves_total",
    "Total number of checkpoint save operations",
    labelnames=["connector_type", "endpoint_identity", "status"],
)

# Error metrics
errors_total = Counter(
    "connector_errors_total",
    "Total number of errors by type",
    labelnames=["connector_type", "endpoint_identity", "error_type", "operation"],
)

# Attachment fetch metrics — see docs/connectors/attachment_handling.md section 9
attachment_fetched_eager_total = Counter(
    "connector_attachment_fetched_eager_total",
    "Total number of attachments fetched eagerly at ingest time",
    labelnames=["connector_type", "endpoint_identity", "media_type", "result"],
)

attachment_fetched_lazy_total = Counter(
    "connector_attachment_fetched_lazy_total",
    "Total number of lazy attachment ref writes and on-demand materializations",
    labelnames=["connector_type", "endpoint_identity", "media_type", "result"],
)

attachment_skipped_oversized_total = Counter(
    "connector_attachment_skipped_oversized_total",
    "Total number of attachments skipped due to per-type or global size cap",
    labelnames=["connector_type", "endpoint_identity", "media_type"],
)

attachment_type_distribution_total = Counter(
    "connector_attachment_type_distribution_total",
    "Count of processed attachments by MIME type",
    labelnames=["connector_type", "endpoint_identity", "media_type"],
)


class ConnectorMetrics:
    """Metrics collector for a specific connector instance.

    Provides convenient methods to record metrics with consistent labels.
    """

    def __init__(self, connector_type: str, endpoint_identity: str) -> None:
        """Initialize metrics collector.

        Args:
            connector_type: Type of connector (e.g., "telegram_bot", "gmail")
            endpoint_identity: Identity of the endpoint (bot username, email, etc.)
        """
        self._connector_type = connector_type
        self._endpoint_identity = endpoint_identity

    def record_ingest_submission(
        self,
        status: str,
        latency: float | None = None,
    ) -> None:
        """Record an ingest API submission attempt.

        Args:
            status: Submission status ("success", "error", "duplicate")
            latency: Optional latency in seconds
        """
        ingest_submissions_total.labels(
            connector_type=self._connector_type,
            endpoint_identity=self._endpoint_identity,
            status=status,
        ).inc()

        if latency is not None:
            ingest_latency_seconds.labels(
                connector_type=self._connector_type,
                endpoint_identity=self._endpoint_identity,
                status=status,
            ).observe(latency)

    @contextmanager
    def track_ingest_submission(self, status_callback: Callable[[], str]) -> Iterator[None]:
        """Context manager to track ingest submission with automatic timing.

        Args:
            status_callback: Callable that returns the final status

        Example:
            with metrics.track_ingest_submission(lambda: "success"):
                await submit_to_ingest(envelope)
        """
        start_time = time.perf_counter()
        try:
            yield
        finally:
            latency = time.perf_counter() - start_time
            status = status_callback()
            self.record_ingest_submission(status=status, latency=latency)

    def record_source_api_call(
        self,
        api_method: str,
        status: str,
    ) -> None:
        """Record a source API call.

        Args:
            api_method: API method name (e.g., "getUpdates", "history.list")
            status: Call status ("success", "error", "rate_limited")
        """
        source_api_calls_total.labels(
            connector_type=self._connector_type,
            endpoint_identity=self._endpoint_identity,
            api_method=api_method,
            status=status,
        ).inc()

    def record_checkpoint_save(self, status: str) -> None:
        """Record a checkpoint save operation.

        Args:
            status: Save status ("success", "error")
        """
        checkpoint_saves_total.labels(
            connector_type=self._connector_type,
            endpoint_identity=self._endpoint_identity,
            status=status,
        ).inc()

    def record_error(self, error_type: str, operation: str) -> None:
        """Record an error occurrence.

        Args:
            error_type: Type of error (e.g., "http_error", "timeout", "parse_error")
            operation: Operation that failed (e.g., "ingest_submit", "fetch_updates")
        """
        errors_total.labels(
            connector_type=self._connector_type,
            endpoint_identity=self._endpoint_identity,
            error_type=error_type,
            operation=operation,
        ).inc()

    def record_attachment_fetched(self, media_type: str, fetch_mode: str, result: str) -> None:
        """Record an attachment fetch event (eager or lazy).

        Args:
            media_type: MIME type of the attachment (e.g., "text/calendar").
            fetch_mode: "eager" for immediate ingest-time downloads, "lazy" for
                        ref writes and on-demand materializations.
            result: "success" or "error".
        """
        if fetch_mode == "eager":
            attachment_fetched_eager_total.labels(
                connector_type=self._connector_type,
                endpoint_identity=self._endpoint_identity,
                media_type=media_type,
                result=result,
            ).inc()
        else:
            attachment_fetched_lazy_total.labels(
                connector_type=self._connector_type,
                endpoint_identity=self._endpoint_identity,
                media_type=media_type,
                result=result,
            ).inc()

    def record_attachment_skipped_oversized(self, media_type: str) -> None:
        """Record an attachment that was skipped due to size policy.

        Args:
            media_type: MIME type of the skipped attachment.
        """
        attachment_skipped_oversized_total.labels(
            connector_type=self._connector_type,
            endpoint_identity=self._endpoint_identity,
            media_type=media_type,
        ).inc()

    def record_attachment_type_distribution(self, media_type: str) -> None:
        """Record a processed attachment for type-distribution analytics.

        Called once per successfully processed attachment regardless of fetch mode.

        Args:
            media_type: MIME type of the attachment.
        """
        attachment_type_distribution_total.labels(
            connector_type=self._connector_type,
            endpoint_identity=self._endpoint_identity,
            media_type=media_type,
        ).inc()


def get_error_type(exc: Exception) -> str:
    """Extract error type from exception.

    Args:
        exc: Exception instance

    Returns:
        Error type string for metrics labeling
    """
    exc_type = type(exc).__name__

    # Map common exception types to semantic error types
    if "HTTPStatus" in exc_type or "HTTP" in exc_type:
        return "http_error"
    if "Timeout" in exc_type:
        return "timeout"
    if "ConnectionError" in exc_type or "ConnectError" in exc_type:
        return "connection_error"
    if "JSON" in exc_type or "Parse" in exc_type:
        return "parse_error"
    if "ValueError" in exc_type or "ValidationError" in exc_type:
        return "validation_error"

    # Default to exception class name
    return exc_type.lower()
