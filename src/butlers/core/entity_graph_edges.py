"""Write-behind helpers for ``public.entity_graph_edges`` (RFC 0031, Slice 2).

See ``about/legends-and-lore/rfcs/0031-public-entity-graph-projection.md``.
Slice 1 (bu-8cdl1.8, PR #4005) created the substrate table; this module is
the shared write path every Slice-2 writer (memory storage, relationship's
central fact writer, commitments) calls into.

**Write-behind contract (RFC 0031 "Write-Behind Contract"):** every function
here MUST be called on the same ``asyncpg`` connection/transaction as the
source-row write it projects. None of them open their own transaction or
swallow exceptions — a failure here propagates to the caller so the caller's
transaction rolls back, and the source write never commits without its edge
(or vice versa). This is the opposite contract from
``butlers.modules.memory.storage._upsert_catalog``'s best-effort,
non-blocking write-behind to ``public.memory_catalog``: that projection is
allowed to lag or fail silently, this one is not.
"""

from __future__ import annotations

import uuid

import asyncpg

#: Sensitivity vocabulary, reused verbatim from the memory-catalog convention
#: (``butlers.modules.memory.search.CATALOG_SENSITIVITY_LEVELS``).
SENSITIVITY_LEVELS: tuple[str, ...] = ("normal", "pii", "confidential")
DEFAULT_SENSITIVITY = "normal"

#: Sensitivities excluded from a live, content-bearing edge — projected as a
#: withheld stub instead (RFC 0031 "Withheld Stub Edges"). Mirrors
#: ``butlers.modules.memory.storage.CATALOG_WRITE_EXCLUDED_SENSITIVITIES``.
WITHHELD_SENSITIVITIES: frozenset[str] = frozenset({"pii", "confidential"})


def is_withheld_sensitivity(sensitivity: str | None) -> bool:
    """Return True if *sensitivity* must be projected as a withheld stub.

    NULL/absent is treated as ``'normal'`` (never withheld), mirroring the
    ``COALESCE(sensitivity, 'normal')`` convention used throughout the
    memory-catalog write path.
    """
    return (sensitivity or DEFAULT_SENSITIVITY) in WITHHELD_SENSITIVITIES


async def project_entity_graph_edge(
    conn: asyncpg.Connection,
    *,
    source_schema: str,
    source_table: str,
    source_id: uuid.UUID,
    subject_entity_id: uuid.UUID,
    predicate: str,
    object_entity_id: uuid.UUID,
    sensitivity: str = DEFAULT_SENSITIVITY,
) -> None:
    """Upsert a live edge for one canonical source row, on *conn*.

    Idempotent on the natural key ``(source_schema, source_table,
    source_id)`` — safe to call again for the same source row (e.g. from a
    backfill re-run) without duplicating an edge.
    """
    await conn.execute(
        """
        INSERT INTO public.entity_graph_edges (
            source_schema, source_table, source_id,
            subject_entity_id, predicate, object_entity_id,
            sensitivity, withheld_reason, updated_at
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, NULL, now())
        ON CONFLICT (source_schema, source_table, source_id) DO UPDATE SET
            subject_entity_id = EXCLUDED.subject_entity_id,
            predicate         = EXCLUDED.predicate,
            object_entity_id  = EXCLUDED.object_entity_id,
            sensitivity       = EXCLUDED.sensitivity,
            withheld_reason   = NULL,
            updated_at        = now()
        """,
        source_schema,
        source_table,
        source_id,
        subject_entity_id,
        predicate,
        object_entity_id,
        sensitivity,
    )


async def withhold_entity_graph_edge(
    conn: asyncpg.Connection,
    *,
    source_schema: str,
    source_table: str,
    source_id: uuid.UUID,
    subject_entity_id: uuid.UUID,
    sensitivity: str,
) -> None:
    """Upsert a count-only withheld stub for one canonical source row.

    ``predicate``/``object_entity_id`` are never set on a withheld row — the
    ``chk_entity_graph_edges_payload_xor_withheld`` constraint enforces this
    structurally, so a bug here fails loudly rather than leaking content
    through a withheld row.
    """
    await conn.execute(
        """
        INSERT INTO public.entity_graph_edges (
            source_schema, source_table, source_id,
            subject_entity_id, predicate, object_entity_id,
            sensitivity, withheld_reason, updated_at
        )
        VALUES ($1, $2, $3, $4, NULL, NULL, $5, 'sensitivity', now())
        ON CONFLICT (source_schema, source_table, source_id) DO UPDATE SET
            subject_entity_id = EXCLUDED.subject_entity_id,
            predicate         = NULL,
            object_entity_id  = NULL,
            sensitivity       = EXCLUDED.sensitivity,
            withheld_reason   = 'sensitivity',
            updated_at        = now()
        """,
        source_schema,
        source_table,
        source_id,
        subject_entity_id,
        sensitivity,
    )


