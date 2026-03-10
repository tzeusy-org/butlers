## Why

The memory module's target-state spec (`docs/modules/memory.md`) and OpenSpec (`module-memory`) define a contract that the live implementation has drifted from in several important places: tenant isolation is absent from the three core memory tables, consolidation has a runtime bug and no concurrency safety, retrieval computes one scoring model but executes another, context assembly is a greedy character fill instead of a deterministic compiler, temporal facts have no DB-level safety, and retention is a fixed 7-day TTL instead of policy-driven. Closing these gaps now — before Jarvis use-cases accumulate real data in health, finance, and relationship domains — prevents costly data migrations later and establishes the correctness foundation the rest of the system assumes.

## What Changes

- **Bugfix:** Consolidation queries `rules.status = 'active'` but the actual column is `maturity`; fix immediately.
- **Tenant/request lineage:** Add `tenant_id`, `request_id`, `retention_class`, and `sensitivity` columns to episodes, facts, and rules. Rebuild indexes as tenant-scoped. Backfill existing data to `'owner'`.
- **Consolidation state machine:** Add lease columns (`leased_until`, `leased_by`, `dead_letter_reason`), enforce `consolidation_status` CHECK constraint, implement `FOR UPDATE SKIP LOCKED` claiming with deterministic `(tenant_id, butler, created_at, id)` ordering and retry/backoff.
- **Effective-confidence retrieval:** Compute `effective_confidence()` on the read path in `recall()` and `search()`, use decayed value for both threshold filtering and composite scoring.
- **Deterministic memory_context:** Rewrite context builder as a compiler with fixed sections (Profile Facts, Task-Relevant Facts, Active Rules, Recent Episodes), deterministic section quotas, stable tie-breakers, `request_context` parameter, and proper token-budget enforcement.
- **Temporal fact safety:** Add `idempotency_key` and `invalid_at` columns to facts, create partial unique index on `(tenant_id, idempotency_key)`, add `observed_at` timestamp.
- **Retention policy:** Introduce `memory_policies` table with per-class TTL, decay rates, archival behavior, and summarization eligibility. Add `rule_applications` audit table. Replace fixed episode TTL with policy-driven retention classes.
- **Shared discovery catalog:** Add `shared.memory_catalog` as a searchable index (not a canonical store) with embedding, tsvector, and provenance pointers back to owning butler schemas. Narrow-grant butler roles INSERT/UPDATE on this table only.
- **MCP tool surface:** Add `request_context`, `retention_class`, `sensitivity` parameters to write tools; add structured `filters` dict to search/recall tools; add `include_recent_episodes` to `memory_context`. All additive, default-safe.
- **Integration tests:** Add Postgres-backed tests for retrieval ranking (effective_confidence used), tenant isolation, consolidation terminal states, temporal fact idempotency, and context assembly determinism.

## Capabilities

### New Capabilities
- `memory-retention-policy`: Policy-driven retention classes, per-class TTL/decay/archival configuration, and rule application audit tracking
- `memory-discovery-catalog`: Cross-butler searchable discovery index in shared schema with provenance pointers to owning butler memory tables

### Modified Capabilities
- `module-memory`: Tenant/request lineage on all memory tables; consolidation state machine with lease-based claiming; effective-confidence retrieval fix; deterministic memory_context compiler; temporal fact idempotency and bitemporal columns; structured MCP filter parameters

## Impact

- **Schema:** 5 new migrations (mem_014 through mem_017 + core_018) adding columns, indexes, constraints, and new tables across butler schemas and shared schema
- **Storage layer:** `storage.py` — all write functions gain `tenant_id`, `request_id`, `retention_class` parameters
- **Search layer:** `search.py` — `recall()` and `search()` compute effective_confidence on read path; all search functions add tenant_id WHERE clauses
- **Context builder:** `tools/context.py` — full rewrite from greedy fill to deterministic section compiler
- **Consolidation:** `consolidation.py` — bugfix for `status` vs `maturity`; lease-based claiming replaces bare `consolidated = false` query
- **MCP tools:** `__init__.py` + `tools/*.py` — additive parameters on write/read/context tools
- **ACLs:** Narrow INSERT/UPDATE grant on `shared.memory_catalog` for butler roles
- **Tests:** New integration test files for retrieval, tenant isolation, consolidation, temporal facts, and context assembly
- **Existing data:** One-time backfill of `tenant_id = 'owner'` on all existing rows; non-destructive
