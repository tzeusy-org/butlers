"""Education butler — curriculum planning tools.

Provides three pure data/DB tools:

- ``curriculum_generate``: Validates a pre-structured concept graph (nodes +
  edges supplied by the caller), enforces structural constraints (max depth 5,
  max 30 nodes, DAG acyclicity), runs a deterministic topological sort with
  tie-breaking (depth → effort → diagnostic mastery), writes sequence integers
  to the DB, annotates node metadata with pedagogy hints (``concept_type`` and
  ``source_refs``), and transitions the mind map to 'active'.

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

from butlers.core.state import state_list
from butlers.tools.education._helpers import _row_to_dict
from butlers.tools.education.concept_types import CONCEPT_TYPES, classify_concept_type
from butlers.tools.education.source_material import PROVENANCE_VALUES, SOURCE_KEY_PREFIX

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Structural constraints
# ---------------------------------------------------------------------------

MAX_NODE_DEPTH = 5
MAX_NODES_PER_MAP = 30

# Mastery statuses that rank "earlier" in the diagnostic tie-break
_DIAGNOSED_STATUSES = {"diagnosed", "learning"}

# Where a source reference came from.  The planner works from model knowledge of
# a registered source, so its refs are "model-recalled" unless the caller says
# otherwise; "referenced" is reserved for refs read out of the source itself.
_VALID_PROVENANCE = PROVENANCE_VALUES
_DEFAULT_PROVENANCE = "model-recalled"


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
# Pedagogy annotation: concept types and source references
# ---------------------------------------------------------------------------


def _normalise_source_refs(
    source_refs: dict[str, Any],
) -> dict[str, list[dict[str, str]]]:
    """Validate the planner-supplied ``{node_label: [source_ref, ...]}`` mapping.

    Structural problems are the caller's bug, so they raise with an actionable
    message.  Whether a referenced source is actually *registered* is a
    knowledge-coverage question and is handled downstream by dropping the ref.

    Raises
    ------
    ValueError
        If the mapping, any entry list, or any individual ref is malformed.
    """
    if not isinstance(source_refs, dict):
        raise ValueError(
            "source_refs must be a mapping of {node_label: [{source_id, location}, ...]}, "
            f"got {type(source_refs).__name__}."
        )

    normalised: dict[str, list[dict[str, str]]] = {}
    for label, refs in source_refs.items():
        if not isinstance(refs, (list, tuple)):
            raise ValueError(
                f"source_refs[{label!r}] must be a list of source_ref dicts, "
                f"got {type(refs).__name__}."
            )

        cleaned: list[dict[str, str]] = []
        for ref in refs:
            if not isinstance(ref, dict):
                raise ValueError(
                    f"source_refs[{label!r}] entries must be dicts with 'source_id' and "
                    f"'location' keys, got {type(ref).__name__}."
                )

            source_id = ref.get("source_id")
            if not isinstance(source_id, str) or not source_id.strip():
                raise ValueError(
                    f"source_refs[{label!r}] has an entry without a non-empty 'source_id'. "
                    "Use source_material_list() to find registered source IDs."
                )

            location = ref.get("location")
            if not isinstance(location, str) or not location.strip():
                raise ValueError(
                    f"source_refs[{label!r}] entry for source {source_id} has no non-empty "
                    "'location'. Omit the ref instead of inventing a location."
                )

            provenance = ref.get("provenance", _DEFAULT_PROVENANCE)
            if provenance not in _VALID_PROVENANCE:
                allowed = ", ".join(sorted(_VALID_PROVENANCE))
                raise ValueError(
                    f"source_refs[{label!r}] has provenance={provenance!r}; must be one of: "
                    f"{allowed}."
                )

            cleaned.append(
                {
                    "source_id": source_id.strip(),
                    "location": location.strip(),
                    "provenance": provenance,
                }
            )
        normalised[label] = cleaned

    return normalised


async def _registered_source_ids(pool: asyncpg.Pool) -> set[str]:
    """Return the IDs of every source currently in the state-store registry."""
    keys = await state_list(pool, prefix=SOURCE_KEY_PREFIX, keys_only=True)
    return {
        key[len(SOURCE_KEY_PREFIX) :]
        for key in keys
        if isinstance(key, str) and key.startswith(SOURCE_KEY_PREFIX)
    }


async def _resolve_source_refs(
    pool: asyncpg.Pool,
    nodes: list[dict[str, Any]],
    source_refs: dict[str, Any],
) -> tuple[dict[str, list[dict[str, str]]], int]:
    """Map label-keyed source refs onto node IDs, dropping the unmappable ones.

    Refs are dropped (not raised on) when they name a label no node carries or a
    source that is not registered — the planner works best-effort from model
    knowledge, and a partial mapping is more useful than a failed curriculum.

    Returns
    -------
    tuple
        ``(refs_by_node_id, skipped_count)``.
    """
    normalised = _normalise_source_refs(source_refs)
    registered = await _registered_source_ids(pool)

    nodes_by_label: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for node in nodes:
        nodes_by_label[node.get("label") or ""].append(node)

    resolved: dict[str, list[dict[str, str]]] = {}
    skipped = 0

    for label, refs in normalised.items():
        targets = nodes_by_label.get(label)
        if not targets:
            skipped += len(refs)
            logger.warning(
                "curriculum source ref skipped: no node labelled %r (%d ref(s))",
                label,
                len(refs),
            )
            continue

        mappable = [ref for ref in refs if ref["source_id"] in registered]
        unregistered = len(refs) - len(mappable)
        if unregistered:
            skipped += unregistered
            logger.warning(
                "curriculum source ref skipped for node %r: %d ref(s) name an unregistered source",
                label,
                unregistered,
            )

        for node in targets:
            resolved.setdefault(node["id"], []).extend(mappable)

    return resolved, skipped


def _build_metadata_patches(
    nodes: list[dict[str, Any]],
    refs_by_node_id: dict[str, list[dict[str, str]]],
) -> tuple[dict[str, dict[str, Any]], int, int]:
    """Build the per-node metadata patch for concept types and source refs.

    A node gets a ``concept_type`` only when it does not already carry a valid
    one and the heuristic classifies it confidently; unclassifiable nodes are
    left alone so the teaching phase falls back to its Socratic default.
    Source refs are merged into any existing list, deduplicated on
    ``(source_id, location)``.

    Returns
    -------
    tuple
        ``(patches_by_node_id, concept_types_assigned, source_refs_assigned)``.
    """
    patches: dict[str, dict[str, Any]] = {}
    concept_types_assigned = 0
    source_refs_assigned = 0

    for node in nodes:
        metadata = node.get("metadata") or {}
        patch: dict[str, Any] = {}

        if metadata.get("concept_type") not in CONCEPT_TYPES:
            inferred = classify_concept_type(node.get("label") or "", node.get("description"))
            if inferred is not None:
                patch["concept_type"] = inferred
                concept_types_assigned += 1

        incoming = refs_by_node_id.get(node["id"]) or []
        if incoming:
            existing = metadata.get("source_refs")
            merged = list(existing) if isinstance(existing, list) else []
            seen = {
                (ref.get("source_id"), ref.get("location"))
                for ref in merged
                if isinstance(ref, dict)
            }
            added = 0
            for ref in incoming:
                key = (ref["source_id"], ref["location"])
                if key in seen:
                    continue
                seen.add(key)
                merged.append(ref)
                added += 1
            if added:
                patch["source_refs"] = merged
                source_refs_assigned += added

        if patch:
            patches[node["id"]] = patch

    return patches, concept_types_assigned, source_refs_assigned


async def _write_metadata_patches(
    pool: asyncpg.Pool,
    patches: dict[str, dict[str, Any]],
) -> None:
    """Shallow-merge metadata patches into mind_map_nodes (single batched UPDATE).

    Patches are passed as ``text[]`` and cast to ``jsonb`` in SQL so the encoding
    does not depend on an array-of-jsonb codec being registered on the pool.
    """
    if not patches:
        return

    node_ids = list(patches)
    payloads = [json.dumps(patches[node_id]) for node_id in node_ids]
    await pool.execute(
        """
        UPDATE education.mind_map_nodes AS n
        SET metadata = n.metadata || s.patch,
            updated_at = now()
        FROM (
            SELECT unnest($1::uuid[]) AS id,
                   unnest($2::text[])::jsonb AS patch
        ) AS s
        WHERE n.id = s.id
        """,
        node_ids,
        payloads,
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
    source_refs: dict[str, Any] | None = None,
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
    6. Annotating node metadata with ``concept_type`` (inferred from label and
       description) and ``source_refs`` (if ``source_refs`` supplied).
    7. Recording the goal in ``mind_maps.metadata`` (if supplied).
    8. Transitioning the mind map status to ``'active'``.

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
    source_refs:
        Optional mapping of ``{node_label: [{source_id, location, provenance?}]}``
        recalled from registered source material (see ``source_material_list``).
        ``provenance`` defaults to ``'model-recalled'``.  Refs naming an
        unregistered source or an unknown node label are dropped and counted in
        ``source_refs_skipped`` — never fabricate a location to keep a ref.

    Returns
    -------
    dict
        Summary dict with keys: ``mind_map_id``, ``node_count``, ``edge_count``,
        ``status``, ``concept_types_assigned``, ``source_refs_assigned``,
        ``source_refs_skipped``.

    Raises
    ------
    ValueError
        If the mind map is not found, structural constraints are violated, or
        ``source_refs`` is malformed.
    """
    # Verify mind map exists and is in a plannable state
    map_row = await pool.fetchrow(
        "SELECT id, status FROM education.mind_maps WHERE id = $1",
        mind_map_id,
    )
    if map_row is None:
        raise ValueError(
            f"Mind map not found: {mind_map_id}. "
            "Use mind_map_list() to find existing mind maps and their IDs."
        )

    map_status = map_row["status"]
    if map_status in ("completed", "abandoned"):
        raise ValueError(
            f"Cannot generate curriculum for mind map {mind_map_id} with status={map_status!r}. "
            f"Only 'draft' or 'active' maps can be planned. "
            f"Create a new mind map with teaching_flow_start(topic=...) instead."
        )

    # Load graph from DB
    nodes, edges = await _fetch_nodes_and_edges(pool, mind_map_id)

    node_count = len(nodes)
    edge_count = len(edges)

    if node_count == 0:
        raise ValueError(
            f"Mind map {mind_map_id} has no nodes — cannot generate curriculum. "
            "Add nodes first with mind_map_node_create(mind_map_id=..., "
            "label=..., depth=...)."
        )

    # Structural constraint validation
    _validate_constraints(nodes, edges, mind_map_id=mind_map_id)
    _check_dag_acyclicity(nodes, edges)

    # Apply diagnostic seeding before sort (influences tie-breaking)
    if diagnostic_results:
        nodes = await _apply_diagnostic_seeding(pool, nodes, diagnostic_results)

    # Resolve source refs against the registry before annotating metadata
    refs_by_node_id: dict[str, list[dict[str, str]]] = {}
    source_refs_skipped = 0
    if source_refs:
        refs_by_node_id, source_refs_skipped = await _resolve_source_refs(pool, nodes, source_refs)

    # Topological sort with tie-breaking
    ordered_ids = _topological_sort_with_tiebreak(nodes, edges)

    # Write sequences to DB
    await _write_sequences(pool, ordered_ids)

    # Annotate node metadata with concept types and mapped source refs
    patches, concept_types_assigned, source_refs_assigned = _build_metadata_patches(
        nodes, refs_by_node_id
    )
    await _write_metadata_patches(pool, patches)

    # Transition to 'active'; merge goal into metadata if supplied
    if goal is not None:
        await pool.execute(
            """
            UPDATE education.mind_maps
            SET metadata = metadata || $1::jsonb,
                status = 'active',
                updated_at = now()
            WHERE id = $2
            """,
            {"goal": goal},
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
        "curriculum_generate: mind_map_id=%s nodes=%d edges=%d goal=%r "
        "concept_types=%d source_refs=%d skipped=%d",
        mind_map_id,
        node_count,
        edge_count,
        goal,
        concept_types_assigned,
        source_refs_assigned,
        source_refs_skipped,
    )

    return {
        "mind_map_id": mind_map_id,
        "node_count": node_count,
        "edge_count": edge_count,
        "status": "active",
        "concept_types_assigned": concept_types_assigned,
        "source_refs_assigned": source_refs_assigned,
        "source_refs_skipped": source_refs_skipped,
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
        raise ValueError(
            f"Mind map not found: {mind_map_id}. "
            "Use mind_map_list() to find existing mind maps and their IDs."
        )

    map_status = map_row["status"]
    if map_status == "abandoned":
        raise ValueError(
            f"Cannot replan mind map {mind_map_id}: status is 'abandoned'. "
            "Create a new mind map with teaching_flow_start(topic=...) instead."
        )
    if map_status == "completed":
        raise ValueError(
            f"Cannot replan mind map {mind_map_id}: status is 'completed'. "
            "Create a new mind map with teaching_flow_start(topic=...) to study further."
        )

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
