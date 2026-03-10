## Context

The memory module has a well-defined target-state spec (`docs/modules/memory.md`) and a detailed OpenSpec (`module-memory`), but the live implementation has drifted in several places. An external architecture review identified 8 specific gaps between spec and code. The existing implementation is operational across 8 butlers with real data. Changes must be non-destructive, backward-compatible, and deployable incrementally.

Current migration head: `mem_013` (fix_owner_tenant_id). Core migration head includes `core_014` (entities_to_shared). The memory module runs in per-butler schemas with `shared.entities` as the cross-butler identity plane.

Key files:
- `src/butlers/modules/memory/storage.py` — write layer (no tenant_id params)
- `src/butlers/modules/memory/search.py` — read layer (recall uses raw confidence, not effective_confidence)
- `src/butlers/modules/memory/tools/context.py` — context builder (greedy character fill)
- `src/butlers/modules/memory/consolidation.py` — orchestrator (queries `rules.status` instead of `rules.maturity`)
- `src/butlers/modules/memory/__init__.py` — MCP tool registration (18 tools, no request_context params)

## Goals / Non-Goals

**Goals:**
- Fix the runtime crash in consolidation (`status` vs `maturity` column)
- Add tenant_id and request_id lineage to all three memory tables
- Make recall/search use effective_confidence (decayed) for scoring and filtering
- Replace the greedy context builder with a deterministic section-based compiler
- Add DB-level safety for temporal fact writes (idempotency_key, invalid_at)
- Introduce policy-driven retention classes replacing fixed episode TTL
- Add a shared discovery catalog for cross-butler memory search without breaking ownership isolation
- Add integration tests covering retrieval ranking, tenant isolation, consolidation states, and temporal idempotency

**Non-Goals:**
- Embedding model migration (stay on all-MiniLM-L6-v2 384-d; `embedding_versions` table deferred)
- Cross-butler direct SQL access (catalog is discovery-only, not a canonical store)
- Breaking changes to existing MCP tool signatures (all changes are additive with safe defaults)
- Multi-tenant production deployment (backfill to single `'owner'` tenant; multi-tenant is future work)
- Dashboard UI changes (API models may gain fields but existing endpoints stay compatible)

## Decisions

### D1: Migration ordering and numbering

**Decision:** Five migrations in strict dependency order: mem_014 (lineage+retention columns), mem_015 (consolidation state machine), mem_016 (temporal fact safety), mem_017 (retention policy tables), core_019 (shared discovery catalog).

**Rationale:** mem_013 already exists (fix_owner_tenant_id), so the new chain starts at mem_014. Lineage columns come first because all subsequent migrations and code changes depend on tenant_id being present. The core migration uses core_019 because it touches the shared schema (same pattern as core_014 for entities).

**Alternatives considered:**
- Single large migration: rejected — too risky to roll back, harder to test incrementally
- Code-only changes first, schema later: rejected — tenant_id needs to be in the schema before search/storage code can use it

### D2: Tenant_id backfill strategy

**Decision:** Use `DEFAULT 'owner'` on the new tenant_id columns so all existing rows automatically get the owner tenant. No separate backfill migration needed.

**Rationale:** This is a single-user Jarvis system today. The `'owner'` tenant value is consistent with the existing `shared.entities` tenant_id pattern. When multi-tenant support is needed, a separate migration can partition data.

**Alternatives considered:**
- Backfill to `'shared'`: rejected — entity tools use `'shared'` as the tenant for cross-butler entities, but memory data is butler-local and should use the owner identity
- Nullable tenant_id: rejected — spec requires tenant-bounded queries; nullable would require COALESCE everywhere

### D3: Consolidation bugfix approach

**Decision:** Fix the `rules.status` → `rules.maturity` bug directly in `consolidation.py` as part of the consolidation state machine work. No separate migration needed — it's a code-only fix.

**Rationale:** The query `SELECT id, content, status FROM rules WHERE status = 'active'` references a non-existent column. The fix is to query `maturity` and filter by appropriate values (not `'anti_pattern'` and not `metadata.forgotten`). This can ship immediately as a standalone commit before the migrations.

### D4: Effective-confidence computation location

**Decision:** Compute effective_confidence in `recall()` and `search()` at query time (read path), not via a materialized column or periodic sweep.

