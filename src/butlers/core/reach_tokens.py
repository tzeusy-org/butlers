"""Chat-bound Telegram button tokens for revoking one break-through clause.

``rch1:<clause uuid>:<16 hex>`` fits Telegram's 64-byte callback_data limit (58 bytes). The
signature binds the clause to the chat it was listed in, so a token lifted into another chat or
forged without the connector secret is rejected before any API call. The token only ever names a
revoke; the sender is separately verified as the primary owner channel at the connector and again
at the API.
"""

from __future__ import annotations

import hmac
import re
from uuid import UUID

from butlers.core.approval_callbacks import sign_callback_payload

REACH_PREFIX = "rch1"
_TOKEN = re.compile(r"rch1:([0-9a-f-]{36}):([0-9a-f]{16})\Z")


def mint_reach_revoke_token(clause_id: UUID, *, chat_id: str, secret: str | bytes) -> str:
    """Sign one clause revoke for a resolved chat."""
    if not isinstance(clause_id, UUID):
        raise ValueError("a reach token requires a clause UUID")
    if not isinstance(chat_id, str) or not chat_id or len(chat_id) > 128:
        raise ValueError("a reach token requires a resolved chat identity")
    signature = sign_callback_payload(f"rch1|{clause_id}|{chat_id}", secret=secret)
    return f"rch1:{clause_id}:{signature}"


def verify_reach_revoke_token(token: object, *, chat_id: str, secret: str | bytes) -> UUID | None:
    """Return the clause id for a genuine token minted for this chat, else None."""
    if not isinstance(token, str) or len(token.encode()) > 64:
        return None
    match = _TOKEN.fullmatch(token)
    if match is None:
        return None
    try:
        clause_id = UUID(match[1])
        if str(clause_id) != match[1]:
            return None
        expected = mint_reach_revoke_token(clause_id, chat_id=chat_id, secret=secret)
    except (ValueError, TypeError):
        return None
    return clause_id if hmac.compare_digest(token, expected) else None
