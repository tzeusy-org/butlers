"""Unit contracts for deterministic approval-request push construction."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

pytestmark = pytest.mark.unit


def _pending_action() -> dict[str, object]:
    requested_at = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)
    return {
        "id": uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        "tool_name": "relationship_assert_fact",
        "requested_at": requested_at,
        "expires_at": requested_at + timedelta(hours=72),
        "why": "The owner asked to preserve this relationship fact.",
        "blast_radius": "contact",
        "reversibility": "compensable",
    }


def test_build_approval_request_is_templated_and_carries_signed_actions() -> None:
    """The push is fixed daemon text, with both one-tap tokens and an edit link."""
    from butlers.modules.approvals.notifications import build_approval_request_envelope

    action = _pending_action()
    envelope = build_approval_request_envelope(
        action=action,
        origin_butler="relationship",
        owner_recipient="100200300",
        callback_secret="test-secret",
        dashboard_base_url="https://dashboard.example.test",
    )

    assert envelope["delivery"] == {
        "intent": "approval_request",
        "channel": "telegram",
        "message": envelope["delivery"]["message"],
        "recipient": "100200300",
    }
    assert "relationship_assert_fact" in envelope["delivery"]["message"]
    assert "The owner asked to preserve this relationship fact." in envelope["delivery"]["message"]
    assert "Blast radius: contact" in envelope["delivery"]["message"]
    assert "Reversibility: compensable" in envelope["delivery"]["message"]
    assert "Expires:" in envelope["delivery"]["message"]
    assert envelope["actions"][0]["verb"] == "approve"
    assert envelope["actions"][0]["callback_token"].startswith("apr1:")
    assert envelope["actions"][1]["verb"] == "reject"
    assert envelope["actions"][2] == {
        "verb": "open_dashboard",
        "dashboard_url": "https://dashboard.example.test/approvals/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
    }


def test_burst_modes_hold_the_first_three_then_emit_one_digest() -> None:
    """The fourth park creates the only digest; later parks stay collapsed."""
    from butlers.modules.approvals.notifications import select_approval_push_mode

    assert select_approval_push_mode(park_count=1, digest_already_emitted=False) == "single"
    assert select_approval_push_mode(park_count=3, digest_already_emitted=False) == "single"
    assert select_approval_push_mode(park_count=4, digest_already_emitted=False) == "burst_digest"
    assert select_approval_push_mode(park_count=5, digest_already_emitted=True) == "collapsed"
