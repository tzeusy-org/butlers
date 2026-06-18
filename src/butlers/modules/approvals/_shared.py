"""Shared primacy check helper for the approvals module.

Both the email guard (``email_guard.py``) and the approval gate (``gate.py``)
need to verify whether a specific channel address is the *primary* entry for an
entity in ``relationship.entity_facts``.  This module provides a single canonical
implementation so both code-paths enforce identical policy.

Migration bead 7 (bu-akads): primacy is now read from
``relationship.entity_facts.primary`` (a BOOL column on the triple row)
rather than from ``public.contact_info.is_primary``.

Public surface
--------------
- :func:`is_primary_contact` — returns True when (entity_id, channel_type,
  channel_value) matches an active triple in ``relationship.entity_facts``
  with ``"primary" = true``.
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import asyncpg

from butlers.identity import _TELEGRAM_USERNAME_CHANNEL_TYPES, _telegram_username_candidates

logger = logging.getLogger(__name__)

# Channel-type → predicate mapping (mirrors identity._CHANNEL_TYPE_TO_PREDICATE)
# telegram_chat_id maps to has-handle per RFC 0004 Amendment 3 (bu-oluyt.1):
# non-secret routing handles belong in entity_facts, not entity_info.
_CHANNEL_TYPE_TO_PREDICATE: dict[str, str] = {
    "email": "has-email",
    "phone": "has-phone",
    "telegram": "has-handle",
    "telegram_user_id": "has-handle",
    "telegram_user_client": "has-handle",
    "telegram_chat_id": "has-handle",  # non-secret routing handle → entity_facts
    "linkedin": "has-handle",
    "twitter": "has-handle",
    "website": "has-website",
    "other": "has-handle",
    "whatsapp_jid": "has-handle",
}


async def is_primary_contact(
    pool: asyncpg.Pool,
    entity_id: uuid.UUID,
    channel_type: str,
    channel_value: str,
) -> bool:
    """Return True if *channel_value* is the primary entry for *entity_id*/*channel_type*.

    Queries ``relationship.entity_facts`` for the matching active triple and
    returns the value of the ``"primary"`` column.  Returns ``False`` on any
    DB error or missing row so that non-primary addresses fall through to the
    rules/parking flow.

    Migration bead 7 (bu-akads): previously queried ``public.contact_info``
    with ``contact_id``.  Now queries ``relationship.entity_facts`` with
    ``entity_id`` and the mapped predicate.

    Parameters
    ----------
    pool:
        asyncpg connection pool (must have SELECT on ``relationship.entity_facts``).
    entity_id:
        UUID of the entity in ``public.entities``.
    channel_type:
        The channel type (e.g. ``"email"``, ``"telegram"``).
    channel_value:
        The channel value to check (e.g. an email address or chat ID).

    Returns
    -------
    bool
        ``True`` when the triple exists, is active, and ``"primary"`` is true;
        ``False`` otherwise (triple absent, not primary, or DB error).

    Notes
    -----
    Only meaningful for channel-based lookups.  ``contact_id`` dispatch has no
    specific address to check and must be handled separately by the caller.
    """
    predicate = _CHANNEL_TYPE_TO_PREDICATE.get(channel_type)
    if predicate is None:
        # Unknown channel type — treat as non-primary
        return False

    # Build the ordered list of values to check.  For Telegram username channel
    # types, outbound tools may supply '@Username' while the canonical storage
    # form (from the contacts backfill) strips the leading '@'.  Apply the same
    # normalisation as resolve_contact_by_channel so the primacy check stays
    # consistent with resolution (bu-c4f7f).
    if channel_type in _TELEGRAM_USERNAME_CHANNEL_TYPES:
        candidates = _telegram_username_candidates(channel_value)
    else:
        candidates = [channel_value]

    try:
        for candidate in candidates:
            row = await pool.fetchrow(
                """
                SELECT "primary"
                FROM relationship.entity_facts
                WHERE subject    = $1
                  AND predicate  = $2
                  AND object     = $3
                  AND object_kind = 'literal'
                  AND validity   = 'active'
                LIMIT 1
                """,
                entity_id,
                predicate,
                candidate,
            )
            if row is not None:
                return bool(row["primary"])
        return False
    except Exception:  # noqa: BLE001
        logger.warning(
            "approvals: could not determine is_primary for entity %s %s=%r; "
            "treating as non-primary (will fall through to rules/park flow)",
            entity_id,
            channel_type,
            channel_value,
            exc_info=True,
        )
        return False
