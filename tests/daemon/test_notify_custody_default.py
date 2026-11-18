"""notify() owner-default custody fallback at the tool boundary (bu-s11n0s.1 slice 4).

REQ-endpoint-custody-holds-007 'Owner fallback uses real unheld account' and 'No unheld
destination'. The custody choice itself is covered in tests/core/test_custody_egress.py; here
the tool must act on it: move the notice to email, or refuse with a ledger row and no send.
"""

import pytest

from butlers.core.custody_egress import OwnerDefaultTarget
from tests.daemon.test_notify_confirm import notification  # noqa: F401

pytestmark = pytest.mark.unit


async def test_owner_default_moves_to_unheld_email(notification):  # noqa: F811
    notify, daemon, guards = notification
    guards["_owner_default_custody_target"].return_value = OwnerDefaultTarget(
        channel="email", recipient="owner@example.test"
    )

    result = await notify(message="Done.")

    assert result["status"] == "ok"
    resolve = daemon._resolve_default_notify_recipient.await_args.kwargs
    assert resolve["channel"] == "email"
    assert resolve["recipient"] == "owner@example.test"
    sent = daemon.switchboard_client.call_tool.await_args.args[1]["notify_request"]
    assert sent["delivery"]["channel"] == "email"


@pytest.mark.parametrize("reason", ["endpoint_held", "eligibility_unknown"])
async def test_no_unheld_owner_target_refuses_with_a_ledger_row(notification, reason):  # noqa: F811
    notify, daemon, guards = notification
    guards["_owner_default_custody_target"].return_value = OwnerDefaultTarget(
        channel=None, recipient=None, refusal=reason
    )

    result = await notify(message="Done.")

    assert result["status"] == "error"
    assert result["code"] == reason
    assert result["retryable"] is (reason == "eligibility_unknown")
    daemon.switchboard_client.call_tool.assert_not_awaited()
    rows = [
        call.kwargs
        for call in guards["record_attention_event"].await_args_list
        if call.kwargs.get("reason") == reason
    ]
    assert len(rows) == 1
    assert rows[0]["outcome"] == "failed"
    assert rows[0]["source"] == "notify"


async def test_forced_channel_never_consults_the_fallback(notification):  # noqa: F811
    notify, _daemon, guards = notification
    await notify(message="Done.", channel="telegram")
    guards["_owner_default_custody_target"].assert_not_awaited()
