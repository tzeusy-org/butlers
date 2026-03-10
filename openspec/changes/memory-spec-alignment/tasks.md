## 1. Immediate Bugfix — Consolidation status vs maturity

- [ ] 1.1 Fix `consolidation.py` line 113-119: change `SELECT id, content, status FROM rules WHERE status = 'active'` to `SELECT id, content, maturity FROM rules WHERE maturity NOT IN ('anti_pattern') AND (metadata->>'forgotten')::boolean IS NOT TRUE AND source_butler = $1`
- [ ] 1.2 Add unit test for consolidation rule-fetching query to prevent regression

## 2. Effective-Confidence Retrieval Fix (code-only, no migration)

- [ ] 2.1 Update `search.py:recall()` to compute `effective_confidence()` for each result and use decayed value in both `min_confidence` filtering and `compute_composite_score()` call
- [ ] 2.2 Update `search.py:search()` to compute `effective_confidence()` for facts/rules when `min_confidence > 0` filtering is applied
- [ ] 2.3 Add unit tests verifying: decayed facts are filtered correctly, permanent facts (decay_rate=0) pass through, composite score uses decayed confidence

## 3. Migration: mem_014 — Tenant/Request Lineage and Retention Columns

- [ ] 3.1 Create `src/butlers/modules/memory/migrations/mem_014.py`: add `tenant_id`, `request_id`, `retention_class`, `sensitivity` to episodes, facts, rules with type-specific defaults
- [ ] 3.2 In mem_014: rebuild indexes as tenant-scoped (`idx_episodes_tenant_butler_status_created`, `idx_facts_tenant_scope_validity`, `idx_rules_tenant_scope_maturity`)
- [ ] 3.3 Update `storage.py:store_episode()` — add `tenant_id`, `request_id`, `retention_class` parameters
- [ ] 3.4 Update `storage.py:store_fact()` — add `tenant_id`, `request_id`, `retention_class`, `sensitivity` parameters; scope supersession checks to tenant_id
- [ ] 3.5 Update `storage.py:store_rule()` — add `tenant_id`, `request_id`, `retention_class` parameters
- [ ] 3.6 Update `search.py` — add `tenant_id` parameter to `semantic_search`, `keyword_search`, `hybrid_search`, `recall`, `search` and add WHERE clause for tenant filtering
- [ ] 3.7 Update MCP tool wrappers in `__init__.py` — add `request_context` parameter to write tools, extract `tenant_id`/`request_id` from it
- [ ] 3.8 Add integration test: verify tenant isolation (fact stored with tenant_id='A' is not returned by search scoped to tenant_id='B')

## 4. Migration: mem_015 — Consolidation State Machine

- [ ] 4.1 Create `src/butlers/modules/memory/migrations/mem_015.py`: add `leased_until`, `leased_by`, `dead_letter_reason`, `next_consolidation_retry_at` columns; rename `retry_count` → `consolidation_attempts`, `last_error` → `last_consolidation_error`; add CHECK constraint on `consolidation_status`
- [ ] 4.2 Update `consolidation.py:run_consolidation()` — replace bare `consolidated = false` query with `FOR UPDATE SKIP LOCKED` lease-based claiming ordered by `(tenant_id, butler, created_at, id)`
- [ ] 4.3 Implement retry logic: exponential backoff via `next_consolidation_retry_at`, max retries → dead_letter
- [ ] 4.4 Update `consolidation_executor.py` — set terminal states (consolidated/failed/dead_letter) per episode, clear lease on completion
- [ ] 4.5 Add integration test: concurrent consolidation workers don't process same episodes; failed episode retries; dead-letter after max retries

## 5. Migration: mem_016 — Temporal Fact Safety

- [ ] 5.1 Create `src/butlers/modules/memory/migrations/mem_016.py`: add `idempotency_key`, `observed_at`, `invalid_at` columns to facts; create partial unique index `idx_facts_temporal_idempotency` on `(tenant_id, idempotency_key) WHERE idempotency_key IS NOT NULL`
- [ ] 5.2 Update `storage.py:store_fact()` — auto-generate idempotency_key for temporal facts (SHA-256 of composite key), use ON CONFLICT DO NOTHING, return existing fact ID on conflict
- [ ] 5.3 Add integration test: duplicate temporal fact write is idempotent (same key → no new row); property facts remain unaffected; explicit idempotency_key is honored

