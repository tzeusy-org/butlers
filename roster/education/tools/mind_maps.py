"""Education butler — mind map CRUD operations."""

from __future__ import annotations

from typing import Any

import asyncpg

from butlers.tools.education._helpers import _row_to_dict


async def mind_map_create(pool: asyncpg.Pool, title: str) -> str:
    """Create a new mind map with status='active' and NULL root_node_id.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    title:
        Human-readable title for the mind map (e.g. "Python", "Calculus").

    Returns
    -------
    str
        The UUID of the newly created mind map.
    """
    row = await pool.fetchrow(
        """
        INSERT INTO education.mind_maps (title, status)
        VALUES ($1, 'active')
        RETURNING id
        """,
        title,
    )
    return str(row["id"])


async def mind_map_get(pool: asyncpg.Pool, mind_map_id: str) -> dict[str, Any] | None:
    """Retrieve a mind map by ID.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    mind_map_id:
        UUID of the mind map.

    Returns
    -------
    dict or None
        The mind map row as a dict, or None if not found.
    """
    row = await pool.fetchrow(
        "SELECT * FROM education.mind_maps WHERE id = $1",
        mind_map_id,
    )
    if row is None:
        return None
    return _row_to_dict(row)


async def mind_map_list(
    pool: asyncpg.Pool,
    status: str | None = None,
) -> list[dict[str, Any]]:
    """List mind maps, optionally filtered by status.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    status:
        Optional status filter ('active', 'completed', or 'abandoned').

    Returns
    -------
    list of dict
        Mind map rows ordered by created_at descending.
    """
    if status is not None:
        rows = await pool.fetch(
            "SELECT * FROM education.mind_maps WHERE status = $1 ORDER BY created_at DESC",
            status,
        )
    else:
        rows = await pool.fetch(
            "SELECT * FROM education.mind_maps ORDER BY created_at DESC",
        )
    return [_row_to_dict(row) for row in rows]


async def mind_map_update_status(
    pool: asyncpg.Pool,
    mind_map_id: str,
    status: str,
) -> None:
    """Update the status of a mind map.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    mind_map_id:
        UUID of the mind map.
    status:
        New status value ('active', 'completed', or 'abandoned').

    Raises
    ------
    ValueError
        If the mind map is not found.
    """
    result = await pool.execute(
        """
        UPDATE education.mind_maps
        SET status = $1, updated_at = now()
        WHERE id = $2
        """,
        status,
        mind_map_id,
    )
    # asyncpg returns "UPDATE N" — check N > 0
    affected = int(result.split()[-1])
    if affected == 0:
        raise ValueError(f"Mind map not found: {mind_map_id}")
