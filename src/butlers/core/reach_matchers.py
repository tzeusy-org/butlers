"""Parse the matcher words of a Telegram ``/reach add`` command.

A proposal names fixed matchers only: a sender domain, a channel, a security kind and a scope.
Free text is never carried: every word must be a recognised ``key:value`` pair (or a bare domain)
whose value is bounded and drawn from the same closed sets as a stored clause, otherwise the
whole command is refused. The database definer validates the result again.
"""

from __future__ import annotations

import re

CHANNELS = frozenset({"telegram", "email", "whatsapp", "dashboard"})
SECURITY_KINDS = frozenset(
    {"new_sign_in", "password_changed", "mfa_changed", "recovery_changed", "deletion_scheduled"}
)
SCOPES = frozenset({"always", "quiet_hours_only"})
_DOMAIN = re.compile(
    r"(?=.{1,253}\Z)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*"
)
_MAX_WORDS = 4


def parse_reach_matchers(words: str) -> dict[str, str] | None:
    """Return ``{"sender_domain"|"channel"|"security_kind"|"scope": value}`` or ``None``.

    ``None`` means the text is not a valid proposal (no matcher, unknown field, repeated field,
    an out-of-set value or any word that is not a matcher). ``scope`` alone is not a matcher.
    """
    parts = words.split()
    if not parts or len(parts) > _MAX_WORDS:
        return None
    result: dict[str, str] = {}
    for part in parts:
        key, sep, value = part.partition(":")
        if not sep:
            key, value = "domain", part
        value = value.lower()
        field, allowed = {
            "domain": ("sender_domain", None),
            "channel": ("channel", CHANNELS),
            "kind": ("security_kind", SECURITY_KINDS),
            "scope": ("scope", SCOPES),
        }.get(key.lower(), (None, None))
        if field is None or field in result:
            return None
        if allowed is not None and value not in allowed:
            return None
        if field == "sender_domain" and _DOMAIN.fullmatch(value) is None:
            return None
        result[field] = value
    if not any(k in result for k in ("sender_domain", "channel", "security_kind")):
        return None
    return result
