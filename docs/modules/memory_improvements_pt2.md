# Memory System Improvements — Part 2 (Residual Gaps)

Audience: implementation agent.
Status: validated against codebase as of 2026-03-10.
Context: follow-up to `memory_improvements.md` (the original plan). Most of that
plan has been implemented through mem_001–mem_020. This document captures the
**remaining correctness and completeness gaps** confirmed by code review.

---

## What was implemented well

These items from the original plan are now landed and working:

- **Shared entity registry**: core_014 + mem_006 align everything to `shared.entities`.
- **Tenant/request lineage columns**: mem_014 adds `tenant_id`, `request_id`,
  `retention_class`, `sensitivity` to episodes/facts/rules.
- **Retrieval scoring**: `search.py` uses `effective_confidence` with decayed
  confidence in composite scoring inside `recall()`.
- **Deterministic context assembly**: `memory_context` is a section compiler with
  four sections, budget fractions, tenant scoping, and explicit tie-breakers.
- **Lease-based consolidation skeleton**: mem_015 adds lease columns; runner uses
  `FOR UPDATE SKIP LOCKED` and backoff/dead-letter states.
- **Temporal fact columns**: mem_016 adds `idempotency_key`, `observed_at`,
  `invalid_at` to facts.
- **Retention policy table**: mem_017 + mem_020 create and correct `memory_policies`.
- **Broad test surface**: dedicated suites for consolidation, search, storage,
  entities, and module integration.

---

## Confirmed remaining gaps

### 1. `episodes.retention_class` not persisted on write

**Location**: `storage.py:319-322`

`store_episode()` accepts `retention_class` as a parameter and uses it to look up
TTL from `memory_policies`, but the INSERT statement does not include
`retention_class` in the column list. The column exists (added by mem_014 with
default `'transient'`) but every episode gets the migration default rather than
the caller's value.

**Fix**: Add `retention_class` (and `sensitivity`) to the episodes INSERT.

**Priority**: High — without this, retention-class-aware decay sweeps treat all
episodes identically.

---

### 2. Consolidation groups by `butler` only, not `(tenant_id, butler)`

**Location**: `consolidation.py:164-170`

The SELECT orders by `(tenant_id, butler, created_at, id)` — correct. But the
Python grouping loop (line 167-170) groups only by `butler_name`, collapsing
episodes from different tenants into the same group.

**Fix**: Group by `(tenant_id, butler_name)` tuple. The dict key should be
`(row["tenant_id"], row["butler"])`.

**Priority**: Medium — single-tenant deployments are unaffected, but multi-tenant
correctness requires this.

---

### 3. Consolidation executor does not propagate tenant/request context

**Location**: `consolidation_executor.py:96-107, 126-135`

`execute_consolidation()` calls `store_fact()` and `store_rule()` without passing
`tenant_id` or `request_id`, so they default to `"owner"` and `None`. The
executor signature (line 50-59) does not accept these parameters either.

**Fix**:
1. Add `tenant_id` and `request_id` to `execute_consolidation()` signature.
2. Pass them through to `store_fact()` and `store_rule()` calls.
3. In the runner, extract `tenant_id` from the episode group and thread it in.

**Priority**: High — consolidation-derived facts/rules lose their tenant lineage.

---

### 4. `memory_events` inserts omit `tenant_id`

**Location**: `consolidation.py:357`, `consolidation_executor.py:205-214`

Both consolidation failure and success event inserts write only `event_type`,
`actor`, and `payload` — no `tenant_id`. The column exists (mem_003 creates it,
or it was added later) but consolidation paths never populate it.

**Fix**: Include `tenant_id` in the INSERT for memory_events from consolidation
paths. Extract from the episode group being processed.

**Priority**: Medium — audit/lineage completeness for the most important mutation
path.

---

### 5. `shared.memory_catalog` has no migration

**Location**: `storage.py:188-223`, `search.py:789-858`

