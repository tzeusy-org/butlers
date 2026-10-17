"""RFC 0021 signer with a separate ifb1 domain and server-created-time binding.

A token is 60 bytes: ifb1:<candidate UUID>:<u|n|x>:<hmac16>. The creation
instant is never caller-authoritative; verifiers load it from the candidate.
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

INSIGHT_FEEDBACK_PREFIX: Final = "ifb1"
FEEDBACK_VERBS: Final = {"useful": "u", "not_now": "n", "never": "x"}
_SIGNATURE_RE = re.compile(r"^[0-9a-f]{16}$")


@dataclass(frozen=True, slots=True)
class InsightFeedbackToken:
    insight_id: UUID
    verdict: str


def _signature(*, insight_id: UUID, verdict: str, created_at: datetime, secret: str | bytes) -> str:
    bound_at = callback_timestamp(created_at)
    payload = f"{INSIGHT_FEEDBACK_PREFIX}:{insight_id}:{FEEDBACK_VERBS[verdict]}:{bound_at}"
    return sign_callback_payload(payload, secret=secret)


def mint_insight_feedback_token(
    *,
    insight_id: UUID | str,
    verdict: str,
    created_at: datetime,
    secret: str | bytes,
) -> str:
    """Mint a bounded verdict token for one insight."""
    try:
        normalized_id = insight_id if isinstance(insight_id, UUID) else UUID(str(insight_id))
    except ValueError as exc:
        raise ApprovalCallbackTokenError("Insight feedback insight_id must be a UUID.") from exc
    if verdict not in FEEDBACK_VERBS:
        raise ApprovalCallbackTokenError("Unsupported insight feedback verdict.")
    signature = _signature(
        insight_id=normalized_id, verdict=verdict, created_at=created_at, secret=secret
    )
    token = f"{INSIGHT_FEEDBACK_PREFIX}:{normalized_id}:{FEEDBACK_VERBS[verdict]}:{signature}"
    if len(token.encode("utf-8")) > MAX_APPROVAL_CALLBACK_DATA_BYTES:
        raise ApprovalCallbackTokenError("Insight feedback token exceeds Telegram's limit.")
    return token


def parse_insight_feedback_token(token: str | object) -> InsightFeedbackToken | None:
    """Extract an *unverified* insight id and verdict, only to look the insight up."""
    if not isinstance(token, str):
        return None
    try:
        if len(token.encode("utf-8")) > MAX_APPROVAL_CALLBACK_DATA_BYTES:
            return None
    except UnicodeEncodeError:
        return None
    parts = token.split(":")
    if len(parts) != 4 or parts[0] != INSIGHT_FEEDBACK_PREFIX:
        return None
    _, raw_id, raw_verb, signature = parts
    if raw_verb not in FEEDBACK_VERBS.values() or not _SIGNATURE_RE.fullmatch(signature):
        return None
    try:
        insight_id = UUID(raw_id)
    except ValueError:
        return None
    if str(insight_id) != raw_id:
        return None
    return InsightFeedbackToken(
        insight_id=insight_id, verdict=next(k for k, v in FEEDBACK_VERBS.items() if v == raw_verb)
    )


def verify_insight_feedback_token(
    token: str | object,
    *,
    created_at: datetime,
    secret: str | bytes,
) -> InsightFeedbackToken | None:
    """Return the verified token, or ``None`` for any untrusted input."""
    parsed = parse_insight_feedback_token(token)
    if parsed is None:
        return None
    try:
        expected = _signature(
            insight_id=parsed.insight_id,
            verdict=parsed.verdict,
            created_at=created_at,
            secret=secret,
        )
    except ApprovalCallbackTokenError:
        return None
    if not hmac.compare_digest(str(token).rsplit(":", 1)[-1], expected):
        return None
    return parsed
