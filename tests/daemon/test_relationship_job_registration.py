"""Regression tests for Relationship deterministic schedule registration."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytestmark = pytest.mark.unit

_RELATIONSHIP_TOML = Path(__file__).resolve().parents[2] / "roster" / "relationship" / "butler.toml"


def _load_relationship_schedules() -> list[dict[str, Any]]:
    import tomllib

    with _RELATIONSHIP_TOML.open("rb") as fh:
        config = tomllib.load(fh)
    return config.get("butler", {}).get("schedule", [])


def test_relationship_job_schedules_have_registered_handlers() -> None:
    """Every relationship schedule using job dispatch must resolve to a callable handler."""
    from butlers.scheduled_jobs import (
        _DETERMINISTIC_SCHEDULE_JOB_REGISTRY,
        _resolve_deterministic_schedule_job_name,
    )

    relationship_jobs = _DETERMINISTIC_SCHEDULE_JOB_REGISTRY["relationship"]
    schedules = _load_relationship_schedules()
    missing: list[str] = []

    for schedule in schedules:
        if schedule.get("dispatch_mode") != "job":
            continue

        job_name = schedule["job_name"]
        resolved = _resolve_deterministic_schedule_job_name(
            butler_name="relationship",
            trigger_source=f"schedule:{schedule['name']}",
            job_name=job_name,
        )

        if resolved != job_name or not callable(relationship_jobs.get(job_name)):
            missing.append(job_name)

    assert not missing, f"Relationship deterministic jobs not registered: {missing}"


@pytest.mark.asyncio
async def test_relationship_episodic_predicate_curation_handler_dispatches_roster_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The registry wrapper should call the Relationship roster job implementation."""
    from butlers.scheduled_jobs import _DETERMINISTIC_SCHEDULE_JOB_REGISTRY

    calls: dict[str, Any] = {}

    async def run_episodic_predicate_curation(pool: Any) -> dict[str, Any]:
        calls["pool"] = pool
        return {
            "facts_scanned": 0,
            "episodic_found": 0,
            "flagged_new": 0,
            "skipped_already_pending": 0,
            "errors": 0,
        }

    monkeypatch.setattr(
        "butlers.jobs._roster_loader.load_roster_jobs",
        lambda name: SimpleNamespace(
            run_episodic_predicate_curation=run_episodic_predicate_curation
        ),
    )

    pool = object()
    handler = _DETERMINISTIC_SCHEDULE_JOB_REGISTRY["relationship"]["episodic_predicate_curation"]

    result = await handler(pool, {"ignored": True})

    assert calls == {"pool": pool}
    assert result == {
        "facts_scanned": 0,
        "episodic_found": 0,
        "flagged_new": 0,
        "skipped_already_pending": 0,
        "errors": 0,
    }


def test_meeting_debrief_is_scheduled_for_the_evening() -> None:
    """The debrief job is a daily 18:00 job-mode schedule on the Relationship butler."""
    (schedule,) = [s for s in _load_relationship_schedules() if s["name"] == "meeting-debrief"]

    assert schedule["dispatch_mode"] == "job"
    assert schedule["job_name"] == "meeting_debrief"
    assert schedule["cron"] == "0 18 * * *"


@pytest.mark.asyncio
async def test_meeting_debrief_handler_injects_the_insight_proposer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The registry wrapper hands the job the switchboard broker's proposer."""
    from butlers.scheduled_jobs import _DETERMINISTIC_SCHEDULE_JOB_REGISTRY
    from butlers.tools.switchboard.insight.broker import propose_insight_candidate

    calls: dict[str, Any] = {}

    async def fake_run(pool: Any, *, insight_proposer: Any) -> dict[str, Any]:
        calls["pool"] = pool
        calls["proposer"] = insight_proposer
        return {"prompted": 0}

    monkeypatch.setattr("butlers.jobs.meeting_debrief.run_meeting_debrief", fake_run)

    handler = _DETERMINISTIC_SCHEDULE_JOB_REGISTRY["relationship"]["meeting_debrief"]
    result = await handler("pool", None)

    assert result == {"prompted": 0}
    assert calls == {"pool": "pool", "proposer": propose_insight_candidate}
