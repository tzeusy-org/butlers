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
    """Retrieve a mind map by ID, including its nodes and edges.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    mind_map_id:
        UUID of the mind map.

    Returns
    -------
    dict or None
        The mind map row as a dict with ``nodes`` (list of node dicts) and
        ``edges`` (list of edge dicts) fields, or None if not found.
    """
    row = await pool.fetchrow(
        "SELECT * FROM education.mind_maps WHERE id = $1",
        mind_map_id,
    )
    if row is None:
        return None
    result = _row_to_dict(row)

    node_rows = await pool.fetch(
        """
        SELECT * FROM education.mind_map_nodes
        WHERE mind_map_id = $1
        ORDER BY depth ASC, label ASC
        """,
        mind_map_id,
    )
    result["nodes"] = [_row_to_dict(nr) for nr in node_rows]

    edge_rows = await pool.fetch(
        """
        SELECT parent_node_id::text, child_node_id::text, edge_type
        FROM education.mind_map_edges e
        JOIN education.mind_map_nodes n ON e.parent_node_id = n.id
        WHERE n.mind_map_id = $1
        ORDER BY e.parent_node_id, e.child_node_id
        """,
        mind_map_id,
    )
    result["edges"] = [dict(er) for er in edge_rows]

    return result


async def mind_map_list(
    pool: asyncpg.Pool,
    status: str | None = None,
) -> list[dict[str, Any]]:
    """List mind maps, optionally filtered by status.

    Call this before starting a new curriculum to check if a related mind map
    already exists. If so, extend it (via ``mind_map_node_create`` /
    ``mind_map_edge_create`` + ``curriculum_replan``) rather than creating a
    duplicate.

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
