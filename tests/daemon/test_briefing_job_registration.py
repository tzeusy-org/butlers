"""Tests for daily briefing job handler registration in _DETERMINISTIC_SCHEDULE_JOB_REGISTRY.

Covers task 4.3 of openspec/changes/cross-butler-daily-briefing/tasks.md:
  - daily_briefing_contribution registered for health, finance, relationship,
    travel, education, home
  - collect_briefing_contributions registered for general
  - All 7 handlers are resolvable via _resolve_deterministic_schedule_job_name
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit

# Specialist butlers that must expose daily_briefing_contribution
_SPECIALIST_BUTLERS = ("health", "finance", "relationship", "travel", "education", "home")


class TestBriefingJobRegistration:
    def test_daily_briefing_contribution_registered_for_all_specialist_butlers(self):
        """daily_briefing_contribution must appear in the registry for every specialist."""
        from butlers.daemon import _DETERMINISTIC_SCHEDULE_JOB_REGISTRY

        missing = [
            butler
            for butler in _SPECIALIST_BUTLERS
            if "daily_briefing_contribution"
            not in _DETERMINISTIC_SCHEDULE_JOB_REGISTRY.get(butler, {})
        ]
        assert not missing, (
            f"daily_briefing_contribution not registered for specialist butlers: {missing}"
        )

    def test_collect_briefing_contributions_registered_for_general(self):
        """collect_briefing_contributions must be registered for the general butler."""
        from butlers.daemon import _DETERMINISTIC_SCHEDULE_JOB_REGISTRY

        jobs = _DETERMINISTIC_SCHEDULE_JOB_REGISTRY.get("general", {})
        assert "collect_briefing_contributions" in jobs, (
            "collect_briefing_contributions not registered for general butler"
        )

    def test_all_seven_briefing_handlers_are_callable(self):
        """All 7 briefing job handler references in the registry must be callable."""
        from butlers.daemon import _DETERMINISTIC_SCHEDULE_JOB_REGISTRY

        # 6 specialist daily_briefing_contribution + 1 general collect_briefing_contributions
        handler_entries: list[tuple[str, str]] = [
            (butler, "daily_briefing_contribution") for butler in _SPECIALIST_BUTLERS
        ] + [("general", "collect_briefing_contributions")]

        not_callable = []
        for butler, job_name in handler_entries:
            handler = _DETERMINISTIC_SCHEDULE_JOB_REGISTRY.get(butler, {}).get(job_name)
            if not callable(handler):
                not_callable.append(f"{butler}/{job_name}")

        assert not not_callable, f"Non-callable handlers in registry: {not_callable}"

    def test_briefing_handlers_are_resolvable_via_resolve_function(self):
        """_resolve_deterministic_schedule_job_name returns job names for briefing jobs."""
        from butlers.daemon import _resolve_deterministic_schedule_job_name

        # Specialist butlers
        for butler in _SPECIALIST_BUTLERS:
            resolved = _resolve_deterministic_schedule_job_name(
                butler_name=butler,
                trigger_source="schedule:daily_briefing_contribution",
                job_name="daily_briefing_contribution",
            )
            assert resolved == "daily_briefing_contribution", (
                f"Expected 'daily_briefing_contribution' for {butler}, got {resolved!r}"
            )

        # General butler
        resolved = _resolve_deterministic_schedule_job_name(
            butler_name="general",
            trigger_source="schedule:collect_briefing_contributions",
            job_name="collect_briefing_contributions",
        )
        assert resolved == "collect_briefing_contributions", (
            f"Expected 'collect_briefing_contributions' for general, got {resolved!r}"  # noqa: E501
        )

    def test_handler_count_is_exactly_seven(self):
        """Registry must contain exactly 7 new briefing handler slots (not more, not fewer)."""
        from butlers.daemon import _DETERMINISTIC_SCHEDULE_JOB_REGISTRY

        specialist_count = sum(
            1
            for butler in _SPECIALIST_BUTLERS
            if "daily_briefing_contribution" in _DETERMINISTIC_SCHEDULE_JOB_REGISTRY.get(butler, {})
        )
        general_jobs = _DETERMINISTIC_SCHEDULE_JOB_REGISTRY.get("general", {})
        general_has_collect = "collect_briefing_contributions" in general_jobs

        total = specialist_count + int(general_has_collect)
        assert total == 7, f"Expected exactly 7 briefing handler slots, got {total}"
