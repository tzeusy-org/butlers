"""Fail-closed authentication for owner-only dashboard control surfaces."""

from __future__ import annotations

import hmac
import os
from typing import Annotated, Literal

from fastapi import Header, HTTPException

from butlers.metrics_registry import get_or_create_counter

dashboard_owner_control_total = get_or_create_counter(
    "dashboard_owner_control_total",
    "Fail-closed dashboard owner-control authentication outcomes.",
    labelnames=["outcome"],
)


def _record(outcome: str) -> None:
    try:
        dashboard_owner_control_total.labels(outcome=outcome).inc()
    except Exception:
        # Metrics must never change an authorization decision.
        pass


def require_dashboard_owner_control(
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> Literal["owner"]:
    """Authenticate the single dashboard owner independently of optional API auth.

    Sensitive observation and recovery routes must not inherit the general
    middleware's development-mode fail-open behavior.  Configuration absence is
    operational unavailability (503); a configured boundary with a missing or
    mismatched credential is unauthorised (401).  Both decisions happen before
    a route acquires a database pool or observes protected state.
    """
    expected = os.environ.get("DASHBOARD_API_KEY", "")
    if not expected:
        _record("unavailable")
        raise HTTPException(status_code=503, detail="Dashboard owner control is unavailable")
    if not x_api_key or not hmac.compare_digest(x_api_key, expected):
        _record("denied")
        raise HTTPException(status_code=401, detail="Missing or invalid dashboard owner key")
    _record("allowed")
    return "owner"
