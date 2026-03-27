"""Seasonal period awareness for temporal intelligence.

Provides pure functions for:
  - validate_period_dates  — Validate month/day combinations for a seasonal period
  - is_period_active       — Check if a single period is active on a given date
  - filter_active_periods  — Filter a list of periods to only active ones

Supports year-wrapping periods (e.g., Nov–Jan winter holidays).

See: openspec/specs/seasonal-awareness/spec.md
"""

from __future__ import annotations

import calendar
from datetime import date
from typing import Any


def validate_period_dates(
    *,
    start_month: int,
    start_day: int,
    end_month: int,
    end_day: int,
) -> None:
    """Validate month/day combinations for a seasonal period.

    Raises:
        ValueError: If any month/day combination is invalid (e.g., February 30).
    """
    for label, month, day in [
        ("start", start_month, start_day),
        ("end", end_month, end_day),
    ]:
        if not (1 <= month <= 12):
            raise ValueError(f"Invalid {label}_month={month}: must be 1–12")

        # Use the maximum days in that month (for non-leap year check)
        max_day = calendar.monthrange(2001, month)[1]  # 2001 = non-leap year
        if not (1 <= day <= max_day):
            raise ValueError(
                f"Invalid {label} date: {calendar.month_name[month]} {day} is not a valid date"
                f" (max day for month {month} is {max_day})"
            )


def is_period_active(period: dict[str, Any], *, today: date | None = None) -> bool:
    """Check if a single seasonal period is currently active.

    Handles year-boundary wrapping (e.g., start_month=11, end_month=1).

    Args:
        period: A dict with start_month, start_day, end_month, end_day, enabled fields.
        today: Date to test against (defaults to date.today()).

    Returns:
        True if the period is enabled and today falls within its date range.
    """
    if not period.get("enabled", True):
        return False

    if today is None:
        today = date.today()

    start_month = period["start_month"]
    start_day = period["start_day"]
    end_month = period["end_month"]
    end_day = period["end_day"]

    # Use month-day tuples for comparison (year-agnostic)
    current_md = (today.month, today.day)
    start_md = (start_month, start_day)
    end_md = (end_month, end_day)

    if start_md <= end_md:
        # Same-year range (e.g., Jan 1 – Apr 15)
        return start_md <= current_md <= end_md
    else:
        # Year-wrapping range (e.g., Nov 15 – Jan 10)
        # Active if current is >= start OR <= end
        return current_md >= start_md or current_md <= end_md


def filter_active_periods(
    periods: list[dict[str, Any]], *, today: date | None = None
) -> list[dict[str, Any]]:
    """Filter a list of seasonal periods to only those active on the given date.

    Args:
        periods: List of period dicts.
        today: Date to test against (defaults to date.today()).

    Returns:
        List of active period dicts (may be empty).
    """
    if today is None:
        today = date.today()
    return [p for p in periods if is_period_active(p, today=today)]
