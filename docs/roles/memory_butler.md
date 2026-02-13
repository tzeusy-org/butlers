# Memory Butler: Permanent Definition

Status: Normative (Target State)  
Last updated: 2026-02-13  
Primary owner: Platform/Core

## 1. Role
The Memory Butler is the shared long-term memory system for the butler platform.

It is responsible for:
- Persisting memory artifacts (`episodes`, `facts`, `rules`) with provenance.
- Serving low-latency retrieval for runtime context injection.
- Running lifecycle maintenance (consolidation, decay, cleanup, supersession).
- Enforcing memory quality and safety contracts (confidence decay, anti-pattern learning, forget/retract flows).

This document is the authoritative target-state contract for Memory Butler behavior and architecture.

## 2. Design Goals
- Make all butlers stateful over time without coupling domain logic to storage internals.
- Preserve accuracy and provenance while allowing memory to evolve and decay.
- Keep retrieval low-latency and fail-open for user-facing workflows.
- Make memory transparent and governable through auditable lifecycle transitions.
- Scale from single-user deployments to multi-tenant fleets without architecture rewrites.

## 2.1 Base Contract Overrides
Inherits unchanged:
- All clauses in `docs/roles/base_butler.md` apply unless explicitly listed in `Overrides`.

Overrides:
- `base_clause`: `9.2 Memory Contract / Non-memory butlers integrate with memory only via Memory Butler MCP tools/endpoints`
  `override`: This restriction applies to non-memory butlers only. Memory Butler itself is the system of record and owns direct database access for memory reads/writes, consolidation, and maintenance.
  `rationale`: The role cannot operate through itself as an external consumer; it is the owning service boundary.
- `base_clause`: `9.2 Memory Contract / Runtime context retrieval uses memory_context(...) semantics`
  `override`: Memory Butler must provide `memory_context(...)` plus batched/async retrieval primitives for high-throughput callers; `memory_context(...)` remains backward-compatible.
  `rationale`: Preserves compatibility while enabling scale-oriented retrieval APIs.
- `base_clause`: `9.2 Memory Contract / Memory retrieval/storage failures must be fail-open for runtime execution`
  `override`: Fail-open applies to non-memory callers consuming Memory Butler. Inside Memory Butler, integrity-invariant failures (invalid transitions, schema invariant violations, provenance corruption) are fail-closed and must block mutation completion with explicit error classification.
  `rationale`: Callers should not fail user tasks due to memory unavailability, but the memory system must not continue after integrity corruption.

Additions:
- Memory Butler defines stricter contracts for memory lifecycle state machines, provenance, and retrieval scoring than the base contract.

## 3. Scope and Boundaries
### In scope
- Shared memory schema design and lifecycle policy.
- Memory MCP tool surfaces and retrieval behavior.
- Consolidation orchestration and decay policies.
- Memory observability, SLOs, and operational safety.
- Read model support for dashboard memory views.

### Out of scope
- Channel-specific delivery behavior (Telegram/Email UX).
- Domain decisions owned by specialist butlers.
- Replacing specialist per-butler operational state stores.

## 4. System Architecture Contract
### 4.1 Logical Components
- `Memory MCP API`: synchronous read/write tools consumed by butlers.
- `Consolidation Worker`: transforms episodes into facts/rules and confirmations.
- `Decay & Hygiene Worker`: confidence decay, fading/expiry transitions, cleanup.
- `Embedding Engine`: local embedding generation, model/version managed centrally.
- `Memory DB`: PostgreSQL + pgvector, authoritative store.
- `Read Projections`: query-optimized views/materializations for dashboard and analytics.

### 4.2 Mandatory Runtime Flows
1. `Session start (read path)`:
   - Caller requests memory context via `memory_context(trigger_prompt, butler, token_budget, request_context?)`.
   - Memory Butler resolves tenant/request lineage from authenticated `request_context`, runs scoped retrieval, and returns ordered facts/rules (+ optional recent episodes) within budget.
2. `Session completion (write path)`:
   - Caller stores an episode via `memory_store_episode(...)`.
   - Episode becomes eligible for asynchronous consolidation.
3. `Consolidation`:
   - Worker consumes unconsolidated episodes in deterministic batches.
   - Worker emits new facts/rules, supersessions, confirmations, and provenance links.
4. `Lifecycle maintenance`:
   - Scheduled sweeps update fading/expired states and perform episode retention cleanup.

