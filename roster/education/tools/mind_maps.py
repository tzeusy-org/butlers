"""Education butler — mind map CRUD operations."""

from __future__ import annotations

from typing import Any

import asyncpg

from butlers.tools.education._helpers import _row_to_dict, _transaction


async def mind_map_create(pool: asyncpg.Pool, title: str) -> str:
    """Create a new mind map with status='draft' and NULL root_node_id.

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
        VALUES ($1, 'draft')
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


class MindMapLifecycleError(ValueError):
    """A stored map cannot make the requested lifecycle transition."""


_TRANSITIONS = {
    "draft": {"active", "abandoned"},
    "active": {"completed", "abandoned"},
    "completed": {"active"},
    "abandoned": {"active"},
}


async def mind_map_update_status(pool: asyncpg.Pool, mind_map_id: str, status: str) -> None:
    """Change status under the same parent-row lock used by node removal.

    Creation alone enters draft. Refused transitions never write; activation
    requires at least one persisted node. Database triggers guard direct SQL.
    """
    if status not in {"active", "completed", "abandoned"}:
        raise MindMapLifecycleError(f"Invalid target status: {status!r}")
    async with _transaction(pool) as connection:
        row = await connection.fetchrow(
            "SELECT status FROM education.mind_maps WHERE id = $1 FOR UPDATE", mind_map_id
        )
        if row is None:
            raise ValueError(f"Mind map not found: {mind_map_id}")
        if status not in _TRANSITIONS.get(row["status"], set()):
            raise MindMapLifecycleError(f"Cannot transition from {row['status']} to {status}")
        if status == "active" and not await connection.fetchval(
            "SELECT EXISTS (SELECT 1 FROM education.mind_map_nodes WHERE mind_map_id = $1)",
            mind_map_id,
        ):
            raise MindMapLifecycleError("A curriculum with no concepts cannot be activated")
        await connection.execute(
            "UPDATE education.mind_maps SET status = $2, updated_at = now() WHERE id = $1",
            mind_map_id,
            status,
        )


async def mind_map_abandon_stale(
    pool: asyncpg.Pool,
    inactivity_days: int = 30,
) -> list[str]:
    """Abandon stalled drafts and inactive populated maps through the shared sweep.

    Empty drafts use a strict 24-hour creation threshold. Populated maps use
    newest node activity, independently of the flow's session clock, with the strict
    ``inactivity_days`` boundary. Completed and all-mastered maps stay unchanged;
    already-abandoned maps retry only idempotent review-schedule cleanup.

    This backs the registered weekly staleness-abandonment scheduled job.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    inactivity_days:
        Inactivity threshold in days. A map is abandoned only when its most
        recent activity is strictly older than this many days.

    Returns
    -------
    list of str
        The UUIDs of the mind maps that were transitioned to ``'abandoned'``.
    """
    from butlers.tools.education.teaching_flows import _abandon_stale_maps

    async def delete_schedule(name: str) -> None:
        await pool.execute("DELETE FROM scheduled_tasks WHERE name = $1", name)

    return await _abandon_stale_maps(
        pool, node_activity=True, stale_days=inactivity_days, schedule_delete=delete_schedule
    )
