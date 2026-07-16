"""Unit tests for the shared Telegram approval callback-token contract."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest

from butlers.core.approval_callbacks import (
    ApprovalCallbackTokenError,
    mint_approval_callback_token,
    verify_approval_callback_token,
)

pytestmark = pytest.mark.unit

_ACTION_ID = UUID("12345678-1234-5678-1234-567812345678")
_REQUESTED_AT = datetime(2026, 7, 17, 1, 2, 3, 456789, tzinfo=UTC)
_SECRET = "test-only-approval-callback-secret"


def test_callback_token_round_trip_is_telegram_safe() -> None:
    token = mint_approval_callback_token(
        action_id=_ACTION_ID,
        verb="a",
        requested_at=_REQUESTED_AT,
        secret=_SECRET,
    )

    verified = verify_approval_callback_token(
        token,
        requested_at=_REQUESTED_AT,
        secret=_SECRET,
    )

    assert token.startswith("apr1:12345678-1234-5678-1234-567812345678:a:")
    assert len(token.encode("utf-8")) == 60
    assert verified is not None
    assert verified.action_id == _ACTION_ID
    assert verified.verb == "a"


def test_callback_token_rejects_tampered_hmac() -> None:
    token = mint_approval_callback_token(
        action_id=_ACTION_ID,
        verb="a",
        requested_at=_REQUESTED_AT,
        secret=_SECRET,
    )
    tampered = f"{token[:-1]}{'0' if token[-1] != '0' else '1'}"

    assert (
        verify_approval_callback_token(
            tampered,
            requested_at=_REQUESTED_AT,
            secret=_SECRET,
        )
        is None
    )


def test_callback_token_binds_requested_at() -> None:
    token = mint_approval_callback_token(
        action_id=_ACTION_ID,
        verb="a",
        requested_at=_REQUESTED_AT,
        secret=_SECRET,
    )

    assert (
        verify_approval_callback_token(
            token,
            requested_at=_REQUESTED_AT.replace(microsecond=0),
            secret=_SECRET,
        )
        is None
    )


def test_callback_token_rejects_wrong_verb() -> None:
    token = mint_approval_callback_token(
        action_id=_ACTION_ID,
        verb="a",
        requested_at=_REQUESTED_AT,
        secret=_SECRET,
    )

    assert (
        verify_approval_callback_token(
            token,
            requested_at=_REQUESTED_AT,
            secret=_SECRET,
            expected_verb="r",
        )
        is None
    )
    with pytest.raises(ApprovalCallbackTokenError, match="Unsupported approval callback verb"):
        mint_approval_callback_token(
            action_id=_ACTION_ID,
            verb="approve",
            requested_at=_REQUESTED_AT,
            secret=_SECRET,
        )


def test_callback_token_rejects_oversize_payload() -> None:
    oversized = f"apr1:{_ACTION_ID}:a:{'0' * 21}"

    assert len(oversized.encode("utf-8")) > 64
    assert (
        verify_approval_callback_token(
            oversized,
            requested_at=_REQUESTED_AT,
            secret=_SECRET,
        )
        is None
    )