### 4.3 Determinism and Sharding Contract
- All retrieval and lifecycle operations MUST be tenant-bounded by default.
- Queue-backed lifecycle workers MUST preserve deterministic ordering within a shard key of `(tenant_id, butler)`.
- Global ordering across all shards is not required.

### 4.4 Reliability Contract
- Retrieval/write failures are fail-open for non-memory butlers (caller continues user task).
- Memory Butler itself must fail-closed on data integrity violations (invalid transitions, broken schema invariants).
- All lifecycle workers must be idempotent.

## 5. Data Model Contract
Memory Butler uses distinct schemas for three memory classes.

### 5.1 Episodes (observations)
- Purpose: high-volume, short-lived raw session observations.
- Required fields: `id`, `tenant_id`, `butler`, `session_id`, `content`, `importance`, `consolidated`, `consolidation_status`, `consolidation_attempts`, `last_consolidation_error`, `next_consolidation_retry_at`, `created_at`, `expires_at`, `metadata`.
- Lifecycle: TTL-managed; unconsolidated rows are protected from capacity cleanup until expiry.
- Consolidation state machine: `pending -> consolidated|failed|dead_letter`.
- Compatibility: `consolidated=true` is a compatibility projection for `consolidation_status='consolidated'`.

### 5.2 Facts (semantic memory)
- Purpose: durable user/world knowledge in subject-predicate-content form.
- Required fields: `id`, `tenant_id`, `subject`, `predicate`, `content`, `scope`, `validity`, `confidence`, `decay_rate`, `permanence`, `source_butler`, `source_episode_id`, `supersedes_id`, `created_at`, `last_confirmed_at`, `last_referenced_at`, `metadata`, `tags`.
- Lifecycle states: `active`, `fading`, `superseded`, `expired`, `retracted`.
- Backward-compatibility: legacy `forgotten` inputs/reads MUST normalize to canonical `retracted`.
- Constraint: for active facts, `(tenant_id, scope, subject, predicate)` uniqueness MUST be DB-enforced (partial unique index).

### 5.3 Rules (procedural memory)
- Purpose: behavior guidance learned from repeated outcomes.
- Required fields: `id`, `tenant_id`, `content`, `scope`, `maturity`, `confidence`, `decay_rate`, `effectiveness_score`, `applied_count`, `success_count`, `harmful_count`, `source_butler`, `source_episode_id`, `created_at`, `last_applied_at`, `last_evaluated_at`, `last_confirmed_at`, `metadata`, `tags`.
- Maturity states: `candidate`, `established`, `proven`, `anti_pattern`.
- Feedback policy: harmful evidence is weighted more heavily than helpful evidence.

### 5.4 Memory Links (provenance graph)
- Purpose: explicit relation edges among episodes/facts/rules.
- Required fields: `tenant_id`, `source_type`, `source_id`, `target_type`, `target_id`, `relation`, `created_at`.
- Required relation types: `derived_from`, `supports`, `contradicts`, `supersedes`, `related_to`.
- Requirement: links are append-oriented and queryable both inbound/outbound.

### 5.5 Target-State Supporting Tables
- `memory_events` (append-only):
  - Canonical audit/event stream for all memory mutations and lifecycle transitions.
  - Enables replay, analytics, and post-incident reconstruction.
  - Required fields: `id`, `tenant_id`, `event_type`, `entity_type`, `entity_id`, `occurred_at`, `actor`, `request_id`, `payload`.
  - This table is mandatory for target-state compliance.
- `rule_applications`:
  - Explicit per-application outcome records instead of aggregate counters only.
  - Enables evidence-based maturity promotion and richer harmful diagnostics.
- `embedding_versions`:
  - Tracks model/version per row and supports rolling re-embed migrations.

## 6. Retrieval and Context Contract
### 6.1 Retrieval Modes
- `semantic`: vector similarity.
- `keyword`: PostgreSQL full-text.
- `hybrid`: RRF fusion over semantic + keyword.

### 6.2 Scoring Contract
Composite score must combine:
- Relevance.
- Importance.
- Recency.
- Effective confidence.

Baseline formula:
`score = 0.4*relevance + 0.3*importance + 0.2*recency + 0.1*effective_confidence`

Confidence decay:
`effective_confidence = confidence * exp(-decay_rate * days_since_last_confirmed)`

