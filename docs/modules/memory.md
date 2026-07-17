# Memory Module

> **Purpose:** Persistent memory subsystem providing episodes, facts, rules, and entity-anchored knowledge with retrieval, consolidation, and decay lifecycle.
> **Audience:** Contributors and module developers.
> **Prerequisites:** [Module System](module-system.md).

## Overview

The Memory module gives butlers the ability to remember. It is a reusable module that any butler loads locally -- each butler owns its own memory data in its own database schema, with no cross-butler SQL access.

The module is responsible for:

- **Persisting memory artifacts** (episodes, facts, rules) with provenance in the hosting butler's database.
- **Low-latency retrieval** for runtime context injection via semantic, keyword, and hybrid search.
- **Lifecycle maintenance** including consolidation (episodes into facts/rules), confidence decay, fading, expiry, and cleanup.
- **Entity resolution** -- mapping ambiguous name strings to stable identity anchors in the `public.entities` table.

Source: `src/butlers/modules/memory/__init__.py` and `src/butlers/modules/memory/tools/`.

## Configuration

Enable in `butler.toml`:

```toml
[modules.memory]
# Retrieval defaults
[modules.memory.retrieval]
context_token_budget = 3000
default_limit = 20
default_mode = "hybrid"  # "semantic", "keyword", or "hybrid"

[modules.memory.retrieval.score_weights]
relevance = 0.4
importance = 0.3
recency = 0.2
confidence = 0.1

# Optional: write summaries to public.memory_catalog
enable_shared_catalog = false
```

## Memory Artifact Types

### Episodes (Observations)

High-volume, short-lived session observations. Append-only. Each episode records what happened during a butler session, tagged with importance and a session ID. Episodes have a TTL and are eligible for consolidation into durable artifacts.

Consolidation states: `pending` -> `consolidated | failed | dead_letter`.

### Facts (Semantic Memory)

Durable subject-predicate-content knowledge. Facts are the primary retrieval unit for context injection. Key properties:

- **Entity-anchored**: Facts link to entities via `entity_id` for stable identity (resolves the "which Chloe?" problem).
- **Scoped**: Facts live in namespaces (`global`, `health`, `relationship`, `finance`, etc.).
- **Lifecycle states**: `active` -> `fading` -> `superseded | expired | retracted`.
- **Confidence decay**: Effective confidence decreases over time via `confidence * exp(-decay_rate * days_since_last_confirmed)`.
- **Permanence levels**: `permanent`, `stable`, `standard`, `volatile`, `ephemeral` -- controlling decay rate and retention.

### Rules (Procedural Memory)

Behavior guidance learned from repeated outcomes. Rules track maturity (`candidate` -> `established` -> `proven` or `anti_pattern`), effectiveness scores, and application counts. Harmful evidence is weighted more heavily than helpful evidence, so bad rules demote faster than good rules promote.

## Tools Provided

The module registers 23 MCP tools:

| Tool | Category | Description |
|------|----------|-------------|
| `memory_store_episode` | Writing | Store a raw episode from a runtime session |
| `memory_store_fact` | Writing | Store a durable fact with entity anchoring and predicate validation |
| `memory_store_rule` | Writing | Store a behavioral rule |
| `memory_search` | Reading | Search memory by query with filters |
| `memory_recall` | Reading | Recall facts/rules relevant to a prompt |
| `memory_get` | Reading | Get a specific memory artifact by ID |
| `memory_context` | Context | Assemble sectioned context within a token budget |
| `memory_confirm` | Feedback | Confirm a fact/rule (resets decay clock) |
| `memory_mark_helpful` | Feedback | Mark a rule application as helpful |
| `memory_mark_harmful` | Feedback | Mark a rule application as harmful |
| `memory_forget` | Management | Retract a memory artifact |
| `memory_stats` | Management | Get memory statistics |
| `memory_predicate_list` | Predicates | List registered predicates |
| `memory_predicate_search` | Predicates | Hybrid search for predicates (trigram + full-text + semantic) |
| `memory_entity_create` | Entities | Create a new entity identity |
| `memory_entity_get` | Entities | Retrieve an entity record |
| `memory_entity_update` | Entities | Update entity fields |
| `memory_entity_resolve` | Entities | Resolve a name string to entity candidates |
| `memory_entity_merge` | Entities | Merge two entities (re-point all facts) |
| `memory_entity_neighbors` | Entities | Get graph neighbors of an entity |
| `memory_run_consolidation` | Maintenance | Trigger episode consolidation |
| `memory_run_episode_cleanup` | Maintenance | Clean up expired episodes |
| `memory_catalog_search` | Catalog | Search the shared memory catalog |

## Retrieval

The module supports three retrieval modes fused via composite scoring:

