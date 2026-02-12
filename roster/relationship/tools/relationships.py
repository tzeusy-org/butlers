"""Relationships — bidirectional relationships and relationship types taxonomy."""

from __future__ import annotations

import uuid
from typing import Any

import asyncpg

from butlers.tools.relationship._schema import contact_name_expr, table_columns
from butlers.tools.relationship.feed import _log_activity


async def relationship_types_list(
    pool: asyncpg.Pool, group: str | None = None
) -> dict[str, list[dict[str, Any]]]:
    """List relationship types, grouped by category.

    Returns a dict keyed by group name, each value is a list of type dicts
    with id, forward_label, and reverse_label.
    If group is specified, returns only types in that group.
    """
    if group is not None:
        rows = await pool.fetch(
            """
            SELECT id, "group", forward_label, reverse_label
            FROM relationship_types
            WHERE "group" = $1
            ORDER BY forward_label
            """,
            group,
        )
    else:
        rows = await pool.fetch(
            """
            SELECT id, "group", forward_label, reverse_label
            FROM relationship_types
            ORDER BY "group", forward_label
            """
        )

    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        d = dict(row)
        g = d.pop("group")
        if g not in grouped:
            grouped[g] = []
        grouped[g].append(d)
    return grouped


async def relationship_type_get(pool: asyncpg.Pool, type_id: uuid.UUID) -> dict[str, Any] | None:
    """Get a single relationship type by ID."""
    row = await pool.fetchrow(
        """
        SELECT id, "group", forward_label, reverse_label
        FROM relationship_types
        WHERE id = $1
        """,
        type_id,
    )
    return dict(row) if row else None


async def _resolve_relationship_type(
    pool: asyncpg.Pool,
    type_id: uuid.UUID | None = None,
    type_label: str | None = None,
) -> dict[str, Any]:
    """Resolve a relationship type from either type_id or freetext label.

    Returns the relationship_type record dict.
    Raises ValueError if neither matches.
    """
    if type_id is not None:
        rt = await relationship_type_get(pool, type_id)
        if rt is not None:
            return rt
        raise ValueError(f"Relationship type {type_id} not found")

    if type_label is not None:
        # Try matching forward_label or reverse_label (case-insensitive)
        row = await pool.fetchrow(
            """
            SELECT id, "group", forward_label, reverse_label
            FROM relationship_types
            WHERE LOWER(forward_label) = LOWER($1)
               OR LOWER(reverse_label) = LOWER($1)
            LIMIT 1
            """,
            type_label,
        )
        if row is not None:
            return dict(row)
        # Fall back to 'custom' type
        row = await pool.fetchrow(
            """
            SELECT id, "group", forward_label, reverse_label
            FROM relationship_types
            WHERE forward_label = 'custom'
            LIMIT 1
            """
        )
        if row is not None:
            return dict(row)
        raise ValueError(
            f"No matching relationship type for '{type_label}' and no 'custom' fallback found"
        )

    raise ValueError("Either type_id or type (label) must be provided")


async def relationship_add(
    pool: asyncpg.Pool,
    contact_a: uuid.UUID,
    contact_b: uuid.UUID,
    type: str | None = None,
    type_id: uuid.UUID | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """Create a bidirectional relationship (two rows).

    Accepts either:
      - type_id: UUID of a relationship_type (preferred)
      - type: freetext label for backward compat (matched against taxonomy)

    The reverse row automatically gets the correct reverse_label.
    """
    # Check if relationship_types table exists (for backward compat)
    has_types_table = await pool.fetchval(
        """
        SELECT EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_name = 'relationship_types'
        )
        """
    )

    if has_types_table:
        rt = await _resolve_relationship_type(pool, type_id=type_id, type_label=type)
        forward_label = rt["forward_label"]
        reverse_label = rt["reverse_label"]
        rt_id = rt["id"]

        # Check if relationships table has relationship_type_id column
        has_type_id_col = await pool.fetchval(
            """
            SELECT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'relationships' AND column_name = 'relationship_type_id'
            )
            """
        )

        if has_type_id_col:
            row_a = await pool.fetchrow(
                """
                INSERT INTO relationships
                    (contact_a, contact_b, type, relationship_type_id, notes)
                VALUES ($1, $2, $3, $4, $5)
                RETURNING *
                """,
                contact_a,
                contact_b,
                forward_label,
                rt_id,
                notes,
            )
            await pool.execute(
                """
                INSERT INTO relationships
                    (contact_a, contact_b, type, relationship_type_id, notes)
                VALUES ($1, $2, $3, $4, $5)
                """,
                contact_b,
                contact_a,
                reverse_label,
                rt_id,
                notes,
            )
        else:
            row_a = await pool.fetchrow(
                """
                INSERT INTO relationships (contact_a, contact_b, type, notes)
                VALUES ($1, $2, $3, $4)
                RETURNING *
                """,
                contact_a,
                contact_b,
                forward_label,
                notes,
            )
            await pool.execute(
                """
                INSERT INTO relationships (contact_a, contact_b, type, notes)
                VALUES ($1, $2, $3, $4)
                """,
                contact_b,
                contact_a,
                reverse_label,
                notes,
            )
    else:
        # Legacy path: no relationship_types table, use freetext type directly
        if type is None:
            raise ValueError("type is required when relationship_types table is not available")
        row_a = await pool.fetchrow(
            """
            INSERT INTO relationships (contact_a, contact_b, type, notes)
            VALUES ($1, $2, $3, $4)
            RETURNING *
            """,
            contact_a,
            contact_b,
            type,
            notes,
        )
        await pool.execute(
            """
            INSERT INTO relationships (contact_a, contact_b, type, notes)
            VALUES ($1, $2, $3, $4)
            """,
            contact_b,
            contact_a,
            type,
            notes,
        )

    result = dict(row_a)
    label = result.get("type", type or "unknown")
    await _log_activity(
        pool, contact_a, "relationship_added", f"Added '{label}' relationship with {contact_b}"
    )
    await _log_activity(
        pool, contact_b, "relationship_added", f"Added '{label}' relationship with {contact_a}"
    )
    return result


async def relationship_list(pool: asyncpg.Pool, contact_id: uuid.UUID) -> list[dict[str, Any]]:
    """List all relationships for a contact."""
    contact_cols = await table_columns(pool, "contacts")
    name_sql = contact_name_expr(contact_cols, alias="c")
    rows = await pool.fetch(
        f"""
        SELECT r.*, {name_sql} AS related_name
        FROM relationships r
        JOIN contacts c ON r.contact_b = c.id
        WHERE r.contact_a = $1
        ORDER BY r.created_at
        """,
        contact_id,
    )
    return [dict(row) for row in rows]


async def relationship_remove(
    pool: asyncpg.Pool, contact_a: uuid.UUID, contact_b: uuid.UUID
) -> None:
    """Remove both directions of a relationship."""
    await pool.execute(
        """
        DELETE FROM relationships
        WHERE (contact_a = $1 AND contact_b = $2)
           OR (contact_a = $2 AND contact_b = $1)
        """,
        contact_a,
        contact_b,
    )
    await _log_activity(
        pool, contact_a, "relationship_removed", f"Removed relationship with {contact_b}"
    )
    await _log_activity(
        pool, contact_b, "relationship_removed", f"Removed relationship with {contact_a}"
    )
