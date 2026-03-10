## Why

The memory module's original improvement plan (memory_improvements.md) has been largely implemented through mem_001–mem_020, but an external code review identified 8 residual correctness and completeness gaps where runtime code either doesn't persist data it accepts, doesn't propagate tenant context through mutation paths, or lacks schema backing for referenced tables. These gaps undermine multi-tenant correctness, retention-aware cleanup, and audit completeness. Fixing them now prevents the gaps from compounding as the memory system sees wider use.

## What Changes

- **Fix episode retention persistence**: `store_episode()` accepts `retention_class` and `sensitivity` but doesn't include them in the INSERT — every episode gets the migration default instead of the caller's value.
- **Fix consolidation tenant grouping**: The consolidation runner groups episodes by `butler` only, not `(tenant_id, butler)`, mixing tenants in multi-tenant deployments.
- **Fix consolidation executor tenant propagation**: `execute_consolidation()` calls `store_fact()`/`store_rule()` without `tenant_id`/`request_id`, so consolidation-derived knowledge loses its tenant lineage.
- **Fix memory_events tenant_id in consolidation paths**: Both success and failure event inserts from consolidation omit `tenant_id`.
- **Add `shared.memory_catalog` migration**: Runtime code references this table for discovery writes and searches, but no migration creates it.
- **Add migration integration tests**: Current migration tests only inspect source code — they don't apply migrations to a real PostgreSQL schema, so code-vs-schema drift is invisible to CI.
- **Add memory_events enrichment columns**: `request_id`, `memory_type`, `memory_id`, `actor_butler` were planned but never added.
- **Add `embedding_versions` table**: Planned for tracking embedding model changes; not yet created.

## Capabilities

### New Capabilities

- `memory-catalog-schema`: Migration creating `shared.memory_catalog` table to back the existing runtime catalog code.
- `memory-migration-integration-tests`: Real-DB migration tests that apply the full mem chain and verify schema correctness.
- `memory-events-enrichment`: Enriched audit columns on `memory_events` and the `embedding_versions` tracking table.

### Modified Capabilities

- `module-memory`: Consolidation tenant safety (grouping, executor propagation, events), episode retention/sensitivity persistence.

## Impact

- **Code**: `storage.py` (episode INSERT), `consolidation.py` (grouping logic, event inserts), `consolidation_executor.py` (signature + store calls)
- **Migrations**: New mem_021 (events enrichment + embedding_versions), new core/memory migration for `shared.memory_catalog`
- **Tests**: New migration integration test suite using `fresh_migration_db` fixture; updated consolidation tests for tenant safety
- **APIs/MCP tools**: No surface changes — all fixes are internal correctness
