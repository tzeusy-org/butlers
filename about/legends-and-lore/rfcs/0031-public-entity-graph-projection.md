# RFC 0031: Public Entity Graph Projection

**Status:** Draft (Slice 1 of 7 landed: substrate only)
**Date:** 2026-09-05

## Summary

This RFC defines `public.entity_graph_edges`, a write-behind projection of
entity-to-entity relationships sourced from each butler's canonical fact
stores (`relationship.entity_facts`, memory facts/rules, commitments). The
fleet already writes an entity anchor (`entity_id` / `object_entity_id`) on
every catalog row, but nothing reads it as a graph: answering "what do we
know about this person" today costs an LLM session per butler. This RFC
gives the fleet a single, zero-LLM-traversable table instead.

This document also records the RFC's full slice plan (`Slice Plan` below).
**Only Slice 1 — the table substrate and this RFC — has landed.** Slices 2-7
(writers, backfill, traversal tools, catalog read, dossier API, dashboard
surface, and debt riders) are future work; this RFC describes their intended
shape so later slices implement against one design rather than re-deriving
it, but nothing past the substrate is live yet.

## Motivation

`public.memory_catalog` already carries `entity_id` and `object_entity_id`
columns (`core_009_memory_catalog.py`, added by `core_024`) and indexes both
(`idx_memory_catalog_entity_id`, `idx_memory_catalog_object_entity_id`). In
principle every fact and rule the fleet writes is already entity-anchored on
both ends of a relationship. In practice nothing queries those columns as a
graph — `src/butlers/modules/memory/search.py`'s catalog search functions
(`_catalog_semantic_search`, `_catalog_keyword_search`, lines ~830-908) do
similarity and full-text search, never traversal. `relationship.entity_facts`
is granted to the `relationship` role alone, so no other butler can even read
it directly.

The result: "what do we know about this person" has no deterministic answer
anywhere in the fleet. The only way to assemble one today is to fan an LLM
session out across every butler and ask each to summarize what it knows about
an entity — the same class of cost RFC 0010 and RFC 0030 already rejected for
simpler aggregation questions. A recursive graph walk over a purpose-built
projection table answers it in one SQL query, with zero LLM sessions.

## Design

### Governing Intent

[`docs/concepts/identity-model.md`](../../../docs/concepts/identity-model.md)
remains the source of truth for entity identity and channel resolution. This
RFC does not change that model; it adds a read surface *over* it. The
`public.entities` row and its `relationship.entity_facts` channel triples are
unaffected — this RFC's table records that a relationship *exists* between
two entities, not who those entities are or how to reach them.

### Table: `public.entity_graph_edges`

```sql
CREATE TABLE public.entity_graph_edges (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Provenance: the canonical source row this edge projects.
    source_schema       TEXT NOT NULL,
    source_table        TEXT NOT NULL,
    source_id           UUID NOT NULL,

    -- Graph anchor. The subject is always a real entity; the object is a
    -- real entity only for a live (non-withheld) edge.
    subject_entity_id   UUID NOT NULL REFERENCES public.entities(id) ON DELETE CASCADE,
    predicate           TEXT,
    object_entity_id    UUID REFERENCES public.entities(id) ON DELETE CASCADE,

    -- Sensitivity tier of the originating fact (reuses the memory-catalog
    -- vocabulary). Recorded on every row, live or withheld.
    sensitivity         TEXT NOT NULL DEFAULT 'normal'
        CHECK (sensitivity IN ('normal', 'pii', 'confidential')),

    -- NULL for a live edge. 'sensitivity' marks a count-only stub.
    withheld_reason     TEXT CHECK (withheld_reason IN ('sensitivity')),

    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_entity_graph_edges_source
        UNIQUE (source_schema, source_table, source_id),

    CONSTRAINT chk_entity_graph_edges_payload_xor_withheld CHECK (
        (withheld_reason IS NULL AND predicate IS NOT NULL AND object_entity_id IS NOT NULL)
        OR
        (withheld_reason IS NOT NULL AND predicate IS NULL AND object_entity_id IS NULL)
    )
);
```

