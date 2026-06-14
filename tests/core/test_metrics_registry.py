"""Tests for import-safe Prometheus metric registration (bu-um02p).

These cover the ``get_or_create_*`` helpers that make module-level metric
definitions idempotent so re-imports / pytest-xdist workers / test reloads do
not raise ``Duplicated timeseries in CollectorRegistry``.
"""

from __future__ import annotations

import importlib

import pytest
from prometheus_client import REGISTRY

from butlers.metrics_registry import (
    get_or_create_counter,
    get_or_create_gauge,
    get_or_create_histogram,
    get_or_create_summary,
)


def test_counter_second_definition_returns_same_collector() -> None:
    name = "test_metrics_registry_counter_total"
    first = get_or_create_counter(name, "doc", labelnames=["k"])
    second = get_or_create_counter(name, "doc", labelnames=["k"])
    assert first is second
    # And it actually works.
    first.labels(k="v").inc()


def test_gauge_second_definition_returns_same_collector() -> None:
    name = "test_metrics_registry_gauge"
    first = get_or_create_gauge(name, "doc")
    second = get_or_create_gauge(name, "doc")
    assert first is second


def test_histogram_second_definition_returns_same_collector() -> None:
    name = "test_metrics_registry_histogram"
    first = get_or_create_histogram(name, "doc")
    second = get_or_create_histogram(name, "doc")
    assert first is second


def test_summary_second_definition_returns_same_collector() -> None:
    name = "test_metrics_registry_summary"
    first = get_or_create_summary(name, "doc")
    second = get_or_create_summary(name, "doc")
    assert first is second


def test_type_mismatch_still_raises() -> None:
    """A name already registered as a different metric type must still error."""
    name = "test_metrics_registry_type_clash"
    get_or_create_counter(name, "doc")
    with pytest.raises(ValueError):
        get_or_create_gauge(name, "doc")


@pytest.mark.parametrize(
    "module_path",
    [
        "butlers.api.routers.audit",
        "butlers.connectors.metrics",
        "butlers.api.routers.dashboard_briefing",
        "butlers.api.routers.google_health",
        "butlers.connectors.google_drive",
    ],
)
def test_module_reimport_does_not_raise_duplicate_timeseries(module_path: str) -> None:
    """Reloading a metrics-defining module must not collide in the registry.

    This is the exact failure mode (audit_log_appended_total registered twice)
    that flaked the finance bulk suite under pytest-xdist.
    """
    module = importlib.import_module(module_path)
    importlib.reload(module)  # would raise ValueError before the fix


def test_audit_counter_is_present_after_reload() -> None:
    audit = importlib.import_module("butlers.api.routers.audit")
    importlib.reload(audit)
    assert REGISTRY._names_to_collectors.get("audit_log_appended") is not None
