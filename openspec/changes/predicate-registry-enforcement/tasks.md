## 1. Predicate Normalization

- [ ] 1.1 Add `normalize_predicate(predicate: str) -> str` helper in `tools/writing.py` — lowercase, replace hyphens/spaces with underscores, strip leading `is_` prefix
- [ ] 1.2 Apply normalizer in `tools/writing.py::memory_store_fact()` before passing predicate to `storage.store_fact()`
- [ ] 1.3 Write unit tests for normalization: lowercase, hyphens, spaces, `is_` prefix, combined, already-canonical, empty edge cases

## 2. Registry Enforcement in store_fact

- [ ] 2.1 Add registry lookup query in `storage.py::store_fact()` — `SELECT is_edge, is_temporal FROM predicate_registry WHERE name = $1` after entity validation, before idempotency check
- [ ] 2.2 Add `is_edge` enforcement: if registry says `is_edge=true` and `object_entity_id` is NULL, raise `ValueError` with predicate name and recovery message
- [ ] 2.3 Add `is_temporal` enforcement: if registry says `is_temporal=true` and `valid_at` is NULL, raise `ValueError` with predicate name and recovery message
- [ ] 2.4 Write unit tests for `is_edge` enforcement: edge predicate without `object_entity_id` rejected, with `object_entity_id` passes, non-edge predicate unaffected, unregistered predicate unaffected
- [ ] 2.5 Write unit tests for `is_temporal` enforcement: temporal predicate without `valid_at` rejected, with `valid_at` passes, non-temporal predicate unaffected, unregistered predicate unaffected

## 3. Fuzzy Matching for Novel Predicates

- [ ] 3.1 Add `_fuzzy_match_predicates(conn, predicate: str) -> list[dict]` helper in `storage.py` — fetch all registry names, compute Levenshtein distance and prefix overlap, return matches within threshold
- [ ] 3.2 Integrate fuzzy matching into `store_fact()` return path — when predicate is not in registry, attach suggestions to the return value (change return type from `uuid.UUID` to `dict` with `id` and optional `suggestions`)
- [ ] 3.3 Update `tools/writing.py::memory_store_fact()` to forward suggestions from storage result to MCP response
- [ ] 3.4 Write unit tests for fuzzy matching: typo within edit distance 2, common prefix match, no close match returns empty, suggestions are non-blocking

## 4. Auto-Registration of Novel Predicates

- [ ] 4.1 Add auto-registration logic in `storage.py::store_fact()` — after successful INSERT, if predicate was not in registry, execute `INSERT INTO predicate_registry ... ON CONFLICT DO NOTHING` with inferred flags
- [ ] 4.2 Look up entity_type from `shared.entities` for `expected_subject_type` inference (entity_id is already validated, reuse the connection)
- [ ] 4.3 Write unit tests: auto-registration after novel write, inferred `is_edge` and `is_temporal` flags, concurrent safety (ON CONFLICT DO NOTHING), registered predicates are NOT re-inserted

## 5. Structured Error Responses

- [ ] 5.1 Add `_infer_recovery_steps(exc: ValueError) -> str` helper in `__init__.py` — pattern-match error message to return specific recovery instructions
- [ ] 5.2 Wrap `_writing.memory_store_fact()` call in `__init__.py::memory_store_fact` with `try/except ValueError` — return `{"error": ..., "message": ..., "recovery": ...}` dict
- [ ] 5.3 Write unit tests: each validation failure type returns structured dict with correct recovery message, MCP response is `isError=false`

## 6. Predicate Registry Search Indexes (Migration)

- [ ] 6.1 Write migration: `CREATE EXTENSION IF NOT EXISTS pg_trgm`, add `search_vector` tsvector column with trigger, add `description_embedding` vector(384) column to `predicate_registry`
- [ ] 6.2 Write migration: create GIN index on `name` using `gin_trgm_ops`, create GIN index on `search_vector`
- [ ] 6.3 Backfill `search_vector` for existing seeded predicates; generate `description_embedding` for predicates with non-NULL descriptions
- [ ] 6.4 Enrich seed predicate descriptions with synonyms and related concepts (e.g., `parent_of` description should mention "father", "mother", "parent", "child relationship")

## 7. memory_predicate_search MCP Tool (Hybrid Retrieval)

- [ ] 7.1 Add `predicate_search(pool, query, embedding_engine, scope=None)` in `tools/reading.py` — three-signal retrieval: trigram on name, full-text on search_vector, semantic on description_embedding
- [ ] 7.2 Implement RRF fusion: `score = SUM(1 / (60 + rank_i))` across trigram, full-text, and semantic result lists
- [ ] 7.3 Add `memory_predicate_search(query, scope=None)` MCP tool in `__init__.py` — delegates to predicate_search, returns results with scores
- [ ] 7.4 Write unit tests: trigram typo recovery, full-text description match, semantic conceptual match, RRF ordering, empty query returns all, scope filter

## 8. Integration Tests and Audit

- [ ] 8.1 Audit all direct callers of `store_fact()` in `roster/*/tools/` — verify they pass correct `object_entity_id` for edge predicates and `valid_at` for temporal predicates
- [ ] 8.2 Run full memory module test suite — verify no regressions from registry enforcement (existing tests that use registered predicates must still pass)
- [ ] 8.3 Run full API test suite — verify dashboard endpoints still work