**Rationale:** The decay sweep already runs daily for fading/expiry transitions, but retrieval needs up-to-the-second accuracy. Computing on the read path means results always reflect current decay state. The computation is O(1) per row (one `exp()` call) — negligible overhead.

**Alternatives considered:**
- Materialized `effective_confidence` column updated by sweep: rejected — stale between sweeps, requires index rebuilds
- Database-computed generated column: rejected — PostgreSQL doesn't support `now()` in generated columns

### D5: Context builder architecture

**Decision:** Rewrite `memory_context` as a section compiler with fixed quota allocation:
1. **Profile Facts** (30% of budget) — facts about the owner entity, sorted by importance
2. **Task-Relevant Facts** (35% of budget) — facts matching the trigger prompt, sorted by composite score
3. **Active Rules** (20% of budget) — rules sorted by maturity (proven > established > candidate) then effectiveness
4. **Recent Episodes** (15% of budget, optional) — last N episodes for the butler, only when `include_recent_episodes=True`

Within each section, items are ordered by composite score DESC, created_at DESC, id ASC (deterministic tie-breaker from spec). Token budget enforcement uses the existing `token_budget * 4` character approximation — a real tokenizer adds a dependency for marginal accuracy gain.

**Rationale:** The spec requires deterministic, sectioned output with hard budget enforcement. Fixed quotas ensure every section gets representation even when one section has many high-scoring results. Profile facts are separated from task-relevant facts because owner identity context should always be present regardless of the trigger prompt.

**Alternatives considered:**
- Dynamic quota allocation based on result counts: rejected — non-deterministic, makes output unpredictable
- tiktoken-based token counting: rejected — adds a dependency, 4-char approximation is within 10% for English text

### D6: Temporal fact idempotency key generation

**Decision:** Auto-generate `idempotency_key` as a hash of `(entity_id, object_entity_id, scope, predicate, valid_at, source_episode_id)` when not explicitly provided. Use SHA-256 truncated to 32 hex chars. The partial unique index `ON facts (tenant_id, idempotency_key) WHERE idempotency_key IS NOT NULL` prevents duplicate temporal writes.

**Rationale:** Temporal facts (meals, sleep, transactions) are the most likely to be written twice during consolidation retries. The composite key captures the essential identity of a temporal observation. Property facts don't need this — they already have uniqueness via supersession.

**Alternatives considered:**
- Caller-provided idempotency keys only: rejected — consolidation would need to generate them anyway, and callers shouldn't need to think about this
- Full DB-level composite unique index: rejected — too many nullable columns make the index unwieldy

### D7: Retention policy enforcement mechanism

**Decision:** The `memory_policies` table defines per-class policy. Episode TTL is computed from the policy's `ttl_days` at write time (replacing the fixed 7-day default). Fact/rule decay rates can be overridden by policy. The decay sweep reads policies to determine thresholds. A `retention_class` column on each memory table drives policy lookup.

**Rationale:** This replaces the hardcoded `_DEFAULT_EPISODE_TTL_DAYS = 7` with a configurable, class-aware system. Default retention classes with sensible defaults mean existing behavior is preserved for rows that get `DEFAULT 'transient'` (episodes) or `DEFAULT 'operational'` (facts).

### D8: Shared discovery catalog — ownership model

**Decision:** Butler roles get narrow `INSERT, UPDATE` grants on `shared.memory_catalog` only (not full schema-wide grants). Catalog entries are written by the owning butler when facts/rules are created or updated. The catalog stores a searchable summary + provenance pointer, not the full memory item. Cross-butler search queries the catalog; full recall routes back to the owning schema.

**Rationale:** This follows the existing pattern from `shared.entities` (butlers have direct DML access). Routing through Switchboard adds latency and complexity for what is essentially a write-behind index. The catalog is eventually consistent — a missing entry means the memory won't appear in cross-butler search, but canonical data is always in the owning schema.

**Alternatives considered:**
- Switchboard-mediated writes: rejected — adds RPC hop for every memory write, Switchboard becomes a bottleneck
- Shared memory service: rejected — spec explicitly lists this as a non-goal
- Trigger-based catalog updates: rejected — cross-schema triggers are fragile and hard to debug

## Risks / Trade-offs

**[Risk] Migration on live data with active butlers** → Run migrations during low-activity window. All column additions use `DEFAULT` values and `NOT NULL` with defaults, so they're safe online DDL. Test each migration against a snapshot of production data first.