async def project_or_withhold_entity_graph_edge(
    conn: asyncpg.Connection,
    *,
    source_schema: str,
    source_table: str,
    source_id: uuid.UUID,
    subject_entity_id: uuid.UUID,
    predicate: str,
    object_entity_id: uuid.UUID,
    sensitivity: str = DEFAULT_SENSITIVITY,
) -> None:
    """Project a live edge, or a withheld stub when *sensitivity* excludes payload.

    The dispatch a caller needs whenever its source row carries a real
    sensitivity classification (currently: memory facts). Callers whose
    source table has no sensitivity concept (e.g. ``relationship.entity_facts``)
    should call :func:`project_entity_graph_edge` directly instead.
    """
    if is_withheld_sensitivity(sensitivity):
        await withhold_entity_graph_edge(
            conn,
            source_schema=source_schema,
            source_table=source_table,
            source_id=source_id,
            subject_entity_id=subject_entity_id,
            sensitivity=sensitivity,
        )
    else:
        await project_entity_graph_edge(
            conn,
            source_schema=source_schema,
            source_table=source_table,
            source_id=source_id,
            subject_entity_id=subject_entity_id,
            predicate=predicate,
            object_entity_id=object_entity_id,
            sensitivity=sensitivity,
        )


async def delete_entity_graph_edge(
    conn: asyncpg.Connection,
    *,
    source_schema: str,
    source_table: str,
    source_id: uuid.UUID,
) -> None:
    """Delete the projected edge for one canonical source row, if any.

    A no-op when no edge was ever projected for this natural key — callers
    may invoke this unconditionally for any retracted/superseded/deleted
    source row without first checking whether it was an edge-bearing row.
    """
    await conn.execute(
        "DELETE FROM public.entity_graph_edges "
        "WHERE source_schema = $1 AND source_table = $2 AND source_id = $3",
        source_schema,
        source_table,
        source_id,
    )


async def delete_entity_graph_edges(
    conn: asyncpg.Connection,
    *,
    source_schema: str,
    source_table: str,
    source_ids: list[uuid.UUID],
) -> None:
    """Bulk variant of :func:`delete_entity_graph_edge` for a batch cascade."""
    if not source_ids:
        return
    await conn.execute(
        "DELETE FROM public.entity_graph_edges "
        "WHERE source_schema = $1 AND source_table = $2 AND source_id = ANY($3)",
        source_schema,
        source_table,
        source_ids,
    )


# ---------------------------------------------------------------------------
# Backfill (RFC 0031 Slice 2: "idempotent backfill job over existing source
# rows"). Each function upserts on the same natural key the live writers use,
# so re-running any of them (including after a partial prior run) never
# duplicates an edge — matching the write-behind contract's idempotency
# guarantee. Only currently-active source rows are backfilled: a superseded
# or retracted row's edge is removed by the live writer at the moment it
# transitions, so a pre-existing (already-superseded) historical row was
# never meant to carry a live edge going forward either.
# ---------------------------------------------------------------------------


async def backfill_relationship_entity_facts_edges(pool: asyncpg.Pool, *, limit: int = 500) -> int:
    """Backfill active entity-to-entity ``relationship.entity_facts`` rows.

    Only ``object_kind = 'entity'`` rows describe an entity-to-entity
    relationship; literal contact-channel triples (``has-email``, etc.) are
    never graph edges. ``relationship.entity_facts`` has no sensitivity
    column, so every backfilled row is a live edge (sensitivity='normal'),
    matching the live writer in ``relationship_assert_fact.py``.
    """
    sql = """
        WITH candidates AS (
            SELECT ef.id, ef.subject, ef.predicate, ef.object
            FROM relationship.entity_facts ef
            WHERE ef.object_kind = 'entity'
              AND ef.validity = 'active'
              AND NOT EXISTS (
                  SELECT 1 FROM public.entity_graph_edges g
                  WHERE g.source_schema = 'relationship'
                    AND g.source_table = 'entity_facts'
                    AND g.source_id = ef.id
              )
            LIMIT $1
        ),
        inserted AS (
            INSERT INTO public.entity_graph_edges (
                source_schema, source_table, source_id,
                subject_entity_id, predicate, object_entity_id,
                sensitivity, withheld_reason, updated_at
            )
            SELECT 'relationship', 'entity_facts', c.id,
                   c.subject, c.predicate, c.object::uuid,
                   'normal', NULL, now()
            FROM candidates c
            ON CONFLICT (source_schema, source_table, source_id) DO NOTHING
            RETURNING 1
        )
        SELECT COUNT(*) FROM inserted
    """
    count = await pool.fetchval(sql, limit)
    return int(count or 0)