- **Semantic**: Vector similarity search using embeddings (MiniLM).
- **Keyword**: PostgreSQL full-text search (tsvector).
- **Hybrid**: Reciprocal-rank fusion over semantic and keyword results.

Composite score formula:

```
score = 0.4 * relevance + 0.3 * importance + 0.2 * recency + 0.1 * effective_confidence
```

### Context Assembly (`memory_context`)

The `memory_context` tool assembles a deterministic, sectioned context block within a hard token budget. Four sections in fixed order (empty sections omitted):

1. **Profile Facts** (30% of budget) -- owner entity facts.
2. **Task-Relevant Facts** (35% of budget) -- composite-scored recall matches.
3. **Active Rules** (20% of budget) -- sorted by maturity rank.
4. **Recent Episodes** (15% of budget) -- opt-in via `include_recent_episodes=True`.

## Consolidation

A background worker consumes unconsolidated episodes in deterministic batches and emits new facts, rules, supersessions, confirmations, and provenance links. Every episode reaches exactly one terminal state: `consolidated`, `failed`, or `dead_letter`. Consolidation output is schema-validated before persistence.

## Decay and Hygiene

A scheduled sweep computes effective confidence for facts and rules:

- Above retrieval threshold: normal (active).
- Below retrieval threshold but above expiry threshold: `fading`.
- Below expiry threshold: `expired` or `retracted`.

For facts, `fading` is recorded on the `validity` column itself (not merely a
`metadata.status` key) — every reader of the fading count (the dashboard API,
the `memory_stats` MCP tool) queries `validity = 'fading'` directly. Fading
facts are still live: recall/search, entity-scoped reads, the catalog
backfill, and `store_fact`'s supersession lookup all continue to include
them, and the sweep re-evaluates `validity IN ('active', 'fading')` on every
run so a fading fact can recover to `active` (e.g. after `memory_confirm`) or
progress to `expired`. Rules have no `validity` column and keep using
`metadata.status = 'fading'`.

Rules' terminal soft-delete state — the equivalent of a fact's `expired` /
`retracted` validity — is the boolean `metadata->>'forgotten'` flag, set by
`memory_forget` and by the decay sweep once a rule's effective confidence
falls below its expiry threshold. Unlike fading, there is no separate
"forgotten" column to add: a rule has exactly two liveness states (live, or
forgotten), not a multi-value lifecycle, so a boolean JSONB flag is the
right-sized representation (bu-5ud8p.2). Every reader that reports rule
counts or lists rules — the dashboard API (`GET /api/memory/stats`'s
`candidate_rules`/`established_rules`/`proven_rules`/`anti_pattern_rules`,
including the "Proven rules" KPI; `GET /api/memory/rules`; the
`GET /api/memory/inspect?kind=rule` search bar), the `memory_stats` MCP tool,
and the recall/search/consolidation/catalog-backfill internals — filters on
`(metadata->>'forgotten')::boolean IS NOT TRUE` so a forgotten rule never
counts as a live belief or shows up in a browse/search list. `GET
/api/memory/rules` accepts an explicit `?forgotten=true` override for
auditing forgotten rules; `GET /api/memory/rules/{id}` (fetch by ID) and
`GET /api/memory/activity` (the chronological creation feed) are intentionally
unfiltered, mirroring how a fact stays fetchable by ID regardless of
validity.

Anti-pattern detection: rules with repeated harmful, low-effectiveness outcomes transition to `anti_pattern` status and are surfaced as warnings rather than guidance.

Episode cleanup removes expired rows and enforces capacity limits starting with the oldest consolidated rows.

## Shared Discovery Catalog

When `enable_shared_catalog = true` (the module default), `store_fact`/`store_rule` write a searchable summary row to `public.memory_catalog` — a cross-butler discovery index (see `openspec/specs/memory-discovery-catalog/spec.md`). The catalog is a discovery index, not a canonical store: full retrieval always routes back to the owning butler's schema via the row's `(source_schema, source_table, source_id)` provenance pointer.

**Atomic disownment.** Every path that disowns a canonical memory — `memory_forget` (plain and correction-driven), the decay sweep's terminal expiry transition (facts -> `expired`, rules -> `metadata.forgotten`), and `purge_superseded_facts` — cascades a matching catalog-row disownment (`confidence = 0`, `invalid_at` set) in the **same transaction** as the state change, so a crash between the two can never leave the catalog serving a memory the canonical store has already retracted. `store_fact`'s own supersession cascade remains a separate, best-effort, eventually-consistent write-behind (outside the write transaction) — that path predates and is unrelated to the disownment guarantee above. Fading transitions never cascade: fading facts stay live for retrieval per the memory-retention-policy spec.

