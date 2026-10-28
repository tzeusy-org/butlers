"""Confirm tokens carry information and cannot authorize approval effects."""

from uuid import uuid4

import pytest

from butlers.core.confirm_tokens import mint_confirm_token, verify_confirm_token

pytestmark = pytest.mark.unit


def test_recipient_bound_tokens_reject_tampering_and_foreign_domains():
    prompt = uuid4()
    token = mint_confirm_token(prompt, 2, chat_id="-10042", secret="synthetic-only")
    assert len(token.encode()) <= 64
    assert verify_confirm_token(token, chat_id="-10042", secret="synthetic-only") == (prompt, 2)
    for bad, chat, secret in (
        (token.replace(":2:", ":1:"), "-10042", "synthetic-only"),
        (token, "other-chat", "synthetic-only"),
        (token, "-10042", "wrong"),
        (token.replace("cfm1:", "apr1:"), "-10042", "synthetic-only"),
        ("cgi:2026-10-27:correct", "-10042", "synthetic-only"),
        (token + "x", "-10042", "synthetic-only"),
        (None, "-10042", "synthetic-only"),
    ):
        assert verify_confirm_token(bad, chat_id=chat, secret=secret) is None
    for index in (-1, 4, True):
        with pytest.raises(ValueError):
            mint_confirm_token(prompt, index, chat_id="42", secret="synthetic-only")
