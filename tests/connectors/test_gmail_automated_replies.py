"""Gmail auto-replies and bounces ingest as Tier 2 from headers alone (bu-s11n0s.18)."""

from __future__ import annotations

from typing import Any

import pytest

from butlers.connectors.gmail import GmailConnectorRuntime
from butlers.connectors.gmail_policy import (
    INGESTION_TIER_FULL,
    INGESTION_TIER_METADATA,
    INGESTION_TIER_SKIP,
    LabelFilterPolicy,
    PolicyTierAssigner,
    _gmail_tier_counter,
    detect_automated_reply,
    evaluate_message_policy,
)
from butlers.ingestion_policy import PolicyDecision

pytestmark = pytest.mark.unit


def _message(headers: list[dict[str, str]], *, parts: list[str] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "headers": [{"name": "From", "value": "Someone <someone@example.com>"}, *headers]
    }
    if parts:
        payload["mimeType"] = "multipart/mixed"
        payload["parts"] = [{"mimeType": mime} for mime in parts]
    return {"id": "m1", "threadId": "t1", "labelIds": ["INBOX"], "payload": payload}


def _evaluate(message: dict[str, Any]):
    return evaluate_message_policy(
        message,
        label_filter=LabelFilterPolicy(include_labels=frozenset(), exclude_labels=frozenset()),
        tier_assigner=PolicyTierAssigner(user_email="me@example.com"),
        endpoint_identity="test",
    )


def _counter(ingestion_tier: str, reason: str) -> float:
    """Current value of the tier counter for the test endpoint."""
    return _gmail_tier_counter.labels(
        endpoint_identity="test", ingestion_tier=ingestion_tier, reason=reason
    )._value.get()


def _header(name: str, value: str) -> dict[str, str]:
    return {"name": name, "value": value}


@pytest.mark.parametrize(
    ("headers", "expected_kind"),
    [
        ([_header("Auto-Submitted", "auto-replied")], "auto_reply"),
        ([_header("X-Autoreply", "yes")], "auto_reply"),
        ([_header("X-Autorespond", "yes")], "auto_reply"),
        ([_header("Precedence", "auto_reply")], "auto_reply"),
        (
            [
                _header(
                    "Content-Type",
                    'multipart/report; report-type=delivery-status; boundary="b"',
                )
            ],
            "bounce",
        ),
        # Ordinary mail stays ordinary.
        ([_header("Auto-Submitted", "no")], None),
        # RFC 3834 auto-generated mail (alerts, receipts) belongs to the owner's rules.
        ([_header("Auto-Submitted", "auto-generated")], None),
        ([_header("auto-submitted", "auto-generated; reason=receipt")], None),
        ([_header("Auto-Submitted", "auto-notified")], None),
        ([_header("List-Id", "News <news.example.com>"), _header("Precedence", "list")], None),
        ([_header("List-Id", "News <news.example.com>")], None),
        ([_header("Precedence", "bulk")], None),
        ([_header("Precedence", "list")], None),
        ([_header("Content-Type", "multipart/report; report-type=disposition-notification")], None),
        ([], None),
    ],
)
def test_automated_messages_ingest_as_metadata_only_with_their_reason(headers, expected_kind):
    reason = expected_kind or "pass_through"
    tier = "metadata" if expected_kind else "full"
    before = _counter(tier, reason)

    result = _evaluate(_message(headers))

    assert result.automated_kind == expected_kind
    assert _counter(tier, reason) == before + 1
    if expected_kind is None:
        assert result.ingestion_tier == INGESTION_TIER_FULL
        assert result.triage_action == "pass_through"
    else:
        assert result.ingestion_tier == INGESTION_TIER_METADATA
        assert result.should_ingest is True


def test_a_mailer_daemon_with_a_delivery_status_part_is_a_bounce():
    headers = [_header("From", "Mail Delivery Subsystem <MAILER-DAEMON@googlemail.com>")]
    message = _message(headers, parts=["text/plain", "message/delivery-status"])
    message["payload"]["headers"] = headers

    assert _evaluate(message).automated_kind == "bounce"
    # The same sender without a delivery-status part is not enough evidence.
    del message["payload"]["parts"]
    assert _evaluate(message).automated_kind is None


@pytest.mark.parametrize("headers", [None, "garbage", 5, [None, {"name": 1, "value": 2}], {}])
def test_missing_or_malformed_headers_are_ordinary_mail(headers):
    assert detect_automated_reply(headers) is None
    message = {"id": "m1", "labelIds": ["INBOX"], "payload": {"headers": headers}}
    assert _evaluate(message).ingestion_tier == INGESTION_TIER_FULL
    assert _evaluate({"id": "m2", "labelIds": []}).ingestion_tier == INGESTION_TIER_FULL


@pytest.mark.parametrize("action", ["pass_through", "route_to", "low_priority_queue"])
def test_a_global_action_never_upgrades_an_automated_message_to_tier_1(action):
    result = _evaluate(_message([_header("Auto-Submitted", "auto-replied")]))

    resolved = GmailConnectorRuntime._apply_global_action_tier(
        result, PolicyDecision(action=action)
    )

    assert resolved.ingestion_tier == INGESTION_TIER_METADATA
    assert resolved.automated_kind == "auto_reply"


def test_a_global_skip_still_wins_over_an_automated_message():
    result = _evaluate(_message([_header("Auto-Submitted", "auto-replied")]))

    # The connector drops a global-skip decision before it resolves the tier.
    resolved = GmailConnectorRuntime._apply_global_action_tier(
        result, PolicyDecision(action="skip")
    )

    assert resolved.ingestion_tier == INGESTION_TIER_SKIP
