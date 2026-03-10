## Context

The memory module has been through a major improvement cycle (mem_001–mem_020) implementing shared entities, tenant lineage, lease-based consolidation, temporal facts, retention policies, and deterministic context assembly. An external code review identified 8 residual gaps where runtime code either doesn't persist accepted parameters, doesn't propagate tenant context through mutation paths, or references tables without migrations. All gaps are internal correctness issues — no MCP tool surface changes required.

Current migration chain: mem_001 → mem_020 (linear, no conflicts).
Current core migration state: shared.entities exists; shared.memory_catalog does not.

## Goals / Non-Goals

**Goals:**
- Fix all data persistence gaps where accepted parameters are silently dropped (episode retention_class/sensitivity)
- Make consolidation end-to-end tenant-safe (grouping, executor writes, audit events)
- Create migration for shared.memory_catalog to back existing runtime code
- Add memory_events enrichment columns for audit completeness
- Add embedding_versions table for future model migration tracking
- Establish real-DB migration integration tests to prevent future schema-vs-code drift

**Non-Goals:**
- No MCP tool signature changes
- No new MCP tools
- No changes to search/recall scoring logic (already correct)
- No changes to the context assembler (already correct)
- No multi-tenant deployment support beyond correctness (no tenant provisioning, no tenant admin tools)

## Decisions

### D1: Fix episode INSERT in-place rather than new migration

The `episodes` table already has `retention_class` and `sensitivity` columns (mem_014). The bug is purely in `store_episode()`'s INSERT SQL — it omits those columns. Fix is a 2-line code change, no migration needed.

**Alternative considered**: Adding a backfill migration for existing episodes. Rejected because the default values from mem_014 (`transient`, `normal`) are already correct defaults — only future writes need fixing.

### D2: Consolidation tenant grouping uses composite key

Change the Python dict key from `butler_name` to `(tenant_id, butler_name)`. The downstream executor signature gains `tenant_id` and `request_id` parameters, threaded from the episode group. The runner extracts `tenant_id` from the first episode in each group (all episodes in a group share the same tenant_id by construction, since the SELECT orders by tenant_id first).

**Alternative considered**: Separate SQL queries per tenant. Rejected — adds complexity and round-trips for no benefit since the current `FOR UPDATE SKIP LOCKED` pattern already handles concurrency.

### D3: memory_events enrichment as additive migration (mem_021)

Add `request_id`, `memory_type`, `memory_id`, `actor_butler` columns to `memory_events` as nullable TEXT/UUID columns. No backfill needed — existing rows keep NULLs. Update consolidation event inserts to populate `tenant_id` and the new columns.

### D4: shared.memory_catalog as memory module migration (mem_022)

Create the table via a memory migration using schema-qualified `shared.memory_catalog` DDL, matching the columns already referenced in `_upsert_catalog()` and `memory_catalog_search()`. This avoids a dependency on the external core migration system.

**Alternative considered**: Wait for core_023. Rejected — the runtime code already exists and the feature flag already references it. Having the table exist (even if the flag is off) is strictly better than silent failures.

### D5: embedding_versions as part of mem_021

Bundle with events enrichment since both are additive schema-only changes with no runtime code dependencies. Keeps migration count lower.

### D6: Migration integration tests use fresh_migration_db fixture

The repo already has this fixture in `tests/config/conftest.py`. The new test suite will:
1. Apply full mem chain (mem_001→mem_022) to a fresh schema
2. Verify column existence and types via `information_schema`
3. Run a store_episode → store_fact → store_rule cycle against the real schema
4. Verify tenant_id, retention_class, sensitivity are persisted correctly

This catches the class of bug exemplified by gap #1 (accepted-but-not-persisted parameters).

## Risks / Trade-offs

**[Risk: Consolidation grouping change affects single-tenant deployments]**
→ Mitigation: Composite key `("owner", butler)` behaves identically to `butler` alone in single-tenant mode. No behavioral change for existing deployments.

**[Risk: shared.memory_catalog migration runs in wrong schema context]**
→ Mitigation: Use fully-qualified `shared.memory_catalog` in DDL. Add IF NOT EXISTS guard. The migration is idempotent.

**[Risk: Migration integration tests require PostgreSQL in CI]**
→ Mitigation: Tests already require PostgreSQL (the `fresh_migration_db` fixture exists). Gate with `pytest.mark.db` if needed.

**[Risk: mem_021/mem_022 ordering conflicts with concurrent work]**
→ Mitigation: Check for any in-flight memory migrations before committing. The linear chain makes conflicts visible immediately.
