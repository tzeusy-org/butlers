"""Decision Desk one-tap prompts: ``dsk1`` tokens, the notify contract, and markup.

bu-ckkpz.3, REQ-owner-decision-desk-004. Unit-level: no database, no network.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from butlers.core.approval_callbacks import (
    ApprovalCallbackTokenError,
    mint_approval_callback_token,
    verify_approval_callback_token,
)
from butlers.core.decision_callbacks import (
    mint_decision_callback_token,
    parse_decision_callback_token,
    verify_decision_callback_token,
)
from butlers.core_tools._routing import _owner_control_reply_markup
from butlers.jobs.decision_routing import build_decision_request_envelope, compose_prompt_message
from butlers.tools.switchboard.routing.contracts import parse_notify_request
from tests.decision_desk_helpers import make_bead

pytestmark = pytest.mark.unit

_PROMPT_ID = uuid.UUID("12345678-1234-5678-1234-567812345678")
_CREATED_AT = datetime(2026, 10, 8, 1, 2, 3, 456789, tzinfo=UTC)
_SECRET = "test-only-decision-callback-secret"
_DASHBOARD = "https://dashboard.example.test/decisions?bead=bu-test1"


def _token(index: int = 0, *, created_at: datetime = _CREATED_AT, secret: str = _SECRET) -> str:
    return mint_decision_callback_token(
        prompt_id=_PROMPT_ID, option_index=index, created_at=created_at, secret=secret
    )


# ---------------------------------------------------------------------------
# dsk1 tokens
# ---------------------------------------------------------------------------


def test_token_round_trip_fits_telegram_callback_data() -> None:
    token = _token(15)

    verified = verify_decision_callback_token(token, created_at=_CREATED_AT, secret=_SECRET)

    assert token.startswith(f"dsk1:{_PROMPT_ID}:15:")
    assert len(token.encode("utf-8")) <= 64
    assert verified is not None
    assert (verified.prompt_id, verified.option_index) == (_PROMPT_ID, 15)


@pytest.mark.parametrize(
    ("created_at", "secret"),
    [
        (_CREATED_AT + timedelta(microseconds=1), _SECRET),
        (_CREATED_AT, "another-secret"),
    ],
)
def test_token_is_bound_to_prompt_time_and_secret(created_at, secret) -> None:
    assert verify_decision_callback_token(_token(), created_at=created_at, secret=secret) is None


def test_tampered_index_does_not_verify() -> None:
    token = _token(0)
    forged = token.replace(":0:", ":1:", 1)

    assert parse_decision_callback_token(forged) is not None
    assert verify_decision_callback_token(forged, created_at=_CREATED_AT, secret=_SECRET) is None


@pytest.mark.parametrize(
    "token",
    [
        "apr1:12345678-1234-5678-1234-567812345678:a:0123456789abcdef",
        "dsk1:12345678-1234-5678-1234-567812345678:16:0123456789abcdef",
        "dsk1:12345678-1234-5678-1234-567812345678:01:0123456789abcdef",
        "dsk1:12345678123456781234567812345678:0:0123456789abcdef",
        "dsk1:12345678-1234-5678-1234-567812345678:0:0123456789ABCDEF",
        "dsk1:12345678-1234-5678-1234-567812345678:0",
        None,
    ],
)
def test_malformed_tokens_do_not_parse(token) -> None:
    assert parse_decision_callback_token(token) is None


def test_prefixes_cannot_cross_verify() -> None:
    approval = mint_approval_callback_token(
        action_id=_PROMPT_ID, verb="a", requested_at=_CREATED_AT, secret=_SECRET
    )
    decision = _token(0)

    assert verify_decision_callback_token(approval, created_at=_CREATED_AT, secret=_SECRET) is None
    assert (
        verify_approval_callback_token(decision, requested_at=_CREATED_AT, secret=_SECRET) is None
    )


@pytest.mark.parametrize("index", [-1, 16, True])
def test_minting_rejects_out_of_range_indexes(index) -> None:
    with pytest.raises(ApprovalCallbackTokenError):
        _token(index)


# ---------------------------------------------------------------------------
# notify.v1 decision_request contract
# ---------------------------------------------------------------------------


def _choose(index: int, label: str | None = None) -> dict:
    return {
        "verb": "choose",
        "label": label if label is not None else f"{index + 1}. Option",
        "callback_token": _token(index % 16),
        "dashboard_url": _DASHBOARD,
    }


def _request(actions: list[dict], **delivery) -> dict:
    return {
        "schema_version": "notify.v1",
        "origin_butler": "switchboard",
        "delivery": {
            "intent": "decision_request",
            "channel": "telegram",
            "message": "Decision needed.",
            "recipient": "100200300",
            **delivery,
        },
        "actions": actions,
    }


_OPEN = {"verb": "open_dashboard", "dashboard_url": _DASHBOARD}


def test_decision_request_accepts_choices_and_one_dashboard_link() -> None:
    parsed = parse_notify_request(_request([_choose(0), _choose(1), _OPEN]))

    assert parsed.delivery.intent == "decision_request"
    assert [action.verb for action in parsed.actions or ()] == [
        "choose",
        "choose",
        "open_dashboard",
    ]


@pytest.mark.parametrize(
    ("actions", "match"),
    [
        ([_OPEN], "choose"),
        ([_choose(0)], "open_dashboard"),
        ([_choose(0), _OPEN, _OPEN], "open_dashboard"),
        ([_choose(i) for i in range(17)] + [_OPEN], "16"),
        ([{**_choose(0), "label": None}, _OPEN], "label"),
        ([{**_choose(0), "label": "x" * 65}, _OPEN], "64"),
        ([{**_choose(0), "callback_token": None}, _OPEN], "callback_token"),
        (
            [
                _choose(0),
                {"verb": "approve", "callback_token": _token(1), "dashboard_url": _DASHBOARD},
                _OPEN,
            ],
            "decision_request",
        ),
    ],
)
def test_decision_request_rejects_malformed_actions(actions, match) -> None:
    with pytest.raises(ValidationError, match=match):
        parse_notify_request(_request(actions))


def test_decision_request_requires_a_recipient() -> None:
    with pytest.raises(ValidationError, match="recipient"):
        parse_notify_request(_request([_choose(0), _OPEN], recipient=None))


def test_choose_and_labels_are_refused_on_approval_requests() -> None:
    approval_url = "https://dashboard.example.test/approvals/x"
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
    approve = {"verb": "approve", "callback_token": "apr1:x", "dashboard_url": approval_url}
    reject = {"verb": "reject", "callback_token": "apr1:y", "dashboard_url": approval_url}
    with pytest.raises(ValidationError):
        parse_notify_request(
            {
                **base,
                "actions": [approve, reject, _choose(0), {**_OPEN, "dashboard_url": approval_url}],
            }
        )
    with pytest.raises(ValidationError, match="label"):
        parse_notify_request(
            {
                **base,
                "actions": [
                    {**approve, "label": "Yes"},
                    reject,
                    {**_OPEN, "dashboard_url": approval_url},
                ],
            }
        )


# ---------------------------------------------------------------------------
# Messenger rendering and the routing job's envelope
# ---------------------------------------------------------------------------


def test_markup_renders_one_row_per_choice_then_the_dashboard() -> None:
    parsed = parse_notify_request(_request([_choose(0, "1. Ship"), _choose(1, "2. Hold"), _OPEN]))

    markup = _owner_control_reply_markup("decision_request", parsed.actions)

    assert markup == {
        "inline_keyboard": [
            [{"text": "1. Ship", "callback_data": _token(0)}],
            [{"text": "2. Hold", "callback_data": _token(1)}],
            [{"text": "Open dashboard", "url": _DASHBOARD}],
        ]
    }


def test_routing_envelope_parses_and_truncates_long_labels() -> None:
    bead = make_bead(options=("Ship it", "y" * 100))

    envelope = build_decision_request_envelope(
        bead=bead,
        prompt_id=_PROMPT_ID,
        created_at=_CREATED_AT,
        recipient="100200300",
        callback_secret=_SECRET,
        dashboard_base_url="https://dashboard.example.test/",
    )
    parsed = parse_notify_request(envelope)

    actions = parsed.actions or ()
    assert [a.verb for a in actions] == ["choose", "choose", "open_dashboard"]
    assert actions[0].label == "1. Ship it"
    assert len(actions[1].label or "") == 64 and (actions[1].label or "").endswith("…")
    assert actions[2].dashboard_url == "https://dashboard.example.test/decisions?bead=bu-test1"
    for index, action in enumerate(actions[:2]):
        verified = verify_decision_callback_token(
            action.callback_token, created_at=_CREATED_AT, secret=_SECRET
        )
        assert verified is not None and verified.option_index == index


def test_prompt_message_lists_every_option_in_full_and_the_default() -> None:
    long_option = "y" * 100
    message = compose_prompt_message(make_bead(options=("Ship it", long_option), default="Ship it"))

    assert "Decision needed: Pick a rollout" in message
    assert "1. Ship it" in message
    assert f"2. {long_option}" in message
    assert "Default if unanswered: Ship it" in message