**[Risk] Consolidation bugfix may surface previously-hidden failures** → The current code crashes when trying to fetch rules for dedup context (non-existent `status` column). After fixing, consolidation may surface new issues from rules that were never previously considered. Mitigate by running consolidation in dry-run mode first after the fix.

**[Risk] Effective-confidence filtering may reduce recall results** → Currently `recall()` uses raw confidence (always 1.0 for new facts), so nothing is filtered. After the fix, decayed facts may drop below `min_confidence=0.2`. This is correct behavior but may surprise users who see fewer results. Mitigate by logging when effective_confidence filtering removes results.

**[Risk] Context builder rewrite changes system prompt content** → Butlers currently see a flat list of facts + rules. The new sectioned format with Profile/Task/Rules/Episodes changes the shape of injected context. This could affect runtime behavior. Mitigate by testing with representative trigger prompts and comparing old vs new context output.

**[Risk] Retention class backfill assigns default classes to existing data** → All existing episodes get `'transient'`, facts get `'operational'`, rules get `'rule'`. Some existing facts may warrant `'personal_profile'` or `'health_log'` classification. Mitigate by documenting that initial classification is conservative and can be reclassified later.

**[Risk] Shared catalog index adds write overhead to every memory store** → Every `store_fact` and `store_rule` call now also writes to `shared.memory_catalog`. Mitigate by making catalog writes async (fire-and-forget within the same transaction, or as a post-commit hook). If catalog write fails, the canonical memory is still stored — catalog is eventually consistent.

## Migration Plan

**Phase 1: Immediate bugfix (no migration)**
1. Fix `consolidation.py` — change `rules.status` to `rules.maturity`, fix SELECT column list
2. Test consolidation dry-run against production data
3. Deploy

**Phase 2: Lineage + retrieval correctness (mem_014 + code changes)**
1. Run `mem_014` — adds tenant_id, request_id, retention_class, sensitivity to all tables
2. Update storage.py — add tenant_id params to all write functions
3. Update search.py — add tenant_id WHERE clauses, compute effective_confidence in recall/search
4. Update MCP tools — add request_context params (additive, optional)
5. Test: tenant isolation, effective_confidence scoring, backward compatibility

**Phase 3: Consolidation state machine (mem_015)**
1. Run `mem_015` — adds lease columns, CHECK constraint
2. Update consolidation.py — FOR UPDATE SKIP LOCKED claiming, retry/backoff
3. Test: concurrent consolidation safety, terminal states

**Phase 4: Temporal safety + retention (mem_016 + mem_017)**
1. Run `mem_016` — adds idempotency_key, invalid_at, observed_at
2. Run `mem_017` — creates memory_policies, rule_applications tables
3. Update storage.py — auto-generate idempotency keys, policy-driven TTL
4. Update context.py — rewrite as section compiler
5. Test: temporal idempotency, retention policy enforcement, context determinism

**Phase 5: Shared discovery (core_019)**
1. Run `core_019` — creates shared.memory_catalog with grants
2. Add catalog write path to storage.py (fire-and-forget after canonical write)
3. Add cross-butler search endpoint
4. Test: catalog consistency, cross-butler search

**Rollback:** Each migration is independently reversible. If a migration causes issues, `alembic downgrade` to the previous head. Code changes are behind the schema — if tenant_id column doesn't exist, code falls back to current behavior via `try/except` or schema version check.

## Open Questions

1. **Profile fact identification:** How should the context builder identify "profile facts" for the owner entity? Options: (a) facts where `entity_id` matches the owner entity, (b) facts with a specific `retention_class='personal_profile'`, (c) facts with scope matching a configured "profile scope". Leaning toward (a) since the owner entity is always available via `shared.entities WHERE 'owner' = ANY(roles)`.

2. **Catalog write timing:** Should catalog entries be written synchronously within the `store_fact` transaction, or asynchronously via a background worker? Synchronous is simpler but adds latency. Async risks eventual consistency gaps. Leaning toward synchronous for now (catalog write is a single INSERT/upsert, fast).

3. **Retention class assignment for consolidation outputs:** When consolidation creates a new fact from episodes, what retention_class should it get? Options: (a) default based on butler domain (health butler → `'health_log'`), (b) LLM decides during consolidation, (c) always `'operational'` with manual reclassification. Leaning toward (a) with butler.toml config for default retention class.
