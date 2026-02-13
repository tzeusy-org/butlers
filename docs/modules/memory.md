# Memory Module: Permanent Definition

Status: Normative (Target State)
Last updated: 2026-02-13
Primary owner: Platform/Core

## 1. Module
The Memory module is a reusable module that relevant butlers load locally.

It is responsible for:
- Persisting memory artifacts (`episodes`, `facts`, `rules`) with provenance in the hosting butler's database.
- Serving low-latency retrieval for runtime context injection.
- Running lifecycle maintenance (consolidation, decay, cleanup, supersession).
- Enforcing memory quality and safety contracts (confidence decay, anti-pattern learning, retract flows).

This document is the authoritative target-state contract for memory behavior when the module is enabled.

## 2. Design Goals
- Keep memory architecture aligned with platform isolation: each butler owns its own DB and memory data.
- Make memory available as a common module, not a special infrastructure service.
- Preserve accuracy and provenance while allowing memory to evolve and decay.
- Keep retrieval low-latency and fail-open for user-facing workflows.
- Make memory transparent and governable through auditable lifecycle transitions.

## 3. Applicability and Boundaries
### In scope
- Module configuration and tool registration contract.
- Memory schema and lifecycle policy for hosting butler DBs.
- Retrieval behavior, context assembly, and scoring.
- Consolidation and hygiene workers executed inside hosting butler runtime.
- Read model support for dashboard memory views.

### Out of scope
- Dedicated shared memory-role process.
- Direct cross-butler memory SQL access.
- Channel-specific delivery behavior (Telegram/Email UX).

## 4. Runtime Architecture Contract
### 4.1 Local components (per hosting butler)
- `Memory tools`: module-registered MCP tools on the hosting butler MCP server.
- `Consolidation worker`: transforms episodes into facts/rules and confirmations.
- `Decay and hygiene worker`: confidence decay, fading/expiry transitions, cleanup.
- `Embedding engine`: local embedding generation, model/version managed by module config.
- `Memory tables`: module-owned tables in the hosting butler DB.

### 4.2 Mandatory runtime flows
1. `Session start (read path)`
- Hosting daemon retrieves memory context with `memory_context(trigger_prompt, butler, token_budget, request_context?)`.
- Module resolves tenant/request lineage from authenticated context and returns ordered facts/rules (+ optional recent episodes) within budget.
2. `Session completion (write path)`
- Hosting daemon stores an episode via `memory_store_episode(...)`.
- Episode becomes eligible for asynchronous consolidation.
3. `Consolidation`
- Worker consumes unconsolidated episodes in deterministic batches.
- Worker emits new facts/rules, supersessions, confirmations, and provenance links.
4. `Lifecycle maintenance`
- Scheduled sweeps update fading/expired states and perform episode retention cleanup.

### 4.3 Determinism and isolation
- All retrieval and lifecycle operations MUST be tenant-bounded by default.
- Worker execution MUST preserve deterministic ordering within `(tenant_id, butler)` shard keys.
- Data lives in the hosting butler DB; memory rows do not cross butler DB boundaries.

### 4.4 Reliability
- Memory retrieval/storage failures must be fail-open for runtime execution (log and continue).
- Module internals must fail-closed on integrity-invariant violations (invalid transitions, broken schema constraints, provenance corruption).
- Lifecycle workers must be idempotent.

## 5. Data Model Contract
The module defines three primary memory classes plus provenance/audit tables.

### 5.1 Episodes (observations)
- Purpose: high-volume, short-lived session observations.
- Required fields: `id`, `tenant_id`, `butler`, `session_id`, `content`, `importance`, `consolidated`, `consolidation_status`, `consolidation_attempts`, `last_consolidation_error`, `next_consolidation_retry_at`, `created_at`, `expires_at`, `metadata`.
- Lifecycle: TTL-managed; unconsolidated rows are protected from capacity cleanup until expiry.
- Consolidation states: `pending -> consolidated|failed|dead_letter`.

### 5.2 Facts (semantic memory)
- Purpose: durable subject-predicate-content knowledge.
- Required fields: `id`, `tenant_id`, `subject`, `predicate`, `content`, `scope`, `validity`, `confidence`, `decay_rate`, `permanence`, `source_butler`, `source_episode_id`, `supersedes_id`, `created_at`, `last_confirmed_at`, `last_referenced_at`, `metadata`, `tags`.
- Lifecycle states: `active`, `fading`, `superseded`, `expired`, `retracted`.
- Backward compatibility: legacy `forgotten` MUST normalize to canonical `retracted`.
- Constraint: for active facts, `(tenant_id, scope, subject, predicate)` uniqueness MUST be DB-enforced (partial unique index).

