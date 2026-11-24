"""The quiet-hours tripwire fires on a test-supplied live clock at every hour, and only then."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from butlers.core import approvals_policy
from butlers.core.approvals_policy import is_policy_quiet_now
from butlers.testing.quiet_hours_tripwire import (
    checked_policy_local_now,
    failure_message,
    is_test_source,
)

pytestmark = pytest.mark.unit

# The core_160 seed: 23:00-08:00 Asia/Singapore.
_SEEDED = {"quiet_start_hour": 23, "quiet_end_hour": 8, "timezone": "Asia/Singapore"}
_CLEARED = {"quiet_start_hour": None, "quiet_end_hour": None, "timezone": "Asia/Singapore"}
_PINNED = datetime(2026, 11, 19, 6, 0, tzinfo=UTC)

# Production code that reads its own clock: compiled under a non-test filename so
# no test frame on the stack binds the instant it passes.
_PRODUCTION_SOURCE = """
def decide(checked, policy, wall_clock):
    return checked(policy, now=wall_clock())
"""


def _production_decide():
    namespace: dict = {}
    exec(compile(_PRODUCTION_SOURCE, "/repo/src/butlers/core/fake_owner.py", "exec"), namespace)
    return namespace["decide"]


def _checked(wall_clock: datetime):
    violations: list[str] = []
    checked = checked_policy_local_now(
        approvals_policy._policy_local_now, violations, wall_clock=lambda: wall_clock
    )
    return violations, checked


@pytest.mark.parametrize("hour", range(24))
def test_a_test_supplied_live_clock_against_seeded_quiet_hours_fires_at_every_hour(hour):
    wall = datetime(2026, 11, 20, hour, 7, tzinfo=UTC)
    violations, checked = _checked(wall)

    checked(_SEEDED, now=wall)

    assert len(violations) == 1
    assert "test_quiet_hours_tripwire.py" in violations[0]
    assert "23-08 'Asia/Singapore'" in violations[0]


def test_a_live_clock_within_the_tolerance_still_fires():
    wall = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
    violations, checked = _checked(wall)
    supplied = wall - timedelta(minutes=4)

    checked(_SEEDED, now=supplied)

    assert violations


def test_production_reading_its_own_clock_does_not_fire():
    wall = datetime(2026, 11, 20, 18, 0, tzinfo=UTC)
    violations, checked = _checked(datetime(2026, 11, 20, 18, 0, 1, tzinfo=UTC))

    _production_decide()(checked, _SEEDED, lambda: wall + timedelta(microseconds=1))

    assert violations == []


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


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("/repo/tests/modules/test_insight_outcomes.py", True),
        ("/repo/roster/finance/tests/test_jobs.py", True),
        ("/repo/conftest.py", True),
        ("/repo/src/butlers/core/approvals_policy.py", False),
        ("/repo/src/butlers/testing/quiet_hours_tripwire.py", False),
        ("/repo/roster/switchboard/tools/insight/broker.py", False),
    ],
)
def test_test_sources_are_tests_and_conftests_only(filename, expected):
    assert is_test_source(filename) is expected


def test_the_autouse_tripwire_wraps_every_quiet_hours_reader():
    # The root conftest's autouse fixture has replaced the module global that
    # is_policy_quiet_now (and every other quiet-hours reader) calls by name.
    assert approvals_policy._policy_local_now.__qualname__.startswith("checked_policy_local_now")
    assert is_policy_quiet_now(_SEEDED, now=_PINNED) is False  # 14:00 SGT, pinned: no trip


def test_the_failure_names_both_ways_out():
    message = failure_message(["tests/x.py:1 supplied the live clock ..."])

    assert "now=<fixed datetime>" in message
    assert "quiet_start_hour = " in message
    assert "tests/x.py:1" in message