Landed in `alembic/versions/core/core_215_entity_graph_edges.py`.

### Why A Projection, Not A Live View

`relationship.entity_facts` alone cannot serve as the graph: it is
schema-isolated to the `relationship` role (per-schema PostgreSQL role
isolation, RFC 0006), and it is only one of three sources — memory facts/rules
and commitments also produce entity-to-entity relationships that never touch
`relationship.entity_facts` at all. A `public`-schema table populated by
write-behind projection, one per source table, is the only shape that unifies
all three without a cross-schema `SELECT` grant on each butler's private
tables (which the fleet's schema-isolation model, RFC 0006, forecloses) or a
Switchboard MCP fan-out per traversal step (which reintroduces the
per-question LLM cost this RFC exists to remove).

### Write-Behind Contract

**The projection write MUST happen in the same transaction as the source
fact write.** A writer that inserts, retracts, or deletes a source row (a
`relationship.entity_facts` triple, a memory fact/rule, a commitment) issues
the matching `INSERT ... ON CONFLICT (source_schema, source_table, source_id)
DO UPDATE` (or `DELETE`) against `public.entity_graph_edges` inside that same
database transaction. If the projection write fails, the transaction rolls
back and the source write never commits. This is the one non-negotiable
invariant: the graph must never be able to silently diverge from the fact
store it projects, because a caller that trusts a stale or missing edge has
no way to detect the gap without re-deriving from source — which is the exact
cost this table exists to avoid paying per query.

`(source_schema, source_table, source_id)` is a stable natural key ("this
specific row in this specific table"), which is also what makes backfill
idempotent: a backfill job can re-run `INSERT ... ON CONFLICT ... DO UPDATE`
over every existing source row with no risk of duplicating an edge that a
live writer already projected.

### Withheld Stub Edges

Some source facts are excluded from the graph projection outright because
their sensitivity exceeds what the projection is willing to persist as a
traversable, content-bearing edge (independent of whatever read-time
sensitivity ceiling a caller might additionally be subject to). Dropping
those facts silently would make graph coverage dishonest: a caller counting
"how many relationships does this entity have" would get a number smaller
than the true source count, with no signal that anything was omitted.

Instead, the writer inserts a **withheld stub**: `subject_entity_id` and
`sensitivity` are recorded, but `predicate` and `object_entity_id` are left
NULL and `withheld_reason = 'sensitivity'`. The
`chk_entity_graph_edges_payload_xor_withheld` constraint makes "a withheld
row carries no payload" a schema-enforced fact rather than a per-writer
convention — no future writer bug can accidentally leak content through a
withheld row, because the columns that would carry it are structurally
unpopulatable whenever `withheld_reason` is set. A coverage statement (see
Slice Plan, S5) can then report "N relationships known, M withheld for
sensitivity" honestly, without ever exposing what the M withheld relationships
are.

### Traversal Shape (Future: Slice 3)

Both intended core tools (`entity_graph_walk`, `entity_graph_path`) are plain
recursive CTEs over `subject_entity_id` / `object_entity_id`, filtered to
`withheld_reason IS NULL` (a withheld stub has no `object_entity_id` to
traverse through) and depth-capped to bound worst-case fan-out. Neither tool
involves an LLM call; the "zero-LLM" property is what makes per-question
traversal cheap enough to expose directly instead of needing a batch
precompute (contrast RFC 0010/0030, which exist because their underlying
aggregation was expensive enough to need one).

### Non-Goals

No belief revision, contradiction docket, or ontology unification. This
table projects "a relationship was asserted between these two entities by
this source row" — it does not resolve conflicting predicates, merge
duplicate relationship types across sources, or attempt any RDF-style
ontology normalization. Those are separate, larger problems this RFC
deliberately does not take on.

No writes from readers. Every write to `public.entity_graph_edges` is a
write-behind projection triggered by a write to its source table. No tool
this RFC defines (or any future traversal/dossier tool) writes to this table
directly.

## Slice Plan

- **S1 (this RFC + `core_215_entity_graph_edges.py`, landed):** substrate
  table, indexes, grants.
- **S2 (future):** write-behind writers in memory storage, `relationship`
  `assert_fact`, and commitments, plus the idempotent backfill job over
  existing source rows.
- **S3 (future):** zero-LLM `entity_graph_walk` / `entity_graph_path` core
  tools (recursive CTE), added to a new `graph` core tool group.
- **S4 (future):** entity catalog read integration — surfacing graph
  coverage alongside existing catalog search results.
- **S5 (future):** `/api/entities/{id}/dossier` — per-source receipts plus a
  coverage statement (`N relationships known, M withheld for sensitivity`,
  counts drawn from this table, never fabricated).
- **S6 (future):** dashboard surface — an `EntityDetailPage` dossier panel
  consuming S5's API.
- **S7 (future, debt riders):** fixes the `init-db.sql:376-379` DELETE grant
  contradiction and the degraded-envelope gap for entity activity, both
  discovered during this epic's evidence-gathering but out of scope for the
  graph substrate itself.

## Grant Model

All butler roles with a memory/relationship/commitments write surface
receive `SELECT, INSERT, UPDATE, DELETE` on `public.entity_graph_edges`
(`core_215_entity_graph_edges.py`; the role list mirrors
`core_210_expected_signals.py`'s `_ALL_BUTLER_ROLES`). This differs
deliberately from `public.memory_catalog`'s grant model, which withholds
`DELETE` because catalog garbage collection is centralized. Here, each edge
is 1:1 owned by the writer that projected it from its own source row: the
same transaction that retracts or deletes a source fact must be able to
retract or delete the edge it produced, or the write-behind invariant above
is unenforceable. There is no centralized GC step for this table.

## Integration

- **RFC 0004 / `docs/concepts/identity-model.md`:** This table adds a read
  surface over the existing entity anchor; it does not change entity
  resolution, channel handling, or the owner-entity carve-out.
- **RFC 0006:** `public.entity_graph_edges` follows the same
  `public`-schema, per-role-grant pattern as `public.entities` and
  `public.memory_catalog` — a shared table with narrow, explicit grants
  rather than a cross-schema `SELECT` exception.
- **`public.memory_catalog` (`core_009`/`core_024`):** The `entity_id` /
  `object_entity_id` columns that motivated this RFC remain in place and
  unchanged; this table does not replace or migrate them; a future slice
  (S4) integrates catalog search results with graph coverage, not the
  other way around.
- **RFC 0010 / RFC 0030:** Same underlying principle — deterministic,
  zero-LLM data access beats per-question LLM fan-out — applied to
  entity-graph traversal instead of aggregate telemetry. Unlike RFC 0010/0030,
  no cross-schema read exception is needed here: the projection table itself
  lives in `public`, so ordinary per-role grants suffice.

## Alternatives Considered

**Cross-schema `SELECT` grant on `relationship.entity_facts` for every
butler.** Rejected: only covers one of the three source stores (misses
memory facts/rules and commitments entirely), and widens
`relationship`-schema read access fleet-wide for a table that also carries
other relationship data not meant for general consumption.

**Switchboard MCP fan-out per traversal step.** Rejected for the same reason
RFC 0010 rejected it for briefing aggregation: an N-hop walk would cost up to
N LLM sessions instead of one deterministic recursive query.

**Silently dropping sensitivity-excluded facts instead of writing a withheld
stub.** Rejected: makes coverage counts dishonest — a caller has no way to
distinguish "this entity truly has no more relationships" from "some were
hidden" without an explicit accounting row.

**A live view over the three source tables instead of a projection.**
Rejected: the sources live in different schemas under different role
grants, so a `public`-schema view would either need `SECURITY DEFINER`
(the same trust-widening problem RFC 0010's guardrails exist to avoid) or
would fail on unauthorized-role reads of the underlying tables. A
write-behind projection sidesteps the cross-schema read problem entirely by
having each source's own role write into a table it already has a grant on.
