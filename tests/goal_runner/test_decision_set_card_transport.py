"""The owner-notify transport adapter maps one notify result to a card send result."""

from __future__ import annotations

import pytest

from butlers.goal_runner.decision_set_cards import owner_notify_transport
from butlers.jobs import owner_notify
from butlers.jobs.owner_notify import OwnerNotifyResult

pytestmark = pytest.mark.unit


async def _send(monkeypatch, result: OwnerNotifyResult):
    seen: dict = {}

    async def fake(pool, **kwargs):
        seen.update(kwargs)
        return result

    monkeypatch.setattr(owner_notify, "send_owner_telegram_checked", fake)
    sent = await owner_notify_transport(object())({"delivery": {"recipient": "9"}})
    return sent, seen


async def test_a_delivered_card_carries_the_providers_message_id(monkeypatch):
    sent, seen = await _send(monkeypatch, OwnerNotifyResult("delivered", True, "4242"))
    assert (sent.outcome, sent.provider_ref) == ("sent", "4242")
    assert seen["envelope"] == {"delivery": {"recipient": "9"}}


@pytest.mark.parametrize(
    ("result", "outcome"),
    [
        (OwnerNotifyResult("suppressed", False), "failed"),
        (OwnerNotifyResult("failed", False), "failed"),
        (OwnerNotifyResult("failed", True), "unknown"),
    ],
)
async def test_only_a_proven_non_send_is_failed(monkeypatch, result, outcome):
    sent, _ = await _send(monkeypatch, result)
    assert (sent.outcome, sent.provider_ref) == (outcome, None)
