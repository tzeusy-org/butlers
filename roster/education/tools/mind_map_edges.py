"""Education butler — mind map edge management with DAG acyclicity validation."""

from __future__ import annotations

import asyncpg


async def _check_cycle(
    pool: asyncpg.Pool,
    parent_node_id: str,
    child_node_id: str,
) -> bool:
    """Return True if adding parent→child edge would create a cycle.

    Uses a recursive CTE that walks backwards from parent_node_id following
    existing prerequisite edges.  If it can reach child_node_id (meaning
    child is an ancestor of parent), the edge would create a cycle.

    Self-loops (parent == child) return True immediately.
    """
    if parent_node_id == child_node_id:
        return True

    # Walk ancestors of parent_node_id. If child_node_id is reachable as an
    # ancestor, adding parent→child creates a cycle.
    row = await pool.fetchrow(
        """
        WITH RECURSIVE ancestors AS (
            SELECT parent_node_id AS node_id
            FROM education.mind_map_edges
            WHERE child_node_id = $1
              AND edge_type = 'prerequisite'
            UNION
            SELECT e.parent_node_id
            FROM education.mind_map_edges e
            JOIN ancestors a ON e.child_node_id = a.node_id
            WHERE e.edge_type = 'prerequisite'
        )
        SELECT EXISTS (
            SELECT 1 FROM ancestors WHERE node_id = $2
        ) AS has_cycle
        """,
        parent_node_id,
        child_node_id,
    )
    return bool(row["has_cycle"])


async def _recompute_depths(
    pool: asyncpg.Pool,
    start_node_id: str,
) -> None:
    """Recompute depth for all nodes in the same mind map as start_node_id.

    Depth = longest path from any root (node with no incoming prerequisite edges).
    Uses a single efficient recursive query over the full map, avoiding
    the N+1 correlated-subquery pattern.
    """
    await pool.execute(
        """
        WITH RECURSIVE
        -- All nodes in the same mind map as start_node_id
        map_nodes AS (
            SELECT id FROM education.mind_map_nodes
            WHERE mind_map_id = (
                SELECT mind_map_id FROM education.mind_map_nodes WHERE id = $1
            )
        ),
        -- BFS from roots: root nodes (no incoming prerequisite edges) start at depth 0
        node_depths AS (
            SELECT mn.id, 0 AS depth
            FROM map_nodes mn
            WHERE NOT EXISTS (
                SELECT 1 FROM education.mind_map_edges e
                WHERE e.child_node_id = mn.id AND e.edge_type = 'prerequisite'
            )
            UNION ALL
            SELECT e.child_node_id, nd.depth + 1
            FROM education.mind_map_edges e
            JOIN node_depths nd ON e.parent_node_id = nd.id
            WHERE e.edge_type = 'prerequisite'
        ),
        -- Longest path from any root wins
        final_depths AS (
            SELECT id, MAX(depth) AS computed_depth
            FROM node_depths
            GROUP BY id
        )
        UPDATE education.mind_map_nodes n
        SET depth = fd.computed_depth, updated_at = now()
        FROM final_depths fd
        WHERE n.id = fd.id
          AND n.depth IS DISTINCT FROM fd.computed_depth
        """,
        start_node_id,
    )


async def mind_map_edge_create(
    pool: asyncpg.Pool,
    parent_node_id: str,
    child_node_id: str,
    edge_type: str = "prerequisite",
) -> None:
    """Create an edge from parent to child in the mind map DAG.

    Validates:
    - edge_type must be 'prerequisite' or 'related'
    - Both nodes must belong to the same mind map
    - No cycles (self-loop, 2-node, multi-hop) for 'prerequisite' edges

    After creation, recomputes depth of child and all its descendants.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    parent_node_id:
        UUID of the parent (prerequisite) node.
    child_node_id:
        UUID of the child (dependent) node.
    edge_type:
        Edge type — 'prerequisite' (default) or 'related'.

    Raises
    ------
    ValueError
        If edge_type is invalid, nodes are in different mind maps, or
        adding the edge would create a cycle.
    """
    if edge_type not in ("prerequisite", "related"):
        raise ValueError(f"Invalid edge_type: {edge_type!r}. Must be 'prerequisite' or 'related'.")

    # Verify both nodes exist and are in the same mind map
    rows = await pool.fetch(
        """
        SELECT id, mind_map_id FROM education.mind_map_nodes
        WHERE id = ANY($1::uuid[])
        """,
        [parent_node_id, child_node_id],
    )
    found = {str(r["id"]): str(r["mind_map_id"]) for r in rows}
    if parent_node_id not in found:
        raise ValueError(f"Parent node not found: {parent_node_id}")
    if child_node_id not in found:
        raise ValueError(f"Child node not found: {child_node_id}")
    if found[parent_node_id] != found[child_node_id]:
        raise ValueError(
            f"Nodes belong to different mind maps: "
            f"parent in {found[parent_node_id]}, child in {found[child_node_id]}"
        )

    # Cycle detection for prerequisite edges
    if edge_type == "prerequisite":
        if await _check_cycle(pool, parent_node_id, child_node_id):
            raise ValueError(
                f"Adding edge {parent_node_id} → {child_node_id} would create a cycle."
            )

    await pool.execute(
        """
        INSERT INTO education.mind_map_edges (parent_node_id, child_node_id, edge_type)
        VALUES ($1, $2, $3)
        ON CONFLICT (parent_node_id, child_node_id) DO UPDATE
            SET edge_type = EXCLUDED.edge_type
        """,
        parent_node_id,
        child_node_id,
        edge_type,
    )

    # Recompute depths starting from child
    await _recompute_depths(pool, child_node_id)


async def mind_map_edge_delete(
    pool: asyncpg.Pool,
    parent_node_id: str,
    child_node_id: str,
) -> None:
    """Delete an edge between two nodes (idempotent).

    After deletion, recomputes depths for the child and all its descendants.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    parent_node_id:
        UUID of the parent node.
    child_node_id:
        UUID of the child node.
    """
    await pool.execute(
        """
        DELETE FROM education.mind_map_edges
        WHERE parent_node_id = $1 AND child_node_id = $2
        """,
        parent_node_id,
        child_node_id,
    )
    # Recompute depths of child and descendants (child may now be a root)
    await _recompute_depths(pool, child_node_id)
