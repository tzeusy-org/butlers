"""Tests for temporal intelligence features.

Covers tasks 12.1–12.11 from openspec/changes/temporal-intelligence/tasks.md:
  12.1 Deadline validation (future date, threshold bounds, non-empty thresholds)
  12.2 Deadline countdown computation and threshold firing
  12.3 Deadline status state machine transitions
  12.4 Deadline dependencies blocking threshold evaluation
  12.5 Event chain creation, trigger detection, and action materialization
  12.6 Event chain depth limit enforcement
  12.7 Seasonal period active detection (same-year, cross-year, disabled, multiple)
  12.8 Quiet hours enforcement (high bypass, medium/low deferral, outside hours, no preferences)
  12.9 Deferred notification flush (delivery, expiry, retry on failure)
  12.10 Per-channel quiet hours overrides
  12.11 tick() integration with all three new passes

All tests are pure unit tests (no Docker/DB required) unless explicitly marked integration.
They test the temporal logic functions expected to be implemented in:
  - butlers.core.temporal.deadlines  — deadline validation, countdown, state machine
  - butlers.core.temporal.event_chains — chain schema validation, depth limit
  - butlers.core.temporal.seasonal   — active period detection
  - butlers.core.temporal.delivery   — quiet hours check, deliver_at computation
"""

from __future__ import annotations

import shutil
import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import pytest

pytestmark = [pytest.mark.unit]

docker_available = shutil.which("docker") is not None

# ---------------------------------------------------------------------------
# Helpers shared across sections
# ---------------------------------------------------------------------------


def _make_threshold(days_before: int, severity: str = "info") -> dict[str, Any]:
    return {"days_before": days_before, "severity": severity}


def _future_date(days: int = 30) -> date:
    return (datetime.now(UTC) + timedelta(days=days)).date()


def _past_date(days: int = 1) -> date:
    return (datetime.now(UTC) - timedelta(days=days)).date()


# ---------------------------------------------------------------------------
# 12.1 — Deadline validation
# ---------------------------------------------------------------------------


class TestDeadlineValidation:
    """Tests for deadline input validation (target_date, thresholds, lead_time_days).

    These cover scenarios from:
    - openspec/specs/deadline-tracking/spec.md  §Deadline Registration
    - tasks.md §12.1
    """

    def test_valid_deadline_passes_validation(self):
        """A correctly formed deadline does not raise."""
        from butlers.core.temporal.deadlines import validate_deadline_input

        # Should not raise
        validate_deadline_input(
            target_date=_future_date(60),
            lead_time_days=42,
            alert_thresholds=[
                _make_threshold(42, "info"),
                _make_threshold(14, "warning"),
                _make_threshold(3, "critical"),
            ],
        )

    def test_target_date_must_be_future(self):
        """A past target_date raises ValueError."""
        from butlers.core.temporal.deadlines import validate_deadline_input

        with pytest.raises(ValueError, match="future"):
            validate_deadline_input(
                target_date=_past_date(10),
                lead_time_days=7,
                alert_thresholds=[_make_threshold(7)],
            )

    def test_target_date_today_raises(self):
        """target_date == today should be rejected (must be strictly future)."""
        from butlers.core.temporal.deadlines import validate_deadline_input

        with pytest.raises(ValueError, match="future"):
            validate_deadline_input(
                target_date=datetime.now(UTC).date(),
                lead_time_days=1,
                alert_thresholds=[_make_threshold(1)],
            )

    def test_empty_alert_thresholds_raises(self):
        """Empty alert_thresholds list raises ValueError."""
        from butlers.core.temporal.deadlines import validate_deadline_input

        with pytest.raises(ValueError, match="threshold"):
            validate_deadline_input(
                target_date=_future_date(30),
                lead_time_days=30,
                alert_thresholds=[],
            )

    def test_threshold_days_before_cannot_exceed_lead_time(self):
        """A threshold with days_before > lead_time_days raises ValueError."""
        from butlers.core.temporal.deadlines import validate_deadline_input

        with pytest.raises(ValueError, match="lead_time_days|days_before"):
            validate_deadline_input(
                target_date=_future_date(60),
                lead_time_days=14,
                alert_thresholds=[_make_threshold(30, "info")],
            )

    def test_threshold_days_before_equal_lead_time_is_valid(self):
        """days_before == lead_time_days is valid (boundary is allowed)."""
        from butlers.core.temporal.deadlines import validate_deadline_input

        # Should not raise
        validate_deadline_input(
            target_date=_future_date(60),
            lead_time_days=14,
            alert_thresholds=[_make_threshold(14, "info")],
        )

    def test_multiple_thresholds_validated_individually(self):
        """All thresholds are validated; any exceeding lead_time_days raises."""
        from butlers.core.temporal.deadlines import validate_deadline_input

        with pytest.raises(ValueError):
            validate_deadline_input(
                target_date=_future_date(60),
                lead_time_days=14,
                alert_thresholds=[
                    _make_threshold(14, "info"),  # valid
                    _make_threshold(7, "warning"),  # valid
                    _make_threshold(30, "critical"),  # invalid — exceeds 14
                ],
            )

    def test_lead_time_days_must_be_positive(self):
        """lead_time_days <= 0 raises ValueError."""
        from butlers.core.temporal.deadlines import validate_deadline_input

        with pytest.raises(ValueError):
            validate_deadline_input(
                target_date=_future_date(60),
                lead_time_days=0,
                alert_thresholds=[_make_threshold(1)],
            )


# ---------------------------------------------------------------------------
# 12.2 — Deadline countdown computation and threshold firing
# ---------------------------------------------------------------------------


class TestDeadlineCountdown:
    """Tests for countdown computation and threshold matching.

    Covers scenarios from:
    - openspec/specs/deadline-tracking/spec.md  §Deadline Countdown Evaluation
    - tasks.md §12.2
    """

    def test_days_remaining_computed_correctly(self):
        """days_remaining = (target_date - today).days."""
        from butlers.core.temporal.deadlines import compute_days_remaining

        target = _future_date(42)
        result = compute_days_remaining(target_date=target)
        assert 41 <= result <= 42  # tolerance for time-of-day boundary

    def test_threshold_fires_when_days_remaining_equals_threshold(self):
        """Threshold fires when days_remaining exactly equals days_before."""
        from butlers.core.temporal.deadlines import find_unfired_threshold

        thresholds = [_make_threshold(42, "info"), _make_threshold(14, "warning")]
        fired: list[dict] = []
        result = find_unfired_threshold(
            days_remaining=42,
            alert_thresholds=thresholds,
            fired_thresholds=fired,
        )
        assert result is not None
        assert result["days_before"] == 42
        assert result["severity"] == "info"

    def test_threshold_fires_when_past_threshold_day(self):
        """Threshold fires when days_remaining < days_before (missed threshold due to downtime)."""
        from butlers.core.temporal.deadlines import find_unfired_threshold

        thresholds = [_make_threshold(14, "warning")]
        fired: list[dict] = []
        result = find_unfired_threshold(
            days_remaining=12,  # past the 14-day mark
            alert_thresholds=thresholds,
            fired_thresholds=fired,
        )
        assert result is not None
        assert result["days_before"] == 14

    def test_already_fired_threshold_does_not_re_fire(self):
        """A threshold that has already fired is excluded from matching."""
        from butlers.core.temporal.deadlines import find_unfired_threshold

        thresholds = [_make_threshold(14, "warning")]
        fired = [_make_threshold(14, "warning")]  # already fired
        result = find_unfired_threshold(
            days_remaining=14,
            alert_thresholds=thresholds,
            fired_thresholds=fired,
        )
        assert result is None

    def test_no_threshold_fires_when_days_remaining_above_all(self):
        """No threshold fires when days_remaining > all days_before values."""
        from butlers.core.temporal.deadlines import find_unfired_threshold

        thresholds = [_make_threshold(14, "warning"), _make_threshold(3, "critical")]
        result = find_unfired_threshold(
            days_remaining=30,
            alert_thresholds=thresholds,
            fired_thresholds=[],
        )
        assert result is None

    def test_most_urgent_threshold_selected_when_multiple_eligible(self):
        """When multiple thresholds are eligible, the one closest to today fires first."""
        from butlers.core.temporal.deadlines import find_unfired_threshold

        thresholds = [
            _make_threshold(42, "info"),
            _make_threshold(14, "warning"),
            _make_threshold(3, "critical"),
        ]
        # days_remaining=3 means all three have been passed; only 3-day not fired
        fired = [_make_threshold(42, "info"), _make_threshold(14, "warning")]
        result = find_unfired_threshold(
            days_remaining=3,
            alert_thresholds=thresholds,
            fired_thresholds=fired,
        )
        assert result is not None
        assert result["days_before"] == 3


