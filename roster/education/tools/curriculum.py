"""Education butler — curriculum planning tools.

Provides three pure data/DB tools:

- ``curriculum_generate``: Validates a pre-structured concept graph (nodes +
  edges supplied by the caller), enforces structural constraints (max depth 5,
  max 30 nodes, DAG acyclicity), runs a deterministic topological sort with
  tie-breaking (depth → effort → diagnostic mastery), writes sequence integers
  to the DB, and transitions the mind map to 'active'.

- ``curriculum_replan``: Re-computes sequence numbers in response to updated
  mastery state without modifying the existing DAG structure.  Marks
  fully-mastered nodes as skippable in metadata.

- ``curriculum_next_node``: Returns the frontier node with the lowest
  sequence number, or None when the frontier is empty or the map is
  completed/abandoned.

The LLM orchestration (concept decomposition) happens at the butler session
level via skill prompts, not here.  These tools only handle the pure
data/persistence layer.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from typing import Any

import asyncpg

from butlers.tools.education._helpers import _row_to_dict

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Structural constraints
# ---------------------------------------------------------------------------

MAX_NODE_DEPTH = 5
MAX_NODES_PER_MAP = 30

# Mastery statuses that rank "earlier" in the diagnostic tie-break
_DIAGNOSED_STATUSES = {"diagnosed", "learning"}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _topological_sort_with_tiebreak(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
) -> list[str]:
    """Compute a deterministic learning sequence via topological sort + tie-breaking.

    Tie-breaking priority (lowest → first):
    1. depth (shallower nodes first)
    2. effort_minutes (lower effort first; None treated as infinity)
    3. mastery rank: diagnosed/learning before unseen
    4. label (alphabetical, for full determinism)

    Parameters
    ----------
    nodes:
        List of node dicts. Each must have keys: ``id``, ``depth``,
        ``effort_minutes`` (may be None), ``mastery_status``.
    edges:
        List of edge dicts for ``edge_type='prerequisite'`` only.
        Each must have keys: ``parent_node_id``, ``child_node_id``.

    Returns
    -------
    list of str
        Node IDs in learning order (lowest sequence first).

    Raises
    ------
    ValueError
        If the graph contains a cycle (should be pre-validated, but
        guarded here as a safety net).
    """
    node_map = {n["id"]: n for n in nodes}

    # Build adjacency: out-edges and in-degree
    in_degree: dict[str, int] = {n["id"]: 0 for n in nodes}
    out_edges: dict[str, list[str]] = defaultdict(list)

    for edge in edges:
        parent = str(edge["parent_node_id"])
        child = str(edge["child_node_id"])
        out_edges[parent].append(child)
        in_degree[child] += 1

    def _sort_key(node_id: str) -> tuple:
        n = node_map[node_id]
        depth = n.get("depth") or 0
        effort = n.get("effort_minutes")
        effort_key = effort if effort is not None else 999_999
        status = n.get("mastery_status", "unseen")
        # diagnosed/learning rank before unseen (0 < 1)
        mastery_rank = 0 if status in _DIAGNOSED_STATUSES else 1
        label = n.get("label", "")
        return (depth, effort_key, mastery_rank, label)

    # Kahn's algorithm with priority-sorted frontier
    # Use a list sorted on each iteration for full determinism without a heap
    frontier: list[str] = [nid for nid, deg in in_degree.items() if deg == 0]
    ordered: list[str] = []

    while frontier:
        # Sort frontier by tiebreak key, pick the first (smallest) element
        frontier.sort(key=_sort_key)
        current = frontier.pop(0)
        ordered.append(current)

        for neighbor in out_edges[current]:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                frontier.append(neighbor)

    if len(ordered) != len(nodes):
        raise ValueError(
            f"Cycle detected during topological sort: "
            f"processed {len(ordered)} of {len(nodes)} nodes."
        )

    return ordered


def _validate_constraints(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    *,
    mind_map_id: str,
) -> None:
    """Enforce structural constraints on the concept graph.

    Raises
    ------
    ValueError
        If the graph violates max-node or max-depth constraints.
    """
    node_count = len(nodes)
    if node_count > MAX_NODES_PER_MAP:
        raise ValueError(
            f"Node count limit exceeded for mind map {mind_map_id}: "
            f"{node_count} nodes (max {MAX_NODES_PER_MAP})."
        )

    for node in nodes:
        depth = node.get("depth") or 0
        if depth > MAX_NODE_DEPTH:
            raise ValueError(
                f"Node depth limit exceeded for node {node['id']} "
                f"(label={node.get('label')!r}) in mind map {mind_map_id}: "
                f"depth={depth} (max {MAX_NODE_DEPTH})."
            )


def _check_dag_acyclicity(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
) -> None:
    """Detect cycles in the prerequisite graph using DFS.

    Raises
    ------
    ValueError
        If a cycle is detected.
    """
    adj: dict[str, list[str]] = {n["id"]: [] for n in nodes}
    for edge in edges:
        parent = str(edge["parent_node_id"])
        child = str(edge["child_node_id"])
        if parent == child:
            raise ValueError(f"Self-loop detected on node {parent}.")
        adj[parent].append(child)

    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {n["id"]: WHITE for n in nodes}

    def _dfs(node_id: str) -> None:
        color[node_id] = GRAY
        for neighbor in adj.get(node_id, []):
            if color[neighbor] == GRAY:
                raise ValueError(
                    f"Cycle detected: traversal reached {neighbor!r} from {node_id!r}."
                )
            if color[neighbor] == WHITE:
                _dfs(neighbor)
        color[node_id] = BLACK

    for nid in adj:
        if color[nid] == WHITE:
            _dfs(nid)


async def _fetch_nodes_and_edges(
    pool: asyncpg.Pool,
    mind_map_id: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Load all nodes and prerequisite edges for a mind map from the DB."""
    node_rows = await pool.fetch(
        """
        SELECT id, label, depth, effort_minutes, mastery_status, mastery_score, metadata, sequence
        FROM education.mind_map_nodes
        WHERE mind_map_id = $1
        ORDER BY depth ASC, label ASC
        """,
        mind_map_id,
    )
    nodes = [_row_to_dict(row) for row in node_rows]

    edge_rows = await pool.fetch(
        """
        SELECT parent_node_id::text, child_node_id::text
        FROM education.mind_map_edges e
        JOIN education.mind_map_nodes n ON e.parent_node_id = n.id
        WHERE n.mind_map_id = $1
          AND e.edge_type = 'prerequisite'
        """,
        mind_map_id,
    )
    edges = [dict(row) for row in edge_rows]

    return nodes, edges


