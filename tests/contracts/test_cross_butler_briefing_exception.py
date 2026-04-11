"""Contract tests for the Cross-Butler Daily Briefing Exception (RFC 0010).

Catches drift between the four authoritative artefacts that together define the
briefing pipeline:

1. ``src/butlers/jobs/briefing.py::SPECIALIST_BUTLERS`` — the canonical tuple
   consumed by the aggregator at runtime.
2. ``alembic/versions/core/core_063_v_briefing_contributions.py::_SPECIALIST_SCHEMAS``
   — the schemas unioned into ``general.v_briefing_contributions``.
3. ``src/butlers/scheduled_jobs.py::_DETERMINISTIC_SCHEDULE_JOB_REGISTRY`` — the
   per-butler deterministic job handlers.
4. ``roster/<butler>/butler.toml`` — the schedule entries that fire the jobs.

Whenever a specialist is added or removed, all four artefacts must move in
lock-step. The three contract tests below fail loudly and pinpoint the
offender instead of leaving the repo in a half-migrated state.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from butlers.config import ButlerType, load_config
from butlers.jobs.briefing import SPECIALIST_BUTLERS

pytestmark = pytest.mark.contract

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ROSTER_DIR = _REPO_ROOT / "roster"
_MIGRATION_PATH = (
    _REPO_ROOT / "alembic" / "versions" / "core" / "core_063_v_briefing_contributions.py"
)


def _migration_specialist_schemas() -> tuple[str, ...]:
    """Import the migration module and return its ``_SPECIALIST_SCHEMAS`` tuple.

    Loading the module directly (instead of re-parsing text) guarantees that if
    the tuple is renamed or restructured, this test fails with a clear
    ``AttributeError`` rather than silently asserting against stale string
    scraping.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "_core_063_v_briefing_contributions", _MIGRATION_PATH
    )
    assert spec is not None and spec.loader is not None, f"could not load {_MIGRATION_PATH}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return tuple(module._SPECIALIST_SCHEMAS)


class TestSpecialistSetIsSingleSourcedAcrossArtefacts:
    """The specialist butler set must be identical across every artefact.

    We treat ``briefing.SPECIALIST_BUTLERS`` as the single source of truth and
    assert every other artefact projects from it.
    """

    def test_migration_matches_jobs_module(self):
        assert tuple(sorted(_migration_specialist_schemas())) == tuple(sorted(SPECIALIST_BUTLERS))

    def test_job_registry_has_handler_per_specialist(self):
        from butlers.scheduled_jobs import _DETERMINISTIC_SCHEDULE_JOB_REGISTRY

        for butler in SPECIALIST_BUTLERS:
            jobs = _DETERMINISTIC_SCHEDULE_JOB_REGISTRY.get(butler, {})
            assert "daily_briefing_contribution" in jobs, (
                f"{butler!r} is listed in SPECIALIST_BUTLERS but has no "
                "daily_briefing_contribution handler in _DETERMINISTIC_SCHEDULE_JOB_REGISTRY"
            )

        # The aggregator lives on general, not on a specialist.
        assert "collect_briefing_contributions" in _DETERMINISTIC_SCHEDULE_JOB_REGISTRY.get(
            "general", {}
        )

    def test_each_specialist_roster_has_briefing_schedule(self):
        """Every specialist butler.toml must schedule the contribution job."""
        missing: list[str] = []
        for butler in SPECIALIST_BUTLERS:
            cfg = load_config(_ROSTER_DIR / butler)
            assert cfg.type == ButlerType.BUTLER, (
                f"{butler!r} must be a butler-typed agent "
                f"(staffers cannot participate in briefing contribution)"
            )
            if not any(
                s.job_name == "daily_briefing_contribution" and s.dispatch_mode.value == "job"
                for s in cfg.schedules
            ):
                missing.append(butler)
        assert not missing, (
            f"Specialists missing daily_briefing_contribution schedule in roster: {missing}"
        )


class TestStaffersExcludedFromBriefing:
    """Staffers (switchboard, messenger, qa) must never appear as contributors.

    Reason: staffers serve the ecosystem, not the user; their state does not
    map to domain highlights. ``briefing.SPECIALIST_BUTLERS`` and the migration
    schema list must never contain a staffer name.
    """

    _STAFFER_NAMES: frozenset[str] = frozenset({"switchboard", "messenger", "qa"})

    def test_staffers_not_in_specialists(self):
        overlap = self._STAFFER_NAMES & set(SPECIALIST_BUTLERS)
        assert not overlap, f"Staffers leaked into SPECIALIST_BUTLERS: {sorted(overlap)}"

    def test_staffer_rosters_dont_register_briefing_contribution(self):
        from butlers.scheduled_jobs import _DETERMINISTIC_SCHEDULE_JOB_REGISTRY

        for name in self._STAFFER_NAMES:
            jobs = _DETERMINISTIC_SCHEDULE_JOB_REGISTRY.get(name, {})
            assert "daily_briefing_contribution" not in jobs, (
                f"Staffer {name!r} must not have a daily_briefing_contribution handler"
            )


class TestBriefingScheduleConsistency:
    """Contribution cron and aggregation cron must stay ordered and aligned."""

    def test_contribution_and_aggregation_schedules(self):
        """Every specialist runs at the spec'd cron; aggregator runs after them."""
        expected_contribution_cron = "55 6 * * *"
        expected_aggregation_cron = "58 6 * * *"

        for butler in SPECIALIST_BUTLERS:
            cfg = load_config(_ROSTER_DIR / butler)
            match = next(
                (s for s in cfg.schedules if s.job_name == "daily_briefing_contribution"),
                None,
            )
            assert match is not None, f"{butler} missing briefing schedule"
            assert match.cron == expected_contribution_cron, (
                f"{butler}: contribution cron drifted to {match.cron!r}; "
                f"expected {expected_contribution_cron!r}"
            )

        general_cfg = load_config(_ROSTER_DIR / "general")
        agg = next(
            (s for s in general_cfg.schedules if s.job_name == "collect_briefing_contributions"),
            None,
        )
        assert agg is not None, "general missing collect_briefing_contributions schedule"
        assert agg.cron == expected_aggregation_cron, (
            f"general: aggregation cron drifted to {agg.cron!r}; "
            f"expected {expected_aggregation_cron!r}"
        )
