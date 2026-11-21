"""Tripwire: a test's live clock may not reach a configured quiet-hours window.

The core_160 migration seeds the Owner Attention Policy as 23:00-08:00
Asia/Singapore (15:00-24:00 UTC). A test that drives an owner-attention path
(``delivery_cycle``, the timed lane, ``policy_quiet_hours_deliver_at``...) with
the real clock while that policy is set asserts something that is true for
fifteen hours of the day and false for nine. It passes on the author's
afternoon, then turns every CI shard that runs it red each evening UTC until
someone pins it (bu-gfi2kd, and the commitment-lifecycle case the injected-clock
guard was written for).

The source guard (``tests/config/test_injected_clock_guard.py``) cannot catch
this once the author writes ``# live-clock: <reason>``: it can check that a
reason exists, not that the reason accounts for quiet hours. This tripwire
checks the behaviour instead. Every quiet-hours decision in the codebase
resolves the policy's local time through ``approvals_policy._policy_local_now``,
so wrapping it sees each decision with the instant it was asked about. If a
policy with quiet hours is being evaluated at (about) the real wall clock, the
test's outcome depends on the hour it happened to run at, and the tripwire fails
it at every hour, not only inside the window.

Limits: the "about the real clock" test is a five-minute tolerance, so a live
clock offset by hours (``datetime.now(UTC) + timedelta(hours=3)``) is not
detected, and a pinned instant within five minutes of the real clock would be a
false positive (pinned instants are fixed dates, so in practice it is not).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

LIVE_CLOCK_TOLERANCE = timedelta(minutes=5)

PolicyLocalState = tuple[datetime, int, int] | None
PolicyLocalNow = Callable[..., PolicyLocalState]


def checked_policy_local_now(
    original: PolicyLocalNow,
    violations: list[str],
    *,
    wall_clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> PolicyLocalNow:
    """Wrap *original* so a live-clock read of a configured quiet window is recorded."""

    def checked(policy: dict[str, Any] | None, *, now: datetime) -> PolicyLocalState:
        state = original(policy, now=now)
        if state is not None and policy is not None and abs(now - wall_clock()) < (
            LIVE_CLOCK_TOLERANCE
        ):
            _, quiet_start, quiet_end = state
            violations.append(
                f"quiet hours {quiet_start:02d}-{quiet_end:02d} {policy.get('timezone')!r} "
                f"were evaluated at the live clock ({now.isoformat()})"
            )
        return state

    return checked


def failure_message(violations: list[str]) -> str:
    return (
        "This test evaluated the Owner Attention Policy's quiet hours at the real "
        "wall clock, so its outcome depends on the hour it runs (bu-gfi2kd). Pin the "
        "instant with now=<fixed datetime>, or clear the policy's quiet hours "
        "(UPDATE public.approvals_policy SET quiet_start_hour = NULL, "
        "quiet_end_hour = NULL) if quiet hours are not under test.\n" + "\n".join(violations)
    )