### 6.3 Scope Contract
- Tenant boundary is mandatory: default reads/writes are constrained to caller tenant.
- For non-memory callers: results are limited to caller tenant + `global + caller scope`.
- For memory admin workloads: unscoped reads are allowed with explicit intent, but remain tenant-bounded unless elevated cross-tenant authorization is present.

### 6.4 Context Assembly Contract
- `memory_context` must produce deterministic sectioned output:
  - Facts (highest priority first).
  - Rules (ordered by maturity and score).
  - Optional recent episodes.
- Hard token budget enforcement is mandatory and MUST use a deterministic tokenizer selected by retrieval config (no character-count heuristics in target state).
- Stable ordering MUST be deterministic with explicit tie-breakers: `score DESC`, then `created_at DESC`, then `id ASC`.
- Section quotas MUST be deterministic and configurable (facts/rules/episodes), with fixed overflow policy of dropping lowest-ranked candidates first.
- Output should include lightweight confidence/maturity annotations.

### 6.5 Retrieval Quality Improvements (Disruptive, Recommended)
- Two-stage retrieval:
  - Stage 1: broad candidate fetch via vector + lexical indices.
  - Stage 2: rerank with cross-encoder or lightweight judge model.
- Query intent routing:
  - Distinguish identity lookup, preference lookup, and procedure lookup for better weighting defaults.
- Result diversification:
  - Prevent top-k domination by one predicate/subject cluster.

## 7. Write, Consolidation, and Lifecycle Contract
### 7.1 Write Semantics
- `memory_store_episode` is append-only.
- `memory_store_fact` supports supersession and relation-link creation.
- `memory_store_rule` initializes `candidate` maturity and baseline confidence.
- `memory_confirm` updates confirmation anchors for facts/rules.
- `memory_mark_helpful` and `memory_mark_harmful` drive rule effectiveness/maturity transitions.
- `memory_forget` MUST emit an audit event and apply canonical lifecycle transitions (facts: `retracted`; rules/episodes: retrieval-excluded tombstone semantics per schema).

### 7.2 Consolidation Contract
- Consolidation runs in bounded batches with deterministic ordering per `(tenant_id, butler)` shard.
- Every episode entering the consolidation workflow must eventually end in exactly one terminal state: `consolidated`, `failed`, or `dead_letter`; retry metadata is mandatory for intermediate retry paths.
- Consolidation output must be structured and schema-validated before persistence.
- Duplicate extraction must be controlled through persisted idempotency keys and DB-enforced uniqueness where applicable.

### 7.3 Decay and Hygiene Contract
- Daily decay sweep computes effective confidence for facts/rules.
- Transition thresholds:
  - `>= retrieval_threshold`: normal.
  - `< retrieval_threshold and >= expiry_threshold`: fading.
  - `< expiry_threshold`: expired/retracted per type policy.
- Rule inversion:
  - Repeated harmful low-effectiveness rules become `anti_pattern` warnings.
- Episode cleanup:
  - Expired rows are deleted.
  - Capacity cleanup targets oldest consolidated rows first.

## 8. Backend Design and Scaling
### 8.1 Storage and Indexing
- PostgreSQL is the system of record.
- pgvector is required for semantic retrieval.
- Full-text indexes are required for lexical fallback and hybrid search.
- Default index posture:
  - Small/medium deployments: `ivfflat`.
  - Larger deployments: migrate to `hnsw` for better recall-latency tradeoff.

### 8.2 Load Model
Expected per-user steady state (current ecosystem profile):
- Episodes: 5-50/day (TTL window keeps active set small).
- Facts: 1-5/week net growth with supersession.
- Rules: 1-5/month net growth.

Estimated fleet profile (10,000 active users, 30 sessions/day/user):
- Context reads: ~300,000/day average (~3.5 RPS, burst 30-50 RPS).
- Episode writes: ~300,000/day average (~3.5 RPS, burst 20-40 RPS).
- Consolidation writes: typically 5-15% of episode volume.

This load is still compatible with a single regional PostgreSQL primary plus read replicas, provided batch workers and index hygiene are enforced.

### 8.3 Horizontal Scaling Strategy (Disruptive, Recommended)
- Split planes:
  - Write plane (tool writes + workers) on primary.
  - Read plane (`memory_context`, search, dashboard) on replicas/materialized views.
- Queue-backed workers:
  - Move consolidation/decay from purely cron-triggered to queue + scheduler hybrid for burst resilience.
  - Preserve deterministic execution within `(tenant_id, butler)` shard ordering.