async def _write_sequences(
    pool: asyncpg.Pool,
    ordered_ids: list[str],
) -> None:
    """Write sequence integers (1-based) to mind_map_nodes rows (single batched UPDATE)."""
    if not ordered_ids:
        return

    sequences = list(range(1, len(ordered_ids) + 1))
    await pool.execute(
        """
        UPDATE education.mind_map_nodes AS n
        SET sequence = s.seq,
            updated_at = now()
        FROM (
            SELECT unnest($1::uuid[]) AS id,
                   unnest($2::integer[]) AS seq
        ) AS s
        WHERE n.id = s.id
          AND n.sequence IS DISTINCT FROM s.seq
        """,
        ordered_ids,
        sequences,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def curriculum_generate(
    pool: asyncpg.Pool,
    mind_map_id: str,
    *,
    goal: str | None = None,
    diagnostic_results: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate a concept graph, run topological sort, assign learning sequence.

    Called during the PLANNING phase after all nodes and edges have been
    persisted. Every concept in the curriculum plan MUST be a node in the DB
    before calling this — use ``mind_map_node_create()`` and
    ``mind_map_edge_create()`` first.

    The concept graph (nodes + edges) is assumed to already be persisted in the
    DB via prior calls to ``mind_map_node_create()`` and ``mind_map_edge_create()``.
    This function handles:

    1. Loading the full graph from the DB.
    2. Validating structural constraints (max 30 nodes, max depth 5, DAG).
    3. Applying diagnostic mastery seeding (if ``diagnostic_results`` supplied).
    4. Running the deterministic topological sort with tie-breaking.
    5. Writing ``sequence`` integers back to the DB.
    6. Recording the goal in ``mind_maps.metadata`` (if supplied).
    7. Transitioning the mind map status to ``'active'``.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    mind_map_id:
        UUID of the mind map to process.
    goal:
        Optional learning goal used to scope the curriculum
        (stored in ``mind_maps.metadata``; scoping is done by the LLM session).
    diagnostic_results:
        Optional mapping of ``{node_label: quality_score}`` from a prior
        diagnostic session.  Nodes with ``quality >= 3`` receive
        ``mastery_status='diagnosed'`` with a proportional ``mastery_score``.
        Quality 5 maps to mastery_score 0.9 (never 1.0).

    Returns
    -------
    dict
        Summary dict with keys: ``mind_map_id``, ``node_count``, ``edge_count``,
        ``status``.

    Raises
    ------
    ValueError
        If the mind map is not found, or structural constraints are violated.
    """
    # Verify mind map exists and is in a plannable state
    map_row = await pool.fetchrow(
        "SELECT id, status FROM education.mind_maps WHERE id = $1",
        mind_map_id,
    )
    if map_row is None:
        raise ValueError(f"Mind map not found: {mind_map_id}")

    map_status = map_row["status"]
    if map_status in ("completed", "abandoned"):
        raise ValueError(
            f"Cannot generate curriculum for mind map {mind_map_id} with status={map_status!r}."
        )

    # Load graph from DB
    nodes, edges = await _fetch_nodes_and_edges(pool, mind_map_id)

    node_count = len(nodes)
    edge_count = len(edges)

    if node_count == 0:
        raise ValueError(f"Mind map {mind_map_id} has no nodes — cannot generate curriculum.")

    # Structural constraint validation
    _validate_constraints(nodes, edges, mind_map_id=mind_map_id)
    _check_dag_acyclicity(nodes, edges)

    # Apply diagnostic seeding before sort (influences tie-breaking)
    if diagnostic_results:
        nodes = await _apply_diagnostic_seeding(pool, nodes, diagnostic_results)

    # Topological sort with tie-breaking
    ordered_ids = _topological_sort_with_tiebreak(nodes, edges)

    # Write sequences to DB
    await _write_sequences(pool, ordered_ids)

    # Transition to 'active'; merge goal into metadata if supplied
    if goal is not None:
        goal_json = json.dumps({"goal": goal})
        await pool.execute(
            """
            UPDATE education.mind_maps
            SET metadata = metadata || $1::jsonb,
                status = 'active',
                updated_at = now()
            WHERE id = $2
            """,
            goal_json,
            mind_map_id,
        )
    else:
        await pool.execute(
            """
            UPDATE education.mind_maps
            SET status = 'active', updated_at = now()
            WHERE id = $1
            """,
            mind_map_id,
        )

    logger.info(
        "curriculum_generate: mind_map_id=%s nodes=%d edges=%d goal=%r",
        mind_map_id,
        node_count,
        edge_count,
        goal,
    )

    return {
        "mind_map_id": mind_map_id,
        "node_count": node_count,
        "edge_count": edge_count,
        "status": "active",
    }


async def _apply_diagnostic_seeding(
    pool: asyncpg.Pool,
    nodes: list[dict[str, Any]],
    diagnostic_results: dict[str, Any],
) -> list[dict[str, Any]]:
    """Apply diagnostic mastery seeding to nodes based on quality scores.

    Nodes with quality >= 3 receive mastery_status='diagnosed' with a
    proportional mastery_score (max 0.9, never 1.0).  Unmatched labels
    are silently discarded.  Returns the updated nodes list.
    """
    # Build label → quality mapping (case-sensitive)
    label_quality: dict[str, Any] = {}
    for label, quality in diagnostic_results.items():
        label_quality[label] = quality

    updated_nodes = []
    for node in nodes:
        label = node.get("label", "")
        quality = label_quality.get(label)
        if quality is not None and quality >= 3:
            # Map quality 3-5 → mastery_score 0.3-0.9 (never 1.0)
            mastery_score = min(0.9, (quality / 5.0) * 0.9 + 0.0)
            # Round to avoid floating point noise
            mastery_score = round(mastery_score, 4)
            await pool.execute(
                """
                UPDATE education.mind_map_nodes
                SET mastery_status = 'diagnosed',
                    mastery_score = $1,
                    updated_at = now()
                WHERE id = $2
                """,
                mastery_score,
                node["id"],
            )
            node = dict(node)
            node["mastery_status"] = "diagnosed"
            node["mastery_score"] = mastery_score
        updated_nodes.append(node)

    return updated_nodes


async def curriculum_replan(
    pool: asyncpg.Pool,
    mind_map_id: str,
    *,
    reason: str | None = None,
) -> dict[str, Any]:
    """Re-compute learning sequence based on current mastery state.

    Use this to extend an existing curriculum: add new nodes and edges first
    (via ``mind_map_node_create`` / ``mind_map_edge_create``), then call this
    function to re-sequence the entire graph. Prefer this over creating a new
    mind map when the user's request overlaps with an existing active curriculum.

    Re-runs the topological sort with fresh mastery data from the DB.
    Does NOT modify the existing DAG structure (nodes/edges).
    Marks fully-mastered nodes (mastery_score >= 0.9) as skippable in metadata.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    mind_map_id:
        UUID of the mind map to replan.
    reason:
        Optional free-text reason for the replan (logged for observability).
        When provided, the LLM session may add nodes before calling this
        function — this function only re-sorts whatever is in the DB.

    Returns
    -------
    dict
        Summary dict with keys: ``mind_map_id``, ``node_count``, ``edge_count``,
        ``status``.

    Raises
    ------
    ValueError
        If the mind map is not found or is in ``'abandoned'`` status.
    """
    map_row = await pool.fetchrow(
        "SELECT id, status FROM education.mind_maps WHERE id = $1",
        mind_map_id,
    )
    if map_row is None:
        raise ValueError(f"Mind map not found: {mind_map_id}")

    map_status = map_row["status"]
    if map_status == "abandoned":
        raise ValueError(f"Cannot replan mind map {mind_map_id}: status is 'abandoned'.")
    if map_status == "completed":
        raise ValueError(f"Cannot replan mind map {mind_map_id}: status is 'completed'.")

    logger.info(
        "curriculum_replan: mind_map_id=%s reason=%r",
        mind_map_id,
        reason,
    )

    # Load current graph from DB (mastery state is up-to-date)
    nodes, edges = await _fetch_nodes_and_edges(pool, mind_map_id)
    node_count = len(nodes)
    edge_count = len(edges)

    # Mark mastered nodes (mastery_score >= 0.9) as skippable in metadata (single batched UPDATE)
    await pool.execute(
        """
        UPDATE education.mind_map_nodes
        SET metadata = metadata || '{"skippable": true}'::jsonb,
            updated_at = now()
        WHERE mind_map_id = $1
          AND mastery_status = 'mastered'
          AND mastery_score >= 0.9
          AND NOT (metadata @> '{"skippable": true}')
        """,
        mind_map_id,
    )

    # Re-run topological sort
    ordered_ids = _topological_sort_with_tiebreak(nodes, edges)

    # Write new sequences
    await _write_sequences(pool, ordered_ids)

    return {
        "mind_map_id": mind_map_id,
        "node_count": node_count,
        "edge_count": edge_count,
        "status": map_status,
    }


async def curriculum_next_node(
    pool: asyncpg.Pool,
    mind_map_id: str,
) -> dict[str, Any] | None:
    """Return the frontier node with the lowest sequence number.

    Frontier = nodes where:
    - ``mastery_status IN ('unseen', 'diagnosed', 'learning')``
    - AND every prerequisite parent has ``mastery_status = 'mastered'``
      (or the node has no incoming prerequisite edges)

    Returns ``None`` when:
    - The mind map is 'completed' or 'abandoned'.
    - The frontier is empty (all nodes mastered or all remaining nodes
      are blocked by unmastered prerequisites).

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    mind_map_id:
        UUID of the mind map.

    Returns
    -------
    dict or None
        The next node to study as a dict, or None.
    """
    # Fast-path: check map status first
    map_row = await pool.fetchrow(
        "SELECT status FROM education.mind_maps WHERE id = $1",
        mind_map_id,
    )
    if map_row is None:
        return None

    if map_row["status"] in ("completed", "abandoned"):
        return None

    # Query the frontier, ordered by sequence ASC
    row = await pool.fetchrow(
        """
        SELECT n.*
        FROM education.mind_map_nodes n
        WHERE n.mind_map_id = $1
          AND n.mastery_status IN ('unseen', 'diagnosed', 'learning')
          AND NOT EXISTS (
              SELECT 1
              FROM education.mind_map_edges e
              JOIN education.mind_map_nodes parent ON e.parent_node_id = parent.id
              WHERE e.child_node_id = n.id
                AND e.edge_type = 'prerequisite'
                AND parent.mastery_status != 'mastered'
          )
        ORDER BY n.sequence ASC NULLS LAST
        LIMIT 1
        """,
        mind_map_id,
    )
    if row is None:
        return None
    return _row_to_dict(row)
