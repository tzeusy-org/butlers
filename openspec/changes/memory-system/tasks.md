## 1. Memory Module Scaffold & Schema

- [ ] 1.1 Create `src/butlers/modules/memory.py` (module entrypoint and registration hooks)
- [ ] 1.2 Add memory config models under `src/butlers/config.py` for `[modules.memory]`
- [ ] 1.3 Add/verify dependencies for local embeddings and pgvector usage
- [ ] 1.4 Create/adjust Alembic memory migration chain (`mem_*`) for module-owned tables: episodes, facts, rules, memory_links, memory_events
- [ ] 1.5 Ensure memory migrations apply to each butler DB with memory module enabled
- [ ] 1.6 Add migration tests for clean bootstrap and idempotent reruns

## 2. Embedding & Indexing

- [ ] 2.1 Implement embedding engine (MiniLM-L6, 384 dims) with single/batch encode APIs
- [ ] 2.2 Ensure embedding encode paths disable progress bars/log noise
- [ ] 2.3 Implement search-vector helpers for full-text indexing
- [ ] 2.4 Add tests for embedding dimensions, determinism envelope, and edge-case text handling

## 3. Core Storage Operations

- [ ] 3.1 Implement episode storage with embedding/search vector population and TTL defaults
- [ ] 3.2 Implement fact storage with permanence mapping and active-fact supersession
- [ ] 3.3 Implement rule storage with candidate maturity defaults
- [ ] 3.4 Implement memory links creation for provenance/supersession relations
- [ ] 3.5 Implement typed get/read helpers with reference-count/last-referenced bumping
- [ ] 3.6 Implement forget/retract semantics with canonical validity states
- [ ] 3.7 Add storage tests covering lifecycle transitions and schema constraints

## 4. Search & Recall

- [ ] 4.1 Implement semantic search (pgvector cosine)
- [ ] 4.2 Implement keyword search (tsvector/tsquery)
- [ ] 4.3 Implement hybrid search fusion (RRF)
- [ ] 4.4 Implement composite scoring (relevance/importance/recency/effective_confidence)
- [ ] 4.5 Implement scope filtering and tenant-bounded defaults
- [ ] 4.6 Implement `memory_recall` high-level retrieval API
- [ ] 4.7 Add tests for ranking, thresholds, tie-break determinism, and scope filtering

## 5. Decay, Maturity, and Feedback

- [ ] 5.1 Implement `memory_confirm` for rules/facts
- [ ] 5.2 Implement `memory_mark_helpful` and promotion thresholds
- [ ] 5.3 Implement `memory_mark_harmful` with 4x harmful weighting and demotion logic
- [ ] 5.4 Implement anti-pattern inversion (`anti_pattern` maturity)
- [ ] 5.5 Implement daily decay sweep transitions (`active`/`fading`/`expired`)
- [ ] 5.6 Add tests for decay math and maturity transitions

## 6. Memory Module Tool Surface

- [ ] 6.1 Register memory tools from module hook on hosting butler MCP servers
- [ ] 6.2 Implement writing tools: `memory_store_episode`, `memory_store_fact`, `memory_store_rule`
- [ ] 6.3 Implement reading tools: `memory_search`, `memory_recall`, `memory_get`
- [ ] 6.4 Implement feedback tools: `memory_confirm`, `memory_mark_helpful`, `memory_mark_harmful`
- [ ] 6.5 Implement management tools: `memory_forget`, `memory_stats`
- [ ] 6.6 Implement deterministic `memory_context` assembly with token budgeting and stable ordering
- [ ] 6.7 Add tool-level tests for success/error behavior and request-context propagation

## 7. Consolidation & Hygiene Jobs

- [ ] 7.1 Implement consolidation scheduler integration in module startup
- [ ] 7.2 Implement consolidation batching and deterministic shard ordering
- [ ] 7.3 Implement prompt composition and output parsing for fact/rule extraction
- [ ] 7.4 Implement idempotent persistence of consolidation outputs and provenance links
- [ ] 7.5 Implement retry metadata and terminal states (`consolidated`, `failed`, `dead_letter`)
- [ ] 7.6 Implement episode cleanup with TTL and capacity rules
- [ ] 7.7 Add tests for consolidation, retries, cleanup, and event emission

## 8. Runtime Integration

- [ ] 8.1 Update spawner pre-run path to call local `memory_context(...)` when module enabled
- [ ] 8.2 Keep fail-open behavior for memory_context errors (log + continue)
- [ ] 8.3 Update post-session path to call local `memory_store_episode(...)`
- [ ] 8.4 Keep fail-open behavior for episode-write errors (log + continue)
- [ ] 8.5 Ensure no dedicated external memory MCP server is injected into ephemeral configs
- [ ] 8.6 Add tests for module-enabled and module-disabled paths

## 9. Dashboard Integration

- [ ] 9.1 Implement per-butler memory endpoints (`/api/butlers/:name/memory/...`)
- [ ] 9.2 Implement tenant-wide `/api/memory/*` aggregation via butler API/tool fanout
- [ ] 9.3 Implement fact/rule edit and soft-delete flows through memory tool APIs
- [ ] 9.4 Build/update UI: butler-scoped memory tab and aggregated `/memory` page
- [ ] 9.5 Add tests for filtering, supersession edits, and aggregation correctness

## 10. Rollout & Validation

- [ ] 10.1 Enable module in target roster butlers and validate startup/migrations
- [ ] 10.2 Run targeted quality gates for memory module and integration paths
- [ ] 10.3 Run full quality gate before merge-readiness
- [ ] 10.4 Update docs/spec references from role-level memory service to module model
