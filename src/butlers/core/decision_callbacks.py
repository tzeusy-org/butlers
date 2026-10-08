"""Signed one-tap callback tokens for Owner Decision Desk prompts (bu-ckkpz.3).

``dsk1:<prompt id>:<option index>:<hmac16>`` names one option of one decision
prompt. It reuses the RFC 0021 approval-token signer and Tier-1 secret
(``APPROVAL_CALLBACK_SECRET``); the ``dsk1`` prefix is inside the signed payload,
so an ``apr1`` signature can never verify as a decision tap or vice versa. As
with ``apr1``, the binding time (the prompt's ``created_at``) is not in the
token: the verifier loads it from the prompt row. A UUID prompt id and a
two-digit index make the token at most 61 bytes, inside Telegram's 64-byte
``callback_data`` limit.
"""

from __future__ import annotations

import hmac
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Final
from uuid import UUID

from butlers.core.approval_callbacks import (
    MAX_APPROVAL_CALLBACK_DATA_BYTES,
    ApprovalCallbackTokenError,
    callback_timestamp,
    sign_callback_payload,
)

DECISION_CALLBACK_PREFIX: Final = "dsk1"
MAX_DECISION_OPTIONS: Final = 16
_SIGNATURE_RE = re.compile(r"^[0-9a-f]{16}$")
_INDEX_RE = re.compile(r"^(0|[1-9][0-9]?)$")


@dataclass(frozen=True, slots=True)
class DecisionCallbackToken:
    prompt_id: UUID
    option_index: int


def _signature(
    *, prompt_id: UUID, option_index: int, created_at: datetime, secret: str | bytes
) -> str:
    bound_at = callback_timestamp(created_at)
    payload = f"{DECISION_CALLBACK_PREFIX}:{prompt_id}:{option_index}:{bound_at}"
    return sign_callback_payload(payload, secret=secret)


def mint_decision_callback_token(
    *,
    prompt_id: UUID | str,
    option_index: int,
    created_at: datetime,
    secret: str | bytes,
) -> str:
    """Mint the ``dsk1`` token for option *option_index* of prompt *prompt_id*."""
    try:
        normalized_id = prompt_id if isinstance(prompt_id, UUID) else UUID(str(prompt_id))
    except ValueError as exc:
        raise ApprovalCallbackTokenError("Decision callback prompt_id must be a UUID.") from exc
    if (
        isinstance(option_index, bool)
        or not isinstance(option_index, int)
        or not 0 <= option_index < MAX_DECISION_OPTIONS
    ):
        raise ApprovalCallbackTokenError("Decision callback option_index is out of range.")
    signature = _signature(
        prompt_id=normalized_id, option_index=option_index, created_at=created_at, secret=secret
    )
    token = f"{DECISION_CALLBACK_PREFIX}:{normalized_id}:{option_index}:{signature}"
    if len(token.encode("utf-8")) > MAX_APPROVAL_CALLBACK_DATA_BYTES:
        raise ApprovalCallbackTokenError("Decision callback token exceeds Telegram's limit.")
    return token


def parse_decision_callback_token(token: str | object) -> DecisionCallbackToken | None:
    """Extract an *unverified* prompt id and index, only to look the prompt up."""
    if not isinstance(token, str) or len(token.encode("utf-8")) > MAX_APPROVAL_CALLBACK_DATA_BYTES:
        return None
    parts = token.split(":")
    if len(parts) != 4 or parts[0] != DECISION_CALLBACK_PREFIX:
        return None
    _, raw_id, raw_index, signature = parts
    if not _INDEX_RE.fullmatch(raw_index) or not _SIGNATURE_RE.fullmatch(signature):
        return None
    try:
        prompt_id = UUID(raw_id)
    except ValueError:
        return None
    if str(prompt_id) != raw_id or int(raw_index) >= MAX_DECISION_OPTIONS:
        return None
    return DecisionCallbackToken(prompt_id=prompt_id, option_index=int(raw_index))


def verify_decision_callback_token(
    token: str | object,
    *,
    created_at: datetime,
    secret: str | bytes,
) -> DecisionCallbackToken | None:
    """Return the verified token, or ``None`` for any untrusted input."""
    parsed = parse_decision_callback_token(token)
    if parsed is None:
        return None
    try:
        expected = _signature(
            prompt_id=parsed.prompt_id,
            option_index=parsed.option_index,
            created_at=created_at,
            secret=secret,
        )
    except ApprovalCallbackTokenError:
        return None
    if not hmac.compare_digest(str(token).rsplit(":", 1)[-1], expected):
        return None
    return parsed


__all__ = [
    "DECISION_CALLBACK_PREFIX",
    "MAX_DECISION_OPTIONS",
    "DecisionCallbackToken",
    "mint_decision_callback_token",
    "parse_decision_callback_token",
    "verify_decision_callback_token",
]