# ---------------------------------------------------------------------------
# 12.3 — Deadline status state machine transitions
# ---------------------------------------------------------------------------


class TestDeadlineStatusStateMachine:
    """Tests for deadline_status transitions.

    Covers scenarios from:
    - openspec/specs/deadline-tracking/spec.md  §Deadline Status Transitions
    - tasks.md §12.3
    """

    def test_pending_transitions_to_alerted_on_first_non_critical_threshold(self):
        """First non-critical threshold fires: pending → alerted."""
        from butlers.core.temporal.deadlines import compute_next_deadline_status

        new_status = compute_next_deadline_status(
            current_status="pending",
            fired_threshold=_make_threshold(14, "info"),
        )
        assert new_status == "alerted"

    def test_alerted_transitions_to_escalated_on_critical_threshold(self):
        """Critical threshold fires on alerted deadline: alerted → escalated."""
        from butlers.core.temporal.deadlines import compute_next_deadline_status

        new_status = compute_next_deadline_status(
            current_status="alerted",
            fired_threshold=_make_threshold(3, "critical"),
        )
        assert new_status == "escalated"

    def test_pending_transitions_to_escalated_on_critical_threshold(self):
        """A critical threshold on a pending deadline skips alerted, goes to escalated."""
        from butlers.core.temporal.deadlines import compute_next_deadline_status

        new_status = compute_next_deadline_status(
            current_status="pending",
            fired_threshold=_make_threshold(3, "critical"),
        )
        assert new_status == "escalated"

    def test_completed_status_is_terminal(self):
        """Completed status does not change on further threshold fires."""
        from butlers.core.temporal.deadlines import compute_next_deadline_status

        new_status = compute_next_deadline_status(
            current_status="completed",
            fired_threshold=_make_threshold(7, "warning"),
        )
        assert new_status == "completed"

    def test_expired_status_is_terminal(self):
        """Expired status does not change on further threshold fires."""
        from butlers.core.temporal.deadlines import compute_next_deadline_status

        new_status = compute_next_deadline_status(
            current_status="expired",
            fired_threshold=_make_threshold(7, "warning"),
        )
        assert new_status == "expired"

    def test_deadline_transitions_to_expired_when_past_target_date(self):
        """When target_date has passed, status transitions to expired."""
        from butlers.core.temporal.deadlines import compute_expiry_transition

        new_status, should_disable = compute_expiry_transition(
            current_status="alerted",
            target_date=_past_date(1),
        )
        assert new_status == "expired"
        assert should_disable is True

    def test_pending_deadline_expires_if_no_thresholds_fired(self):
        """pending → expired when target_date passes with no threshold ever firing."""
        from butlers.core.temporal.deadlines import compute_expiry_transition

        new_status, should_disable = compute_expiry_transition(
            current_status="pending",
            target_date=_past_date(1),
        )
        assert new_status == "expired"
        assert should_disable is True

    def test_completed_deadline_not_expired(self):
        """Completed deadlines do not transition to expired."""
        from butlers.core.temporal.deadlines import compute_expiry_transition

        new_status, should_disable = compute_expiry_transition(
            current_status="completed",
            target_date=_past_date(5),
        )
        assert new_status == "completed"
        assert should_disable is False

    def test_no_transition_when_target_date_still_future(self):
        """No expiry transition when target_date is in the future."""
        from butlers.core.temporal.deadlines import compute_expiry_transition

        new_status, should_disable = compute_expiry_transition(
            current_status="alerted",
            target_date=_future_date(10),
        )
        assert new_status == "alerted"
        assert should_disable is False


# ---------------------------------------------------------------------------
# 12.4 — Deadline dependencies blocking threshold evaluation
# ---------------------------------------------------------------------------


class TestDeadlineDependencies:
    """Tests for dependency-based blocking of threshold evaluation.

    Covers scenarios from:
    - openspec/specs/deadline-tracking/spec.md  §Deadline Dependencies
    - tasks.md §12.4
    """

    def test_deadline_blocked_when_dependency_not_completed(self):
        """Deadline evaluation is skipped if a dependency is not completed."""
        from butlers.core.temporal.deadlines import is_deadline_blocked

        dep_id = str(uuid.uuid4())
        dependency_statuses = {dep_id: "pending"}
        blocked = is_deadline_blocked(
            depends_on=[dep_id],
            dependency_statuses=dependency_statuses,
        )
        assert blocked is True

    def test_deadline_unblocked_when_all_dependencies_completed(self):
        """Deadline evaluates normally when all dependencies are completed."""
        from butlers.core.temporal.deadlines import is_deadline_blocked

        dep_id = str(uuid.uuid4())
        dependency_statuses = {dep_id: "completed"}
        blocked = is_deadline_blocked(
            depends_on=[dep_id],
            dependency_statuses=dependency_statuses,
        )
        assert blocked is False

    def test_deadline_with_no_dependencies_is_not_blocked(self):
        """Deadline with empty depends_on is not blocked."""
        from butlers.core.temporal.deadlines import is_deadline_blocked

        blocked = is_deadline_blocked(
            depends_on=[],
            dependency_statuses={},
        )
        assert blocked is False

    def test_deadline_blocked_if_any_dependency_not_completed(self):
        """If one of multiple dependencies is incomplete, the deadline is blocked."""
        from butlers.core.temporal.deadlines import is_deadline_blocked

        dep_a = str(uuid.uuid4())
        dep_b = str(uuid.uuid4())
        dependency_statuses = {dep_a: "completed", dep_b: "alerted"}
        blocked = is_deadline_blocked(
            depends_on=[dep_a, dep_b],
            dependency_statuses=dependency_statuses,
        )
        assert blocked is True

    def test_deadline_blocked_when_dependency_is_alerted(self):
        """alerted status still blocks — only completed unblocks."""
        from butlers.core.temporal.deadlines import is_deadline_blocked

        dep_id = str(uuid.uuid4())
        dependency_statuses = {dep_id: "alerted"}
        assert is_deadline_blocked(depends_on=[dep_id], dependency_statuses=dependency_statuses)

    def test_deadline_blocked_when_dependency_is_expired(self):
        """expired dependency blocks (expired ≠ completed)."""
        from butlers.core.temporal.deadlines import is_deadline_blocked

        dep_id = str(uuid.uuid4())
        dependency_statuses = {dep_id: "expired"}
        assert is_deadline_blocked(depends_on=[dep_id], dependency_statuses=dependency_statuses)


# ---------------------------------------------------------------------------
# 12.5 — Event chain creation, trigger detection, action materialization
# ---------------------------------------------------------------------------


