"""Tests for butlers.jobs.owner_notify.send_owner_telegram (bu-idc5mx.4).

Every branch records exactly one attention-ledger row under the caller's origin butler and
never retries a send. Suppression drops the message rather than deferring it.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.jobs import owner_notify
from butlers.jobs.owner_notify import send_owner_telegram

pytestmark = pytest.mark.unit

_MOD = "butlers.jobs.owner_notify"


@pytest.fixture
def boundary(monkeypatch: pytest.MonkeyPatch) -> dict[str, MagicMock]:
    """Patch the notify boundary to a deliverable default; tests override one seam each."""
    result = MagicMock(is_error=False, data={"status": "sent", "notification_id": "n-1"})
    client = MagicMock()
    client.call_tool = AsyncMock(return_value=result)
    mocks = {
        "suppression": AsyncMock(return_value=None),
        "recipient": AsyncMock(return_value="12345"),
        "client_getter": MagicMock(return_value=client),
        "client": client,
        "ledger": AsyncMock(),
    }
    monkeypatch.setattr(f"{_MOD}.check_owner_notify_suppression", mocks["suppression"])
    monkeypatch.setattr(f"{_MOD}.resolve_owner_telegram_recipient", mocks["recipient"])
    monkeypatch.setattr(f"{_MOD}.get_current_switchboard_client", mocks["client_getter"])
    monkeypatch.setattr(f"{_MOD}.record_attention_event", mocks["ledger"])
    return mocks


async def _send(**kwargs) -> str:
    return await send_owner_telegram(
        MagicMock(), origin_butler="relationship", message="hello", log_context="test.job", **kwargs
    )


def _only_ledger_row(boundary: dict[str, MagicMock]) -> dict:
    boundary["ledger"].assert_awaited_once()
    kwargs = boundary["ledger"].await_args.kwargs
    assert kwargs["origin_butler"] == "relationship"
    assert kwargs["source"] == "notify"
    return kwargs


async def test_suppressed_send_is_dropped_and_recorded(boundary):
    boundary["suppression"].return_value = "quiet_hours"

    assert await _send() == owner_notify.SUPPRESSED

    boundary["recipient"].assert_not_awaited()
    boundary["client"].call_tool.assert_not_awaited()
    row = _only_ledger_row(boundary)
    assert (row["outcome"], row["reason"]) == ("suppressed", "quiet_hours")


async def test_missing_recipient_fails_without_delivery(boundary):
    boundary["recipient"].return_value = None

    assert await _send() == owner_notify.FAILED

    boundary["client"].call_tool.assert_not_awaited()
    row = _only_ledger_row(boundary)
    assert (row["outcome"], row["reason"]) == ("failed", "no_recipient_configured")


async def test_missing_switchboard_client_fails_with_no_fallback(boundary):
    boundary["client_getter"].return_value = None

    assert await _send() == owner_notify.FAILED

    row = _only_ledger_row(boundary)
    assert (row["outcome"], row["reason"]) == ("failed", "switchboard_client_unavailable")


@pytest.mark.parametrize(
    ("raised", "result", "reason"),
    [
        (TimeoutError(), None, "delivery_error:TimeoutError"),
        (None, MagicMock(is_error=True, data=None), "delivery_error"),
        (None, MagicMock(is_error=False, data={"status": "failed"}), "delivery_error"),
    ],
    ids=["raised", "is_error", "status_failed"],
)
async def test_deliver_error_is_recorded_once_and_not_retried(boundary, raised, result, reason):
    if raised is not None:
        boundary["client"].call_tool.side_effect = raised
    else:
        boundary["client"].call_tool.return_value = result

    assert await _send() == owner_notify.FAILED

    boundary["client"].call_tool.assert_awaited_once()
    row = _only_ledger_row(boundary)
    assert (row["outcome"], row["reason"]) == ("failed", reason)


async def test_delivered_sends_notify_v1_to_owner_and_records_ref(boundary):
    assert await _send(dedup_key="job:attempt") == owner_notify.DELIVERED

    tool, args = boundary["client"].call_tool.await_args.args
    assert tool == "deliver"
    assert args["source_butler"] == "relationship"
    request = args["notify_request"]
    assert request["schema_version"] == "notify.v1"
    assert request["origin_butler"] == "relationship"
    assert request["delivery"] == {
        "intent": "send",
        "channel": "telegram",
        "message": "hello",
        "recipient": "12345",
    }
    row = _only_ledger_row(boundary)
    assert (row["outcome"], row["notification_ref"]) == ("delivered", "n-1")
    assert row["dedup_key"] == "job:attempt"
    boundary["suppression"].assert_awaited_once()
    assert boundary["suppression"].await_args.kwargs == {"log_context": "test.job"}


@pytest.mark.parametrize(
    ("job_args", "message"),
    [
        (
            {
                "kind": "due",
                "issuer": "SP Group",
                "deadline": {"target_date": "2026-10-28", "days_remaining": 3},
            },
            "Paperwork reminder (SP Group): payment due Wed 28 Oct 2026, in 3 days.",
        ),
        (
            {
                "kind": "cancel_by",
                "issuer": "<b>AXA</b> http://x.io",
                "deadline": {"target_date": "2026-11-03", "days_remaining": 1},
            },
            "Paperwork reminder (bAXAb httpxio): cancel by Tue 3 Nov 2026, tomorrow.",
        ),
        (
            {
                "kind": "expiry",
                "issuer": None,
                "deadline": {"target_date": "2026-11-02", "days_remaining": 0},
            },
            "Paperwork reminder: expires Mon 2 Nov 2026, today.",
        ),
        ({"kind": "pay_now", "issuer": "Evil", "deadline": {"target_date": "2026-11-02"}}, None),
    ],
    ids=["due", "inert_issuer", "no_issuer", "unknown_kind"],
)
async def test_paperwork_deadline_notice_sends_only_a_fixed_template_to_the_owner(
    boundary, job_args, message
):
    """Every butler's registry runs the notice job under its own origin; text is templated."""
    from butlers.scheduled_jobs import get_deterministic_schedule_job_registry

    handler = get_deterministic_schedule_job_registry()["finance"]["paperwork_deadline_notify"]

    result = await handler(MagicMock(), job_args)

    if message is None:
        assert result == {"outcome": "skipped", "reason": "invalid_kind"}
        boundary["client"].call_tool.assert_not_awaited()
        return
    assert result == {"outcome": owner_notify.DELIVERED}
    request = boundary["client"].call_tool.await_args.args[1]["notify_request"]
    assert request["origin_butler"] == "finance"
    assert request["delivery"]["message"] == message


