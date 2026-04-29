"""Shared primacy check helper for the approvals module.

Both the email guard (``email_guard.py``) and the approval gate (``gate.py``)
need to verify whether a specific channel address is the *primary* entry for a
contact in ``public.contact_info``.  This module provides a single canonical
implementation so both code-paths enforce identical policy.

Public surface
--------------
- :func:`is_primary_contact` — returns True when (contact_id, channel_type,
  channel_value) matches a row in ``public.contact_info`` with ``is_primary=True``.
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import asyncpg

logger = logging.getLogger(__name__)


async def is_primary_contact(
    pool: asyncpg.Pool,
    contact_id: uuid.UUID,
    channel_type: str,
    channel_value: str,
) -> bool:
    """Return True if *channel_value* is the primary entry for *contact_id*/*channel_type*.

    Queries ``public.contact_info`` for the matching ``(contact_id, type, value)``
    row and returns ``is_primary``.  Returns ``False`` on any DB error or missing
    row so that non-primary addresses fall through to the rules/parking flow.

    Parameters
    ----------
    pool:
        asyncpg connection pool (must have SELECT on ``public.contact_info``).
    contact_id:
        UUID of the contact in ``public.contacts``.
    channel_type:
        The ``contact_info.type`` value (e.g. ``"email"``, ``"telegram"``).
    channel_value:
        The ``contact_info.value`` to check (e.g. an email address or chat ID).

    Returns
    -------
    bool
        ``True`` when the row exists and ``is_primary`` is set; ``False``
        otherwise (row absent, ``is_primary`` false, or DB error).

    Notes
    -----
    Only meaningful for channel-based lookups.  ``contact_id`` dispatch has no
    specific address to check and must be handled separately by the caller.
    """
    try:
        row = await pool.fetchrow(
            """
            SELECT is_primary
            FROM public.contact_info
            WHERE contact_id = $1
              AND type = $2
              AND value = $3
            """,
            contact_id,
            channel_type,
            channel_value,
        )
        if row is None:
            return False
        return bool(row["is_primary"])
    except Exception:  # noqa: BLE001
        logger.warning(
            "approvals: could not determine is_primary for contact %s %s=%r; "
            "treating as non-primary (will fall through to rules/park flow)",
            contact_id,
            channel_type,
            channel_value,
            exc_info=True,
        )
        return False