class TestEventChainActionSchema:
    """Tests for event chain action schema validation.

    Covers scenarios from:
    - openspec/specs/event-chains/spec.md  §Event Chain Action Schema
    - tasks.md §12.5
    """

    def test_valid_prompt_action_passes_validation(self):
        """A prompt action with required fields does not raise."""
        from butlers.core.temporal.event_chains import validate_chain_actions

        actions = [{"action_type": "prompt", "delay_minutes": 0, "prompt": "Log visit"}]
        validate_chain_actions(actions)  # should not raise

    def test_valid_job_action_passes_validation(self):
        """A job action with required fields does not raise."""
        from butlers.core.temporal.event_chains import validate_chain_actions

        actions = [{"action_type": "job", "delay_minutes": 60, "job_name": "archive_docs"}]
        validate_chain_actions(actions)  # should not raise

    def test_invalid_action_type_raises(self):
        """Unknown action_type (e.g. 'webhook') raises ValueError."""
        from butlers.core.temporal.event_chains import validate_chain_actions

        actions = [{"action_type": "webhook", "delay_minutes": 0, "url": "https://example.com"}]
        with pytest.raises(ValueError, match="action_type|webhook"):
            validate_chain_actions(actions)

    def test_prompt_action_missing_prompt_raises(self):
        """prompt action without 'prompt' field raises ValueError."""
        from butlers.core.temporal.event_chains import validate_chain_actions

        actions = [{"action_type": "prompt", "delay_minutes": 0}]
        with pytest.raises(ValueError, match="prompt"):
            validate_chain_actions(actions)

    def test_job_action_missing_job_name_raises(self):
        """job action without 'job_name' field raises ValueError."""
        from butlers.core.temporal.event_chains import validate_chain_actions

        actions = [{"action_type": "job", "delay_minutes": 0}]
        with pytest.raises(ValueError, match="job_name"):
            validate_chain_actions(actions)

    def test_negative_delay_minutes_raises(self):
        """delay_minutes < 0 raises ValueError."""
        from butlers.core.temporal.event_chains import validate_chain_actions

        actions = [{"action_type": "prompt", "delay_minutes": -5, "prompt": "Do it"}]
        with pytest.raises(ValueError, match="delay_minutes"):
            validate_chain_actions(actions)

    def test_empty_actions_list_raises(self):
        """Empty actions list raises ValueError."""
        from butlers.core.temporal.event_chains import validate_chain_actions

        with pytest.raises(ValueError, match="action"):
            validate_chain_actions([])


class TestEventChainMaterialization:
    """Tests for action materialization into one-shot scheduled tasks.

    Covers scenarios from:
    - openspec/specs/event-chains/spec.md  §Event Chain Action Materialization
    - tasks.md §12.5
    """

    def test_materialization_creates_tasks_with_chain_lineage(self):
        """Materialized tasks have source='chain' and name derived from chain name."""
        from butlers.core.temporal.event_chains import materialize_chain_actions

        now = datetime.now(UTC)
        chain_name = "post-dentist"
        actions = [
            {"action_type": "prompt", "delay_minutes": 0, "prompt": "Log visit"},
            {"action_type": "prompt", "delay_minutes": 1440, "prompt": "Send reminder"},
        ]
        tasks = materialize_chain_actions(chain_name=chain_name, actions=actions, fired_at=now)

        assert len(tasks) == 2
        assert tasks[0]["name"] == "chain:post-dentist:0"
        assert tasks[0]["source"] == "chain"
        assert tasks[1]["name"] == "chain:post-dentist:1"
        assert tasks[1]["source"] == "chain"

    def test_materialization_sets_correct_next_run_at_for_delayed_actions(self):
        """Actions with delay have next_run_at offset by delay_minutes."""
        from butlers.core.temporal.event_chains import materialize_chain_actions

        now = datetime.now(UTC)
        actions = [
            {"action_type": "prompt", "delay_minutes": 0, "prompt": "Immediate"},
            {"action_type": "job", "delay_minutes": 60, "job_name": "archive"},
        ]
        tasks = materialize_chain_actions(chain_name="test-chain", actions=actions, fired_at=now)

        assert abs((tasks[0]["next_run_at"] - now).total_seconds()) < 2
        expected_delayed = now + timedelta(minutes=60)
        assert abs((tasks[1]["next_run_at"] - expected_delayed).total_seconds()) < 2

    def test_materialized_tasks_have_until_at_for_auto_disable(self):
        """Each materialized task has until_at set to auto-disable after firing."""
        from butlers.core.temporal.event_chains import materialize_chain_actions

        now = datetime.now(UTC)
        actions = [{"action_type": "prompt", "delay_minutes": 0, "prompt": "Do it"}]
        tasks = materialize_chain_actions(chain_name="my-chain", actions=actions, fired_at=now)

        assert tasks[0]["until_at"] is not None
        # until_at should be shortly after next_run_at (1 minute per spec)
        assert tasks[0]["until_at"] > tasks[0]["next_run_at"]
        delta = tasks[0]["until_at"] - tasks[0]["next_run_at"]
        assert delta <= timedelta(minutes=2)

    def test_materialized_tasks_set_trigger_source(self):
        """Materialized tasks have trigger_source='chain:<chain-name>'."""
        from butlers.core.temporal.event_chains import materialize_chain_actions

        now = datetime.now(UTC)
        actions = [{"action_type": "prompt", "delay_minutes": 0, "prompt": "Go"}]
        tasks = materialize_chain_actions(chain_name="my-chain", actions=actions, fired_at=now)

        # trigger_source should be accessible in the materialized task dict
        assert tasks[0].get("trigger_source") == "chain:my-chain"

    def test_prompt_action_materialized_with_correct_dispatch_mode(self):
        """Prompt actions materialize as dispatch_mode='prompt'."""
        from butlers.core.temporal.event_chains import materialize_chain_actions

        now = datetime.now(UTC)
        actions = [{"action_type": "prompt", "delay_minutes": 0, "prompt": "Do something"}]
        tasks = materialize_chain_actions(chain_name="c", actions=actions, fired_at=now)
        assert tasks[0]["dispatch_mode"] == "prompt"
        assert tasks[0]["prompt"] == "Do something"

    def test_job_action_materialized_with_correct_dispatch_mode(self):
        """Job actions materialize as dispatch_mode='job'."""
        from butlers.core.temporal.event_chains import materialize_chain_actions

        now = datetime.now(UTC)
        actions = [
            {
                "action_type": "job",
                "delay_minutes": 60,
                "job_name": "archive_docs",
                "job_args": {"dry_run": False},
            }
        ]
        tasks = materialize_chain_actions(chain_name="c", actions=actions, fired_at=now)
        assert tasks[0]["dispatch_mode"] == "job"
        assert tasks[0]["job_name"] == "archive_docs"
        assert tasks[0]["job_args"] == {"dry_run": False}


# ---------------------------------------------------------------------------
# 12.6 — Event chain depth limit enforcement
# ---------------------------------------------------------------------------


class TestEventChainDepthLimit:
    """Tests for 3-level cascade depth limit.

    Covers scenarios from:
    - openspec/specs/event-chains/spec.md  §Event Chain Depth Limit
    - tasks.md §12.6
    """

    def test_chain_at_depth_0_fires_normally(self):
        """Chain at depth 0 (directly triggered) fires all actions."""
        from butlers.core.temporal.event_chains import should_fire_chain

        result = should_fire_chain(chain_depth=0)
        assert result is True

    def test_chain_at_depth_2_fires_normally(self):
        """Chain at depth 2 (below limit of 3) fires normally."""
        from butlers.core.temporal.event_chains import should_fire_chain

        result = should_fire_chain(chain_depth=2)
        assert result is True

    def test_chain_at_depth_3_is_skipped(self):
        """Chain at depth 3 (at limit) is skipped."""
        from butlers.core.temporal.event_chains import should_fire_chain

        result = should_fire_chain(chain_depth=3)
        assert result is False

    def test_chain_at_depth_4_is_skipped(self):
        """Chain at depth 4 (exceeds limit) is skipped."""
        from butlers.core.temporal.event_chains import should_fire_chain

        result = should_fire_chain(chain_depth=4)
        assert result is False

    def test_depth_exceeded_emits_warning(self, caplog):
        """Exceeding depth limit logs a warning with chain name and depth."""
        import logging

        from butlers.core.temporal.event_chains import should_fire_chain

        with caplog.at_level(logging.WARNING, logger="butlers.core.temporal.event_chains"):
            should_fire_chain(chain_depth=3, chain_name="cascade-chain")

        assert "cascade-chain" in caplog.text or "depth" in caplog.text.lower()


# ---------------------------------------------------------------------------
# 12.7 — Seasonal period active detection
# ---------------------------------------------------------------------------


