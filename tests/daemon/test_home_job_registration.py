"""Tests for home butler deterministic job handler registration.

Covers the job registry registration requirement from:
  openspec/specs/home-deterministic-jobs/spec.md — Requirement: Job Registry Registration

Verifies that device_health_check, environment_report, energy_digest, and
maintenance_schedule_check are registered in _DETERMINISTIC_SCHEDULE_JOB_REGISTRY['home']
and that all four handlers are callable.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit

_HOME_DETERMINISTIC_JOBS = (
    "device_health_check",
    "environment_report",
    "energy_digest",
    "maintenance_schedule_check",
)


class TestHomeJobRegistration:
    def test_all_four_home_jobs_are_registered(self):
        """All four home deterministic job handlers must appear in the registry."""
        from butlers.daemon import _DETERMINISTIC_SCHEDULE_JOB_REGISTRY

        home_jobs = _DETERMINISTIC_SCHEDULE_JOB_REGISTRY.get("home", {})
        missing = [job for job in _HOME_DETERMINISTIC_JOBS if job not in home_jobs]
        assert not missing, (
            "Home deterministic jobs not registered in "
            f"_DETERMINISTIC_SCHEDULE_JOB_REGISTRY['home']: {missing}"
        )

    def test_all_four_home_job_handlers_are_callable(self):
        """All four home job handler references in the registry must be callable."""
        from butlers.daemon import _DETERMINISTIC_SCHEDULE_JOB_REGISTRY

        home_jobs = _DETERMINISTIC_SCHEDULE_JOB_REGISTRY.get("home", {})
        not_callable = [
            job_name
            for job_name in _HOME_DETERMINISTIC_JOBS
            if not callable(home_jobs.get(job_name))
        ]
        assert not not_callable, (
            f"Non-callable or missing handlers in _DETERMINISTIC_SCHEDULE_JOB_REGISTRY['home']: "
            f"{not_callable}"
        )

    def test_home_registry_still_contains_memory_maintenance_and_briefing_jobs(self):
        """Existing home registry entries must not be disturbed by new additions."""
        from butlers.daemon import _DETERMINISTIC_SCHEDULE_JOB_REGISTRY

        home_jobs = _DETERMINISTIC_SCHEDULE_JOB_REGISTRY.get("home", {})
        expected_existing = {
            "daily_briefing_contribution",
            "memory_consolidation",
            "memory_episode_cleanup",
            "memory_purge_superseded",
        }
        missing = expected_existing - home_jobs.keys()
        assert not missing, f"Pre-existing home registry entries were removed: {missing}"

    def test_home_jobs_are_resolvable_via_resolve_function(self):
        """_resolve_deterministic_schedule_job_name must return the correct job name for each."""
        from butlers.daemon import _resolve_deterministic_schedule_job_name

        for job_name in _HOME_DETERMINISTIC_JOBS:
            resolved = _resolve_deterministic_schedule_job_name(
                butler_name="home",
                trigger_source=f"schedule:{job_name}",
                job_name=job_name,
            )
            assert resolved == job_name, f"Expected {job_name!r} for home butler, got {resolved!r}"