async def backfill_memory_facts_edges(
    pool: asyncpg.Pool, *, source_schema: str, limit: int = 500
) -> int:
    """Backfill one butler's active edge-facts (``facts`` table, schema-scoped pool).

    *pool* must be scoped (via ``search_path``) to the owning butler's
    schema, matching ``run_memory_catalog_backfill``'s calling convention —
    ``facts`` is queried unqualified. *source_schema* is the same schema
    name, used as the projected edge's provenance column.
    """
    sql = """
        WITH candidates AS (
            SELECT f.id, f.entity_id, f.predicate, f.object_entity_id, f.sensitivity
            FROM facts f
            WHERE f.entity_id IS NOT NULL
              AND f.object_entity_id IS NOT NULL
              AND f.validity IN ('active', 'fading')
              AND NOT EXISTS (
                  SELECT 1 FROM public.entity_graph_edges g
                  WHERE g.source_schema = $1
                    AND g.source_table = 'facts'
                    AND g.source_id = f.id
              )
            LIMIT $2
        ),
        live AS (
            INSERT INTO public.entity_graph_edges (
                source_schema, source_table, source_id,
                subject_entity_id, predicate, object_entity_id,
                sensitivity, withheld_reason, updated_at
            )
            SELECT $1, 'facts', c.id, c.entity_id, c.predicate, c.object_entity_id,
                   COALESCE(c.sensitivity, 'normal'), NULL, now()
            FROM candidates c
            WHERE NOT (COALESCE(c.sensitivity, 'normal') = ANY($3))
            ON CONFLICT (source_schema, source_table, source_id) DO NOTHING
            RETURNING 1
        ),
        withheld AS (
            INSERT INTO public.entity_graph_edges (
                source_schema, source_table, source_id,
                subject_entity_id, predicate, object_entity_id,
                sensitivity, withheld_reason, updated_at
            )
            SELECT $1, 'facts', c.id, c.entity_id, NULL, NULL,
                   COALESCE(c.sensitivity, 'normal'), 'sensitivity', now()
            FROM candidates c
            WHERE COALESCE(c.sensitivity, 'normal') = ANY($3)
            ON CONFLICT (source_schema, source_table, source_id) DO NOTHING
            RETURNING 1
        )
        SELECT (SELECT COUNT(*) FROM live) + (SELECT COUNT(*) FROM withheld)
    """
    count = await pool.fetchval(sql, source_schema, limit, list(WITHHELD_SENSITIVITIES))
    return int(count or 0)


async def backfill_commitment_edges(pool: asyncpg.Pool, *, limit: int = 500) -> int:
    """Backfill active commitment-class ``public.owner_conditions`` rows.

    Only rows with a ``counterparty_entity_id`` and a directed
    (``owner_to_other``/``other_to_owner``) direction describe an
    entity-to-entity relationship; ``self`` commitments have no counterparty
    to link and are skipped, matching the live writer in
    ``butlers.core.commitments``. A no-op (0 rows) when no owner entity
    exists yet.
    """
    sql = """
        WITH owner AS (
            SELECT id FROM public.entities WHERE 'owner' = ANY(roles) LIMIT 1
        ),
        candidates AS (
            SELECT oc.id,
                   oc.metadata ->> 'direction' AS direction,
                   (oc.metadata ->> 'counterparty_entity_id')::uuid AS counterparty_entity_id
            FROM public.owner_conditions oc
            WHERE oc.metadata ->> 'class' = 'commitment'
              AND oc.metadata ->> 'counterparty_entity_id' IS NOT NULL
              AND oc.metadata ->> 'direction' IN ('owner_to_other', 'other_to_owner')
              AND oc.state IN ('open', 'aging')
              AND NOT EXISTS (
                  SELECT 1 FROM public.entity_graph_edges g
                  WHERE g.source_schema = 'public'
                    AND g.source_table = 'owner_conditions'
                    AND g.source_id = oc.id
              )
            LIMIT $1
        ),
        inserted AS (
            INSERT INTO public.entity_graph_edges (
                source_schema, source_table, source_id,
                subject_entity_id, predicate, object_entity_id,
                sensitivity, withheld_reason, updated_at
            )
            SELECT
                'public', 'owner_conditions', c.id,
                CASE WHEN c.direction = 'other_to_owner'
                     THEN c.counterparty_entity_id ELSE owner.id END,
                'committed-to',
                CASE WHEN c.direction = 'other_to_owner'
                     THEN owner.id ELSE c.counterparty_entity_id END,
                'normal', NULL, now()
            FROM candidates c, owner
            ON CONFLICT (source_schema, source_table, source_id) DO NOTHING
            RETURNING 1
        )
        SELECT COUNT(*) FROM inserted
    """
    count = await pool.fetchval(sql, limit)
    return int(count or 0)
