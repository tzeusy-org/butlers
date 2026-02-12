"""Groups — organize contacts into groups."""

from __future__ import annotations

import uuid
from typing import Any

import asyncpg

from butlers.tools.relationship._schema import contact_name_expr, table_columns
from butlers.tools.relationship.contacts import _parse_contact
from butlers.tools.relationship.feed import _log_activity


async def group_create(pool: asyncpg.Pool, name: str, type: str | None = None) -> dict[str, Any]:
    """Create a contact group."""
    cols = await table_columns(pool, "groups")
    if "type" in cols:
        row = await pool.fetchrow(
            "INSERT INTO groups (name, type) VALUES ($1, $2) RETURNING *",
            name,
            type or "custom",
        )
    else:
        row = await pool.fetchrow(
            "INSERT INTO groups (name) VALUES ($1) RETURNING *",
            name,
        )
    return dict(row)


async def group_add_member(
    pool: asyncpg.Pool,
    group_id: uuid.UUID,
    contact_id: uuid.UUID,
    role: str | None = None,
) -> dict[str, Any]:
    """Add a contact to a group."""
    cols = await table_columns(pool, "group_members")
    if "role" in cols:
        await pool.execute(
            "INSERT INTO group_members (group_id, contact_id, role) VALUES ($1, $2, $3)",
            group_id,
            contact_id,
            role,
        )
    else:
        await pool.execute(
            "INSERT INTO group_members (group_id, contact_id) VALUES ($1, $2)",
            group_id,
            contact_id,
        )
    await _log_activity(pool, contact_id, "group_joined", f"Joined group {group_id}")
    return {"group_id": group_id, "contact_id": contact_id, "role": role}


async def group_list(pool: asyncpg.Pool) -> list[dict[str, Any]]:
    """List all groups."""
    rows = await pool.fetch("SELECT * FROM groups ORDER BY name")
    return [dict(row) for row in rows]


async def group_members(pool: asyncpg.Pool, group_id: uuid.UUID) -> list[dict[str, Any]]:
    """List all members of a group."""
    contact_cols = await table_columns(pool, "contacts")
    name_sql = contact_name_expr(contact_cols, alias="c")
    rows = await pool.fetch(
        f"""
        SELECT c.*, {name_sql} AS name
        FROM contacts c
        JOIN group_members gm ON c.id = gm.contact_id
        WHERE gm.group_id = $1
        ORDER BY {name_sql}
        """,
        group_id,
    )
    return [_parse_contact(row) for row in rows]
