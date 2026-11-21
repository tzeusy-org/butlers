"""Tripwire: a test may not hand its live clock to a configured quiet-hours window.

The core_160 migration seeds the Owner Attention Policy as 23:00-08:00
Asia/Singapore (15:00-24:00 UTC). A test that drives an owner-attention path
(``delivery_cycle``, the timed lane, ``policy_quiet_hours_deliver_at``...) with
``now=datetime.now(UTC)`` while that policy is set asserts something that is true
for fifteen hours of the day and false for nine. It passes on the author's
afternoon, then turns every CI shard that runs it red each evening UTC until
someone pins it (bu-gfi2kd, and the commitment-lifecycle case the injected-clock
guard was written for).

The source guard (``tests/config/test_injected_clock_guard.py``) cannot catch
this once the author writes ``# live-clock: <reason>``: it can check that a
reason exists, not that the reason accounts for quiet hours. This tripwire
checks the behaviour instead. Every quiet-hours decision in the codebase
resolves the policy's local time through ``approvals_policy._policy_local_now``,
so wrapping it sees each decision and the instant it was asked about. It records
a violation when all three hold:

- the policy has a configured quiet window;
- the instant is (about) the real wall clock; and
- a test-source frame on the call stack holds that same instant, i.e. the test
  supplied it.

The last condition is what keeps it narrow. Production code reading its own
clock (a parked approval scheduling its push, ``notify`` deciding a deferral)
is the normal case and is left to the tests that pin it some other way; the
defect is a test that had an injection point, used the live clock anyway, and
so made its own assertion hour-dependent. That shape fails at every hour, not
only inside the window, so it is caught on the PR that adds it.

Limits, so nobody mistakes a pass for proof:
- Five-minute tolerance: a live clock offset by hours
  (``datetime.now(UTC) + timedelta(hours=3)``) is not detected.
- The instant must still be visible in a test frame's locals (a parameter,
  local or fixture value); one built inline in a call expression is matched
  only if some frame binds an equal value.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import PurePath
from types import FrameType
from typing import Any

LIVE_CLOCK_TOLERANCE = timedelta(minutes=5)

PolicyLocalState = tuple[datetime, int, int] | None
PolicyLocalNow = Callable[..., PolicyLocalState]


def is_test_source(filename: str) -> bool:
    """A test module or conftest: under a ``tests`` directory or named ``test_*.py``."""
    path = PurePath(filename)
    return "tests" in path.parts or path.name.startswith("test_") or path.name == "conftest.py"


def supplying_test_frame(now: datetime, frame: FrameType | None) -> str | None:
    """``file:line`` of the innermost test frame binding a value equal to *now*."""
    while frame is not None:
        if is_test_source(frame.f_code.co_filename) and any(
            isinstance(value, datetime) and value == now for value in frame.f_locals.values()
        ):
            return f"{frame.f_code.co_filename}:{frame.f_lineno}"
        frame = frame.f_back
    return None


def checked_policy_local_now(
    original: PolicyLocalNow,
    violations: list[str],
    *,
    wall_clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> PolicyLocalNow:
    """Wrap *original* so a test-supplied live clock reaching a quiet window is recorded."""

    def checked(policy: dict[str, Any] | None, *, now: datetime) -> PolicyLocalState:
        state = original(policy, now=now)
        if (
            state is not None
            and policy is not None
            and abs(now - wall_clock()) < LIVE_CLOCK_TOLERANCE
        ):
            supplier = supplying_test_frame(now, sys._getframe(1))
            if supplier is not None:
                _, quiet_start, quiet_end = state
                violations.append(
                    f"{supplier} supplied the live clock ({now.isoformat()}) to quiet hours "
                    f"{quiet_start:02d}-{quiet_end:02d} {policy.get('timezone')!r}"
                )
        return state

    return checked


def failure_message(violations: list[str]) -> str:
    return (
        "This test passed the real wall clock into an Owner Attention Policy "
        "quiet-hours decision, so its outcome depends on the hour it runs "
        "(bu-gfi2kd). Pin the instant with now=<fixed datetime>, or clear the "
        "policy's quiet hours (UPDATE public.approvals_policy SET quiet_start_hour = "
        "NULL, quiet_end_hour = NULL) if quiet hours are not under test.\n" + "\n".join(violations)
    )
