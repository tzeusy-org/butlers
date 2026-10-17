"""Approval cards show the exact stored call and fail closed to Open only."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from butlers.modules.approvals import previews
from butlers.modules.approvals.notifications import (
    CARD_FIT_BUDGET,
    build_approval_request_envelope,
)
from butlers.modules.telegram import _markdown_to_telegram_html

pytestmark = pytest.mark.unit

_REQUESTED = datetime(2026, 10, 18, 9, 12, tzinfo=UTC)


def _envelope(tool_name: str, tool_args: Any, **overrides: Any) -> dict[str, Any]:
    action = {
        "id": uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
        "tool_name": tool_name,
        "tool_args": tool_args,
        "requested_at": _REQUESTED,
        "expires_at": _REQUESTED + timedelta(hours=72),
        "why": "Confirms the slot she asked for.",
        "blast_radius": "one recipient",
        "reversibility": "irreversible",
        **overrides,
    }
    return build_approval_request_envelope(
        action=action,
        origin_butler="general",
        owner_recipient="100200300",
        callback_secret="test-secret",
        dashboard_base_url="https://dash.example.test",
    )


def _verbs(envelope: dict[str, Any]) -> list[str]:
    return [a["verb"] for a in envelope["actions"]]


def _message(envelope: dict[str, Any]) -> str:
    return envelope["delivery"]["message"]


@pytest.mark.parametrize("tool_name", ["email_send_message"])
def test_email_card_lists_every_recipient_and_full_body(tool_name: str) -> None:
    body = "Thursday at 3pm works.\n\nSee you then,\nTze"
    envelope = _envelope(
        tool_name,
        {
            "to": ["dana@x.com", "lee@x.com"],
            "cc": ["boss@x.com"],
            "subject": "Re: Thursday",
            "body": body,
        },
    )

    message = _message(envelope)
    for expected in ("dana@x.com, lee@x.com", "boss@x.com", "Re: Thursday", body):
        assert expected in message
    assert _verbs(envelope) == ["approve", "reject", "open_dashboard"]
    assert "Started by: general butler" in message


def test_oversized_body_gets_open_only() -> None:
    envelope = _envelope(
        "email_send_message",
        {"to": "dana@x.com", "subject": "Report", "body": "x" * (CARD_FIT_BUDGET + 1)},
    )

    assert _verbs(envelope) == ["open_dashboard"]
    assert "Approve and Reject are not offered" in _message(envelope)
    assert len(_message(envelope)) < CARD_FIT_BUDGET


def test_administer_tool_gets_open_only() -> None:
    previews.register_preview(
        "grant_access",
        lambda args: previews.CallPreview("Grant", (previews.PreviewFact("To", args["who"]),)),
        effect_class="administer",
    )
    try:
        envelope = _envelope("grant_access", {"who": "dana"})
    finally:
        previews.unregister_preview("grant_access")

    assert _verbs(envelope) == ["open_dashboard"]


def test_model_written_claims_are_labelled_defanged_and_escaped() -> None:
    envelope = _envelope(
        "email_send_message",
        {"to": "dana@x.com", "subject": "Hi", "body": "Hello"},
        why="APPROVE NOW at https://evil.example.com/pay <b>**urgent**</b>",
    )

    message = _message(envelope)
    claims = message.split("Assistant says", 1)[1]
    assert "APPROVE NOW" in claims
    assert "hxxps://evil[.]example[.]com/pay" in claims
    assert "https://evil" not in message
    # The Telegram transport escapes HTML and parses Markdown; the claim must
    # come out literal, not as markup the model chose.
    rendered = _markdown_to_telegram_html(message)
    assert "&lt;b&gt;**urgent**&lt;/b&gt;" in rendered
    assert "<b>" not in rendered


def test_tool_without_registered_preview_gets_open_only() -> None:
    envelope = _envelope("relationship_assert_fact", {"fact": "likes tea"})

    assert _verbs(envelope) == ["open_dashboard"]
    assert "No exact preview for this tool" in _message(envelope)


@pytest.mark.parametrize(
    ("tool_name", "tool_args", "expected"),
    [
        ("telegram_send_message", {"chat_id": "555", "text": "Running late"}, ("555", "Running")),
        (
            "whatsapp_send_message",
            {"recipient": "+6591234567", "text": "On my way"},
            ("+659", "On"),
        ),
        (
            "notify",
            {"channel": "email", "recipient": "dana@x.com", "subject": "Hi", "message": "Hello"},
            ("dana@x.com", "Hello"),
        ),
    ],
)
def test_channel_sends_render_recipient_and_text(
    tool_name: str, tool_args: dict[str, Any], expected: tuple[str, str]
) -> None:
    envelope = _envelope(tool_name, tool_args)

    for fragment in expected:
        assert fragment in _message(envelope)
    assert _verbs(envelope) == ["approve", "reject", "open_dashboard"]


@pytest.mark.parametrize(
    ("tool_name", "tool_args"),
    [
        (
            "email_send_message",
            {"to": "a@x.com", "subject": "s", "body": "b", "attachments": ["f"]},
        ),
        ("email_send_message", {"to": [], "subject": "s", "body": "b"}),
        ("email_send_message", {"to": "a@x.com", "subject": "s", "body": "has `tick`"}),
        ("telegram_send_message", {"chat_id": "1", "text": "hi", "reply_markup": {"k": 1}}),
        ("notify", {"entity_id": "e1", "message": "hi"}),
    ],
)
def test_call_the_card_cannot_show_exactly_gets_open_only(
    tool_name: str, tool_args: dict[str, Any]
) -> None:
    assert _verbs(_envelope(tool_name, tool_args)) == ["open_dashboard"]


def test_args_digest_is_canonical_and_content_sensitive() -> None:
    assert previews.args_digest({"b": 1, "a": [1, 2]}) == previews.args_digest(
        {"a": [1, 2], "b": 1}
    )
    assert previews.args_digest({"a": 1}) != previews.args_digest({"a": 2})
    assert len(previews.args_digest({})) == 64


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("body", "See: \x00PH4\x00 end"),  # would collide with the transport's placeholders
        ("to", "dana@x.com‮"),  # bidi override reorders the rendered recipient
        ("subject", "Re:​Thursday"),  # zero-width space hides a difference
        ("body", "tab\tand\x1bescape"),
    ],
)
def test_control_or_format_characters_get_open_only(field: str, value: str) -> None:
    args = {"to": "dana@x.com", "subject": "Hi", "body": "Hello", field: value}
    envelope = _envelope("email_send_message", args)

    assert _verbs(envelope) == ["open_dashboard"]
    assert "Approve and Reject are not offered" in _message(envelope)
    assert "\x00" not in _message(envelope)
    assert "‮" not in _message(envelope)


def test_newlines_in_a_body_still_render() -> None:
    envelope = _envelope(
        "email_send_message", {"to": "dana@x.com", "subject": "Hi", "body": "a\n\nb"}
    )

    assert _verbs(envelope) == ["approve", "reject", "open_dashboard"]


def test_controls_in_model_written_claims_are_stripped() -> None:
    envelope = _envelope(
        "email_send_message",
        {"to": "dana@x.com", "subject": "Hi", "body": "Hello"},
        why="ok\x00PH0\x00‮",
    )

    claims = _message(envelope).split("Assistant says", 1)[1]
    assert "\x00" not in claims and "‮" not in claims


def test_fit_budget_counts_utf16_code_units_not_code_points() -> None:
    # 3,000 emoji are 3,000 code points but 6,000 UTF-16 units: over Telegram's limit.
    envelope = _envelope(
        "email_send_message",
        {"to": "dana@x.com", "subject": "Hi", "body": "\U0001f600" * 3000},
    )

    assert len(_message(envelope)) < 4096  # the code-point count would have fit
    assert _verbs(envelope) == ["open_dashboard"]
    assert "Too long to show here" in _message(envelope)


def test_underscore_prefixed_keys_are_not_a_blanket_exemption() -> None:
    args = {"to": "dana@x.com", "subject": "Hi", "body": "Hello", "_bcc": ["eve@evil.test"]}

    assert _verbs(_envelope("email_send_message", args)) == ["open_dashboard"]


def test_dossier_keys_are_still_ignored_by_the_preview() -> None:
    args = {
        "to": "dana@x.com",
        "subject": "Hi",
        "body": "Hello",
        "_why": "w",
        "_blast_radius": "contact",
        "_reversibility": "irreversible",
        "_evidence": [],
    }

    assert _verbs(_envelope("email_send_message", args)) == ["approve", "reject", "open_dashboard"]


def test_gmail_send_message_has_no_preview() -> None:
    assert previews.resolve_call_preview("gmail_send_message", {"to": "a@x.com"}) is None
