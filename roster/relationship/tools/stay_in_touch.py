"""Stay in touch — cadence tracking and overdue contact detection."""

from __future__ import annotations

import uuid
from typing import Any

import asyncpg

from butlers.tools.relationship._schema import contact_name_expr, table_columns
from butlers.tools.relationship.contacts import _parse_contact
from butlers.tools.relationship.feed import _log_activity


async def stay_in_touch_set(
    pool: asyncpg.Pool,
    contact_id: uuid.UUID,
    frequency_days: int | None,
) -> dict[str, Any]:
    """Set or clear the stay-in-touch cadence for a contact.

    Pass frequency_days=None to clear the cadence (removes from overdue list).
    """
    row = await pool.fetchrow(
        """
        UPDATE contacts SET stay_in_touch_days = $2, updated_at = now()
        WHERE id = $1
        RETURNING *
        """,
        contact_id,
        frequency_days,
    )
    if row is None:
        raise ValueError(f"Contact {contact_id} not found")
    result = _parse_contact(row)
    if frequency_days is not None:
        await _log_activity(
            pool,
            contact_id,
            "stay_in_touch_set",
            f"Set stay-in-touch cadence to {frequency_days} days",
        )
    else:
        await _log_activity(
            pool, contact_id, "stay_in_touch_cleared", "Cleared stay-in-touch cadence"
        )
    return result


async def contacts_overdue(pool: asyncpg.Pool) -> list[dict[str, Any]]:
    """Return contacts whose last interaction exceeds their stay-in-touch cadence.

    Contacts with a cadence but no interactions are always overdue.
    Contacts with no cadence (NULL) are never returned.
    Archived contacts are excluded.
    """
    contact_cols = await table_columns(pool, "contacts")
    name_sql = contact_name_expr(contact_cols, alias="c")
    rows = await pool.fetch(
        f"""
        SELECT
            c.*,
            MAX(i.occurred_at) AS last_interaction_at,
            CASE
                WHEN MAX(i.occurred_at) IS NULL THEN NULL
                ELSE EXTRACT(EPOCH FROM (now() - MAX(i.occurred_at))) / 86400.0
            END AS days_since_last_interaction
        FROM contacts c
        LEFT JOIN interactions i ON c.id = i.contact_id
        WHERE c.stay_in_touch_days IS NOT NULL
          AND c.archived_at IS NULL
        GROUP BY c.id
        HAVING MAX(i.occurred_at) IS NULL
            OR MAX(i.occurred_at) < now() - make_interval(days => c.stay_in_touch_days)
        ORDER BY {name_sql}
        """
    )
    return [_parse_contact(row) for row in rows]
