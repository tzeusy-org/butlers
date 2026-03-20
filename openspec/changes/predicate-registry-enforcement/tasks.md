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

## 6. memory_predicate_search MCP Tool (Basic — bu-awe0, PR #692)

- [x] 6.1 Add `predicate_search(pool, query, scope=None)` in `tools/reading.py` — prefix + substring matching
- [x] 6.2 Add `memory_predicate_search(query, scope=None)` MCP tool in `__init__.py`
- [x] 6.3 Write unit tests: prefix search, description text search, empty query returns all, scope filter

## 7. Upgrade to Hybrid Retrieval with RRF Fusion (bu-2kk9)

- [ ] 7.1 Write migration: `CREATE EXTENSION IF NOT EXISTS pg_trgm`, add `search_vector` tsvector column with auto-update trigger, add `description_embedding` vector(384) column to `predicate_registry`
- [ ] 7.2 Write migration: create GIN index on `name` using `gin_trgm_ops`, create GIN index on `search_vector`
- [ ] 7.3 Backfill `search_vector` for existing seeded predicates; generate `description_embedding` for predicates with non-NULL descriptions
- [ ] 7.4 Enrich seed predicate descriptions with synonyms and related concepts (e.g., `parent_of` description should mention "father", "mother", "parent", "child relationship")
- [ ] 7.5 Replace `predicate_search()` with three-signal retrieval: trigram on name (pg_trgm), full-text on search_vector (tsvector), semantic on description_embedding (cosine)
- [ ] 7.6 Implement RRF fusion: `score = SUM(1 / (60 + rank_i))` across trigram, full-text, and semantic result lists
- [ ] 7.7 Update MCP tool response to include `score` field, order by fused score DESC
- [ ] 7.8 Write/update tests: trigram typo recovery, full-text description match, semantic conceptual match ('dad' → parent_of), RRF ordering, empty query, scope filter

## 8. Predicate Aliases (bu-dfkd)

- [ ] 8.1 Add `aliases TEXT[] DEFAULT '{}'` column to predicate_registry via migration
- [ ] 8.2 Create unique index on aliases (GIN for containment, plus constraint ensuring no alias collides with a canonical name)
- [ ] 8.3 Add alias resolution in write path: `SELECT name FROM predicate_registry WHERE $1 = ANY(aliases)` before registry lookup
- [ ] 8.4 Include `resolved_from` in store_fact response when alias resolution occurs
- [ ] 8.5 Update search_vector trigger to include aliases in weight B
- [ ] 8.6 Seed aliases for existing edge predicates (parent_of, knows, works_at, sibling_of, lives_in, etc.)
- [ ] 8.7 Write tests: alias resolution, alias uniqueness constraint, aliases in search results, resolved_from in response

## 9. Inverse and Symmetric Predicates (bu-h2la)

- [ ] 9.1 Add `inverse_of TEXT` and `is_symmetric BOOLEAN DEFAULT false` columns to predicate_registry via migration
- [ ] 9.2 Seed inverse pairs: parent_of↔child_of, manages↔reports_to, works_at↔employs; mark knows, sibling_of, lives_with as symmetric
- [ ] 9.3 Update entity detail API to include inverse-resolved facts (facts where entity is object_entity_id, presented with inverse predicate label)
- [ ] 9.4 Write tests: inverse traversal at query time, symmetric predicate bidirectional discovery, no duplicate fact storage

## 10. Predicate Lifecycle (bu-ittf)

- [ ] 10.1 Add `status TEXT DEFAULT 'active'`, `superseded_by TEXT`, `deprecated_at TIMESTAMPTZ` columns via migration
- [ ] 10.2 Add write-time warning: if predicate status='deprecated', include warning with superseded_by in store_fact response
- [ ] 10.3 Auto-registered predicates get `status='proposed'` instead of `'active'`
- [ ] 10.4 Deprecate ~36 unused baseline predicates from migration 005, set superseded_by to their domain-specific replacements
- [ ] 10.5 Write tests: deprecated predicate writes succeed with warning, proposed status on auto-registration, status filtering in predicate_list

## 11. Predicate Scoping (bu-hzvr)

- [ ] 11.1 Add `scope TEXT DEFAULT 'global'` column to predicate_registry via migration
- [ ] 11.2 Backfill scope for all seeded predicates: health→'health', relationship→'relationship', finance→'finance', home→'home', edge/general→'global'
- [ ] 11.3 Update memory_predicate_search scope parameter to filter on registry scope column (replace expected_subject_type workaround)
- [ ] 11.4 Write tests: scope filtering in search, backfill correctness

## 12. Domain/Range Type Validation (bu-typc)

- [ ] 12.1 Extend entity existence check in `store_fact()` to also fetch `entity_type`: `SELECT id, entity_type FROM shared.entities WHERE id = $1`
- [ ] 12.2 After registry lookup, compare actual entity types against `expected_subject_type` and `expected_object_type`; if mismatch, append to `warnings` list
- [ ] 12.3 Propagate `warnings` through write response: `{"id": ..., "warnings": [...]}`
- [ ] 12.4 Write tests: subject type mismatch warns, object type mismatch warns, NULL expected types skip check, matching types produce no warning

## 13. Example Payloads in Registry (bu-exjn)

- [ ] 13.1 Add `example_json JSONB` column to predicate_registry via migration
- [ ] 13.2 Backfill `example_json` for all seeded predicates with realistic `{"content": "...", "metadata": {...}}` payloads from predicate-taxonomy.md
- [ ] 13.3 Include `example_json` in `memory_predicate_search` results and `memory_predicate_list` output
- [ ] 13.4 Write tests: example_json returned in search, NULL for auto-registered predicates

## 14. Integration Tests and Audit

- [ ] 14.1 Audit all direct callers of `store_fact()` in `roster/*/tools/` — verify they pass correct `object_entity_id` for edge predicates and `valid_at` for temporal predicates
- [ ] 14.2 Run full memory module test suite — verify no regressions from all predicate registry changes
- [ ] 14.3 Run full API test suite — verify dashboard endpoints still work
