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
    from butlers.daemon import (
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