The owning `source_schema` for a disownment cascade is resolved from the connection's own `current_schema()` rather than threaded through every caller — butler pools connect with `search_path = <schema>, public`, so this reliably matches whatever schema the row was originally cataloged under.

**Purge semantics.** A purged fact's catalog row is marked stale, not deleted — butler roles hold no `DELETE` grant on `public.memory_catalog` (catalog GC is centralised), so a purge-triggered delete isn't even possible from a butler's own role.

**Consolidation.** `execute_consolidation`/`run_consolidation` accept `enable_shared_catalog`/`source_schema` and forward them to every `store_fact`/`store_rule` call, so consolidation-derived facts/rules are cataloged exactly like directly-stored ones.

### IVFFlat filtered-recall measurement

The live semantic retrieval contract is [`_catalog_semantic_search`](../../src/butlers/modules/memory/search.py): it orders `public.memory_catalog` by `embedding <=> query_embedding` after applying `tenant_id`, `invalid_at IS NULL`, optional `memory_type`, and the caller's sensitivity ceiling. In this catalog, the user-facing notion of a category is the persisted `memory_type` filter (`fact` or `rule`); there is no separate category column.

An operator can measure possible IVFFlat candidate shortfall without changing the live retrieval path, index, or database settings:

```bash
uv run python -m butlers.modules.memory.catalog_measurement \
  --vectors-json ./catalog-measurement-vectors.json \
  --memory-type fact \
  --limit 10
```

Run it only from an operator-controlled environment whose standard `POSTGRES_*`/`DATABASE_URL` configuration already targets the intended database. The JSON file is a top-level list of precomputed, 384-dimensional numeric vectors with finite components representable as pgvector float32 values; it must not contain natural-language queries and is never echoed in the command output. `--max-sensitivity` is a visibility ceiling (default `normal`), resolved inclusively with the same helper as live retrieval. The command returns aggregate-only JSON: filter population, result counts, overlap/recall, latency, whether the planner selected `idx_memory_catalog_embedding`, and safe lifecycle/index/table statistics. Equal-distance results at the limit are treated as rank-equivalent, so arbitrary tie ordering cannot look like recall loss. It never returns catalog text, provenance, row IDs, or vectors.

[`measure_catalog_ivfflat`](../../src/butlers/modules/memory/catalog_measurement.py) uses one repeatable-read PostgreSQL read-only transaction **per vector**, plain `EXPLAIN (FORMAT JSON)`, and the same filters as the live query. Each snapshot contains that vector's candidate count, approximate query, plan observation, and (when under the cap) exact comparison, keeping the comparison coherent while the live catalog changes without retaining one snapshot for the whole vector batch. The exact reference is skipped before it runs when the filtered population exceeds the hard 50,000-row cap. A transaction therefore contains at most four read statements; a run is further bounded to 25 vectors, `limit <= 50`, and a 10-second client-side timeout per database operation. It does **not** issue DDL/DML, `SET`, `ANALYZE`, `VACUUM`, `REINDEX`, or any pgvector/index tuning command. The maintenance observations are read-only snapshots, not maintenance work.

The command can inform a later proposal but cannot authorize tuning. A proposal requires at least 20 observations for which both the exact comparator completed and the named IVFFlat index was planned, plus either mean recall@limit below 0.98 or a candidate-shortfall rate of at least 10% with p95 shortfall of at least one result. Re-run in a separate window and review the aggregate evidence before considering any change. This is catalog IVFFlat evidence only; it is deliberately separate from HNSW production-table work (`bu-715xd`).

## Entity Resolution

The `memory_entity_resolve` tool maps ambiguous name strings to stable entity identities using a 4-tier waterfall: role match -> exact (canonical or alias) -> prefix/substring -> fuzzy (edit distance <= 2). Context boosting from graph neighborhood and caller-provided `context_hints` refines scoring.

Entities are never hard-deleted. Merging sets `metadata.merged_into`; the source entity is tombstoned and excluded from future resolution.

## Database Tables

The module owns tables in the hosting butler's schema (Alembic branch: `memory`):

- `episodes` -- session observations with TTL
- `facts` -- durable SPO knowledge with entity anchoring
- `rules` -- procedural memory with maturity tracking
- `memory_links` -- provenance edges between memory artifacts
- `memory_events` -- append-only audit stream
- `rule_applications` -- per-application outcome records
- `embedding_versions` -- model/version tracking
- `predicate_registry` -- predicate vocabulary with enforcement flags

Entity identity tables live in the `public` schema: `public.entities`.

## Dependencies

None. The memory module is a leaf module with no dependencies on other modules.

## Related Pages

- [Module System](module-system.md)
- [Knowledge Base](knowledge-base.md) -- entity data model and predicate vocabulary
- [Approvals Module](approvals.md) -- approval gates for memory mutations
