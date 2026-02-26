"""Education butler — mind map query functions (frontier, subtree)."""

from __future__ import annotations

from typing import Any

import asyncpg

from butlers.tools.education._helpers import _row_to_dict


async def mind_map_frontier(
    pool: asyncpg.Pool,
    mind_map_id: str,
) -> list[dict[str, Any]]:
    """Return frontier nodes for a mind map.

    Frontier = nodes where:
    - mastery_status IN ('unseen', 'diagnosed', 'learning')
    - AND all incoming prerequisite-edge parents have mastery_status = 'mastered'
      (or the node has no incoming prerequisite edges)

    Results are ordered by depth ASC, effort_minutes ASC NULLS LAST.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    mind_map_id:
        UUID of the mind map.

    Returns
    -------
    list of dict
        Frontier node rows.
    """
    rows = await pool.fetch(
        """
        SELECT n.*
        FROM education.mind_map_nodes n
        WHERE n.mind_map_id = $1
          AND n.mastery_status IN ('unseen', 'diagnosed', 'learning')
          AND NOT EXISTS (
              -- Any prerequisite parent that is NOT mastered blocks the node
              SELECT 1
              FROM education.mind_map_edges e
              JOIN education.mind_map_nodes parent ON e.parent_node_id = parent.id
              WHERE e.child_node_id = n.id
                AND e.edge_type = 'prerequisite'
                AND parent.mastery_status != 'mastered'
          )
        ORDER BY n.depth ASC, n.effort_minutes ASC NULLS LAST
        """,
        mind_map_id,
    )
    return [_row_to_dict(row) for row in rows]


async def mind_map_subtree(
    pool: asyncpg.Pool,
    node_id: str,
) -> list[dict[str, Any]]:
    """Return all descendants of a node (not including the node itself).

    Uses a recursive CTE over ALL edge types. Results are deduplicated.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    node_id:
        UUID of the root node.

    Returns
    -------
    list of dict
        Descendant node rows (deduplicated), ordered by depth ASC, label ASC.
    """
    rows = await pool.fetch(
        """
        WITH RECURSIVE subtree AS (
            -- Direct children (all edge types)
            SELECT e.child_node_id AS node_id
            FROM education.mind_map_edges e
            WHERE e.parent_node_id = $1
            UNION
            -- Recursive: children of children
            SELECT e.child_node_id
            FROM education.mind_map_edges e
            JOIN subtree s ON e.parent_node_id = s.node_id
        )
        SELECT DISTINCT n.*
        FROM education.mind_map_nodes n
        JOIN subtree s ON n.id = s.node_id
        ORDER BY n.depth ASC, n.label ASC
        """,
        node_id,
    )
    return [_row_to_dict(row) for row in rows]
