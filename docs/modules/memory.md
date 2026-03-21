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
- **Entity resolution** -- mapping ambiguous name strings to stable identity anchors in the `shared.entities` table.

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

# Optional: write summaries to shared.memory_catalog
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

Anti-pattern detection: rules with repeated harmful, low-effectiveness outcomes transition to `anti_pattern` status and are surfaced as warnings rather than guidance.

Episode cleanup removes expired rows and enforces capacity limits starting with the oldest consolidated rows.

## Entity Resolution

The `memory_entity_resolve` tool maps ambiguous name strings to stable entity identities using a 5-tier waterfall: exact canonical name -> exact alias -> prefix/substring -> fuzzy (edit distance <= 2). Context boosting from graph neighborhood and caller-provided `context_hints` refines scoring.

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

Entity identity tables live in the `shared` schema: `shared.entities`.

## Dependencies

None. The memory module is a leaf module with no dependencies on other modules.

## Related Pages

- [Module System](module-system.md)
- [Knowledge Base](knowledge-base.md) -- entity data model and predicate vocabulary
- [Approvals Module](approvals.md) -- approval gates for memory mutations
