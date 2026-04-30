"""Tests for Finance deterministic scheduled job registration."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytestmark = pytest.mark.unit


def test_finance_insight_scan_schedule_has_registered_handler() -> None:
    """Finance's configured insight-scan job must resolve to a callable handler."""
    import tomllib

    from butlers.daemon import (
        _DETERMINISTIC_SCHEDULE_JOB_REGISTRY,
        _resolve_deterministic_schedule_job_name,
    )

    toml_path = Path(__file__).resolve().parents[2] / "roster" / "finance" / "butler.toml"
    with toml_path.open("rb") as fh:
        config = tomllib.load(fh)

    schedules = config.get("butler", {}).get("schedule", [])
    insight_scan = next(entry for entry in schedules if entry["name"] == "insight-scan")
    job_name = insight_scan["job_name"]

    resolved = _resolve_deterministic_schedule_job_name(
        butler_name="finance",
        trigger_source="schedule:insight-scan",
        job_name=job_name,
    )

    assert resolved == job_name
    assert callable(_DETERMINISTIC_SCHEDULE_JOB_REGISTRY["finance"].get(job_name))


@pytest.mark.asyncio
async def test_finance_insight_scan_handler_dispatches_roster_job(monkeypatch) -> None:
    """The registry wrapper should call the Finance roster job implementation."""
    from butlers.scheduled_jobs import _DETERMINISTIC_SCHEDULE_JOB_REGISTRY

    calls: dict[str, Any] = {}

    async def run_insight_scan(pool: Any) -> dict[str, Any]:
        calls["pool"] = pool
        return {"submitted": 0, "accepted": 0, "filtered": 0, "errors": 0}

    monkeypatch.setattr(
        "butlers.jobs._roster_loader.load_roster_jobs",
        lambda name: SimpleNamespace(run_insight_scan=run_insight_scan),
    )

    pool = object()
    handler = _DETERMINISTIC_SCHEDULE_JOB_REGISTRY["finance"]["insight_scan"]

    result = await handler(pool, {"ignored": True})

    assert calls == {"pool": pool}
    assert result == {"submitted": 0, "accepted": 0, "filtered": 0, "errors": 0}
