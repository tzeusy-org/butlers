## 1. Episode retention persistence (HIGH)

- [ ] 1.1 Add `retention_class` and `sensitivity` to the `store_episode()` INSERT statement in `storage.py`
- [ ] 1.2 Add corresponding bind parameters to the `pool.execute()` call
- [ ] 1.3 Add unit test verifying `store_episode()` persists caller-provided `retention_class` and `sensitivity` values (not migration defaults)

## 2. Consolidation tenant-safe grouping (MEDIUM)

- [ ] 2.1 Change consolidation runner grouping key from `butler_name` to `(tenant_id, butler_name)` in `consolidation.py`
- [ ] 2.2 Update `group_counts` dict to use the composite key
- [ ] 2.3 Update the processing loop to unpack `(tenant_id, butler_name)` from group keys
- [ ] 2.4 Add unit test verifying episodes from different tenants are grouped separately

## 3. Consolidation executor tenant propagation (HIGH)

- [ ] 3.1 Add `tenant_id` and `request_id` parameters to `execute_consolidation()` signature in `consolidation_executor.py`
- [ ] 3.2 Pass `tenant_id` and `request_id` to all `store_fact()` calls in the executor
- [ ] 3.3 Pass `tenant_id` and `request_id` to all `store_rule()` calls in the executor
- [ ] 3.4 Update runner to extract `tenant_id` from episode group and pass to executor
- [ ] 3.5 Add unit test verifying consolidation-derived facts/rules inherit the source episode group's `tenant_id`

## 4. Memory events tenant_id in consolidation (MEDIUM)

- [ ] 4.1 Add `tenant_id` to the memory_events INSERT in `consolidation.py` (failure path, line ~357)
- [ ] 4.2 Add `tenant_id` to the memory_events INSERT in `consolidation_executor.py` (success path, line ~205)
- [ ] 4.3 Add unit test verifying consolidation events include `tenant_id`

## 5. Migrations: events enrichment + embedding_versions (mem_021)

- [ ] 5.1 Create `021_events_enrichment.py` migration adding `request_id`, `memory_type`, `memory_id`, `actor_butler` columns to `memory_events`
- [ ] 5.2 Add `embedding_versions` table creation to the same migration with seed row for `all-MiniLM-L6-v2`
- [ ] 5.3 Verify migration chain integrity (down_revision = mem_020, revision = mem_021)

## 6. Migration: shared.memory_catalog (mem_022)

- [ ] 6.1 Create `022_shared_memory_catalog.py` migration with schema-qualified `shared.memory_catalog` DDL
- [ ] 6.2 Include unique constraint on `(source_schema, source_table, source_id)`
- [ ] 6.3 Include GIN index on `search_vector`
- [ ] 6.4 Use `IF NOT EXISTS` for idempotent creation
- [ ] 6.5 Verify migration chain integrity (down_revision = mem_021, revision = mem_022)

## 7. Migration integration tests

- [ ] 7.1 Create `tests/modules/memory/test_memory_migration_integration.py`
- [ ] 7.2 Add test: full chain (mem_001→mem_022) applies cleanly to fresh schema
- [ ] 7.3 Add test: critical columns exist with correct types after full chain
- [ ] 7.4 Add test: store_episode/store_fact/store_rule cycle succeeds against real schema with tenant_id, retention_class, sensitivity persisted correctly
- [ ] 7.5 Add test: memory_policies has 8 seeded rows with correct retention classes
- [ ] 7.6 Mark tests with `pytest.mark.db` or equivalent for CI gating

## 8. Update consolidation event inserts for enrichment columns

- [ ] 8.1 Add `actor_butler` to consolidation failure event INSERT in `consolidation.py`
- [ ] 8.2 Add `actor_butler` to consolidation success event INSERT in `consolidation_executor.py`
- [ ] 8.3 Add `memory_type` and `memory_id` where applicable to event inserts

## 9. Validation

- [ ] 9.1 Run `make lint` — all files pass
- [ ] 9.2 Run targeted pytest on modified files
- [ ] 9.3 Run full test suite as final pre-merge validation
