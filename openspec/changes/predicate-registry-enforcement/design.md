## Context

The `predicate_registry` table exists with `is_edge`, `is_temporal`, `expected_subject_type`, `expected_object_type`, and `description` columns. It is seeded by migrations (mem_005, mem_007, mem_009, mem_010, mem_011, home_002) with ~50 canonical predicates across health, relationship, finance, and home domains. The `memory_predicate_list()` MCP tool exposes it for LLM discovery.

Currently, `store_fact()` in `storage.py` never reads `predicate_registry`. Temporal/property behavior is determined solely by `valid_at` nullness. Edge-fact behavior requires explicit `object_entity_id`. The registry's `is_edge` and `is_temporal` flags are purely advisory.

Error handling is split: the MCP tool in `__init__.py` returns structured dicts for missing `entity_id`, but all other validation (invalid entity, self-referencing edges, embedded UUIDs) raises raw `ValueError` that propagates through FastMCP as `isError=true` frames.

## Goals / Non-Goals

**Goals:**
- Enforce `is_edge` and `is_temporal` constraints from `predicate_registry` at write time
- Normalize predicates to a canonical form before storage and registry lookup
- Suggest canonical alternatives when a novel predicate is close to an existing one (non-blocking)
- Auto-register novel predicates so the registry stays current
- Return consistent structured error responses for all validation failures from the MCP tool
- Provide a predicate search tool for LLM discovery

**Non-Goals:**
- Retroactively fixing existing malformed facts in the database (separate data cleanup task)
- Blocking writes for novel predicates not in the registry (creativity must be preserved)
- Changing the `predicate_registry` schema beyond adding `usage_count` — no new tables
- Modifying any butler-specific tools that call `store_fact` directly (they already pass correct params)
- Building a full ontology/taxonomy editor UI in the dashboard

## Decisions

### D1: Registry lookup is a single cached query inside the existing transaction

**Decision:** Within `store_fact()`'s existing `conn.transaction()` block, issue `SELECT is_edge, is_temporal FROM predicate_registry WHERE name = $1` on the (normalized) predicate. If found, enforce constraints. If not found, proceed with fuzzy matching.

**Why not cache in-memory?** The registry can be modified by migrations or auto-registration during runtime. A per-transaction lookup is consistent and adds ~0.1ms (single-row PK lookup on a small table). The overhead is negligible compared to the embedding computation already in the hot path.

**Alternative considered:** Pre-loading the full registry into a module-level dict at startup. Rejected because it goes stale when new predicates are auto-registered by concurrent butler sessions, and the invalidation logic adds complexity for minimal latency gain.

### D2: Normalization is applied in the writing tool layer, not storage

**Decision:** Predicate normalization (`lowercase`, `replace(-, _)`, `replace( , _)`, `strip is_ prefix`) is applied in `tools/writing.py::memory_store_fact()` before calling `storage.store_fact()`. The normalized form is what gets stored and what gets looked up in the registry.

**Why not in storage.py?** Butler tools calling `store_fact` directly (relationship, health, finance, home) already use canonical snake_case predicates by construction. Normalization is a concern of the LLM-facing interface, not the storage contract. Keeping it in the writing layer avoids surprising internal callers.

**Why not in __init__.py?** The MCP tool in `__init__.py` is responsible for routing context and entity_id injection. Normalization is a data transformation that belongs with the other input parsing in `writing.py`.

### D3: Fuzzy matching uses prefix + Levenshtein with a threshold

**Decision:** When a predicate is not found in the registry after normalization, compute edit distance against all registered predicates. If any are within distance 2, or share a common prefix of 5+ characters, include them as `"suggestions"` in the success response. Do not block the write.