## 6. Migration: mem_017 — Retention Policy Tables

- [ ] 6.1 Create `src/butlers/modules/memory/migrations/mem_017.py`: create `memory_policies` table with default retention classes seeded; create `rule_applications` table
- [ ] 6.2 Update `storage.py:store_episode()` — look up TTL from `memory_policies` by `retention_class` instead of hardcoded 7-day default
- [ ] 6.3 Update `storage.py:mark_helpful()` and `mark_harmful()` — insert `rule_applications` audit row
- [ ] 6.4 Update `storage.py:run_decay_sweep()` — read thresholds from `memory_policies` for each retention_class; implement archive-before-delete for classes with `archive_before_delete = true`
- [ ] 6.5 Add integration test: episodic retention_class gets 30-day TTL; health_log class uses policy-driven decay threshold; rule application audit rows are created

## 7. Deterministic memory_context Rewrite

- [ ] 7.1 Rewrite `tools/context.py:memory_context()` as a section compiler with four sections: Profile Facts (30%), Task-Relevant Facts (35%), Active Rules (20%), Recent Episodes (15% opt-in)
- [ ] 7.2 Implement profile fact retrieval: query facts where entity_id matches owner entity (via `shared.entities WHERE 'owner' = ANY(roles)`)
- [ ] 7.3 Implement section-level quota enforcement: each section gets its percentage of `token_budget * 4` chars
- [ ] 7.4 Implement deterministic ordering within sections: composite_score DESC, created_at DESC, id ASC
- [ ] 7.5 Add `include_recent_episodes` and `request_context` parameters to MCP tool wrapper
- [ ] 7.6 Add integration test: same inputs produce identical output; sections respect quotas; empty sections are omitted; recent episodes appear only when opted in

## 8. Migration: core_019 — Shared Discovery Catalog

- [ ] 8.1 Create `src/butlers/migrations/versions/core_019_shared_memory_catalog.py`: create `shared.memory_catalog` table with UNIQUE constraint on `(source_schema, source_table, source_id)`, pgvector IVFFlat index, GIN tsvector index, tenant/entity indexes
- [ ] 8.2 In core_019: grant INSERT, UPDATE on `shared.memory_catalog` to all butler roles (follow core_014 role enumeration pattern)
- [ ] 8.3 Update `storage.py:store_fact()` — add catalog upsert after canonical fact insert (best-effort, logged warning on failure)
- [ ] 8.4 Update `storage.py:store_rule()` — add catalog upsert after canonical rule insert (best-effort)
- [ ] 8.5 Handle fact supersession in catalog: delete/nullify catalog entry for superseded fact, create entry for new fact
- [ ] 8.6 Implement `catalog_search()` function in `search.py` for cross-butler hybrid search on `shared.memory_catalog`
- [ ] 8.7 Add integration test: fact stored in butler schema creates catalog entry; cross-butler search returns results from multiple butlers; catalog write failure doesn't block canonical store

## 9. MCP Tool Surface Updates

- [ ] 9.1 Add `filters` parameter to `memory_search` and `memory_recall` MCP tool wrappers — support keys: `scope`, `entity_id`, `predicate`, `source_butler`, `time_from`, `time_to`, `retention_class`, `sensitivity`
- [ ] 9.2 Wire `filters` dict through to `search.py:search()` and `search.py:recall()` — translate filter keys to SQL WHERE conditions
- [ ] 9.3 Add `retention_class` and `sensitivity` parameters to `memory_store_fact` and `memory_store_rule` MCP tool wrappers
- [ ] 9.4 Add unit test: filters parameter with multiple keys produces correct SQL; unrecognized filter keys are silently ignored

## 10. Documentation and Spec Sync

- [ ] 10.1 Update `docs/modules/memory.md` (target-state spec) to reflect new columns, context sections, retention policy, and catalog
- [ ] 10.2 Update `src/butlers/api/models/memory.py` — add new fields to Episode, Fact, Rule Pydantic models (tenant_id, retention_class, sensitivity, etc.)
- [ ] 10.3 Run full test suite and lint to verify no regressions
