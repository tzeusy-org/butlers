"""Education butler — diagnostic assessment: probe sequencing and mastery seeding.

Implements a three-function diagnostic flow:

1. diagnostic_start      — initialise DIAGNOSING state, return concept inventory
2. diagnostic_record_probe — record a probe result, conservatively seed mastery
3. diagnostic_complete   — finalise diagnostic, transition flow state to PLANNING
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import asyncpg

# ---------------------------------------------------------------------------
# KV-store helpers (thin wrappers so tests can mock them easily)
# ---------------------------------------------------------------------------


async def state_store_get(pool: asyncpg.Pool, key: str) -> Any | None:
    """Read a JSONB value from the core state table by *key*.

    Returns the deserialized Python object, or ``None`` if absent.
    """
    row = await pool.fetchval("SELECT value FROM state WHERE key = $1", key)
    if row is None:
        return None
    if isinstance(row, str):
        return json.loads(row)
    return row


async def state_store_set(pool: asyncpg.Pool, key: str, value: Any) -> None:
    """Upsert *key* → *value* (JSON-serialisable) in the core state table."""
    json_value = json.dumps(value)
    await pool.execute(
        """
        INSERT INTO state (key, value, updated_at, version)
        VALUES ($1, $2::jsonb, now(), 1)
        ON CONFLICT (key) DO UPDATE
            SET value      = EXCLUDED.value,
                updated_at = now(),
                version    = state.version + 1
        """,
        key,
        json_value,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _flow_key(mind_map_id: str) -> str:
    return f"flow:{mind_map_id}"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def diagnostic_start(
    pool: asyncpg.Pool,
    mind_map_id: str,
) -> list[dict[str, Any]]:
    """Initialise a diagnostic assessment for a mind map.

    Verifies the mind map exists, guards against re-starts on non-PENDING flows,
    builds a concept inventory ranked by node depth, writes flow state to the KV
    store, and returns the inventory list.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    mind_map_id:
        UUID of the mind map to diagnose.

    Returns
    -------
    list of dict
        Concept inventory — each item contains:
        ``node_id``, ``label``, ``description``, ``difficulty_rank``.

    Raises
    ------
    ValueError
        If the mind map does not exist, or the flow is already past PENDING.
    """
    # Verify mind map exists
    map_row = await pool.fetchrow(
        "SELECT id FROM education.mind_maps WHERE id = $1",
        mind_map_id,
    )
    if map_row is None:
        raise ValueError(f"Mind map not found: {mind_map_id!r}")

    # Check existing flow state
    flow_key = _flow_key(mind_map_id)
    existing = await state_store_get(pool, flow_key)
    if existing is not None:
        status = existing.get("status", "")
        if status not in ("", "PENDING"):
            raise ValueError(
                f"Cannot start diagnostic: flow is already in state {status!r}. "
                "Only flows with status 'PENDING' (or no flow state) can be diagnosed."
            )

    # Fetch all nodes, use depth as proxy for difficulty_rank (0 = easiest)
    node_rows = await pool.fetch(
        """
        SELECT id::text AS node_id, label, description, depth
        FROM education.mind_map_nodes
        WHERE mind_map_id = $1
        ORDER BY depth ASC, label ASC
        """,
        mind_map_id,
    )

    concept_inventory = [
        {
            "node_id": row["node_id"],
            "label": row["label"],
            "description": row["description"],
            "difficulty_rank": row["depth"],
        }
        for row in node_rows
    ]

    # Initialise flow state
    now = _utc_now_iso()
    flow_state: dict[str, Any] = {
        "status": "DIAGNOSING",
        "mind_map_id": mind_map_id,
        "concept_inventory": concept_inventory,
        "probes_issued": 0,
        "diagnostic_results": {},
        "started_at": now,
        "last_session_at": now,
    }
    await state_store_set(pool, flow_key, flow_state)

    return concept_inventory


async def diagnostic_record_probe(
    pool: asyncpg.Pool,
    mind_map_id: str,
    node_id: str,
    quality: int,
    inferred_mastery: float,
) -> dict[str, Any]:
    """Record a single diagnostic probe result and seed node mastery.

    All database mutations (quiz_responses insert + node update) are executed
    inside a single atomic transaction.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    mind_map_id:
        UUID of the mind map under diagnostic.
    node_id:
        UUID of the node that was probed.
    quality:
        SM-2 quality score 0-5.
    inferred_mastery:
        Estimated mastery score in [0.0, 1.0). Values outside this range are
        rejected. 1.0 is explicitly disallowed — diagnostic scores are always
        conservative seeds.

    Returns
    -------
    dict
        Updated flow state.

    Raises
    ------
    ValueError
        If validation fails or the flow is not in DIAGNOSING status.
    """
    # --- input validation ---
    if not (0 <= quality <= 5):
        raise ValueError(f"quality must be between 0 and 5, got {quality!r}")
    if not (0.0 <= inferred_mastery < 1.0):
        raise ValueError(
            f"inferred_mastery must be in [0.0, 1.0) — diagnostic seeds are never 1.0. "
            f"Got {inferred_mastery!r}"
        )

    # --- flow state check ---
    flow_key = _flow_key(mind_map_id)
    flow_state = await state_store_get(pool, flow_key)
    if flow_state is None or flow_state.get("status") != "DIAGNOSING":
        current = flow_state.get("status") if flow_state else None
        raise ValueError(f"Cannot record probe: flow must be in DIAGNOSING status, got {current!r}")

    # --- DB mutations (atomic) ---
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Verify node belongs to the mind map
            node_row = await conn.fetchrow(
                """
                SELECT id, mastery_status
                FROM education.mind_map_nodes
                WHERE id = $1 AND mind_map_id = $2
                """,
                node_id,
                mind_map_id,
            )
            if node_row is None:
                raise ValueError(f"Node {node_id!r} not found in mind map {mind_map_id!r}")

            # Insert quiz response with response_type='diagnostic'
            await conn.execute(
                """
                INSERT INTO education.quiz_responses
                    (node_id, mind_map_id, question_text, user_answer, quality,
                     response_type, session_id)
                VALUES ($1, $2, $3, $4, $5, 'diagnostic', NULL)
                """,
                node_id,
                mind_map_id,
                # question_text is not available in the tool interface; use a sentinel
                "[diagnostic probe]",
                None,
                quality,
            )

            # Seed mastery only for quality >= 3 (correct answers)
            # Mastery seeds are ALWAYS in [0.3, 0.7] — never 1.0
            if quality >= 3:
                # Clamp inferred_mastery to [0.3, 0.7] as a safety guard
                seeded_score = max(0.3, min(0.7, inferred_mastery))
                await conn.execute(
                    """
                    UPDATE education.mind_map_nodes
                    SET mastery_score  = $1,
                        mastery_status = 'diagnosed',
                        updated_at     = now()
                    WHERE id = $2
                    """,
                    seeded_score,
                    node_id,
                )
            # quality < 3: leave node as 'unseen', do NOT increase mastery_score

    # --- update flow state ---
    flow_state["probes_issued"] = flow_state.get("probes_issued", 0) + 1
    diagnostic_results = flow_state.setdefault("diagnostic_results", {})
    diagnostic_results[node_id] = {
        "quality": quality,
        "inferred_mastery": inferred_mastery,
    }
    await state_store_set(pool, flow_key, flow_state)

    return flow_state


async def diagnostic_complete(
    pool: asyncpg.Pool,
    mind_map_id: str,
) -> dict[str, Any]:
    """Finalise the diagnostic session and transition flow state to PLANNING.

    Computes a mastery summary from the recorded probe results, updates the flow
    state to PLANNING, and returns the summary dict.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    mind_map_id:
        UUID of the mind map under diagnostic.

    Returns
    -------
    dict with keys:
        ``summary`` — per-node dict with quality, inferred_mastery, mastery_status;
        ``unprobed_node_count`` — nodes in inventory that were not probed;
        ``total_concepts_in_inventory`` — size of the concept inventory;
        ``inferred_frontier_rank`` — highest difficulty_rank of correctly probed
        nodes (quality >= 3), or 0 if none.

    Raises
    ------
    ValueError
        If flow is not in DIAGNOSING status, or if no probes were issued.
    """
    flow_key = _flow_key(mind_map_id)
    flow_state = await state_store_get(pool, flow_key)

    if flow_state is None or flow_state.get("status") != "DIAGNOSING":
        current = flow_state.get("status") if flow_state else None
        raise ValueError(
            f"Cannot complete diagnostic: flow must be in DIAGNOSING status, got {current!r}"
        )

    if flow_state.get("probes_issued", 0) == 0:
        raise ValueError(
            "Cannot complete diagnostic: no probes have been issued. "
            "Call diagnostic_record_probe() at least once before completing."
        )

    # Fetch current mastery status for all probed nodes
    diagnostic_results: dict[str, dict[str, Any]] = flow_state.get("diagnostic_results", {})
    concept_inventory: list[dict[str, Any]] = flow_state.get("concept_inventory", [])

    # Build a map of node_id → difficulty_rank from the inventory
    rank_by_node: dict[str, int] = {
        item["node_id"]: item["difficulty_rank"] for item in concept_inventory
    }

    # Fetch mastery status for probed nodes
    probed_node_ids = list(diagnostic_results.keys())
    summary: dict[str, dict[str, Any]] = {}
    inferred_frontier_rank = 0

    if probed_node_ids:
        node_rows = await pool.fetch(
            """
            SELECT id::text AS node_id, mastery_status
            FROM education.mind_map_nodes
            WHERE id = ANY($1::uuid[]) AND mind_map_id = $2
            """,
            probed_node_ids,
            mind_map_id,
        )
        status_by_node = {row["node_id"]: row["mastery_status"] for row in node_rows}

        for nid, probe_data in diagnostic_results.items():
            mastery_status = status_by_node.get(nid, "unseen")
            summary[nid] = {
                "quality": probe_data["quality"],
                "inferred_mastery": probe_data["inferred_mastery"],
                "mastery_status": mastery_status,
            }
            # Track highest difficulty rank for correctly-answered probes
            if probe_data["quality"] >= 3:
                rank = rank_by_node.get(nid, 0)
                if rank > inferred_frontier_rank:
                    inferred_frontier_rank = rank

    total_concepts = len(concept_inventory)
    unprobed_count = total_concepts - len(probed_node_ids)

    # Transition flow state to PLANNING
    flow_state["status"] = "PLANNING"
    flow_state["last_session_at"] = _utc_now_iso()
    await state_store_set(pool, flow_key, flow_state)

    return {
        "summary": summary,
        "unprobed_node_count": max(0, unprobed_count),
        "total_concepts_in_inventory": total_concepts,
        "inferred_frontier_rank": inferred_frontier_rank,
    }