- Multi-tenant partitioning:
  - Partition by `tenant_id` and time for `episodes` and `memory_events`.
  - Keep facts/rules partitioning optional until cardinality requires it.

## 9. SLOs, Observability, and Ops
### 9.1 Target SLOs
- `memory_context` p95 latency: <= 250 ms.
- `memory_search` p95 latency: <= 350 ms.
- Episode write success rate: >= 99.9% (caller still fail-open on misses).
- Consolidation freshness: p95 unconsolidated backlog age <= 6 hours.
- Decay sweep completion: daily run completes within 30 minutes at target load.

### 9.2 Required Telemetry
- Request metrics: QPS, latency, error class by tool.
- Retrieval quality metrics: hit-rate proxies, confidence distribution, top-k diversity.
- Lifecycle metrics: unconsolidated backlog, fading/expired counts, supersession rate, anti-pattern inversions.
- Infra metrics: DB query latency, index bloat, queue depth, worker lag.

### 9.3 Alerting
- Backlog age breach.
- Error-rate spikes on memory_context/search.
- Sweep failures or skipped schedules.
- DB saturation and replica lag thresholds.

## 10. Security, Privacy, and Governance
- All memory rows must have provenance (`tenant_id`, `source_butler`, source link, timestamps, request lineage when available).
- Forget/retract operations must be auditable and reversible where policy allows, backed by `memory_events`.
- Sensitive data controls:
  - Policy-based redaction before episode persistence.
  - Optional field-level encryption for high-risk predicates.
- Access policy:
  - Tool-level tenant + scope checks enforce caller boundaries.
  - Dashboard/admin endpoints require elevated authorization.

## 11. MCP Tool Surface Contract
Required stable tools:
- Writing: `memory_store_episode`, `memory_store_fact`, `memory_store_rule`
- Reading: `memory_search`, `memory_recall`, `memory_get`
- Feedback: `memory_confirm`, `memory_mark_helpful`, `memory_mark_harmful`
- Management: `memory_forget`, `memory_stats`
- Context: `memory_context`

Lineage propagation rules:
- Read/write tools should accept optional `request_context` metadata (at minimum `request_id`, optional `subrequest_id` and `segment_id`) when callers provide it.
- When `request_context.request_id` is present, responses and durable audit/event surfaces must preserve it for trace correlation.

Backward-compatibility rules:
- Existing tool names and core parameters remain stable.
- Additive enhancements must be optional and default-safe.
- Tenant selection MUST come from authenticated request context for default tools; caller-supplied free-text tenant selectors are prohibited for non-admin workflows.
- Legacy `forgotten` fact-state payloads are accepted only as compatibility input/output aliases and normalize to canonical `retracted`.

Recommended additive tools (disruptive but high value):
- `memory_context_batch(requests[])` for fanout-heavy workloads.
- `memory_ingest_batch(episodes[])` for bulk write efficiency.
- `memory_activity_stream(since, scope)` for dashboard and ops.

## 12. Rollout Plan (Target-State Landing)
1. Contract hardening:
   - Add tenant-bound schema invariants and canonical lifecycle-state normalization.
2. Read path upgrades:
   - Introduce deterministic tokenizer-based budgeting and staged retrieval/reranking behind feature flags.
3. Write path upgrades:
   - Add idempotency keys, DB uniqueness guarantees, and structured consolidation job tracking.
4. Event/audit layer:
   - Introduce mandatory `memory_events` and wire lifecycle writes.
5. Scale posture:
   - Add read replicas, queue-backed workers with shard-order guarantees, and partitioning only when trigger thresholds are met.

## 13. Target-State Deltas from Current Implementation
- Consolidation must move from "episode grouping + executor plumbing" to a fully tracked job lifecycle with retries, dead-letter handling, and idempotency keys.
- Retrieval confidence gating must use effective confidence after time decay, not only stored base confidence.
- Context assembly must graduate from simple char-budget formatting to deterministic budgeting with section-level quotas and stable ordering.
- Rule quality tracking should evolve from aggregate counters alone to per-application evidence (`rule_applications`) for better diagnostics and safer promotions.
- Memory lifecycle observability must include an append-only `memory_events` stream for replayable audits.

## 14. Non-Goals
- Turning Memory Butler into a general graph database platform.
- Allowing direct specialist-butler SQL access to memory tables.
- Replacing each butler's operational key-value state store with memory artifacts.
