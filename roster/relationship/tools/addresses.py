"""Addresses — add, list, update, and remove addresses for contacts."""

from __future__ import annotations

import uuid
from typing import Any

import asyncpg


async def address_add(
    pool: asyncpg.Pool,
    contact_id: uuid.UUID,
    line_1: str,
    label: str = "Home",
    line_2: str | None = None,
    city: str | None = None,
    province: str | None = None,
    postal_code: str | None = None,
    country: str | None = None,
    is_current: bool = False,
) -> dict[str, Any]:
    """Add an address for a contact.

    If is_current is True, clears the is_current flag on all other
    addresses for this contact first.
    """
    # Validate country code length if provided
    if country is not None and len(country) != 2:
        raise ValueError("Country must be a 2-letter ISO 3166-1 code")

    if is_current:
        await pool.execute(
            "UPDATE addresses SET is_current = false, updated_at = now() WHERE contact_id = $1",
            contact_id,
        )

    row = await pool.fetchrow(
        """
        INSERT INTO addresses (contact_id, label, line_1, line_2, city, province,
                               postal_code, country, is_current)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        RETURNING *
        """,
        contact_id,
        label,
        line_1,
        line_2,
        city,
        province,
        postal_code,
        country,
        is_current,
    )
    return dict(row)


async def address_list(pool: asyncpg.Pool, contact_id: uuid.UUID) -> list[dict[str, Any]]:
    """List all addresses for a contact, current address first."""
    rows = await pool.fetch(
        """
        SELECT * FROM addresses
        WHERE contact_id = $1
        ORDER BY is_current DESC, created_at
        """,
        contact_id,
    )
    return [dict(row) for row in rows]


async def address_update(
    pool: asyncpg.Pool, address_id: uuid.UUID, **fields: Any
) -> dict[str, Any]:
    """Update an address's fields.

    Supported fields: label, line_1, line_2, city, province, postal_code,
    country, is_current. If is_current is set to True, clears the flag on
    all other addresses for the same contact.
    """
    existing = await pool.fetchrow("SELECT * FROM addresses WHERE id = $1", address_id)
    if existing is None:
        raise ValueError(f"Address {address_id} not found")

    # Validate country if being updated
    country = fields.get("country", existing["country"])
    if country is not None and len(country) != 2:
        raise ValueError("Country must be a 2-letter ISO 3166-1 code")

    label = fields.get("label", existing["label"])
    line_1 = fields.get("line_1", existing["line_1"])
    line_2 = fields.get("line_2", existing["line_2"])
    city = fields.get("city", existing["city"])
    province = fields.get("province", existing["province"])
    postal_code = fields.get("postal_code", existing["postal_code"])
    is_current = fields.get("is_current", existing["is_current"])

    contact_id = existing["contact_id"]

    # If setting as current, clear others first
    if is_current and not existing["is_current"]:
        await pool.execute(
            "UPDATE addresses SET is_current = false, updated_at = now() WHERE contact_id = $1",
            contact_id,
        )

    row = await pool.fetchrow(
        """
        UPDATE addresses
        SET label = $2, line_1 = $3, line_2 = $4, city = $5, province = $6,
            postal_code = $7, country = $8, is_current = $9, updated_at = now()
        WHERE id = $1
        RETURNING *
        """,
        address_id,
        label,
        line_1,
        line_2,
        city,
        province,
        postal_code,
        country,
        is_current,
    )
    return dict(row)


async def address_remove(pool: asyncpg.Pool, address_id: uuid.UUID) -> None:
    """Remove an address by ID."""
    row = await pool.fetchrow(
        "DELETE FROM addresses WHERE id = $1 RETURNING contact_id, label",
        address_id,
    )
    if row is None:
        raise ValueError(f"Address {address_id} not found")
