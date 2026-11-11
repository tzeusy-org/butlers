"""The sweep hook and the owner-notify sender, without a database."""

import pytest

from butlers.goal_runner.owner_failure import OwnerNotifySender, cause_for
from butlers.goal_runner.runner import GoalRunner
from butlers.jobs.owner_notify import OwnerNotifyResult


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
    ("outcome", "call_started", "expected"),
    [
        ("delivered", True, "sent"),
        # Decided before any provider call: nothing was delivered, so it may be sent again.
        ("suppressed", False, "failed"),
        ("failed", False, "failed"),
        # The call began: the provider may have delivered, so it is never sent again.
        ("failed", True, "unknown"),
    ],
)
async def test_the_owner_notify_sender_separates_proven_non_sends_from_ambiguous_ones(
    outcome, call_started, expected
) -> None:
    seen = {}

    async def send(pool, **kwargs):
        seen.update(kwargs)
        return OwnerNotifyResult(outcome, call_started)

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


async def test_owner_notify_reports_whether_the_provider_call_began(monkeypatch) -> None:
    from butlers.jobs import owner_notify

    async def allowed(pool, *, log_context):
        return None

    async def no_recipient(pool):
        return None

    async def recorded(*args, **kwargs):
        return None

    monkeypatch.setattr(owner_notify, "check_owner_notify_suppression", allowed)
    monkeypatch.setattr(owner_notify, "record_attention_event", recorded)
    monkeypatch.setattr(owner_notify, "resolve_owner_telegram_recipient", no_recipient)
    result = await owner_notify.send_owner_telegram_checked(
        object(), origin_butler="switchboard", message="m", log_context="t"
    )
    assert (result.outcome, result.call_started) == ("failed", False)

    async def recipient(pool):
        return "chat"

    class Client:
        async def call_tool(self, *args, **kwargs):
            raise TimeoutError

    monkeypatch.setattr(owner_notify, "resolve_owner_telegram_recipient", recipient)
    monkeypatch.setattr(owner_notify, "get_current_switchboard_client", lambda: Client())
    result = await owner_notify.send_owner_telegram_checked(
        object(), origin_butler="switchboard", message="m", log_context="t"
    )
    assert (result.outcome, result.call_started) == ("failed", True)
    assert (
        await owner_notify.send_owner_telegram(
            object(), origin_butler="switchboard", message="m", log_context="t"
        )
        == "failed"
    )
