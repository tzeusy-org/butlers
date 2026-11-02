"""The always-quiet lint needs BOTH the nominal and the latest staggered firing to be quiet."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from butlers.core.scheduler import schedule_always_in_quiet_window

pytestmark = pytest.mark.unit

SGT_POLICY = {"quiet_start_hour": 22, "quiet_end_hour": 7, "timezone": "Asia/Singapore"}
NOW = datetime(2026, 10, 19, 3, 0, tzinfo=UTC)


def test_a_nominal_firing_just_before_the_window_is_not_always_quiet() -> None:
    # 21:50 SGT is outside the window; a 15 minute stagger can move it to 22:05, which is inside.
    # Only the latest instant is quiet, so the schedule can still be heard and must not warn.
    assert not schedule_always_in_quiet_window(
        "50 21 * * *", timezone="Asia/Singapore", quiet_policy=SGT_POLICY, now=NOW
    )
    # With no stagger the same firing is plainly outside the window either way.
    assert not schedule_always_in_quiet_window(
        "50 21 * * *",
        timezone="Asia/Singapore",
        quiet_policy=SGT_POLICY,
        now=NOW,
        max_stagger_seconds=0,
    )
