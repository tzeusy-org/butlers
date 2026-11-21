"""The quiet-hours live-clock tripwire fires at every hour, not only inside the window."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from butlers.core import approvals_policy
from butlers.core.approvals_policy import is_policy_quiet_now
from butlers.testing.quiet_hours_tripwire import checked_policy_local_now, failure_message

pytestmark = pytest.mark.unit

# The core_160 seed: 23:00-08:00 Asia/Singapore.
_SEEDED = {"quiet_start_hour": 23, "quiet_end_hour": 8, "timezone": "Asia/Singapore"}
_CLEARED = {"quiet_start_hour": None, "quiet_end_hour": None, "timezone": "Asia/Singapore"}
_PINNED = datetime(2026, 11, 19, 6, 0, tzinfo=UTC)


def _checked(wall_clock: datetime) -> tuple[list[str], object]:
    violations: list[str] = []
    checked = checked_policy_local_now(
        approvals_policy._policy_local_now, violations, wall_clock=lambda: wall_clock
    )
    return violations, checked


@pytest.mark.parametrize("hour", range(24))
def test_a_live_clock_against_seeded_quiet_hours_fires_at_every_hour(hour):
    wall = datetime(2026, 11, 20, hour, 7, tzinfo=UTC)
    violations, checked = _checked(wall)

    checked(_SEEDED, now=wall)

    assert len(violations) == 1
    assert "23-08 'Asia/Singapore'" in violations[0]


def test_a_live_clock_within_the_tolerance_still_fires():
    wall = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
    violations, checked = _checked(wall)

    checked(_SEEDED, now=wall - timedelta(minutes=4))

    assert violations


def test_a_pinned_instant_does_not_fire():
    violations, checked = _checked(datetime(2026, 11, 21, 18, 0, tzinfo=UTC))

    checked(_SEEDED, now=_PINNED)

    assert violations == []


def test_a_live_clock_against_cleared_quiet_hours_does_not_fire():
    wall = datetime(2026, 11, 20, 18, 0, tzinfo=UTC)
    violations, checked = _checked(wall)

    checked(_CLEARED, now=wall)
    checked(None, now=wall)

    assert violations == []


def test_the_wrapper_returns_the_original_decision():
    wall = datetime(2026, 11, 20, 18, 0, tzinfo=UTC)  # 02:00 Asia/Singapore
    _, checked = _checked(wall)

    assert checked(_SEEDED, now=wall) == approvals_policy._policy_local_now(_SEEDED, now=wall)


def test_the_autouse_tripwire_wraps_every_quiet_hours_reader():
    # The root conftest's autouse fixture has replaced the module global that
    # is_policy_quiet_now (and every other quiet-hours reader) calls by name.
    assert approvals_policy._policy_local_now.__qualname__.startswith("checked_policy_local_now")
    assert is_policy_quiet_now(_SEEDED, now=_PINNED) is False  # 14:00 SGT, pinned: no trip


def test_the_failure_names_both_ways_out():
    message = failure_message(["quiet hours 23-08 'Asia/Singapore' were evaluated at ..."])

    assert "now=<fixed datetime>" in message
    assert "quiet_start_hour = NULL" in message
    assert "23-08" in message
