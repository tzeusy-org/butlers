"""Contact info — structured contact details and addresses."""

from __future__ import annotations

import uuid
from typing import Any

import asyncpg

from butlers.tools.relationship.contacts import _parse_contact
from butlers.tools.relationship.feed import _log_activity

_CONTACT_INFO_TYPES = {"email", "phone", "telegram", "linkedin", "twitter", "website", "other"}


async def contact_info_add(
    pool: asyncpg.Pool,
    contact_id: uuid.UUID,
    type: str,
    value: str,
    label: str | None = None,
    is_primary: bool = False,
) -> dict[str, Any]:
    """Add a piece of contact information (email, phone, etc.) for a contact."""
    if type not in _CONTACT_INFO_TYPES:
        raise ValueError(
            f"Invalid contact info type '{type}'. Must be one of {sorted(_CONTACT_INFO_TYPES)}"
        )

    # Verify contact exists
    existing = await pool.fetchrow("SELECT id FROM contacts WHERE id = $1", contact_id)
    if existing is None:
        raise ValueError(f"Contact {contact_id} not found")

    # If marking as primary, unset any existing primary for this type
    if is_primary:
        await pool.execute(
            """
            UPDATE shared.contact_info SET is_primary = false
            WHERE contact_id = $1 AND type = $2 AND is_primary = true
            """,
            contact_id,
            type,
        )

    row = await pool.fetchrow(
        """
        INSERT INTO shared.contact_info (contact_id, type, value, label, is_primary)
        VALUES ($1, $2, $3, $4, $5)
        RETURNING *
        """,
        contact_id,
        type,
        value,
        label,
        is_primary,
    )
    result = dict(row)
    desc = f"Added {type}: {value}"
    if label:
        desc += f" ({label})"
    await _log_activity(pool, contact_id, "contact_info_added", desc)
    return result


async def contact_info_list(
    pool: asyncpg.Pool,
    contact_id: uuid.UUID,
    type: str | None = None,
) -> list[dict[str, Any]]:
    """List contact info for a contact, optionally filtered by type."""
    if type is not None:
        rows = await pool.fetch(
            """
            SELECT * FROM shared.contact_info
            WHERE contact_id = $1 AND type = $2
            ORDER BY is_primary DESC, created_at
            """,
            contact_id,
            type,
        )
    else:
        rows = await pool.fetch(
            """
            SELECT * FROM shared.contact_info
            WHERE contact_id = $1
            ORDER BY type, is_primary DESC, created_at
            """,
            contact_id,
        )
    return [dict(row) for row in rows]


async def contact_info_remove(
    pool: asyncpg.Pool,
    contact_info_id: uuid.UUID,
) -> None:
    """Remove a piece of contact information by its ID."""
    row = await pool.fetchrow(
        "SELECT * FROM shared.contact_info WHERE id = $1",
        contact_info_id,
    )
    if row is None:
        raise ValueError(f"Contact info {contact_info_id} not found")

    await pool.execute("DELETE FROM shared.contact_info WHERE id = $1", contact_info_id)
    await _log_activity(
        pool,
        row["contact_id"],
        "contact_info_removed",
        f"Removed {row['type']}: {row['value']}",
    )


async def contact_search_by_info(
    pool: asyncpg.Pool,
    value: str,
    type: str | None = None,
) -> list[dict[str, Any]]:
    """Search contacts by contact info value (reverse lookup).

    Finds all contacts that have a matching contact info entry.
    Optionally filter by info type (email, phone, etc.).
    Uses ILIKE for case-insensitive partial matching.
    """
    if type is not None:
        rows = await pool.fetch(
            """
            SELECT DISTINCT c.*, ci.type AS matched_type, ci.value AS matched_value
            FROM contacts c
            JOIN shared.contact_info ci ON c.id = ci.contact_id
            WHERE ci.type = $1
              AND ci.value ILIKE '%' || $2 || '%'
              AND c.listed = true
            ORDER BY c.first_name, c.last_name, c.nickname
            """,
            type,
            value,
        )
    else:
        rows = await pool.fetch(
            """
            SELECT DISTINCT c.*, ci.type AS matched_type, ci.value AS matched_value
            FROM contacts c
            JOIN shared.contact_info ci ON c.id = ci.contact_id
            WHERE ci.value ILIKE '%' || $1 || '%'
              AND c.listed = true
            ORDER BY c.first_name, c.last_name, c.nickname
            """,
            value,
        )
    return [_parse_contact(row) for row in rows]
