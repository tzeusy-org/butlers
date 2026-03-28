"""Unit tests for deadline pure functions in butlers.core.temporal.deadlines.

Covers tasks 12.1-12.4 from openspec/changes/temporal-intelligence/tasks.md:
  12.1 Deadline validation (future date, threshold bounds, non-empty thresholds)
  12.2 Deadline countdown computation and threshold firing
  12.3 Deadline status state machine transitions
  12.4 Deadline dependencies blocking threshold evaluation

All tests are pure unit tests — no Docker or DB required.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

pytestmark = [pytest.mark.unit]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _future_date(days: int = 30) -> date:
    return (datetime.now(UTC) + timedelta(days=days)).date()


def _past_date(days: int = 1) -> date:
    return (datetime.now(UTC) - timedelta(days=days)).date()


def _threshold(days_before: int, severity: str = "info") -> dict[str, Any]:
    return {"days_before": days_before, "severity": severity}


# ---------------------------------------------------------------------------
# 12.1 — Deadline validation
# ---------------------------------------------------------------------------


class TestDeadlineValidation:
    """validate_deadline_input — field-level validation."""

    def test_valid_inputs_do_not_raise(self):
        from butlers.core.temporal.deadlines import validate_deadline_input

        validate_deadline_input(
            target_date=_future_date(60),
            lead_time_days=42,
            alert_thresholds=[
                _threshold(42, "info"),
                _threshold(14, "warning"),
                _threshold(3, "critical"),
            ],
        )

    def test_past_target_date_raises(self):
        from butlers.core.temporal.deadlines import validate_deadline_input

        with pytest.raises(ValueError, match="future"):
            validate_deadline_input(
                target_date=_past_date(10),
                lead_time_days=7,
                alert_thresholds=[_threshold(7)],
            )

    def test_today_target_date_raises(self):
        """target_date == today is rejected (must be strictly future)."""
        from butlers.core.temporal.deadlines import validate_deadline_input

        with pytest.raises(ValueError, match="future"):
            validate_deadline_input(
                target_date=datetime.now(UTC).date(),
                lead_time_days=1,
                alert_thresholds=[_threshold(1)],
            )

    def test_empty_thresholds_raises(self):
        from butlers.core.temporal.deadlines import validate_deadline_input

        with pytest.raises(ValueError, match="threshold"):
            validate_deadline_input(
                target_date=_future_date(30),
                lead_time_days=30,
                alert_thresholds=[],
            )

    def test_threshold_days_before_exceeds_lead_time_raises(self):
        from butlers.core.temporal.deadlines import validate_deadline_input

        with pytest.raises(ValueError, match="lead_time_days|days_before"):
            validate_deadline_input(
                target_date=_future_date(60),
                lead_time_days=14,
                alert_thresholds=[_threshold(30, "info")],
            )

    def test_threshold_days_before_equal_lead_time_is_valid(self):
        """days_before == lead_time_days is allowed (boundary case)."""
        from butlers.core.temporal.deadlines import validate_deadline_input

        validate_deadline_input(
            target_date=_future_date(60),
            lead_time_days=14,
            alert_thresholds=[_threshold(14, "info")],
        )

    def test_any_threshold_exceeding_lead_time_raises(self):
        """All thresholds are checked; one invalid threshold triggers error."""
        from butlers.core.temporal.deadlines import validate_deadline_input

        with pytest.raises(ValueError):
            validate_deadline_input(
                target_date=_future_date(60),
                lead_time_days=14,
                alert_thresholds=[
                    _threshold(14, "info"),
                    _threshold(7, "warning"),
                    _threshold(30, "critical"),  # invalid
                ],
            )

    def test_zero_lead_time_raises(self):
        from butlers.core.temporal.deadlines import validate_deadline_input

        with pytest.raises(ValueError):
            validate_deadline_input(
                target_date=_future_date(60),
                lead_time_days=0,
                alert_thresholds=[_threshold(1)],
            )

    def test_negative_lead_time_raises(self):
        from butlers.core.temporal.deadlines import validate_deadline_input

        with pytest.raises(ValueError):
            validate_deadline_input(
                target_date=_future_date(60),
                lead_time_days=-5,
                alert_thresholds=[_threshold(1)],
            )

    def test_threshold_missing_days_before_raises(self):
        from butlers.core.temporal.deadlines import validate_deadline_input

        with pytest.raises(ValueError, match="days_before"):
            validate_deadline_input(
                target_date=_future_date(30),
                lead_time_days=30,
                alert_thresholds=[{"severity": "info"}],  # missing days_before
            )


# ---------------------------------------------------------------------------
# 12.2 — Deadline countdown and threshold firing
# ---------------------------------------------------------------------------


class TestDeadlineCountdown:
    """compute_days_remaining and find_unfired_threshold."""

    def test_days_remaining_computed_correctly(self):
        from butlers.core.temporal.deadlines import compute_days_remaining

        target = _future_date(42)
        result = compute_days_remaining(target_date=target)
        assert 41 <= result <= 42  # tolerance for day boundary

    def test_days_remaining_negative_for_past_date(self):
        from butlers.core.temporal.deadlines import compute_days_remaining

        target = _past_date(5)
        result = compute_days_remaining(target_date=target)
        assert result <= -5

    def test_threshold_fires_when_days_equal_threshold(self):
        from butlers.core.temporal.deadlines import find_unfired_threshold

        thresholds = [_threshold(42, "info"), _threshold(14, "warning")]
        result = find_unfired_threshold(
            days_remaining=42,
            alert_thresholds=thresholds,
            fired_thresholds=[],
        )
        assert result is not None
        assert result["days_before"] == 42
        assert result["severity"] == "info"

    def test_threshold_fires_when_days_below_threshold(self):
        """Fires when days_remaining < days_before (missed due to downtime)."""
        from butlers.core.temporal.deadlines import find_unfired_threshold

        result = find_unfired_threshold(
            days_remaining=12,
            alert_thresholds=[_threshold(14, "warning")],
            fired_thresholds=[],
        )
        assert result is not None
        assert result["days_before"] == 14

    def test_already_fired_threshold_excluded(self):
        from butlers.core.temporal.deadlines import find_unfired_threshold

        result = find_unfired_threshold(
            days_remaining=14,
            alert_thresholds=[_threshold(14, "warning")],
            fired_thresholds=[_threshold(14, "warning")],
        )
        assert result is None

    def test_no_threshold_fires_above_all(self):
        from butlers.core.temporal.deadlines import find_unfired_threshold

        result = find_unfired_threshold(
            days_remaining=30,
            alert_thresholds=[_threshold(14, "warning"), _threshold(3, "critical")],
            fired_thresholds=[],
        )
        assert result is None

    def test_most_urgent_threshold_selected_among_multiple_eligible(self):
        """When multiple thresholds are eligible, the smallest days_before wins."""
        from butlers.core.temporal.deadlines import find_unfired_threshold

        thresholds = [
            _threshold(42, "info"),
            _threshold(14, "warning"),
            _threshold(3, "critical"),
        ]
        fired = [_threshold(42, "info"), _threshold(14, "warning")]
        result = find_unfired_threshold(
            days_remaining=3,
            alert_thresholds=thresholds,
            fired_thresholds=fired,
        )
        assert result is not None
        assert result["days_before"] == 3


# ---------------------------------------------------------------------------
# 12.3 — Deadline status state machine
# ---------------------------------------------------------------------------


class TestDeadlineStatusMachine:
    """compute_next_deadline_status and compute_expiry_transition."""

    def test_pending_to_alerted_on_non_critical(self):
        from butlers.core.temporal.deadlines import compute_next_deadline_status

        result = compute_next_deadline_status(
            current_status="pending",
            fired_threshold=_threshold(14, "info"),
        )
        assert result == "alerted"

    def test_pending_to_escalated_on_critical(self):
        from butlers.core.temporal.deadlines import compute_next_deadline_status

        result = compute_next_deadline_status(
            current_status="pending",
            fired_threshold=_threshold(3, "critical"),
        )
        assert result == "escalated"

    def test_alerted_to_escalated_on_critical(self):
        from butlers.core.temporal.deadlines import compute_next_deadline_status

        result = compute_next_deadline_status(
            current_status="alerted",
            fired_threshold=_threshold(3, "critical"),
        )
        assert result == "escalated"

    def test_alerted_stays_alerted_on_non_critical(self):
        from butlers.core.temporal.deadlines import compute_next_deadline_status

        result = compute_next_deadline_status(
            current_status="alerted",
            fired_threshold=_threshold(7, "warning"),
        )
        assert result == "alerted"

    def test_escalated_stays_escalated(self):
        from butlers.core.temporal.deadlines import compute_next_deadline_status

        result = compute_next_deadline_status(
            current_status="escalated",
            fired_threshold=_threshold(3, "critical"),
        )
        assert result == "escalated"

    def test_completed_is_terminal(self):
        from butlers.core.temporal.deadlines import compute_next_deadline_status

        result = compute_next_deadline_status(
            current_status="completed",
            fired_threshold=_threshold(14, "info"),
        )
        assert result == "completed"

    def test_expired_is_terminal(self):
        from butlers.core.temporal.deadlines import compute_next_deadline_status

        result = compute_next_deadline_status(
            current_status="expired",
            fired_threshold=_threshold(14, "info"),
        )
        assert result == "expired"

    def test_expiry_transition_past_date(self):
        from butlers.core.temporal.deadlines import compute_expiry_transition

        new_status, should_disable = compute_expiry_transition(
            current_status="alerted",
            target_date=_past_date(1),
        )
        assert new_status == "expired"
        assert should_disable is True

    def test_no_expiry_for_future_date(self):
        from butlers.core.temporal.deadlines import compute_expiry_transition

        new_status, should_disable = compute_expiry_transition(
            current_status="alerted",
            target_date=_future_date(5),
        )
        assert new_status == "alerted"
        assert should_disable is False

    def test_completed_not_expired_even_if_past(self):
        from butlers.core.temporal.deadlines import compute_expiry_transition

        new_status, should_disable = compute_expiry_transition(
            current_status="completed",
            target_date=_past_date(10),
        )
        assert new_status == "completed"
        assert should_disable is False

    def test_already_expired_stays_expired(self):
        from butlers.core.temporal.deadlines import compute_expiry_transition

        new_status, should_disable = compute_expiry_transition(
            current_status="expired",
            target_date=_past_date(5),
        )
        assert new_status == "expired"
        assert should_disable is False


# ---------------------------------------------------------------------------
# 12.4 — Deadline dependency checking
# ---------------------------------------------------------------------------


class TestDeadlineDependencies:
    """is_deadline_blocked — dependency evaluation."""

    def test_no_dependencies_not_blocked(self):
        from butlers.core.temporal.deadlines import is_deadline_blocked

        assert is_deadline_blocked(depends_on=[], dependency_statuses={}) is False

    def test_all_completed_not_blocked(self):
        from butlers.core.temporal.deadlines import is_deadline_blocked

        dep_id = str(datetime.now(UTC).timestamp())  # unique enough for tests
        assert (
            is_deadline_blocked(
                depends_on=[dep_id],
                dependency_statuses={dep_id: "completed"},
            )
            is False
        )

    def test_pending_dep_blocks(self):
        from butlers.core.temporal.deadlines import is_deadline_blocked

        dep_id = "dep-1"
        assert (
            is_deadline_blocked(
                depends_on=[dep_id],
                dependency_statuses={dep_id: "pending"},
            )
            is True
        )

    def test_missing_dep_status_blocks(self):
        """An unknown dep (no status) should block evaluation."""
        from butlers.core.temporal.deadlines import is_deadline_blocked

        assert (
            is_deadline_blocked(
                depends_on=["unknown-id"],
                dependency_statuses={},
            )
            is True
        )

    def test_mixed_deps_blocks_if_any_incomplete(self):
        from butlers.core.temporal.deadlines import is_deadline_blocked

        assert (
            is_deadline_blocked(
                depends_on=["dep-a", "dep-b"],
                dependency_statuses={"dep-a": "completed", "dep-b": "alerted"},
            )
            is True
        )

    def test_all_completed_multiple_deps(self):
        from butlers.core.temporal.deadlines import is_deadline_blocked

        assert (
            is_deadline_blocked(
                depends_on=["dep-a", "dep-b"],
                dependency_statuses={"dep-a": "completed", "dep-b": "completed"},
            )
            is False
        )
