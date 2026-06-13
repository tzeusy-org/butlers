"""Shared, model-free merge-review evidence + audit-row helpers.

The ``relationship-merge-review`` spec is binding: every entity merge MUST leave a
``relationship.merge_reviews`` audit row **regardless of entry path**, and "when no
compare context exists, the merge endpoint computes the shared/divergent snapshot
server-side at merge time" (spec §"Single-pair review UX"). That requirement covers
session-side tooling (the memory ``memory_entity_merge`` MCP tool and the
relationship ``contact_merge`` MCP tool), not just the dashboard compare flow.

This module is the single source of truth for:

1. computing the deterministic shared/divergent identity-fact evidence for a pair
   of entities (``compute_merge_evidence``), and
2. serialising that evidence into a ``relationship.merge_reviews`` audit row
   (``write_merge_review``).

Both the relationship API router (``roster/relationship/api/router.py``) and the
session-side MCP merge tools delegate here so the audit row is byte-identical
regardless of which entry path triggered the merge.

**No model involvement.** Per ``relationship-merge-review`` §"No model involvement"
and ``relationship-entity-lifecycle`` §"Match — deterministic matching only", this
module performs a deterministic structural diff only: no LLM-provider client, no
spawner invocation, no embedding call, no generated prose. It is FastAPI-free
(no ``HTTPException``) and Pydantic-free (emits plain JSON-serialisable dicts that
match the ``CompareFact`` model's ``model_dump(mode="json")`` shape) so the memory
module can import it without pulling the relationship API model stack.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from butlers.tools.relationship.staleness import identity_staleness_band_sql


def identity_row_to_review_json(row: Any) -> dict[str, Any]:
    """Serialise an identity-store (``relationship.entity_facts``) row to audit JSON.

    The output shape mirrors the relationship API ``CompareFact`` model's
    ``model_dump(mode="json")`` output so audit rows are identical whether written
    from the dashboard compare flow or from session-side tooling. UUIDs and
    datetimes are rendered as strings (JSON-safe).
    """
    observed_at = row["observed_at"]
    last_seen = row["last_seen"]
    return {
        "id": str(row["id"]),
        "entity_id": str(row["subject"]),
        "predicate": row["predicate"],
        "object": row["object"],
        "object_kind": row["object_kind"],
        "store": "identity",
        "src": row["src"],
        "conf": float(row["conf"]) if row["conf"] is not None else 1.0,
        "verified": row["verified"],
        "primary": row["primary"],
        "observed_at": observed_at.isoformat() if observed_at is not None else None,
        "last_seen": last_seen.isoformat() if last_seen is not None else None,
        "staleness_band": row["staleness_band"],
    }


async def fetch_identity_facts(pool: Any, entity_id: UUID) -> list[Any]:
    """Fetch active identity-store facts for an entity with read-time staleness bands.

    Reads only ``relationship.entity_facts`` (the identity store) — the only store
    that feeds the shared/divergent merge evidence (narrative facts never enter
    ``shared`` or ``divergent`` per the merge-review spec).
    """
    return await pool.fetch(
        f"""
        SELECT
            f.id,
            f.subject,
            f.predicate,
            f.object,
            f.object_kind,
            f.src,
            f.conf,
            f.verified,
            f."primary",
            f.observed_at,
            f.last_seen,
            {identity_staleness_band_sql("f")} AS staleness_band
        FROM relationship.entity_facts f
        WHERE f.subject = $1
          AND f.validity = 'active'
        ORDER BY f.predicate, f.created_at DESC, f.id DESC
        """,
        entity_id,
    )


async def fetch_single_cardinality_predicates(pool: Any) -> set[str]:
    """Return the set of predicates with ``cardinality = 'single'`` in the registry.

    Single-cardinality predicates are the only ones that can DIVERGE on merge (an
    entity holds at most one active value). Multi-valued predicates union on merge
    and never conflict (the three-emails-three-rows rule).
    """
    rows = await pool.fetch(
        """
        SELECT predicate
        FROM relationship.entity_predicate_registry
        WHERE cardinality = 'single'
        """
    )
    return {r["predicate"] for r in rows}


def derive_shared_and_divergent_rows(
    a_identity: list[Any],
    b_identity: list[Any],
    single_predicates: set[str],
) -> tuple[list[Any], list[Any]]:
    """Compute the ``shared`` and ``divergent`` row lists from two identity-fact sets.

    - ``shared``: rows where both entities hold an active row with identical
      ``(predicate, object)``. Emitted as the A-row followed by the B-row.
    - ``divergent``: rows for single-cardinality predicates that BOTH entities hold
      but with DIFFERENT objects. Multi-valued predicates never diverge.

    Deterministic: no scoring, no ranking, no generated text. Returns the raw
    asyncpg rows (callers serialise via ``identity_row_to_review_json`` or wrap in
    a presentation model).
    """
    a_pairs = {(r["predicate"], r["object"]) for r in a_identity}
    b_pairs = {(r["predicate"], r["object"]) for r in b_identity}
    shared_keys = a_pairs & b_pairs

    shared: list[Any] = []
    for key in sorted(shared_keys):
        a_row = next(r for r in a_identity if (r["predicate"], r["object"]) == key)
        b_row = next(r for r in b_identity if (r["predicate"], r["object"]) == key)
        shared.append(a_row)
        shared.append(b_row)

    # Divergent: single-cardinality predicates present on both with differing objects.
    a_objs_by_pred: dict[str, set[str]] = {}
    for r in a_identity:
        if r["predicate"] in single_predicates:
            a_objs_by_pred.setdefault(r["predicate"], set()).add(r["object"])
    b_objs_by_pred: dict[str, set[str]] = {}
    for r in b_identity:
        if r["predicate"] in single_predicates:
            b_objs_by_pred.setdefault(r["predicate"], set()).add(r["object"])

    divergent: list[Any] = []
    for predicate in sorted(set(a_objs_by_pred) & set(b_objs_by_pred)):
        if a_objs_by_pred[predicate] == b_objs_by_pred[predicate]:
            # Same single value on both → not a conflict.
            continue
        for r in a_identity:
            if r["predicate"] == predicate and r["object"] not in b_objs_by_pred[predicate]:
                divergent.append(r)
        for r in b_identity:
            if r["predicate"] == predicate and r["object"] not in a_objs_by_pred[predicate]:
                divergent.append(r)

    return shared, divergent


async def compute_merge_evidence(
    pool: Any, entity_a: UUID, entity_b: UUID
) -> dict[str, list[dict[str, Any]]]:
    """Compute the deterministic shared/divergent audit evidence for a pair.

    Returns ``{"shared": [...], "divergent": [...]}`` where each list holds
    JSON-serialisable dicts (the ``CompareFact`` JSON shape). This is the snapshot
    written into a ``relationship.merge_reviews`` row when no compare context was
    supplied by the caller (session-side merges) — computed before the merge mutates
    rows so the evidence reflects the pre-merge state.

    No scoring, no ranking, no generated text — deterministic structural diff over
    the identity store only.
    """
    a_identity = await fetch_identity_facts(pool, entity_a)
    b_identity = await fetch_identity_facts(pool, entity_b)
    single_predicates = await fetch_single_cardinality_predicates(pool)

    shared_rows, divergent_rows = derive_shared_and_divergent_rows(
        a_identity, b_identity, single_predicates
    )
    return {
        "shared": [identity_row_to_review_json(r) for r in shared_rows],
        "divergent": [identity_row_to_review_json(r) for r in divergent_rows],
    }


async def write_merge_review(
    executor: Any,
    *,
    entity_a: UUID,
    entity_b: UUID,
    shared_facts: list[dict[str, Any]],
    divergent_facts: list[dict[str, Any]],
    outcome: str,
) -> UUID:
    """Insert a ``relationship.merge_reviews`` audit row, returning its id.

    ``executor`` is any asyncpg executor exposing ``fetchval`` — a pool or a
    connection inside an open transaction. The merge path passes the active
    connection so the audit row commits atomically with the merge mutations;
    the dismissal path passes the pool (no surrounding transaction).

    ``shared_facts`` / ``divergent_facts`` are already-serialised JSON dicts (the
    ``CompareFact`` JSON shape — produced by ``compute_merge_evidence`` for the
    session-side paths, or by the API router from its ``CompareFact`` models). Rows
    are written at commit time only (no pending state); both merge and dismissal
    write a row.
    """
    shared_json = json.dumps(shared_facts)
    divergent_json = json.dumps(divergent_facts)
    review_id = await executor.fetchval(
        """
        INSERT INTO relationship.merge_reviews
            (entity_a, entity_b, shared_facts, divergent_facts, outcome, reviewed_at)
        VALUES ($1, $2, $3::jsonb, $4::jsonb, $5, now())
        RETURNING id
        """,
        entity_a,
        entity_b,
        shared_json,
        divergent_json,
        outcome,
    )
    return review_id
