"""Stay in touch — cadence tracking and overdue contact detection."""

from __future__ import annotations

import uuid
from typing import Any

import asyncpg


async def stay_in_touch_set(
    pool: asyncpg.Pool,
    contact_id: uuid.UUID,
    frequency_days: int | None,
) -> dict[str, Any]:
    """Set or clear the stay-in-touch cadence for a contact.

    Writes ``stay_in_touch_days`` onto the contact's linked ``public.entities``
    row (resolved via ``contact_entity_map``), the dedicated column homed there
    by migration rel_031.  Pass frequency_days=None to clear the cadence
    (removes from overdue list).
    """
    row = await pool.fetchrow(
        """
        UPDATE public.entities e
        SET stay_in_touch_days = $2, updated_at = now()
        FROM contact_entity_map cem
        WHERE cem.contact_id = $1 AND e.id = cem.entity_id
        RETURNING cem.contact_id AS id,
                  e.id AS entity_id,
                  e.stay_in_touch_days AS stay_in_touch_days,
                  COALESCE(e.canonical_name, 'Unknown') AS name
        """,
        contact_id,
        frequency_days,
    )
    if row is None:
        raise ValueError(f"Contact {contact_id} not found")
    return dict(row)


async def contacts_overdue(pool: asyncpg.Pool) -> list[dict[str, Any]]:
    """Return contacts overdue for reach-out using tier-aware cadences.

    Effective cadence per contact:
    - If stay_in_touch_days is set: use that value (explicit override)
    - Otherwise: use the Dunbar tier's default cadence
      (tier 5=14d, 15=21d, 50=45d, 150=120d, 500=270d, 1500=never)

    Tier 1500 contacts with no stay_in_touch_days are excluded.
    Archived contacts (listed=false) are excluded.
    Contacts with no interactions and an effective cadence are always overdue.

    Returns contacts enriched with dunbar_tier, dunbar_score,
    effective_cadence, and days_since_last_interaction.
    """
    from butlers.tools.relationship.dunbar import contacts_overdue_with_tiers

    return await contacts_overdue_with_tiers(pool)
