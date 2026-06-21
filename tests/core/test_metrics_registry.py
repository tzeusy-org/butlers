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


@pytest.mark.parametrize(
    ("factory", "name", "labelnames"),
    [
        (get_or_create_counter, "test_metrics_registry_counter_total", ["k"]),
        (get_or_create_gauge, "test_metrics_registry_gauge", None),
        (get_or_create_histogram, "test_metrics_registry_histogram", None),
        (get_or_create_summary, "test_metrics_registry_summary", None),
    ],
)
def test_second_definition_returns_same_collector(factory, name, labelnames) -> None:
    """get_or_create_* is idempotent: a second definition returns the same collector."""
    kwargs = {"labelnames": labelnames} if labelnames is not None else {}
    first = factory(name, "doc", **kwargs)
    second = factory(name, "doc", **kwargs)
    assert first is second
    # And the collector actually works.
    if labelnames is not None:
        first.labels(k="v").inc()


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
    that flaked the finance bulk suite under pytest-xdist. The reload is wrapped
    in ``_reload_restoring_attrs`` so it cannot leak rebound module-level objects
    (e.g. ``_get_db_manager``) into later same-worker tests [bu-uhn47].
    """
    module = importlib.import_module(module_path)
    _reload_restoring_attrs(module)  # would raise ValueError before the fix


def test_audit_counter_is_present_after_reload() -> None:
    audit = importlib.import_module("butlers.api.routers.audit")
    _reload_restoring_attrs(audit)
    assert REGISTRY._names_to_collectors.get("audit_log_appended") is not None


def _reload_restoring_attrs(module: object) -> None:
    """Reload ``module`` then restore its original module-level attributes.

    Isolation [bu-uhn47]: ``importlib.reload`` re-executes the module body and
    rebinds every module-level name to a *new* object — including FastAPI
    dependency stubs like ``_get_db_manager`` and ``APIRouter`` instances.
    Leaving that mutation in place poisons later tests in the same xdist worker:
    a test that overrides ``app.dependency_overrides[module._get_db_manager]``
    would key on the post-reload function while the route was wired to the
    pre-reload one, so the override silently no-ops (observed as the
    google_health grant-flow test seeing ``state='not_configured'``). We
    snapshot the module's ``__dict__`` and restore it after the reload so the
    duplicate-timeseries check still runs but no identity leak escapes.
    """
    before = dict(vars(module))
    try:
        importlib.reload(module)  # type: ignore[arg-type]
    finally:
        current = vars(module)
        for key, value in before.items():
            current[key] = value
        for key in list(current.keys()):
            if key not in before:
                del current[key]
