"""Import-safe Prometheus metric registration helpers.

Module-level ``prometheus_client`` metric definitions (``Counter``, ``Gauge``,
``Histogram``, ``Summary``) register themselves into the global ``REGISTRY`` at
import time. If the same metric name is registered twice — which happens when a
module is re-imported (test reloads, ``importlib.reload``) or imported across
multiple processes that share a registry view (``pytest-xdist`` workers) — the
client raises::

    ValueError: Duplicated timeseries in CollectorRegistry: {...}

That collision was the root cause of a flaky finance ``test_bulk_*`` suite under
``pytest -n`` (bu-um02p): importing ``butlers.api.routers.audit`` a second time
re-ran ``Counter("audit_log_appended_total", ...)`` and exploded.

The ``get_or_create_*`` helpers below make registration idempotent: on the first
definition they create and register the collector; on any subsequent definition
with the same name they return the already-registered collector instead of
raising. Semantics are unchanged — the metric is defined exactly once per
process; re-imports simply reuse it.

Usage::

    from butlers.metrics_registry import get_or_create_counter

    audit_log_appended_total = get_or_create_counter(
        "audit_log_appended_total",
        "Number of rows appended to public.audit_log, partitioned by action.",
        labelnames=["action"],
    )
"""

from __future__ import annotations

from typing import Any

from prometheus_client import REGISTRY, Counter, Gauge, Histogram, Summary
from prometheus_client.metrics import MetricWrapperBase


def _get_or_create[T: MetricWrapperBase](
    metric_cls: type[T], name: str, *args: Any, **kwargs: Any
) -> T:
    """Create ``metric_cls`` named ``name``, or return the existing collector.

    ``prometheus_client`` raises ``ValueError`` if a metric name is already
    registered in the default ``REGISTRY``. When that happens we fetch and
    return the previously-registered collector so re-imports are harmless.
    """
    try:
        return metric_cls(name, *args, **kwargs)
    except ValueError:
        # Already registered (re-import / multiple xdist workers / test reload).
        # prometheus_client appends ``_total`` to Counter names and uses the
        # base name for the collector key, so match on the registered key.
        existing = REGISTRY._names_to_collectors.get(name)
        if existing is None:
            # Counters register under the bare name even when the exposed
            # sample is ``<name>_total``; fall back to a suffix-stripped lookup.
            base = name[: -len("_total")] if name.endswith("_total") else name
            existing = REGISTRY._names_to_collectors.get(base)
        if existing is None:
            # Could not recover the collector — re-raise the original error so
            # the genuine misconfiguration is not silently swallowed.
            raise
        if not isinstance(existing, metric_cls):
            raise
        return existing


def get_or_create_counter(name: str, documentation: str, **kwargs: Any) -> Counter:
    """Idempotent ``prometheus_client.Counter`` definition."""
    return _get_or_create(Counter, name, documentation, **kwargs)


def get_or_create_gauge(name: str, documentation: str, **kwargs: Any) -> Gauge:
    """Idempotent ``prometheus_client.Gauge`` definition."""
    return _get_or_create(Gauge, name, documentation, **kwargs)


def get_or_create_histogram(name: str, documentation: str, **kwargs: Any) -> Histogram:
    """Idempotent ``prometheus_client.Histogram`` definition."""
    return _get_or_create(Histogram, name, documentation, **kwargs)


def get_or_create_summary(name: str, documentation: str, **kwargs: Any) -> Summary:
    """Idempotent ``prometheus_client.Summary`` definition."""
    return _get_or_create(Summary, name, documentation, **kwargs)
