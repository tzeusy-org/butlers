## Why

The `predicate_registry` table tracks canonical predicates with `is_edge`, `is_temporal`, and type metadata — but `store_fact()` never reads it. The registry is purely advisory, seeded for LLM prompt injection via `memory_predicate_list()`. This means:

- An LLM can use `parent_of` (an `is_edge=true` predicate) without supplying `object_entity_id`, silently creating a malformed property fact instead of a proper edge-fact.
- An LLM can omit `valid_at` on a temporal predicate like `interaction`, causing supersession to silently destroy previous interaction records.
- An LLM can invent `family_father_of`, `father_of`, `dad`, `is_parent_of` — all synonymous with the canonical `parent_of` — with no feedback, causing predicate proliferation that fragments queries.
- Error handling is inconsistent: missing `entity_id` returns a structured `{"error": ..., "message": ...}` dict with recovery steps, while all other validation failures raise raw `ValueError` exceptions that surface as MCP `isError=true` frames with unparseable text.

These are not theoretical concerns — live data already contains orphaned relationship facts with entity UUIDs embedded in `content` instead of `object_entity_id`, and plain-text relationship facts (`"Phillip is Chloes dad"`) that should be proper edge-facts.

## What Changes

- **Registry enforcement at write time**: `store_fact()` consults `predicate_registry` when the predicate exists there. If `is_edge=true`, `object_entity_id` is required. If `is_temporal=true`, `valid_at` is required.
- **Predicate normalization**: Incoming predicates are normalized (lowercase, hyphens/spaces → underscores, strip leading `is_`) before storage and before registry lookup.
- **Fuzzy matching for novel predicates**: When a predicate is NOT in the registry, perform a prefix/Levenshtein check against registered predicates. If close matches exist, return them as suggestions in the response alongside the stored fact ID. Do not block the write.
- **Auto-registration of novel predicates**: After a successful write with a non-registered predicate, auto-insert it into `predicate_registry` with `is_edge` and `is_temporal` inferred from the call parameters (`object_entity_id` present → `is_edge=true`, `valid_at` present → `is_temporal=true`).
- **Consistent structured error responses**: All validation failures from the MCP `memory_store_fact` tool return structured `{"error": ..., "message": ..., "recovery": ...}` dicts instead of raw exceptions. The `recovery` field provides specific next steps the LLM should take.
- **Predicate search tool**: New `memory_predicate_search(query)` MCP tool using three-signal hybrid retrieval (trigram fuzzy, full-text tsvector, semantic embedding) fused via Reciprocal Rank Fusion (RRF). Registry gains `search_vector`, `description_embedding`, and GIN indexes.
- **Predicate aliases**: `aliases TEXT[]` column for deterministic synonym resolution at write time. "father_of" resolves to `parent_of` without fuzzy matching.
- **Inverse and symmetric predicates**: `inverse_of` and `is_symmetric` columns for bidirectional entity graph traversal. `parent_of(Alice, Bob)` is discoverable as `child_of(Bob, Alice)` at query time without duplicate storage.
- **Predicate lifecycle**: `status` (active/deprecated/proposed), `superseded_by`, and `deprecated_at` columns. Deprecated predicates still accept writes but return warnings with replacement suggestions.
- **Predicate scoping**: `scope` column matching fact-level scopes (global, health, relationship, finance, home). Serves as namespace, UI grouping, and search filter.
- **Usage tracking**: `usage_count` and `last_used_at` columns, incremented on every `store_fact()`. Enables search ranking by popularity and cleanup of unused auto-registered predicates.

## Capabilities

### New Capabilities
- `predicate-enforcement`: Write-time validation of `is_edge` and `is_temporal` constraints from `predicate_registry`, predicate normalization, fuzzy matching with suggestions for novel predicates, and auto-registration of new predicates after successful writes.

### Modified Capabilities
- `module-memory`: `store_fact` gains registry-aware validation, consistent structured error responses for all failure modes, and predicate normalization. The `memory_store_fact` MCP tool wraps all `ValueError` exceptions into structured dicts. New `memory_predicate_search` tool added.
- `predicate-taxonomy`: Registry changes from advisory documentation to an enforced contract. `is_edge` and `is_temporal` columns become write-time constraints. Novel predicates are auto-inserted after first successful use.

## Impact

- **Storage layer** (`src/butlers/modules/memory/storage.py`): `store_fact()` gains a registry lookup query within the existing transaction, predicate normalization logic, and fuzzy-match helper.
- **MCP tool layer** (`src/butlers/modules/memory/__init__.py`): `memory_store_fact` wraps all storage calls in try/except, returns structured error dicts. New `memory_predicate_search` tool registered.
- **Writing tools** (`src/butlers/modules/memory/tools/writing.py`): Normalizer applied before delegation to storage.
- **Predicate registry migrations**: New migration adding a `usage_count` column (optional, for search ranking) and seeding any missing predicates.
- **All butler tools calling `store_fact` directly** (`roster/relationship/tools/`, `roster/health/tools/`, `roster/finance/tools/`, `roster/home/tools/`): These already pass correct parameters by construction but benefit from the safety net. No code changes expected for these callers.
- **Tests**: New tests for registry enforcement, normalization, fuzzy matching, auto-registration, and structured error responses. Existing tests must still pass (normalization must be backward-compatible with existing predicate strings).
- **Dashboard API**: No changes — the API reads facts and doesn't call `store_fact`.