class TestSeasonalPeriodActiveDetection:
    """Tests for get_active_seasons() logic.

    Covers scenarios from:
    - openspec/specs/seasonal-awareness/spec.md  §Active Period Detection
    - tasks.md §12.7
    """

    def _make_period(
        self,
        name: str,
        start_month: int,
        start_day: int,
        end_month: int,
        end_day: int,
        enabled: bool = True,
    ) -> dict[str, Any]:
        return {
            "id": str(uuid.uuid4()),
            "name": name,
            "start_month": start_month,
            "start_day": start_day,
            "end_month": end_month,
            "end_day": end_day,
            "enabled": enabled,
            "period_type": "annual",
            "butler_name": "test-butler",
        }

    def test_period_active_within_same_year(self):
        """Period is active when today falls within its date range (same year)."""
        from butlers.core.temporal.seasonal import is_period_active

        # Tax season: Jan 1 - Apr 15
        period = self._make_period("tax-season", 1, 1, 4, 15)
        # Test with a date in March
        today = date(2026, 3, 15)
        assert is_period_active(period, today=today) is True

    def test_period_inactive_outside_range(self):
        """Period is not active when today is outside its date range."""
        from butlers.core.temporal.seasonal import is_period_active

        period = self._make_period("tax-season", 1, 1, 4, 15)
        today = date(2026, 6, 1)  # June — outside Jan-Apr
        assert is_period_active(period, today=today) is False

    def test_period_wrapping_year_boundary_active_in_december(self):
        """Year-wrapping period (e.g., Nov 15 – Jan 10) is active in December."""
        from butlers.core.temporal.seasonal import is_period_active

        period = self._make_period("winter-holidays", 11, 15, 1, 10)
        today = date(2026, 12, 20)
        assert is_period_active(period, today=today) is True

    def test_period_wrapping_year_boundary_active_in_january(self):
        """Year-wrapping period is active in January (after the year boundary)."""
        from butlers.core.temporal.seasonal import is_period_active

        period = self._make_period("winter-holidays", 11, 15, 1, 10)
        today = date(2026, 1, 5)
        assert is_period_active(period, today=today) is True

    def test_period_wrapping_year_boundary_inactive_in_february(self):
        """Year-wrapping period is not active after the end date in January."""
        from butlers.core.temporal.seasonal import is_period_active

        period = self._make_period("winter-holidays", 11, 15, 1, 10)
        today = date(2026, 2, 1)  # February — outside Nov-Jan range
        assert is_period_active(period, today=today) is False

    def test_disabled_period_excluded(self):
        """Disabled period is not active regardless of date."""
        from butlers.core.temporal.seasonal import is_period_active

        period = self._make_period("tax-season", 1, 1, 4, 15, enabled=False)
        today = date(2026, 3, 15)  # Would be active if enabled
        assert is_period_active(period, today=today) is False

    def test_multiple_concurrent_periods_both_detected(self):
        """Multiple overlapping periods are all detected as active."""
        from butlers.core.temporal.seasonal import filter_active_periods

        periods = [
            self._make_period("tax-season", 1, 1, 4, 15),
            self._make_period("winter-holidays", 11, 15, 1, 31),
        ]
        # January 20: both tax-season (Jan 1 - Apr 15) and winter-holidays (Nov 15 - Jan 31) active
        today = date(2026, 1, 20)
        active = filter_active_periods(periods, today=today)
        names = [p["name"] for p in active]
        assert "tax-season" in names
        assert "winter-holidays" in names

    def test_disabled_period_excluded_from_filter(self):
        """filter_active_periods excludes disabled periods."""
        from butlers.core.temporal.seasonal import filter_active_periods

        periods = [
            self._make_period("tax-season", 1, 1, 4, 15),
            self._make_period("disabled-season", 1, 1, 4, 15, enabled=False),
        ]
        today = date(2026, 2, 1)
        active = filter_active_periods(periods, today=today)
        names = [p["name"] for p in active]
        assert "tax-season" in names
        assert "disabled-season" not in names

    def test_empty_periods_returns_empty_list(self):
        """No periods → empty active list."""
        from butlers.core.temporal.seasonal import filter_active_periods

        active = filter_active_periods([], today=date(2026, 6, 1))
        assert active == []

    def test_invalid_date_combination_rejected(self):
        """Invalid month/day combinations raise ValueError."""
        from butlers.core.temporal.seasonal import validate_period_dates

        with pytest.raises(ValueError, match="February|invalid|date"):
            validate_period_dates(start_month=2, start_day=30, end_month=3, end_day=15)

    def test_valid_period_dates_pass(self):
        """Valid period dates do not raise."""
        from butlers.core.temporal.seasonal import validate_period_dates

        validate_period_dates(start_month=1, start_day=1, end_month=4, end_day=15)


# ---------------------------------------------------------------------------
# 12.8 — Quiet hours enforcement
# ---------------------------------------------------------------------------