Runtime code references `shared.memory_catalog` for upserts (store_fact,
store_rule write-behind) and searches (semantic + full-text). The module config
gates catalog writes behind a flag noting "only enable after core_023". But no
migration exists to create the table.

The code handles this gracefully (best-effort, fire-and-forget writes), so it
won't crash. But catalog searches will fail if the table doesn't exist.

**Fix**: Create a core migration (or memory migration with `shared.` qualified
DDL) that creates `shared.memory_catalog` with the columns referenced in
`_upsert_catalog()`.

**Priority**: Low — feature-flagged and discovery-only. But should exist before
the flag is enabled.

---

### 6. Migration integration tests are source-inspection only

**Location**: `tests/migrations/test_memory_migrations.py`,
`tests/modules/memory/test_memory_migrations.py`

Current migration tests inspect Python source code and metadata (revision IDs,
down_revision chains, DDL strings). They do not apply migrations to a real
PostgreSQL schema. The repo has a `fresh_migration_db` fixture in
`tests/config/conftest.py` but it is not used for memory migrations.

This means code-vs-schema drift (like gap #1 above) is invisible to CI.

**Fix**: Add integration tests that:
1. Apply the full mem_001→mem_020 chain to a real PostgreSQL schema.
2. Verify expected columns exist with correct types/defaults.
3. Run a basic store_episode/store_fact/store_rule cycle against the resulting schema.
4. Verify fresh install and upgrade-from-baseline both work.

**Priority**: High — this is the gap that allows all other schema-vs-code drift
to persist undetected.

---

### 7. `memory_events` table missing enrichment columns

**Location**: Original plan Feature 5 / mem_017 in plan vs actual mem_017

The original plan called for adding `request_id`, `memory_type`, `memory_id`, and
`actor_butler` columns to `memory_events`. The actual mem_017 migration created
`memory_policies` instead. The memory_events enrichment was never implemented.

**Fix**: Create a new migration (mem_021) adding the enrichment columns to
`memory_events`:
- `request_id TEXT`
- `memory_type TEXT` (episode/fact/rule)
- `memory_id UUID`
- `actor_butler TEXT`

**Priority**: Low — nice-to-have for audit completeness but not blocking
correctness.

---

### 8. `embedding_versions` table not created

**Location**: Original plan proposed this in mem_017

The `embedding_versions` table for tracking active embedding model and re-embed
migrations was planned but never implemented.

**Fix**: Create migration with columns: `id`, `model_name`, `dimensions`,
`is_active`, `created_at`. Low urgency until a model migration is needed.

**Priority**: Low — only needed when switching embedding models.

---

## Items from the external review that are NOT actual gaps

For completeness, these claims from the external review were checked and found
to be incorrect or already resolved:

- **"migrations stop at mem_012"**: False. Migration chain goes to mem_020.
- **"core chain stops at core_022, but code references core_023/024"**: Core
  migrations are managed outside this repo; the memory module's own chain is
  complete. `shared.memory_catalog` is the only unresolved shared-schema table.
- **"fresh schema would be incompatible"**: mem_014 adds tenant_id/request_id
  with backfill defaults. A fresh install applying the full chain gets all
  columns. The INSERT paths include these columns.
- **"MCP tool description says valid_at defaults to now()"**: The actual tool
  docstring in `writing.py:128-131` correctly describes NULL semantics for
  property facts vs temporal facts. No contract drift found.
- **"retention is only partially landed"**: `retention_class` IS persisted on
  facts and rules via their INSERT statements. The gap is episodes only (#1).

---

## Suggested implementation order

1. **#1 + #3** (episode retention persistence + executor tenant propagation) —
   highest-value correctness fixes, small patches.
2. **#2 + #4** (consolidation tenant grouping + events tenant_id) — complete
   multi-tenant consolidation correctness.
3. **#6** (migration integration tests) — prevents future drift.
4. **#5** (shared.memory_catalog migration) — unblocks catalog feature flag.
5. **#7 + #8** (events enrichment + embedding_versions) — polish.
