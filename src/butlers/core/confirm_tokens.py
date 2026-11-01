"""Recipient-bound informational callbacks, separate from approval decisions.

The shared Tier-1 signing key is resolved by callers without environment fallback.
No database lookup is needed to reject a forged callback.
"""

from __future__ import annotations

import hmac
import re
from uuid import UUID

from butlers.core.approval_callbacks import sign_callback_payload

CONFIRM_PREFIX = "cfm1"
_TOKEN = re.compile(r"cfm1:([0-9a-f-]{36}):([0-3]):([0-9a-f]{16})\Z")


def mint_confirm_token(
    confirm_id: UUID, option_index: int, *, chat_id: str, secret: str | bytes
) -> str:
    """Sign one immutable option for a resolved chat; sender is checked at resolution."""
    if (
        not isinstance(confirm_id, UUID)
        or type(option_index) is not int
        or not 0 <= option_index < 4
    ):
        raise ValueError("confirm requires a UUID and option index between 0 and 3")
    if not isinstance(chat_id, str) or not chat_id or len(chat_id) > 128:
        raise ValueError("confirm requires a resolved chat identity")
    signature = sign_callback_payload(f"cfm1|{confirm_id}|{option_index}|{chat_id}", secret=secret)
    return f"cfm1:{confirm_id}:{option_index}:{signature}"


def verify_confirm_token(
    token: object, *, chat_id: str, secret: str | bytes
) -> tuple[UUID, int] | None:
    """Return verified prompt/option identity, or None for untrusted transport input."""
    if not isinstance(token, str) or len(token.encode()) > 64:
        return None
    match = _TOKEN.fullmatch(token)
    if match is None:
        return None
    try:
        prompt, option = UUID(match[1]), int(match[2])
        if str(prompt) != match[1]:
            return None
        expected = mint_confirm_token(prompt, option, chat_id=chat_id, secret=secret)
    except (ValueError, TypeError):
        return None
    return (prompt, option) if hmac.compare_digest(token, expected) else None
