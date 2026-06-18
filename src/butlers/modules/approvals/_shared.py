"""Shared primacy check helper for the approvals module.

Both the email guard (``email_guard.py``) and the approval gate (``gate.py``)
need to verify whether a specific channel address is the *primary* entry for an
entity in ``relationship.entity_facts``.  This module provides a single canonical
implementation so both code-paths enforce identical policy.

Primacy is read from ``relationship.entity_facts.primary`` (a BOOL column on
the triple row).

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

# Sourced from butlers.identity so the approval gate's primacy check stays
# byte-for-byte consistent with reverse-resolution — a drift here silently breaks
# the owner auto-approve bypass (the exact failure mode this module guards).
from butlers.identity import (
    _CHANNEL_TYPE_TO_PREDICATE,
    _TELEGRAM_PREFIX_CHANNEL_TYPES,
    _TELEGRAM_USERNAME_CHANNEL_TYPES,
    _telegram_prefixed_value,
    _telegram_username_candidates,
)

logger = logging.getLogger(__name__)


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

    Queries ``relationship.entity_facts`` with ``entity_id`` and the mapped
    predicate.

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
    # Telegram handles are stored canonically prefixed (telegram:<bare>, rel_019).
    # Add the prefixed form for every telegram channel type so a numeric chat id
    # or @username is primacy-checked against its real stored triple.
    if channel_type in _TELEGRAM_PREFIX_CHANNEL_TYPES:
        prefixed = _telegram_prefixed_value(channel_value)
        if prefixed not in candidates:
            candidates.append(prefixed)

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
