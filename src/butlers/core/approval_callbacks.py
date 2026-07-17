"""Shared signed callback tokens for deterministic approval decisions.

The approval push path mints these tokens and the Telegram bot connector verifies
them.  The signing secret is a Tier 1 system credential named
``APPROVAL_CALLBACK_SECRET``; callers must resolve it through
``CredentialStore.resolve()`` without environment fallback.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final
from uuid import UUID

APPROVAL_CALLBACK_SECRET_KEY: Final = "APPROVAL_CALLBACK_SECRET"
APPROVAL_CALLBACK_PREFIX: Final = "apr1"
MAX_APPROVAL_CALLBACK_DATA_BYTES: Final = 64
_SIGNATURE_HEX_LENGTH: Final = 16
_SUPPORTED_VERBS: Final[frozenset[str]] = frozenset({"a", "r"})
_SIGNATURE_RE = re.compile(r"^[0-9a-f]{16}$")


class ApprovalCallbackTokenError(ValueError):
    """Raised when a caller attempts to mint an invalid approval callback token."""


@dataclass(frozen=True, slots=True)
class ApprovalCallbackToken:
    """Verified action and decision verb carried by an approval callback token."""

    action_id: UUID
    verb: str


def _normalize_action_id(action_id: UUID | str) -> UUID:
    try:
        return action_id if isinstance(action_id, UUID) else UUID(str(action_id))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ApprovalCallbackTokenError("Approval callback action_id must be a UUID.") from exc


def _normalize_verb(verb: str) -> str:
    if verb not in _SUPPORTED_VERBS:
        raise ApprovalCallbackTokenError(
            "Unsupported approval callback verb; expected one of: a, r."
        )
    return verb


def _normalize_requested_at(requested_at: datetime) -> str:
    if requested_at.tzinfo is None or requested_at.utcoffset() is None:
        raise ApprovalCallbackTokenError("Approval callback requested_at must be timezone-aware.")
    return requested_at.astimezone(UTC).isoformat(timespec="microseconds")


def _secret_bytes(secret: str | bytes) -> bytes:
    if isinstance(secret, bytes):
        value = secret
    elif isinstance(secret, str):
        value = secret.encode("utf-8")
    else:
        raise ApprovalCallbackTokenError("Approval callback secret must be text or bytes.")
    if not value:
        raise ApprovalCallbackTokenError("Approval callback secret must not be empty.")
    return value


def _signature(
    *,
    action_id: UUID,
    verb: str,
    requested_at: datetime,
    secret: str | bytes,
) -> str:
    payload = f"{action_id}:{verb}:{_normalize_requested_at(requested_at)}".encode()
    digest = hmac.new(_secret_bytes(secret), payload, hashlib.sha256).hexdigest()
    return digest[:_SIGNATURE_HEX_LENGTH]


def mint_approval_callback_token(
    *,
    action_id: UUID | str,
    verb: str,
    requested_at: datetime,
    secret: str | bytes,
) -> str:
    """Mint an ``apr1`` callback token bound to one pending action and decision.

    The token deliberately contains no timestamp: callers retrieve the pending
    action's stored ``requested_at`` value before verification.  This keeps a
    UUID-backed token to 60 UTF-8 bytes, within Telegram's 64-byte limit.
    """
    normalized_action_id = _normalize_action_id(action_id)
    normalized_verb = _normalize_verb(verb)
    signature = _signature(
        action_id=normalized_action_id,
        verb=normalized_verb,
        requested_at=requested_at,
        secret=secret,
    )
    token = f"{APPROVAL_CALLBACK_PREFIX}:{normalized_action_id}:{normalized_verb}:{signature}"
    if len(token.encode("utf-8")) > MAX_APPROVAL_CALLBACK_DATA_BYTES:
        raise ApprovalCallbackTokenError(
            "Approval callback token exceeds Telegram's "
            f"{MAX_APPROVAL_CALLBACK_DATA_BYTES}-byte limit."
        )
    return token


def verify_approval_callback_token(
    token: str | object,
    *,
    requested_at: datetime,
    secret: str | bytes,
    expected_verb: str | None = None,
) -> ApprovalCallbackToken | None:
    """Return a verified approval callback token, or ``None`` for untrusted input."""
    if not isinstance(token, str) or len(token.encode("utf-8")) > MAX_APPROVAL_CALLBACK_DATA_BYTES:
        return None

    prefix, separator, remainder = token.partition(":")
    if prefix != APPROVAL_CALLBACK_PREFIX or not separator:
        return None
    action_id_raw, separator, remainder = remainder.partition(":")
    if not separator:
        return None
    verb, separator, signature = remainder.partition(":")
    if not separator or not _SIGNATURE_RE.fullmatch(signature):
        return None

    try:
        action_id = _normalize_action_id(action_id_raw)
        if str(action_id) != action_id_raw:
            return None
        normalized_verb = _normalize_verb(verb)
        if expected_verb is not None and normalized_verb != _normalize_verb(expected_verb):
            return None
        expected_signature = _signature(
            action_id=action_id,
            verb=normalized_verb,
            requested_at=requested_at,
            secret=secret,
        )
    except ApprovalCallbackTokenError:
        return None

    if not hmac.compare_digest(signature, expected_signature):
        return None
    return ApprovalCallbackToken(action_id=action_id, verb=normalized_verb)


__all__ = [
    "APPROVAL_CALLBACK_PREFIX",
    "APPROVAL_CALLBACK_SECRET_KEY",
    "MAX_APPROVAL_CALLBACK_DATA_BYTES",
    "ApprovalCallbackToken",
    "ApprovalCallbackTokenError",
    "mint_approval_callback_token",
    "verify_approval_callback_token",
]