class TestQuietHoursEnforcement:
    """Tests for quiet hours check logic.

    Covers scenarios from:
    - openspec/specs/time-aware-delivery/spec.md  §Quiet Hours Enforcement
    - tasks.md §12.8
    """

    def _make_prefs(
        self,
        quiet_start: str = "22:00",
        quiet_end: str = "07:00",
        timezone: str = "UTC",
        override_channels: dict | None = None,
    ) -> dict[str, Any]:
        return {
            "quiet_hours_start": quiet_start,
            "quiet_hours_end": quiet_end,
            "timezone": timezone,
            "batch_low_priority": True,
            "batch_delivery_time": "07:00",
            "override_channels": override_channels or {},
        }

    def test_high_priority_bypasses_quiet_hours(self):
        """High-priority notifications always deliver, even during quiet hours."""
        from butlers.core.temporal.delivery import should_defer_notification

        prefs = self._make_prefs()
        # 23:30 UTC — in quiet hours (22:00-07:00)
        current_time = time(23, 30)
        assert not should_defer_notification(
            priority="high",
            current_time=current_time,
            prefs=prefs,
        )

    def test_medium_priority_deferred_during_quiet_hours(self):
        """Medium-priority notifications are deferred during quiet hours."""
        from butlers.core.temporal.delivery import should_defer_notification

        prefs = self._make_prefs()
        current_time = time(1, 0)  # 01:00 — inside quiet hours
        assert should_defer_notification(
            priority="medium",
            current_time=current_time,
            prefs=prefs,
        )

    def test_low_priority_deferred_during_quiet_hours(self):
        """Low-priority notifications are deferred during quiet hours."""
        from butlers.core.temporal.delivery import should_defer_notification

        prefs = self._make_prefs()
        current_time = time(3, 30)  # inside quiet hours
        assert should_defer_notification(
            priority="low",
            current_time=current_time,
            prefs=prefs,
        )

    def test_notification_delivered_outside_quiet_hours(self):
        """Any priority delivers immediately when outside quiet hours."""
        from butlers.core.temporal.delivery import should_defer_notification

        prefs = self._make_prefs()
        current_time = time(14, 0)  # 14:00 — outside quiet hours
        assert not should_defer_notification(
            priority="medium",
            current_time=current_time,
            prefs=prefs,
        )
        assert not should_defer_notification(
            priority="low",
            current_time=current_time,
            prefs=prefs,
        )

    def test_no_preferences_means_no_quiet_hours(self):
        """When no delivery preferences are configured, all notifications deliver immediately."""
        from butlers.core.temporal.delivery import should_defer_notification

        # None prefs = no quiet hours configuration
        assert not should_defer_notification(
            priority="medium",
            current_time=time(2, 0),
            prefs=None,
        )

    def test_is_in_quiet_hours_overnight(self):
        """is_in_quiet_hours handles overnight ranges (e.g., 22:00-07:00)."""
        from butlers.core.temporal.delivery import is_in_quiet_hours

        assert is_in_quiet_hours(
            current_time=time(23, 30),
            quiet_start=time(22, 0),
            quiet_end=time(7, 0),
        )
        assert is_in_quiet_hours(
            current_time=time(0, 30),
            quiet_start=time(22, 0),
            quiet_end=time(7, 0),
        )
        assert is_in_quiet_hours(
            current_time=time(6, 59),
            quiet_start=time(22, 0),
            quiet_end=time(7, 0),
        )

    def test_is_in_quiet_hours_outside_overnight_range(self):
        """is_in_quiet_hours returns False for daytime when quiet hours are overnight."""
        from butlers.core.temporal.delivery import is_in_quiet_hours

        assert not is_in_quiet_hours(
            current_time=time(14, 0),
            quiet_start=time(22, 0),
            quiet_end=time(7, 0),
        )
        assert not is_in_quiet_hours(
            current_time=time(7, 1),
            quiet_start=time(22, 0),
            quiet_end=time(7, 0),
        )

    def test_is_in_quiet_hours_same_day_range(self):
        """is_in_quiet_hours handles same-day ranges (e.g., 08:00-12:00)."""
        from butlers.core.temporal.delivery import is_in_quiet_hours

        assert is_in_quiet_hours(
            current_time=time(10, 0),
            quiet_start=time(8, 0),
            quiet_end=time(12, 0),
        )
        assert not is_in_quiet_hours(
            current_time=time(13, 0),
            quiet_start=time(8, 0),
            quiet_end=time(12, 0),
        )

    def test_deliver_at_computed_as_next_batch_time(self):
        """deliver_at is computed as the next occurrence of batch_delivery_time."""
        from butlers.core.temporal.delivery import compute_deliver_at

        prefs = self._make_prefs(timezone="UTC")
        # At 23:00 UTC, next batch time (07:00) is tomorrow
        now = datetime(2026, 1, 15, 23, 0, tzinfo=UTC)
        deliver_at = compute_deliver_at(prefs=prefs, now=now)
        assert deliver_at.date() == date(2026, 1, 16)
        assert deliver_at.hour == 7
        assert deliver_at.minute == 0

    def test_deliver_at_is_same_day_when_batch_time_still_ahead(self):
        """When batch time is still ahead today, deliver_at is today."""
        from butlers.core.temporal.delivery import compute_deliver_at

        prefs = self._make_prefs(timezone="UTC")
        # At 04:00 UTC, next 07:00 is later today
        now = datetime(2026, 1, 15, 4, 0, tzinfo=UTC)
        deliver_at = compute_deliver_at(prefs=prefs, now=now)
        assert deliver_at.date() == date(2026, 1, 15)
        assert deliver_at.hour == 7

    def test_deliver_at_uses_user_timezone_for_next_occurrence(self):
        """compute_deliver_at computes batch time in user's timezone, not UTC.

        America/New_York is UTC-5 in January (EST).
        If now is 2026-01-15 10:00 UTC (= 05:00 EST), and batch_delivery_time is 07:00,
        the next 07:00 EST is still today → 07:00 EST = 12:00 UTC.
        """
        from zoneinfo import ZoneInfo

        from butlers.core.temporal.delivery import compute_deliver_at

        prefs = self._make_prefs(timezone="America/New_York")
        prefs["batch_delivery_time"] = "07:00"
        # 10:00 UTC = 05:00 EST — batch time (07:00 EST) is still ahead today
        now = datetime(2026, 1, 15, 10, 0, tzinfo=UTC)
        deliver_at = compute_deliver_at(prefs=prefs, now=now)

        # Result should be 07:00 EST = 12:00 UTC on Jan 15
        expected_local = datetime(2026, 1, 15, 7, 0, tzinfo=ZoneInfo("America/New_York"))
        expected_utc = expected_local.astimezone(UTC)
        assert deliver_at == expected_utc

    def test_deliver_at_schedules_tomorrow_in_user_timezone_when_batch_time_passed(self):
        """When batch_delivery_time has already passed in user's timezone, schedule tomorrow.

        America/New_York is UTC-5 in January (EST).
        If now is 2026-01-15 16:00 UTC (= 11:00 EST), and batch_delivery_time is 07:00,
        07:00 EST has already passed today → next occurrence is 2026-01-16 07:00 EST = 12:00 UTC.
        """
        from zoneinfo import ZoneInfo

        from butlers.core.temporal.delivery import compute_deliver_at

        prefs = self._make_prefs(timezone="America/New_York")
        prefs["batch_delivery_time"] = "07:00"
        # 16:00 UTC = 11:00 EST — batch time (07:00 EST) already passed today
        now = datetime(2026, 1, 15, 16, 0, tzinfo=UTC)
        deliver_at = compute_deliver_at(prefs=prefs, now=now)

        # Result should be 07:00 EST on Jan 16 = 12:00 UTC on Jan 16
        expected_local = datetime(2026, 1, 16, 7, 0, tzinfo=ZoneInfo("America/New_York"))
        expected_utc = expected_local.astimezone(UTC)
        assert deliver_at == expected_utc

    def test_deliver_at_with_positive_utc_offset_timezone(self):
        """compute_deliver_at handles positive-offset timezones (e.g., Asia/Tokyo UTC+9).

        If now is 2026-01-15 23:00 UTC (= 2026-01-16 08:00 JST), and batch_delivery_time is 09:00,
        08:00 JST is before 09:00 JST — batch time still ahead today, so deliver today.
        """
        from zoneinfo import ZoneInfo

        from butlers.core.temporal.delivery import compute_deliver_at

        prefs = self._make_prefs(timezone="Asia/Tokyo")
        prefs["batch_delivery_time"] = "09:00"
        # 2026-01-15 23:00 UTC = 2026-01-16 08:00 JST — batch time (09:00 JST) still ahead
        now = datetime(2026, 1, 15, 23, 0, tzinfo=UTC)
        deliver_at = compute_deliver_at(prefs=prefs, now=now)

        # Result should be 09:00 JST on Jan 16 = 00:00 UTC on Jan 16
        expected_local = datetime(2026, 1, 16, 9, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
        expected_utc = expected_local.astimezone(UTC)
        assert deliver_at == expected_utc

    def test_deliver_at_result_is_utc_aware(self):
        """compute_deliver_at always returns a UTC-aware datetime."""
        from butlers.core.temporal.delivery import compute_deliver_at

        prefs = self._make_prefs(timezone="America/Los_Angeles")
        now = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
        deliver_at = compute_deliver_at(prefs=prefs, now=now)

        assert deliver_at.tzinfo is not None
        # Result must be UTC (zero UTC offset), not just any timezone-aware datetime
        utc_offset = deliver_at.utcoffset()
        assert utc_offset is not None
        assert utc_offset.total_seconds() == 0, (
            f"Expected UTC result (zero offset) but got {deliver_at.tzinfo!r}"
            f" with offset {utc_offset}"
        )

    def test_deliver_at_raises_for_invalid_timezone(self):
        """compute_deliver_at raises ValueError for unrecognized timezone names."""
        from butlers.core.temporal.delivery import compute_deliver_at

        prefs = self._make_prefs(timezone="Invalid/Zone")
        now = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
        with pytest.raises(ValueError, match="Unknown timezone"):
            compute_deliver_at(prefs=prefs, now=now)

    def test_deliver_at_raises_for_naive_now(self):
        """compute_deliver_at raises ValueError when now is a naive datetime."""
        from butlers.core.temporal.delivery import compute_deliver_at

        prefs = self._make_prefs(timezone="UTC")
        now_naive = datetime(2026, 1, 15, 12, 0)  # no tzinfo
        with pytest.raises(ValueError, match="timezone-aware"):
            compute_deliver_at(prefs=prefs, now=now_naive)


# ---------------------------------------------------------------------------
# 12.9 — Deferred notification flush, expiry, retry
# ---------------------------------------------------------------------------


class TestDeferredNotificationFlush:
    """Tests for deferred notification flush logic.

    Covers scenarios from:
    - openspec/specs/time-aware-delivery/spec.md  §Deferred Notification Flush
    - tasks.md §12.9
    """

    def _make_deferred(
        self,
        status: str = "pending",
        deliver_at: datetime | None = None,
        deferred_at: datetime | None = None,
    ) -> dict[str, Any]:
        now = datetime.now(UTC)
        return {
            "id": str(uuid.uuid4()),
            "butler_name": "test-butler",
            "channel": "telegram",
            "message": "Test message",
            "priority": "medium",
            "envelope": {"schema_version": "notify.v1"},
            "deferred_at": deferred_at or now - timedelta(hours=2),
            "deliver_at": deliver_at or now - timedelta(minutes=5),
            "status": status,
            "delivered_at": None,
        }

    def test_pending_notification_past_deliver_at_is_due(self):
        """Notification with deliver_at <= now and status=pending is due for delivery."""
        from butlers.core.temporal.delivery import is_notification_due

        notif = self._make_deferred(
            status="pending",
            deliver_at=datetime.now(UTC) - timedelta(minutes=1),
        )
        assert is_notification_due(notif, now=datetime.now(UTC)) is True

    def test_pending_notification_before_deliver_at_is_not_due(self):
        """Notification with future deliver_at is not yet due."""
        from butlers.core.temporal.delivery import is_notification_due

        notif = self._make_deferred(
            status="pending",
            deliver_at=datetime.now(UTC) + timedelta(hours=2),
        )
        assert is_notification_due(notif, now=datetime.now(UTC)) is False

    def test_delivered_notification_is_not_due(self):
        """Notification with status=delivered is not due."""
        from butlers.core.temporal.delivery import is_notification_due

        notif = self._make_deferred(
            status="delivered",
            deliver_at=datetime.now(UTC) - timedelta(minutes=1),
        )
        assert is_notification_due(notif, now=datetime.now(UTC)) is False

    def test_expired_notification_is_not_due(self):
        """Notification with status=expired is not delivered."""
        from butlers.core.temporal.delivery import is_notification_due

        notif = self._make_deferred(
            status="expired",
            deliver_at=datetime.now(UTC) - timedelta(hours=2),
        )
        assert is_notification_due(notif, now=datetime.now(UTC)) is False

    def test_notification_older_than_24h_past_deliver_at_is_expired(self):
        """Notification pending for > 24h past deliver_at is expired."""
        from butlers.core.temporal.delivery import is_notification_expired

        now = datetime.now(UTC)
        notif = self._make_deferred(
            status="pending",
            deliver_at=now - timedelta(hours=25),
        )
        assert is_notification_expired(notif, now=now) is True

    def test_notification_within_24h_of_deliver_at_not_expired(self):
        """Notification pending for < 24h past deliver_at is not expired."""
        from butlers.core.temporal.delivery import is_notification_expired

        now = datetime.now(UTC)
        notif = self._make_deferred(
            status="pending",
            deliver_at=now - timedelta(hours=23),
        )
        assert is_notification_expired(notif, now=now) is False

    def test_failed_delivery_leaves_status_pending(self):
        """A failed delivery attempt keeps status='pending' for next-tick retry."""
        from butlers.core.temporal.delivery import compute_delivery_result

        result = compute_delivery_result(delivery_succeeded=False)
        assert result["status"] == "pending"
        assert result.get("delivered_at") is None

    def test_successful_delivery_sets_status_delivered(self):
        """Successful delivery sets status='delivered' and delivered_at."""
        from butlers.core.temporal.delivery import compute_delivery_result

        now = datetime.now(UTC)
        result = compute_delivery_result(delivery_succeeded=True, delivered_at=now)
        assert result["status"] == "delivered"
        assert result["delivered_at"] == now

    def test_filter_due_notifications(self):
        """filter_due_notifications returns only pending notifications past deliver_at."""
        from butlers.core.temporal.delivery import filter_due_notifications

        now = datetime.now(UTC)
        notifications = [
            self._make_deferred(status="pending", deliver_at=now - timedelta(minutes=5)),
            self._make_deferred(status="pending", deliver_at=now + timedelta(hours=1)),
            self._make_deferred(status="delivered", deliver_at=now - timedelta(minutes=1)),
            self._make_deferred(status="expired", deliver_at=now - timedelta(hours=30)),
        ]
        due = filter_due_notifications(notifications, now=now)
        assert len(due) == 1
        assert due[0]["status"] == "pending"

    def test_filter_expired_notifications(self):
        """filter_expired_notifications returns pending notifications > 24h past deliver_at."""
        from butlers.core.temporal.delivery import filter_expired_notifications

        now = datetime.now(UTC)
        notifications = [
            self._make_deferred(status="pending", deliver_at=now - timedelta(hours=25)),
            self._make_deferred(status="pending", deliver_at=now - timedelta(hours=10)),
            self._make_deferred(status="delivered", deliver_at=now - timedelta(hours=30)),
        ]
        expired = filter_expired_notifications(notifications, now=now)
        assert len(expired) == 1
        assert expired[0]["deliver_at"] < now - timedelta(hours=24)


# ---------------------------------------------------------------------------
# 12.10 — Per-channel quiet hours overrides
# ---------------------------------------------------------------------------


class TestPerChannelQuietHoursOverrides:
    """Tests for per-channel override resolution in delivery preferences.

    Covers scenarios from:
    - openspec/specs/time-aware-delivery/spec.md  §Per-Channel Quiet Hours Override
    - tasks.md §12.10
    """

    def test_channel_override_applied_when_present(self):
        """Email-specific quiet hours override is applied when notify channel is email."""
        from butlers.core.temporal.delivery import resolve_effective_quiet_hours

        prefs = {
            "quiet_hours_start": "22:00",
            "quiet_hours_end": "07:00",
            "timezone": "UTC",
            "override_channels": {
                "email": {"quiet_hours_start": "20:00", "quiet_hours_end": "09:00"}
            },
        }
        effective = resolve_effective_quiet_hours(channel="email", prefs=prefs)
        assert effective["quiet_hours_start"] == "20:00"
        assert effective["quiet_hours_end"] == "09:00"

    def test_default_quiet_hours_used_when_no_channel_override(self):
        """Default quiet hours apply when the specific channel has no override."""
        from butlers.core.temporal.delivery import resolve_effective_quiet_hours

        prefs = {
            "quiet_hours_start": "22:00",
            "quiet_hours_end": "07:00",
            "timezone": "UTC",
            "override_channels": {
                "email": {"quiet_hours_start": "20:00", "quiet_hours_end": "09:00"}
            },
        }
        effective = resolve_effective_quiet_hours(channel="telegram", prefs=prefs)
        assert effective["quiet_hours_start"] == "22:00"
        assert effective["quiet_hours_end"] == "07:00"

    def test_channel_override_defers_email_during_override_hours(self):
        """Email notification deferred when within email-specific quiet hours."""
        from butlers.core.temporal.delivery import should_defer_notification

        prefs = {
            "quiet_hours_start": "22:00",
            "quiet_hours_end": "07:00",
            "timezone": "UTC",
            "batch_low_priority": True,
            "batch_delivery_time": "07:00",
            "override_channels": {
                "email": {"quiet_hours_start": "20:00", "quiet_hours_end": "09:00"}
            },
        }
        # 21:00 — outside default quiet hours but inside email override (20:00-09:00)
        assert should_defer_notification(
            priority="medium",
            current_time=time(21, 0),
            prefs=prefs,
            channel="email",
        )

    def test_telegram_uses_default_when_email_override_exists(self):
        """Telegram uses default quiet hours even when email has override."""
        from butlers.core.temporal.delivery import should_defer_notification

        prefs = {
            "quiet_hours_start": "22:00",
            "quiet_hours_end": "07:00",
            "timezone": "UTC",
            "batch_low_priority": True,
            "batch_delivery_time": "07:00",
            "override_channels": {
                "email": {"quiet_hours_start": "20:00", "quiet_hours_end": "09:00"}
            },
        }
        # 21:00 — inside email override but outside default quiet hours (22:00-07:00)
        assert not should_defer_notification(
            priority="medium",
            current_time=time(21, 0),
            prefs=prefs,
            channel="telegram",
        )

    def test_no_override_channels_uses_defaults(self):
        """When override_channels is empty/None, defaults apply for all channels."""
        from butlers.core.temporal.delivery import resolve_effective_quiet_hours

        prefs = {
            "quiet_hours_start": "22:00",
            "quiet_hours_end": "07:00",
            "timezone": "UTC",
            "override_channels": {},
        }
        effective = resolve_effective_quiet_hours(channel="email", prefs=prefs)
        assert effective["quiet_hours_start"] == "22:00"
        assert effective["quiet_hours_end"] == "07:00"


# ---------------------------------------------------------------------------
# 12.11 — tick() integration with all three new passes
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
class TestTickIntegration:
    """Integration tests for tick() with deadline, event chain, and deferred notification passes.

    These tests require Docker (testcontainers) for a real PostgreSQL instance.

    Covers scenarios from:
    - openspec/specs/core-scheduler/spec.md  §Tick Handler (extended passes)
    - tasks.md §12.11
    """

    @pytest.fixture(autouse=True)
    def reset_otel_state(self):
        """Reset OpenTelemetry global tracer provider state before and after each test.

        This ensures each test that sets its own TracerProvider can do so cleanly,
        regardless of test execution order in the same worker process.

        Uses unittest.mock.patch to reset the private OTel state rather than directly
        mutating private attributes, which improves resilience across OTel SDK versions.
        """
        import unittest.mock

        from opentelemetry import trace

        with (
            unittest.mock.patch.object(trace, "_TRACER_PROVIDER_SET_ONCE", trace.Once()),
            unittest.mock.patch.object(trace, "_TRACER_PROVIDER", None),
        ):
            yield

    # DDL for temporal intelligence tables
    _SCHEDULED_TASKS_DDL = """
        CREATE TABLE IF NOT EXISTS scheduled_tasks (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name TEXT UNIQUE NOT NULL,
            cron TEXT NOT NULL DEFAULT '* * * * *',
            prompt TEXT,
            dispatch_mode TEXT NOT NULL DEFAULT 'prompt',
            job_name TEXT,
            job_args JSONB,
            complexity TEXT DEFAULT 'medium',
            timezone TEXT NOT NULL DEFAULT 'UTC',
            start_at TIMESTAMPTZ,
            end_at TIMESTAMPTZ,
            until_at TIMESTAMPTZ,
            display_title TEXT,
            calendar_event_id TEXT,
            source TEXT NOT NULL DEFAULT 'db',
            enabled BOOLEAN NOT NULL DEFAULT true,
            next_run_at TIMESTAMPTZ,
            last_run_at TIMESTAMPTZ,
            last_result JSONB,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            -- Temporal intelligence columns
            task_type TEXT NOT NULL DEFAULT 'cron',
            target_date DATE,
            lead_time_days INTEGER,
            alert_thresholds JSONB,
            deadline_status TEXT,
            fired_thresholds JSONB,
            depends_on JSONB
        )
    """

    _EVENT_CHAINS_DDL = """
        CREATE TABLE IF NOT EXISTS event_chains (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name TEXT NOT NULL,
            trigger_type TEXT NOT NULL,
            trigger_reference TEXT NOT NULL,
            actions JSONB NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            butler_name TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """

    _DEFERRED_NOTIFICATIONS_DDL = """
        CREATE TABLE IF NOT EXISTS deferred_notifications (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            butler_name TEXT NOT NULL,
            channel TEXT NOT NULL,
            message TEXT NOT NULL,
            priority TEXT NOT NULL DEFAULT 'medium',
            envelope JSONB NOT NULL,
            deferred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            deliver_at TIMESTAMPTZ NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            delivered_at TIMESTAMPTZ
        )
    """

    _SEASONAL_PERIODS_DDL = """
        CREATE TABLE IF NOT EXISTS seasonal_periods (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name TEXT NOT NULL,
            period_type TEXT NOT NULL DEFAULT 'annual',
            start_month INTEGER NOT NULL,
            start_day INTEGER NOT NULL,
            end_month INTEGER NOT NULL,
            end_day INTEGER NOT NULL,
            timezone TEXT NOT NULL DEFAULT 'UTC',
            metadata JSONB,
            butler_name TEXT NOT NULL,
            enabled BOOLEAN NOT NULL DEFAULT true
        )
    """

    @pytest.fixture
    async def pool(self, postgres_container):
        """Create a fresh database with temporal intelligence tables."""
        import asyncpg

        db_name = f"test_{uuid.uuid4().hex[:12]}"
        admin_conn = await asyncpg.connect(
            host=postgres_container.get_container_host_ip(),
            port=int(postgres_container.get_exposed_port(5432)),
            user=postgres_container.username,
            password=postgres_container.password,
            database="postgres",
        )
        try:
            await admin_conn.execute(f'CREATE DATABASE "{db_name}"')
        finally:
            await admin_conn.close()

        p = await asyncpg.create_pool(
            host=postgres_container.get_container_host_ip(),
            port=int(postgres_container.get_exposed_port(5432)),
            user=postgres_container.username,
            password=postgres_container.password,
            database=db_name,
            min_size=1,
            max_size=3,
        )
        await p.execute(self._SCHEDULED_TASKS_DDL)
        await p.execute(self._EVENT_CHAINS_DDL)
        await p.execute(self._DEFERRED_NOTIFICATIONS_DDL)
        await p.execute(self._SEASONAL_PERIODS_DDL)
        yield p
        await p.close()

    async def test_tick_deadline_pass_fires_due_threshold(self, pool):
        """tick() deadline pass dispatches a deadline task whose threshold is newly due."""
        from butlers.core.scheduler import tick

        # Insert a deadline task whose 30-day threshold is due now
        now = datetime.now(UTC)
        target = _future_date(30)
        await pool.fetchval(
            """
            INSERT INTO scheduled_tasks
                (name, cron, prompt, dispatch_mode, task_type, target_date,
                 lead_time_days, alert_thresholds, deadline_status, fired_thresholds,
                 next_run_at, enabled)
            VALUES
                ($1, '* * * * *', $2, 'prompt', 'deadline', $3, 30,
                 '[{"days_before": 30, "severity": "info"}]',
                 'pending', '[]', $4, true)
            RETURNING id
            """,
            "test-deadline",
            "Review visa renewal checklist",
            target,
            now,  # due now
        )

        dispatched_calls = []

        async def capture_dispatch(**kwargs):
            dispatched_calls.append(kwargs)

        await tick(pool, capture_dispatch)

        # The deadline should have been dispatched
        dispatched_prompts = [c.get("prompt", "") for c in dispatched_calls]
        assert any("visa" in p.lower() or "Review" in p for p in dispatched_prompts), (
            f"Expected deadline dispatch, got: {dispatched_calls}"
        )

    async def test_tick_deadline_pass_sets_span_attribute(self, pool):
        """tick() sets 'deadlines_evaluated' span attribute."""
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

        from butlers.core.scheduler import tick

        exporter = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        trace.set_tracer_provider(provider)

        async def noop_dispatch(**kwargs):
            pass

        await tick(pool, noop_dispatch)

        spans = exporter.get_finished_spans()
        tick_span = next((s for s in spans if s.name == "butler.tick"), None)
        assert tick_span is not None, "butler.tick span not found"
        attrs = dict(tick_span.attributes or {})
        assert "deadlines_evaluated" in attrs

    async def test_tick_deferred_notification_pass_delivers_due_notifications(self, pool):
        """tick() deferred notification pass delivers pending notifications past deliver_at."""
        from butlers.core.scheduler import tick

        now = datetime.now(UTC)
        # Insert a deferred notification that should be delivered
        notif_id = await pool.fetchval(
            """
            INSERT INTO deferred_notifications
                (butler_name, channel, message, priority, envelope, deliver_at, status)
            VALUES
                ('test-butler', 'telegram', 'Queued message', 'medium',
                 '{"schema_version": "notify.v1"}'::jsonb, $1, 'pending')
            RETURNING id
            """,
            now - timedelta(minutes=5),  # 5 minutes ago — past deliver_at
        )

        deliver_calls = []

        async def capture_dispatch(**kwargs):
            deliver_calls.append(kwargs)

        await tick(pool, capture_dispatch)

        # The deferred notification should be marked delivered
        row = await pool.fetchrow(
            "SELECT status, delivered_at FROM deferred_notifications WHERE id = $1", notif_id
        )
        assert row["status"] == "delivered", f"Expected 'delivered', got {row['status']!r}"
        assert row["delivered_at"] is not None

    async def test_tick_deferred_notification_pass_sets_span_attribute(self, pool):
        """tick() sets 'deferred_flushed' span attribute."""
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

        from butlers.core.scheduler import tick

        exporter = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        trace.set_tracer_provider(provider)

        async def noop_dispatch(**kwargs):
            pass

        await tick(pool, noop_dispatch)

        spans = exporter.get_finished_spans()
        tick_span = next((s for s in spans if s.name == "butler.tick"), None)
        assert tick_span is not None
        attrs = dict(tick_span.attributes or {})
        assert "deferred_flushed" in attrs

    async def test_tick_event_chain_pass_fires_calendar_triggered_chain(self, pool):
        """tick() event chain pass fires a chain when its calendar event has ended."""
        from butlers.core.scheduler import tick

        now = datetime.now(UTC)
        # Insert a calendar projection event that ended 2 minutes ago
        await pool.execute(
            """
            CREATE TABLE IF NOT EXISTS calendar_projection (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                butler_name TEXT NOT NULL,
                event_id TEXT NOT NULL,
                title TEXT,
                start_at TIMESTAMPTZ,
                end_at TIMESTAMPTZ,
                chain_triggered BOOLEAN NOT NULL DEFAULT false
            )
            """
        )
        event_id = "google-event-abc123"
        await pool.execute(
            """
            INSERT INTO calendar_projection
                (butler_name, event_id, title, start_at, end_at, chain_triggered)
            VALUES
                ('test-butler', $1, 'Dentist Appointment',
                 $2, $3, false)
            """,
            event_id,
            now - timedelta(hours=1),
            now - timedelta(minutes=2),  # ended 2 min ago
        )

        # Insert an event chain triggered by calendar_event_end for this event
        await pool.execute(
            """
            INSERT INTO event_chains
                (name, trigger_type, trigger_reference, actions, status, butler_name)
            VALUES
                ('post-dentist', 'calendar_event_end', $1,
                 '[{"action_type": "prompt", "delay_minutes": 0, "prompt": "Log dentist visit"}]',
                 'active', 'test-butler')
            """,
            event_id,
        )

        dispatch_calls = []

        async def capture_dispatch(**kwargs):
            dispatch_calls.append(kwargs)

        await tick(pool, capture_dispatch)

        # The chain actions should have been materialized as tasks and/or dispatched
        chain_row = await pool.fetchrow(
            "SELECT status FROM event_chains WHERE name = 'post-dentist'"
        )
        assert chain_row["status"] == "fired", (
            f"Expected chain status='fired', got {chain_row['status']!r}"
        )

    async def test_tick_event_chain_pass_sets_span_attribute(self, pool):
        """tick() sets 'chains_fired' span attribute."""
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

        from butlers.core.scheduler import tick

        exporter = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        trace.set_tracer_provider(provider)

        async def noop_dispatch(**kwargs):
            pass

        await tick(pool, noop_dispatch)

        spans = exporter.get_finished_spans()
        tick_span = next((s for s in spans if s.name == "butler.tick"), None)
        assert tick_span is not None
        attrs = dict(tick_span.attributes or {})
        assert "chains_fired" in attrs

    async def test_tick_cron_tasks_unaffected_by_new_passes(self, pool):
        """Existing cron tasks still dispatch normally after new passes are added."""
        from butlers.core.scheduler import tick

        now = datetime.now(UTC)
        await pool.execute(
            """
            INSERT INTO scheduled_tasks
                (name, cron, prompt, dispatch_mode, task_type, next_run_at, enabled)
            VALUES
                ('regular-cron-task', '* * * * *', 'Daily morning summary',
                 'prompt', 'cron', $1, true)
            """,
            now,
        )

        dispatch_calls = []

        async def capture_dispatch(**kwargs):
            dispatch_calls.append(kwargs)

        result = await tick(pool, capture_dispatch)
        assert result >= 1
        prompts = [c.get("prompt", "") for c in dispatch_calls]
        assert any("morning summary" in p for p in prompts)

    async def test_tick_deadline_expiry_disables_task(self, pool):
        """tick() marks a past-target_date deadline as expired and disabled."""
        from butlers.core.scheduler import tick

        now = datetime.now(UTC)
        past_target = _past_date(1)
        await pool.execute(
            """
            INSERT INTO scheduled_tasks
                (name, cron, prompt, dispatch_mode, task_type, target_date,
                 lead_time_days, alert_thresholds, deadline_status, fired_thresholds,
                 next_run_at, enabled)
            VALUES
                ('expired-deadline', '* * * * *', 'Renew passport', 'prompt', 'deadline', $1,
                 30, '[{"days_before": 30, "severity": "info"}]',
                 'alerted', '[{"days_before": 30, "severity": "info"}]', $2, true)
            """,
            past_target,
            now,
        )

        async def noop_dispatch(**kwargs):
            pass

        await tick(pool, noop_dispatch)

        row = await pool.fetchrow(
            "SELECT deadline_status, enabled FROM scheduled_tasks WHERE name = 'expired-deadline'"
        )
        assert row["deadline_status"] == "expired"
        assert row["enabled"] is False

    async def test_tick_deferred_notification_expiry(self, pool):
        """tick() marks old pending deferred notifications as expired (not delivered)."""
        from butlers.core.scheduler import tick

        now = datetime.now(UTC)
        # A notification 25 hours past its deliver_at
        notif_id = await pool.fetchval(
            """
            INSERT INTO deferred_notifications
                (butler_name, channel, message, priority, envelope, deliver_at, status)
            VALUES
                ('test-butler', 'telegram', 'Very old message', 'low',
                 '{"schema_version": "notify.v1"}'::jsonb, $1, 'pending')
            RETURNING id
            """,
            now - timedelta(hours=25),
        )

        async def noop_dispatch(**kwargs):
            pass

        await tick(pool, noop_dispatch)

        row = await pool.fetchrow(
            "SELECT status FROM deferred_notifications WHERE id = $1", notif_id
        )
        assert row["status"] == "expired"

    async def test_sync_schedules_inserts_deadline_task(self, pool):
        """sync_schedules inserts a deadline entry with all temporal fields."""
        from butlers.core.scheduler import sync_schedules

        target = _future_date(60)
        await sync_schedules(
            pool,
            [
                {
                    "name": "visa-renewal",
                    "cron": "0 9 * * *",
                    "task_type": "deadline",
                    "prompt": "Check visa renewal status and notify",
                    "dispatch_mode": "prompt",
                    "target_date": target.isoformat(),
                    "lead_time_days": 60,
                    "alert_thresholds": [
                        {"days_before": 60, "severity": "info"},
                        {"days_before": 30, "severity": "warning"},
                    ],
                }
            ],
        )

        row = await pool.fetchrow(
            """
            SELECT task_type, target_date, lead_time_days, alert_thresholds
            FROM scheduled_tasks WHERE name = 'visa-renewal'
            """
        )
        assert row is not None
        assert row["task_type"] == "deadline"
        assert row["target_date"] == target
        assert row["lead_time_days"] == 60
        thresholds = row["alert_thresholds"]
        if isinstance(thresholds, str):
            import json as _json

            thresholds = _json.loads(thresholds)
        assert len(thresholds) == 2

    async def test_sync_schedules_deadline_missing_target_date_raises(self, pool):
        """sync_schedules raises ValueError when task_type='deadline' has no target_date."""
        from butlers.core.scheduler import sync_schedules

        with pytest.raises(ValueError, match="target_date"):
            await sync_schedules(
                pool,
                [
                    {
                        "name": "bad-deadline",
                        "cron": "0 9 * * *",
                        "task_type": "deadline",
                        "prompt": "Some prompt calling notify",
                        "dispatch_mode": "prompt",
                        "lead_time_days": 30,
                        "alert_thresholds": [{"days_before": 30, "severity": "info"}],
                    }
                ],
            )

    async def test_sync_schedules_cron_task_type_works_unchanged(self, pool):
        """sync_schedules still inserts plain cron tasks when task_type='cron'."""
        from butlers.core.scheduler import sync_schedules

        await sync_schedules(
            pool,
            [
                {
                    "name": "morning-summary",
                    "cron": "0 8 * * *",
                    "task_type": "cron",
                    "prompt": "Morning briefing — call notify to send summary",
                    "dispatch_mode": "prompt",
                }
            ],
        )

        row = await pool.fetchrow(
            "SELECT task_type FROM scheduled_tasks WHERE name = 'morning-summary'"
        )
        assert row is not None
        # task_type column exists in this test DDL so it should be 'cron'
        assert row["task_type"] == "cron"