### 5.3 Rules (procedural memory)
- Purpose: behavior guidance learned from repeated outcomes.
- Required fields: `id`, `tenant_id`, `content`, `scope`, `maturity`, `confidence`, `decay_rate`, `effectiveness_score`, `applied_count`, `success_count`, `harmful_count`, `source_butler`, `source_episode_id`, `created_at`, `last_applied_at`, `last_evaluated_at`, `last_confirmed_at`, `metadata`, `tags`.
- Maturity states: `candidate`, `established`, `proven`, `anti_pattern`.
- Feedback policy: harmful evidence is weighted more heavily than helpful evidence.

### 5.4 Provenance and audit surfaces
- `memory_links`: relation edges (`derived_from`, `supports`, `contradicts`, `supersedes`, `related_to`).
- `memory_events`: append-only audit stream for all memory mutations/lifecycle transitions.
- `rule_applications`: per-application outcome records.
- `embedding_versions`: model/version tracking and re-embed migrations.

## 6. Retrieval and Context Contract
### 6.1 Retrieval modes
- `semantic`: vector similarity.
- `keyword`: PostgreSQL full-text.
- `hybrid`: reciprocal-rank fusion over semantic and keyword.

### 6.2 Scoring contract
Composite score combines relevance, importance, recency, and effective confidence.

Baseline formula:
`score = 0.4*relevance + 0.3*importance + 0.2*recency + 0.1*effective_confidence`

Confidence decay:
`effective_confidence = confidence * exp(-decay_rate * days_since_last_confirmed)`

### 6.3 Scope contract
- Reads/writes are tenant-bounded by default.
- Within a butler, scope supports `global` plus role-local scopes.
- Cross-butler memory access is not a direct data-plane feature; it requires explicit routed/tool-level integration.

### 6.4 Context assembly contract
- `memory_context` output must be deterministic and sectioned:
- Facts (highest priority first).
- Rules (ordered by maturity and score).
- Optional recent episodes.
- Hard token budget enforcement is mandatory and must use a deterministic tokenizer.
- Stable ordering tie-breakers: `score DESC`, then `created_at DESC`, then `id ASC`.
- Section quotas must be deterministic and configurable.

## 7. Write, Consolidation, and Lifecycle Contract
### 7.1 Write semantics
- `memory_store_episode` is append-only.
- `memory_store_fact` supports supersession and provenance linking.
- `memory_store_rule` initializes `candidate` maturity and baseline confidence.
- `memory_confirm` updates confirmation anchors.
- `memory_mark_helpful` and `memory_mark_harmful` drive effectiveness/maturity transitions.
- `memory_forget` emits an audit event and applies canonical lifecycle transitions.

### 7.2 Consolidation contract
- Consolidation runs in bounded batches with deterministic ordering.
- Every episode entering consolidation must reach exactly one terminal state: `consolidated`, `failed`, or `dead_letter`.
- Consolidation output must be schema-validated before persistence.
- Duplicate extraction must be controlled through idempotency keys and DB uniqueness constraints.

### 7.3 Decay and hygiene contract
- Daily decay sweep computes effective confidence for facts/rules.
- Threshold transitions:
- `>= retrieval_threshold`: normal.
- `< retrieval_threshold and >= expiry_threshold`: fading.
- `< expiry_threshold`: expired/retracted per type policy.
- Repeated harmful, low-effectiveness rules become `anti_pattern` warnings.
- Episode cleanup removes expired rows and enforces capacity starting with oldest consolidated rows.

## 8. Module Configuration Contract
Module config is declared under `[modules.memory]` in each hosting butler's `butler.toml`.

Required/expected settings:
- Embedding model and dimensions.
- Episode retention defaults (`default_ttl_days`, `max_entries`).
- Fact/rule confidence thresholds.
- Rule maturity promotion and anti-pattern thresholds.
- Retrieval defaults (mode, limits, token budget, scoring weights).
- Schedule configuration for consolidation, decay sweep, and episode cleanup.

## 9. MCP Tool Surface Contract
Memory tools are registered on each hosting butler MCP server when the module is enabled.

Required stable tools:
- Writing: `memory_store_episode`, `memory_store_fact`, `memory_store_rule`
- Reading: `memory_search`, `memory_recall`, `memory_get`
- Feedback: `memory_confirm`, `memory_mark_helpful`, `memory_mark_harmful`
- Management: `memory_forget`, `memory_stats`
- Context: `memory_context`

Lineage propagation rules:
- Read/write tools should accept optional `request_context` metadata.
- If `request_context.request_id` is present, responses and durable audit/event surfaces must preserve it for trace correlation.

Backward-compatibility rules:
- Existing tool names and core parameters remain stable.
- Additive enhancements must be optional and default-safe.
- Tenant selection must come from authenticated request context for default workflows.

## 10. Non-Goals
- Reintroducing a dedicated shared memory role/service.
- Allowing direct specialist-butler SQL access to another butler's memory tables.
- Replacing each butler's operational key-value state with memory artifacts.