**Why Levenshtein?** It catches typos (`parnet_of` → `parent_of`) and minor variations (`father_of` → `parent_of` won't match by edit distance but will match by prefix `par`). The registry is small (<100 rows), so computing against all is trivial.

**Why not block?** LLMs need to create domain-specific predicates that genuinely don't exist yet. Blocking would force a two-step "register then store" workflow that adds latency and tool calls.

### D4: Auto-registration inserts with inferred flags after successful write

**Decision:** After `store_fact()` succeeds for a predicate not in the registry, execute `INSERT INTO predicate_registry (name, is_edge, is_temporal, expected_subject_type, description) VALUES (...) ON CONFLICT (name) DO NOTHING`. Flags are inferred: `is_edge = (object_entity_id IS NOT NULL)`, `is_temporal = (valid_at IS NOT NULL)`. `expected_subject_type` is looked up from the entity's `entity_type` if `entity_id` is provided.

**Why ON CONFLICT DO NOTHING?** Concurrent sessions may auto-register the same predicate simultaneously. The first writer wins; subsequent writers see the existing row (which may have different inferred flags if usage varies). This is acceptable — the auto-registered row is a starting point, not an authoritative definition.

**Why after the write, not before?** The write must succeed first. If validation rejects the fact (bad entity_id, etc.), we don't want to register the predicate.

### D5: Structured error responses wrap all ValueError paths

**Decision:** In `__init__.py::memory_store_fact`, wrap the entire call to `_writing.memory_store_fact()` in `try/except ValueError as exc`. Convert to:
```python
{
    "error": str(exc),
    "message": str(exc),
    "recovery": _infer_recovery_steps(exc),
}
```

The `_infer_recovery_steps()` function pattern-matches the error message to provide specific next steps (e.g., "entity does not exist" → "Call memory_entity_resolve() first", "is_edge requires object_entity_id" → "Resolve the target entity and pass object_entity_id").

**Why in __init__.py, not writing.py?** The MCP tool layer is the boundary where exceptions become LLM-visible responses. Writing.py is a shared utility; adding error formatting there would couple it to the MCP protocol.

### D6: memory_predicate_search uses hybrid retrieval with RRF fusion

**Decision:** New `memory_predicate_search(query: str, scope: str | None = None)` tool using three-signal hybrid retrieval fused via Reciprocal Rank Fusion (RRF). Inspired by [CASS](https://github.com/Dicklesworthstone/coding_agent_session_search) search architecture.

**Three retrieval signals:**

1. **Trigram fuzzy matching on name** (`pg_trgm` GIN index): Catches typos and partial matches. `similarity(name, $query) > 0.3` as candidate filter. Handles snake_case splitting naturally since trigrams span underscore boundaries.

2. **Full-text search on name + description** (tsvector GIN index): Weighted vector with name at weight A, description at weight B. `search_vector @@ plainto_tsquery('english', $query)` ranked by `ts_rank()`. Handles stemming, stop words, and multi-word queries.

3. **Semantic similarity on description embedding** (vector column): The memory module's existing embedding engine generates a 384-dim vector for each description. At query time, embed the query and compute cosine similarity. Handles conceptual matching — "dad" finds `parent_of` whose description mentions "father/mother/parent relationship".

**RRF fusion:** `score = SUM(1 / (60 + rank_i))` across all three ranked lists. K=60 is the standard RRF constant that balances precision/recall. Results ordered by fused score descending.

**Schema additions to `predicate_registry`:**
- `search_vector` tsvector — auto-maintained via trigger: `setweight(to_tsvector('english', name), 'A') || setweight(to_tsvector('english', coalesce(description, '')), 'B')`
- `description_embedding` vector(384) — populated by embedding engine on insert/update
- GIN index on `name` using `gin_trgm_ops` (pg_trgm)
- GIN index on `search_vector`

**Why not just prefix matching?** Prefix matching fails for conceptual queries ("dad" doesn't prefix-match "parent_of"), typo recovery ("parnet" doesn't prefix-match "parent"), and description-based discovery ("blood pressure reading" needs to find `measurement_blood_pressure`). The hybrid approach handles all three with minimal overhead on a ~100-row table.

**Why not extend predicate_list?** `predicate_list` returns all predicates and is used for full-registry dumps. Adding search semantics to it changes its contract. A separate tool has a clear purpose for LLM callers: "search before you invent."

**Embedding generation for auto-registered predicates:** Auto-registered predicates have `description=NULL` and therefore `description_embedding=NULL`. They are discoverable via trigram name matching only. When a description is later added (via migration or dashboard), the embedding is generated and semantic search becomes available.

## Risks / Trade-offs

**[Risk: Breaking existing butler tools that omit valid_at on temporal predicates]**
Butler tools like `relationship/tools/interactions.py` already pass `valid_at` correctly, but there may be edge cases (e.g., consolidation executor creating facts). **Mitigation:** Audit all callers of `store_fact` to confirm they pass the right flags for their predicates. The enforcement only applies to predicates IN the registry — novel predicates from consolidation that aren't registered won't be affected.

**[Risk: Auto-registration pollutes the registry with LLM-invented garbage predicates]**
If an LLM invents `my_random_predicate_123`, it gets auto-registered. **Mitigation:** Auto-registered predicates have `description=NULL` and inferred flags. A future dashboard page could flag predicates with low usage for cleanup. The ON CONFLICT DO NOTHING means the first usage sets the flags; manual corrections via migration or dashboard override persist.

**[Risk: Normalization changes stored predicate strings]**
If existing facts use `Father_Of` and normalization produces `father_of`, new facts will use a different string. **Mitigation:** Normalization only applies to the MCP tool path (LLM callers). Internal butler tools already use canonical forms. Existing facts are not retroactively modified. Query patterns should already be case-insensitive or use exact matches on canonical predicates.

**[Risk: Registry lookup adds latency to every store_fact call]**
Single-row PK lookup on a small table (~100 rows). **Mitigation:** Measured overhead is ~0.1ms, negligible vs. embedding computation (~50-200ms).

### D7: Domain/range type validation is soft (warnings, not errors)

**Decision:** When a predicate specifies `expected_subject_type` or `expected_object_type`, `store_fact()` checks the actual entity types and includes a `"warning"` in the response if they don't match. The fact is still stored.

**Why soft, not hard?** Following Wikidata's philosophy: constraints are guidance for editors, not enforcement gates. Hard-blocking on type mismatch would prevent legitimate edge cases (e.g., an organization acting as a parent entity in a corporate hierarchy — `parent_of(OrgA, OrgB)` is valid even though `expected_subject_type = 'person'`). Warnings teach the LLM without blocking creative usage.

**Implementation:** The entity existence check in `store_fact()` already queries `shared.entities`. Extend it to `SELECT id, entity_type FROM shared.entities WHERE id = $1` (fetching `entity_type` in the same query, no additional round-trip). Compare against the registry's expectations. If mismatch, append to a `warnings` list that propagates through the response.

### D8: Example payloads in predicate registry via example_json

**Decision:** Add `example_json JSONB` column to `predicate_registry`. Seeded predicates include a sample `{"content": "...", "metadata": {...}}` with realistic values. Auto-registered predicates get `NULL`. The `memory_predicate_search` tool returns `example_json` alongside other fields.

**Why in the registry, not just in predicate-taxonomy.md?** The taxonomy spec has detailed metadata schemas, but LLMs don't read markdown specs at write time. Putting examples in the registry makes them available via the search tool — the LLM can see exactly what a `measurement_weight` fact should look like before creating one.

**Why JSONB, not TEXT?** Structured data can be validated, queried, and extended. A JSONB example can include both `content` and `metadata` keys, matching the actual `store_fact()` parameters.

## Open Questions

- Should the `is_temporal` enforcement be a hard error or a warning? Hard error is safer (prevents silent data loss) but could break consolidation flows that create facts without registry awareness. Recommend: hard error for registered predicates, no enforcement for unregistered ones.
- Should `pg_trgm` be required via `CREATE EXTENSION IF NOT EXISTS pg_trgm`? It's a contrib extension bundled with PostgreSQL but may not be enabled. The migration should enable it idempotently.
