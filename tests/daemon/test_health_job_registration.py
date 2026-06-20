"""Tests for Health deterministic scheduled job registration.

Covers the cross-signal correlation rollout: the ``insight_scan`` schedule must
resolve to a registered handler AND run on a weekly cadence (changed from the
prior monthly ``0 7 15 * *``).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

pytestmark = pytest.mark.unit

_HEALTH_TOML = Path(__file__).resolve().parents[2] / "roster" / "health" / "butler.toml"


def _load_health_schedules() -> list[dict[str, Any]]:
    import tomllib

    with _HEALTH_TOML.open("rb") as fh:
        config = tomllib.load(fh)
    return config.get("butler", {}).get("schedule", [])


def test_health_insight_scan_schedule_is_weekly() -> None:
    """The health insight-scan must run weekly (Mondays 07:00 UTC), not monthly."""
    schedules = _load_health_schedules()
    insight_scan = next(entry for entry in schedules if entry["name"] == "insight_scan")

    assert insight_scan["cron"] == "0 7 * * 1", (
        "insight_scan cron must be weekly '0 7 * * 1', not the prior monthly '0 7 15 * *'"
    )
    assert insight_scan["dispatch_mode"] == "job"
    assert insight_scan["job_name"] == "insight_scan"


def test_health_insight_scan_schedule_has_registered_handler() -> None:
    """Health's configured insight-scan job must resolve to a callable handler."""
    from butlers.daemon import (
        _DETERMINISTIC_SCHEDULE_JOB_REGISTRY,
        _resolve_deterministic_schedule_job_name,
    )

    schedules = _load_health_schedules()
    insight_scan = next(entry for entry in schedules if entry["name"] == "insight_scan")
    job_name = insight_scan["job_name"]

    resolved = _resolve_deterministic_schedule_job_name(
        butler_name="health",
        trigger_source="schedule:insight_scan",
        job_name=job_name,
    )

    assert resolved == job_name
    assert callable(_DETERMINISTIC_SCHEDULE_JOB_REGISTRY["health"].get(job_name))


@pytest.mark.asyncio
async def test_health_insight_scan_handler_dispatches_roster_job(monkeypatch) -> None:
    """The registry wrapper should call the Health roster job implementation.

    The handler now builds an HA environment reader before dispatching, so both
    ``load_roster_jobs`` and ``build_ha_environment_reader`` must be stubbed.
    The reader is None here (no HA credentials) — the key assertion is that the
    pool is forwarded and the roster job is actually called.
    """
    from butlers.scheduled_jobs import _DETERMINISTIC_SCHEDULE_JOB_REGISTRY

    calls: dict[str, Any] = {}

    async def run_insight_scan(pool: Any, *, ha_environment_reader: Any = None) -> dict[str, Any]:
        calls["pool"] = pool
        calls["ha_environment_reader"] = ha_environment_reader
        return {"candidates_proposed": 0}

    monkeypatch.setattr(
        "butlers.jobs._roster_loader.load_roster_jobs",
        lambda name: SimpleNamespace(run_insight_scan=run_insight_scan),
    )
    monkeypatch.setattr(
        "butlers.jobs.health_ha_reader.build_ha_environment_reader",
        AsyncMock(return_value=None),
    )

    pool = object()
    handler = _DETERMINISTIC_SCHEDULE_JOB_REGISTRY["health"]["insight_scan"]

    result = await handler(pool, {"ignored": True})

    assert calls["pool"] is pool
    assert calls["ha_environment_reader"] is None
    assert result == {"candidates_proposed": 0}
