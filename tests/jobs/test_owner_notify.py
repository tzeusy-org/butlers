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


async def _send() -> str:
    return await send_owner_telegram(
        MagicMock(), origin_butler="relationship", message="hello", log_context="test.job"
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
    assert await _send() == owner_notify.DELIVERED

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
    boundary["suppression"].assert_awaited_once()
    assert boundary["suppression"].await_args.kwargs == {"log_context": "test.job"}