@pytest.mark.parametrize(
    "seam",
    ["suppression", "client_getter"],
    ids=["suppressed_defers_without_ledger_row", "failed_raises"],
)
async def test_undelivered_paperwork_notice_raises_so_the_threshold_stays_unfired(boundary, seam):
    """A deadline threshold has no next run: a dropped notice must fail the dispatch."""
    from butlers.core.temporal.deadlines import DeadlineDispatchDeferred
    from butlers.scheduled_jobs import get_deterministic_schedule_job_registry

    if seam == "suppression":
        boundary["suppression"].return_value = "quiet_hours"
    else:
        boundary["client_getter"].return_value = None
    handler = get_deterministic_schedule_job_registry()["finance"]["paperwork_deadline_notify"]
    job_args = {"kind": "due", "deadline": {"target_date": "2026-10-28", "days_remaining": 1}}

    expected = DeadlineDispatchDeferred if seam == "suppression" else RuntimeError
    with pytest.raises(expected):
        await handler(MagicMock(), job_args)

    boundary["client"].call_tool.assert_not_awaited()
    if seam == "suppression":
        # Retried every tick while quiet hours last: no ledger row per attempt.
        boundary["ledger"].assert_not_awaited()
    else:
        boundary["ledger"].assert_awaited_once()
        row = boundary["ledger"].await_args.kwargs
        assert (row["origin_butler"], row["reason"]) == (
            "finance",
            "switchboard_client_unavailable",
        )


@pytest.mark.parametrize(
    ("failures", "since_last_min", "sends"),
    [
        (0, None, True),
        (1, 4, False),
        (1, 5, True),
        (3, 19, False),
        (3, 20, True),
        (40, 59, False),
        (40, 60, True),
    ],
    ids=["first", "1_waiting", "1_due", "3_waiting", "3_due", "capped_waiting", "capped_due"],
)
async def test_failed_paperwork_notice_backs_off_per_threshold(
    boundary, monkeypatch, failures, since_last_min, sends
):
    """A Switchboard outage writes one failed row per backoff step, not one per tick.

    Failed attempts share a per-(deadline, threshold) dedup key; the wait doubles from
    5 minutes per failure up to an hour, measured from the newest failed row.
    """
    from datetime import timedelta

    from butlers.core.temporal.deadlines import DeadlineDispatchDeferred
    from butlers.scheduled_jobs import get_deterministic_schedule_job_registry

    since_last = None if since_last_min is None else timedelta(minutes=since_last_min)
    attempts = AsyncMock(return_value=(failures, since_last))
    monkeypatch.setattr("butlers.jobs.document_deadlines.failed_attempts", attempts)
    handler = get_deterministic_schedule_job_registry()["finance"]["paperwork_deadline_notify"]

    def args(doc: str = "s3://b/bill.pdf", days_before: int = 3) -> dict:
        return {
            "document_id": doc,
            "kind": "due",
            "deadline": {
                "target_date": "2026-10-28",
                "days_remaining": 2,
                "fired_threshold": {"days_before": days_before, "severity": "warning"},
            },
        }

    if not sends:
        with pytest.raises(DeadlineDispatchDeferred, match="retry_backoff"):
            await handler(MagicMock(), args())
        boundary["client"].call_tool.assert_not_awaited()
        boundary["ledger"].assert_not_awaited()
        return
    assert await handler(MagicMock(), args()) == {"outcome": owner_notify.DELIVERED}
    key = boundary["ledger"].await_args.kwargs["dedup_key"]
    assert attempts.await_args.kwargs == {"dedup_key": key}
    # One key per (document, kind, date, threshold): another threshold starts fresh.
    await handler(MagicMock(), args(days_before=7))
    await handler(MagicMock(), args(doc="s3://b/other.pdf"))
    keys = [c.kwargs["dedup_key"] for c in boundary["ledger"].await_args_list]
    assert len(set(keys)) == 3 and all(k.startswith("paperwork_deadline_notify:") for k in keys)
    assert "bill.pdf" not in key
