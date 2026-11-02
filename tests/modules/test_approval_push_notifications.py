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

    action = {
        **_pending_action(),
        "tool_name": "email_send_message",
        "tool_args": {"to": "dana@x.com", "subject": "Re: Thursday", "body": "Thursday works."},
    }
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
    assert "To: `dana@x.com`" in envelope["delivery"]["message"]
    assert "Thursday works." in envelope["delivery"]["message"]
    assert "Assistant says" in envelope["delivery"]["message"]
    assert "The owner asked to preserve this relationship fact." in envelope["delivery"]["message"]
    assert "Blast radius: `contact`" in envelope["delivery"]["message"]
    assert "Reversibility: `compensable`" in envelope["delivery"]["message"]
    assert "Expires:" in envelope["delivery"]["message"]
    assert envelope["actions"][0]["verb"] == "approve"
    assert envelope["actions"][0]["callback_token"].startswith("apr1:")
    assert envelope["actions"][1]["verb"] == "reject"
    assert envelope["actions"][2] == {
        "verb": "open_dashboard",
        "reference": {
            "kind": "self",
            "route_key": "approval_detail",
            "params": {"action_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"},
        },
    }


def test_burst_modes_hold_the_first_three_then_emit_one_digest() -> None:
    """The fourth park creates the only digest; later parks stay collapsed."""
    from butlers.modules.approvals.notifications import select_approval_push_mode

    assert select_approval_push_mode(park_count=1, digest_already_emitted=False) == "single"
    assert select_approval_push_mode(park_count=3, digest_already_emitted=False) == "single"
    assert select_approval_push_mode(park_count=4, digest_already_emitted=False) == "burst_digest"
    assert select_approval_push_mode(park_count=5, digest_already_emitted=True) == "collapsed"
    from butlers.modules.approvals.notifications import build_approval_digest_envelope

    digest = build_approval_digest_envelope(
        pending_count=4,
        origin_butler="relationship",
        owner_recipient="100200300",
        dashboard_base_url="https://attacker.example.test",
    )
    assert digest["actions"] == [
        {
            "verb": "open_dashboard",
            "reference": {"kind": "self", "route_key": "approvals_queue", "params": {}},
        }
    ]
    assert "https://" not in digest["delivery"]["message"]


def test_choose_and_labels_are_refused_on_approval_requests() -> None:
    from pydantic import ValidationError

    from butlers.tools.switchboard.contracts import parse_notify_request

    base = {
        "schema_version": "notify.v1",
        "origin_butler": "relationship",
        "delivery": {
            "intent": "approval_request",
            "channel": "telegram",
            "message": "Approval needed.",
            "recipient": "100200300",
        },
    }
    approve = {"verb": "approve", "callback_token": "apr1:x"}
    reject = {"verb": "reject", "callback_token": "apr1:y"}
    with pytest.raises(ValidationError):
        parse_notify_request(
            {
                **base,
                "actions": [
                    approve,
                    reject,
                    {
                        "verb": "choose",
                        "label": "Ship",
                        "callback_token": "dsk1:retired",
                    },
                    {
                        "verb": "open_dashboard",
                        "reference": {"kind": "self", "route_key": "approvals_queue", "params": {}},
                    },
                ],
            }
        )
    with pytest.raises(ValidationError, match="label"):
        parse_notify_request(
            {
                **base,
                "actions": [
                    {**approve, "label": "Yes"},
                    reject,
                    {
                        "verb": "open_dashboard",
                        "reference": {"kind": "self", "route_key": "approvals_queue", "params": {}},
                    },
                ],
            }
        )
