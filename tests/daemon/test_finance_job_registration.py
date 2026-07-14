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

    from butlers.scheduled_jobs import (
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


@pytest.mark.parametrize(
    ("schedule_name", "roster_attr"),
    [
        ("bill-reconciliation-sweep", "run_bill_reconciliation_sweep"),
        ("anomaly-insight-scan", "run_anomaly_insight_scan"),
        ("monthly-finance-digest", "run_monthly_finance_digest"),
    ],
)
def test_finance_bu_rvz2o_schedules_have_registered_handlers(
    schedule_name: str, roster_attr: str
) -> None:
    """bu-rvz2o: the three new insight-candidate jobs resolve to callable handlers.

    These replace the old direct-notify prompt-mode tasks (upcoming-bills-check,
    anomaly-digest, monthly-spending-summary + subscription-audit-monthly).
    """
    import tomllib

    from butlers.scheduled_jobs import (
        _DETERMINISTIC_SCHEDULE_JOB_REGISTRY,
        _resolve_deterministic_schedule_job_name,
    )

    toml_path = Path(__file__).resolve().parents[2] / "roster" / "finance" / "butler.toml"
    with toml_path.open("rb") as fh:
        config = tomllib.load(fh)

    schedules = config.get("butler", {}).get("schedule", [])
    entry = next(e for e in schedules if e["name"] == schedule_name)
    job_name = entry["job_name"]

    resolved = _resolve_deterministic_schedule_job_name(
        butler_name="finance",
        trigger_source=f"schedule:{schedule_name}",
        job_name=job_name,
    )

    assert resolved == job_name
    assert callable(_DETERMINISTIC_SCHEDULE_JOB_REGISTRY["finance"].get(job_name))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("job_name", "roster_attr"),
    [
        ("bill_reconciliation_sweep", "run_bill_reconciliation_sweep"),
        ("anomaly_insight_scan", "run_anomaly_insight_scan"),
        ("monthly_finance_digest", "run_monthly_finance_digest"),
    ],
)
async def test_finance_bu_rvz2o_handlers_dispatch_roster_job(
    monkeypatch, job_name: str, roster_attr: str
) -> None:
    """Each new registry wrapper calls the matching Finance roster job implementation."""
    from butlers.scheduled_jobs import _DETERMINISTIC_SCHEDULE_JOB_REGISTRY

    calls: dict[str, Any] = {}

    async def _fake_job(pool: Any) -> dict[str, Any]:
        calls["pool"] = pool
        return {"ok": True}

    monkeypatch.setattr(
        "butlers.jobs._roster_loader.load_roster_jobs",
        lambda name: SimpleNamespace(**{roster_attr: _fake_job}),
    )

    pool = object()
    handler = _DETERMINISTIC_SCHEDULE_JOB_REGISTRY["finance"][job_name]

    result = await handler(pool, {"ignored": True})

    assert calls == {"pool": pool}
    assert result == {"ok": True}
