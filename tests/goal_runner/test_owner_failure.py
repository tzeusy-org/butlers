"""The sweep hook and the owner-notify sender, without a database."""

import pytest

from butlers.goal_runner.owner_failure import OwnerNotifySender, cause_for
from butlers.goal_runner.runner import GoalRunner


class Reporter:
    def __init__(self, error=None) -> None:
        self.error = error
        self.calls = 0

    async def sweep(self) -> int:
        self.calls += 1
        if self.error:
            raise self.error
        return 0


def runner_with(reporter) -> GoalRunner:
    runner = object.__new__(GoalRunner)
    runner.owner_failures = reporter
    return runner


async def test_the_sweep_hook_runs_the_reporter_and_never_raises() -> None:
    reporter = Reporter()
    await runner_with(reporter)._report_owner_failures()
    assert reporter.calls == 1
    failing = Reporter(RuntimeError("boom"))
    await runner_with(failing)._report_owner_failures()
    assert failing.calls == 1


async def test_no_reporter_means_the_hook_does_nothing() -> None:
    await runner_with(None)._report_owner_failures()


@pytest.mark.parametrize(
    ("status", "unavailable", "cause"),
    [
        ("failed", False, "failed"),
        ("budget_exhausted", False, "budget_exhausted"),
        ("failed", True, "capability_unavailable"),
        ("budget_exhausted", True, "capability_unavailable"),
    ],
)
def test_the_cause_word_follows_the_goal_status_and_a_capability_refusal(
    status, unavailable, cause
) -> None:
    assert cause_for(status, unavailable) == cause


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [("delivered", "sent"), ("failed", "failed"), ("suppressed", "failed")],
)
async def test_the_owner_notify_sender_maps_outcomes_without_a_retry(outcome, expected) -> None:
    seen = {}

    async def send(pool, **kwargs):
        seen.update(kwargs)
        return outcome

    async def quiet(pool, **kwargs):
        return "quiet_hours"

    sender = OwnerNotifySender(object(), send=send, suppression=quiet)
    assert await sender.suppressed() is True
    assert await sender.send("hello", dedup_key="k") == expected
    assert seen["origin_butler"] == "switchboard" and seen["dedup_key"] == "k"


async def test_a_clear_owner_is_not_suppressed() -> None:
    async def clear(pool, **kwargs):
        return None

    assert await OwnerNotifySender(object(), suppression=clear).suppressed() is False
