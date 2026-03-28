"""Deadline tracking core logic.

Provides pure functions for:
  - validate_deadline_input       — Validate deadline creation parameters
  - compute_days_remaining        — Compute days_remaining from target_date
  - find_unfired_threshold        — Find the next threshold to fire
  - compute_next_deadline_status  — State machine for deadline_status transitions
  - compute_expiry_transition     — Check if deadline should be marked expired
  - is_deadline_blocked           — Check if deadline depends_on blocks evaluation
  - build_deadline_prompt_context — Build prompt context string for deadline dispatch

These functions are pure (no I/O) and are called by the scheduler's tick() pass
and the deadline_create/update MCP tools.

See: openspec/specs/deadline-tracking/spec.md
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any


def validate_deadline_input(
    *,
    target_date: date,
    lead_time_days: int,
    alert_thresholds: list[dict[str, Any]],
) -> None:
    """Validate deadline creation parameters.

    Raises:
        ValueError: If any validation constraint is violated.
    """
    today = datetime.now(UTC).date()
    if target_date <= today:
        raise ValueError(f"target_date must be in the future (got {target_date}; today is {today})")

    if lead_time_days <= 0:
        raise ValueError(f"lead_time_days must be a positive integer (got {lead_time_days})")

    if not alert_thresholds:
        raise ValueError("alert_thresholds must contain at least one threshold")

    for threshold in alert_thresholds:
        days_before = threshold.get("days_before")
        if days_before is None:
            raise ValueError("Each threshold must have a 'days_before' integer field")
        if days_before > lead_time_days:
            raise ValueError(
                f"Threshold days_before={days_before} cannot exceed lead_time_days={lead_time_days}"
            )


def compute_days_remaining(*, target_date: date) -> int:
    """Compute the number of days remaining until target_date.

    Returns:
        (target_date - today).days — may be negative if target has passed.
    """
    today = datetime.now(UTC).date()
    return (target_date - today).days


def find_unfired_threshold(
    *,
    days_remaining: int,
    alert_thresholds: list[dict[str, Any]],
    fired_thresholds: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Find the highest-priority unfired threshold that is now eligible to fire.

    A threshold is eligible when days_remaining <= threshold.days_before and
    it is not already in fired_thresholds.

    Returns:
        The threshold dict to fire, or None if no threshold is eligible.
    """
    fired_days = {t["days_before"] for t in fired_thresholds}

    # Find all eligible (not yet fired) thresholds
    eligible = [
        t
        for t in alert_thresholds
        if t["days_before"] not in fired_days and days_remaining <= t["days_before"]
    ]

    if not eligible:
        return None

    # Return the one with the smallest days_before (most urgent / closest to today)
    return min(eligible, key=lambda t: t["days_before"])


def compute_next_deadline_status(
    *,
    current_status: str,
    fired_threshold: dict[str, Any],
) -> str:
    """Compute the new deadline_status after a threshold fires.

    State machine:
      pending → alerted   (any non-critical threshold fires)
      pending → escalated (critical threshold fires)
      alerted → escalated (critical threshold fires)
      escalated → escalated (no change)
      completed → completed (terminal)
      expired → expired (terminal)

    Returns:
        New status string.
    """
    if current_status in ("completed", "expired"):
        return current_status  # terminal states

    severity = fired_threshold.get("severity", "info")
    if severity == "critical":
        return "escalated"

    if current_status == "pending":
        return "alerted"

    # alerted or escalated: non-critical threshold doesn't demote
    return current_status


def compute_expiry_transition(
    *,
    current_status: str,
    target_date: date,
) -> tuple[str, bool]:
    """Check if a deadline should be marked expired.

    Returns:
        (new_status, should_disable) — If target_date has passed and the deadline
        is not already completed, transitions to 'expired' and returns should_disable=True.
        Otherwise returns (current_status, False).
    """
    if current_status == "completed":
        return current_status, False

    if current_status == "expired":
        return current_status, False

    today = datetime.now(UTC).date()
    if target_date < today:
        return "expired", True

    return current_status, False


def is_deadline_blocked(
    *,
    depends_on: list[str],
    dependency_statuses: dict[str, str],
) -> bool:
    """Check if a deadline is blocked by incomplete dependencies.

    A deadline is blocked if any of its depends_on task UUIDs has a status
    other than 'completed'.

    Returns:
        True if blocked (should not evaluate thresholds), False otherwise.
    """
    if not depends_on:
        return False

    for dep_id in depends_on:
        status = dependency_statuses.get(dep_id)
        if status != "completed":
            return True

    return False


def build_deadline_prompt_context(
    *,
    original_prompt: str,
    target_date: date,
    days_remaining: int,
    fired_threshold: dict[str, Any],
    deadline_status: str,
    all_thresholds: list[dict[str, Any]],
) -> str:
    """Build an augmented prompt string for deadline dispatch.

    Appends structured deadline context to the original prompt so the LLM
    can understand the urgency, target date, and alert timeline.

    Args:
        original_prompt: The butler's configured prompt text.
        target_date: The deadline's target date.
        days_remaining: Number of days until the deadline.
        fired_threshold: The threshold dict that triggered this dispatch.
        deadline_status: Current status of the deadline (pending/alerted/escalated/...).
        all_thresholds: Full list of alert thresholds for timeline context.

    Returns:
        Augmented prompt string with appended deadline context block.
    """
    context_lines = [
        f"target_date={target_date}",
        f"days_remaining={days_remaining}",
        f"fired_threshold={fired_threshold}",
        f"deadline_status={deadline_status}",
        f"all_thresholds={all_thresholds}",
    ]
    context_block = "[Deadline context: " + ", ".join(context_lines) + "]"
    return f"{original_prompt}\n\n{context_block}"
