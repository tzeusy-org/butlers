## ADDED Requirements

### Requirement: Tenant and request lineage on all memory tables

All three memory tables (episodes, facts, rules) SHALL include `tenant_id` (TEXT NOT NULL, DEFAULT 'owner'), `request_id` (TEXT nullable), `retention_class` (TEXT NOT NULL with type-specific defaults), and `sensitivity` (TEXT NOT NULL, DEFAULT 'normal') columns. These columns provide multi-tenant isolation, request trace correlation, policy-driven lifecycle, and data classification.

Indexes SHALL be rebuilt as tenant-scoped to make `(tenant_id, ...)` the primary access pattern for all queries.

#### Scenario: Episode lineage columns

- **WHEN** an episode is stored
- **THEN** the `episodes` table row MUST contain `tenant_id` (TEXT NOT NULL, DEFAULT 'owner'), `request_id` (TEXT nullable), `retention_class` (TEXT NOT NULL, DEFAULT 'transient'), and `sensitivity` (TEXT NOT NULL, DEFAULT 'normal')
- **AND** the index `idx_episodes_tenant_butler_status_created` MUST exist on `(tenant_id, butler, consolidation_status, created_at)`

#### Scenario: Fact lineage columns

- **WHEN** a fact is stored
- **THEN** the `facts` table row MUST contain `tenant_id` (TEXT NOT NULL, DEFAULT 'owner'), `request_id` (TEXT nullable), `retention_class` (TEXT NOT NULL, DEFAULT 'operational'), and `sensitivity` (TEXT NOT NULL, DEFAULT 'normal')
- **AND** the index `idx_facts_tenant_scope_validity` MUST exist on `(tenant_id, scope, validity) WHERE validity = 'active'`

#### Scenario: Rule lineage columns

- **WHEN** a rule is stored
- **THEN** the `rules` table row MUST contain `tenant_id` (TEXT NOT NULL, DEFAULT 'owner'), `request_id` (TEXT nullable), `retention_class` (TEXT NOT NULL, DEFAULT 'rule'), and `sensitivity` (TEXT NOT NULL, DEFAULT 'normal')
- **AND** the index `idx_rules_tenant_scope_maturity` MUST exist on `(tenant_id, scope, maturity)`

#### Scenario: Existing data backfill

- **WHEN** the migration runs against a database with existing rows
- **THEN** all existing rows in episodes, facts, and rules MUST receive `tenant_id = 'owner'` via the column DEFAULT
- **AND** no separate backfill step SHALL be required

#### Scenario: Tenant-bounded queries

- **WHEN** any search, recall, or context retrieval function is called
- **THEN** the query MUST include a `tenant_id` filter derived from the caller's context
- **AND** results from other tenants MUST NOT be returned

---

### Requirement: Consolidation state machine with lease-based claiming

The consolidation pipeline SHALL use a lease-based claiming model with `FOR UPDATE SKIP LOCKED` to support concurrent consolidation workers. Episodes SHALL progress through a strict state machine: `pending` → `consolidated` | `failed` | `dead_letter`. Every episode MUST reach exactly one terminal state.

#### Scenario: Consolidation lease columns

- **WHEN** the consolidation state machine migration runs
- **THEN** the `episodes` table MUST gain columns: `leased_until` (TIMESTAMPTZ nullable), `leased_by` (TEXT nullable), `dead_letter_reason` (TEXT nullable)
- **AND** column `retry_count` MUST be renamed to `consolidation_attempts`
- **AND** column `last_error` MUST be renamed to `last_consolidation_error`
- **AND** column `next_consolidation_retry_at` (TIMESTAMPTZ nullable) MUST be added
- **AND** a CHECK constraint MUST enforce `consolidation_status IN ('pending', 'consolidated', 'failed', 'dead_letter')`

#### Scenario: Lease-based episode claiming

- **WHEN** the consolidation worker claims episodes for processing
- **THEN** episodes MUST be claimed using `SELECT ... FROM episodes WHERE consolidation_status = 'pending' AND (leased_until IS NULL OR leased_until < now()) AND (next_consolidation_retry_at IS NULL OR next_consolidation_retry_at <= now()) ORDER BY tenant_id, butler, created_at, id FOR UPDATE SKIP LOCKED LIMIT $batch_size`
- **AND** claimed episodes MUST have `leased_until` set to `now() + lease_duration` and `leased_by` set to the worker identifier

#### Scenario: Concurrent workers do not process same episodes

- **WHEN** two consolidation workers run simultaneously
- **THEN** `FOR UPDATE SKIP LOCKED` MUST ensure each episode is claimed by at most one worker
- **AND** if a worker crashes mid-lease, the lease expires and the episode becomes claimable again

#### Scenario: Failed consolidation with retry

- **WHEN** consolidation of an episode fails
- **THEN** `consolidation_attempts` MUST be incremented
- **AND** `last_consolidation_error` MUST be set to the error message
- **AND** `next_consolidation_retry_at` MUST be set using exponential backoff: `now() + (2^attempts * base_interval)`
- **AND** `leased_until` and `leased_by` MUST be cleared

#### Scenario: Dead-letter after max retries

- **WHEN** consolidation of an episode fails and `consolidation_attempts` exceeds the maximum retry count (default 5)
- **THEN** the episode's `consolidation_status` MUST be set to `'dead_letter'`
- **AND** `dead_letter_reason` MUST be set to the error message
- **AND** the episode MUST NOT be retried by future consolidation runs

#### Scenario: Deterministic processing order

- **WHEN** consolidation claims a batch of episodes
- **THEN** episodes MUST be ordered by `(tenant_id, butler, created_at, id)` to ensure deterministic processing order within tenant/butler shards

---

### Requirement: Temporal fact idempotency and bitemporal columns

Temporal facts (facts with `valid_at IS NOT NULL`) SHALL be protected from duplicate writes via an `idempotency_key` column with a partial unique index. An `invalid_at` column SHALL enable bitemporal queries. An `observed_at` column SHALL record when the fact was first observed.

#### Scenario: Idempotency key column and index

- **WHEN** the temporal fact safety migration runs
- **THEN** the `facts` table MUST gain columns: `idempotency_key` (TEXT nullable), `observed_at` (TIMESTAMPTZ DEFAULT now()), `invalid_at` (TIMESTAMPTZ nullable)
- **AND** a partial unique index `idx_facts_temporal_idempotency` MUST exist on `(tenant_id, idempotency_key) WHERE idempotency_key IS NOT NULL`

#### Scenario: Auto-generated idempotency key for temporal facts

- **WHEN** `store_fact` is called with `valid_at IS NOT NULL` and no explicit `idempotency_key`
- **THEN** the storage layer MUST auto-generate an idempotency key as a SHA-256 hash (truncated to 32 hex chars) of `(entity_id, object_entity_id, scope, predicate, valid_at, source_episode_id)`
- **AND** if a fact with the same `(tenant_id, idempotency_key)` already exists, the INSERT MUST be a no-op (ON CONFLICT DO NOTHING) and the existing fact's ID MUST be returned

#### Scenario: Explicit idempotency key takes precedence

- **WHEN** `store_fact` is called with an explicit `idempotency_key`
- **THEN** the provided key MUST be used instead of auto-generation
- **AND** duplicate detection MUST use the provided key

#### Scenario: Property facts do not get idempotency keys

- **WHEN** `store_fact` is called with `valid_at IS NULL` (property fact)
- **THEN** `idempotency_key` MUST remain NULL
- **AND** property fact uniqueness MUST continue to be handled by the existing supersession logic

#### Scenario: Invalid_at for bitemporal queries

- **WHEN** a temporal fact is invalidated (e.g., a correction is recorded)
- **THEN** `invalid_at` MUST be set to the timestamp when the fact was known to be no longer true
- **AND** queries can use `valid_at` and `invalid_at` together to answer "what did we know at time T about time S?"

#### Scenario: Observed_at records observation time

- **WHEN** a fact is stored
- **THEN** `observed_at` MUST default to `now()` — the time the system first learned this fact
- **AND** `observed_at` is distinct from `valid_at` (which records when the fact was true in the real world) and from `created_at` (which records row insertion time)

---

### Requirement: Storage layer accepts tenant and request context

All storage write functions (`store_episode`, `store_fact`, `store_rule`) SHALL accept optional `tenant_id` (default `'owner'`) and `request_id` parameters. These values SHALL be persisted on the stored row and propagated to downstream operations (links, events).

#### Scenario: store_episode with tenant context

- **WHEN** `store_episode` is called with `tenant_id='owner'` and `request_id='req-abc-123'`
- **THEN** the inserted episode row MUST have `tenant_id = 'owner'` and `request_id = 'req-abc-123'`

#### Scenario: store_fact with tenant context

- **WHEN** `store_fact` is called with `tenant_id='owner'`
- **THEN** the inserted fact row MUST have `tenant_id = 'owner'`
- **AND** the supersession check MUST be scoped to the same `tenant_id`

#### Scenario: store_rule with tenant context

- **WHEN** `store_rule` is called with `tenant_id='owner'`
- **THEN** the inserted rule row MUST have `tenant_id = 'owner'`

#### Scenario: Default tenant is owner

- **WHEN** any store function is called without an explicit `tenant_id`
- **THEN** the row MUST be stored with `tenant_id = 'owner'`

---

### Requirement: Search and recall use effective_confidence for scoring and filtering

The `recall()` and `search()` functions SHALL compute `effective_confidence` (decayed) for each result and use the decayed value for both threshold filtering and composite scoring. Raw `confidence` SHALL NOT be used for ranking or filtering.

#### Scenario: Recall uses effective_confidence in composite scoring

- **WHEN** `recall()` computes the composite score for a fact or rule
- **THEN** the `effective_confidence` MUST be computed as `confidence * exp(-decay_rate * days_since_last_confirmed)`
- **AND** the composite score MUST use `effective_confidence` (not raw `confidence`) in the formula: `0.4 * relevance + 0.3 * (importance / 10.0) + 0.2 * recency + 0.1 * effective_confidence`

#### Scenario: Recall filters by effective_confidence threshold

- **WHEN** `recall()` applies the `min_confidence` threshold
- **THEN** the comparison MUST use `effective_confidence` (decayed), not raw `confidence`
- **AND** facts/rules whose effective_confidence has decayed below `min_confidence` MUST be excluded from results

#### Scenario: Search filters by effective_confidence

- **WHEN** `search()` applies `min_confidence` filtering
- **THEN** the comparison MUST use `effective_confidence` (decayed) for facts and rules
- **AND** episodes (which have no confidence/decay) MUST NOT be filtered by confidence

#### Scenario: Permanent facts are unaffected

- **WHEN** a fact has `decay_rate = 0.0` (permanent)
- **THEN** `effective_confidence` MUST equal raw `confidence`
- **AND** the fact MUST NOT be penalized by the decayed scoring

---

### Requirement: MCP tools accept request_context and structured filters

Write tools SHALL accept an optional `request_context` dict containing `request_id` and `tenant_id`. Read tools SHALL accept a structured `filters` dict for advanced filtering. The `memory_context` tool SHALL accept `include_recent_episodes` and `request_context` parameters. All new parameters are additive and optional with safe defaults.

#### Scenario: Write tools accept request_context

- **WHEN** `memory_store_episode`, `memory_store_fact`, or `memory_store_rule` is called with `request_context={"request_id": "req-123", "tenant_id": "owner"}`
- **THEN** the `request_id` and `tenant_id` MUST be extracted from request_context and passed to the storage layer
- **AND** if `request_context` is None, `tenant_id` MUST default to `'owner'` and `request_id` MUST default to None

#### Scenario: Write tools accept retention_class and sensitivity

- **WHEN** `memory_store_fact` is called with `retention_class='health_log'` and `sensitivity='pii'`
- **THEN** the stored fact MUST have `retention_class = 'health_log'` and `sensitivity = 'pii'`
- **AND** if omitted, `retention_class` MUST use the type-specific default and `sensitivity` MUST default to `'normal'`

#### Scenario: Read tools accept structured filters

- **WHEN** `memory_search` or `memory_recall` is called with `filters={"scope": "health", "entity_id": "<uuid>", "predicate": "weight", "time_from": "2026-01-01", "retention_class": "health_log"}`
- **THEN** all provided filter keys MUST be applied as AND conditions in the query
- **AND** unrecognized filter keys MUST be silently ignored
- **AND** if `filters` is None, no additional filtering MUST be applied (backward compatible)

#### Scenario: memory_context accepts include_recent_episodes

- **WHEN** `memory_context` is called with `include_recent_episodes=True`
- **THEN** the context output MUST include a `## Recent Episodes` section containing the most recent episodes for the butler
- **AND** episodes MUST be ordered by `created_at DESC` and limited by the section's quota allocation

#### Scenario: memory_context accepts request_context

- **WHEN** `memory_context` is called with `request_context={"request_id": "req-456"}`
- **THEN** the `request_id` MUST be available for trace correlation in audit logs
- **AND** `tenant_id` from request_context MUST scope the recall query

---

## MODIFIED Requirements

### Requirement: Three memory types with distinct schemas and lifecycles

The module SHALL support three primary memory types: episodes (high-volume, short-lived session observations), facts (durable subject-predicate-content semantic knowledge), and rules (behavioral guidance learned from repeated outcomes). Each type SHALL have its own PostgreSQL table with type-specific columns, lifecycle states, and retrieval semantics.

#### Scenario: Episode schema and defaults

- **WHEN** an episode is stored
- **THEN** the `episodes` table row MUST contain: `id` (UUID PK), `tenant_id` (TEXT NOT NULL, DEFAULT 'owner'), `butler` (TEXT NOT NULL), `session_id` (UUID nullable), `content` (TEXT NOT NULL), `embedding` (vector(384)), `search_vector` (tsvector), `importance` (FLOAT, default 5.0), `reference_count` (INTEGER, default 0), `consolidated` (BOOLEAN, default false), `consolidation_status` (VARCHAR(20), default 'pending'), `consolidation_attempts` (INTEGER, default 0), `last_consolidation_error` (TEXT nullable), `next_consolidation_retry_at` (TIMESTAMPTZ nullable), `leased_until` (TIMESTAMPTZ nullable), `leased_by` (TEXT nullable), `dead_letter_reason` (TEXT nullable), `request_id` (TEXT nullable), `retention_class` (TEXT NOT NULL, DEFAULT 'transient'), `sensitivity` (TEXT NOT NULL, DEFAULT 'normal'), `created_at` (TIMESTAMPTZ), `last_referenced_at` (TIMESTAMPTZ nullable), `expires_at` (TIMESTAMPTZ), `metadata` (JSONB, default '{}')
- **AND** a CHECK constraint MUST enforce `consolidation_status IN ('pending', 'consolidated', 'failed', 'dead_letter')`

#### Scenario: Fact schema and defaults

- **WHEN** a fact is stored
- **THEN** the `facts` table row MUST contain: `id` (UUID PK), `tenant_id` (TEXT NOT NULL, DEFAULT 'owner'), `subject` (TEXT NOT NULL), `predicate` (TEXT NOT NULL), `content` (TEXT NOT NULL), `embedding` (vector(384)), `search_vector` (tsvector), `importance` (FLOAT, default 5.0), `confidence` (FLOAT, default 1.0), `decay_rate` (FLOAT, default 0.008), `permanence` (TEXT, default 'standard'), `source_butler` (TEXT nullable), `source_episode_id` (UUID FK to episodes, ON DELETE SET NULL), `supersedes_id` (UUID self-FK, ON DELETE SET NULL), `entity_id` (UUID FK to shared.entities, ON DELETE RESTRICT, nullable), `object_entity_id` (UUID FK to shared.entities, ON DELETE RESTRICT, nullable), `validity` (TEXT, default 'active'), `scope` (TEXT, default 'global'), `valid_at` (TIMESTAMPTZ nullable, default NULL), `invalid_at` (TIMESTAMPTZ nullable), `idempotency_key` (TEXT nullable), `observed_at` (TIMESTAMPTZ DEFAULT now()), `request_id` (TEXT nullable), `retention_class` (TEXT NOT NULL, DEFAULT 'operational'), `sensitivity` (TEXT NOT NULL, DEFAULT 'normal'), `reference_count` (INTEGER, default 0), `created_at` (TIMESTAMPTZ), `last_referenced_at` (TIMESTAMPTZ nullable), `last_confirmed_at` (TIMESTAMPTZ nullable), `tags` (JSONB, default '[]'), `metadata` (JSONB, default '{}')

#### Scenario: Rule schema and defaults

- **WHEN** a rule is stored
- **THEN** the `rules` table row MUST contain: `id` (UUID PK), `tenant_id` (TEXT NOT NULL, DEFAULT 'owner'), `content` (TEXT NOT NULL), `embedding` (vector(384)), `search_vector` (tsvector), `scope` (TEXT, default 'global'), `maturity` (TEXT, default 'candidate'), `confidence` (FLOAT, default 0.5), `decay_rate` (FLOAT, default 0.008), `permanence` (TEXT, default 'standard'), `effectiveness_score` (FLOAT, default 0.0), `applied_count` (INTEGER, default 0), `success_count` (INTEGER, default 0), `harmful_count` (INTEGER, default 0), `source_episode_id` (UUID FK to episodes), `source_butler` (TEXT nullable), `request_id` (TEXT nullable), `retention_class` (TEXT NOT NULL, DEFAULT 'rule'), `sensitivity` (TEXT NOT NULL, DEFAULT 'normal'), `created_at` (TIMESTAMPTZ), `last_applied_at` (TIMESTAMPTZ nullable), `last_evaluated_at` (TIMESTAMPTZ nullable), `last_confirmed_at` (TIMESTAMPTZ nullable), `reference_count` (INTEGER, default 0), `last_referenced_at` (TIMESTAMPTZ nullable), `tags` (JSONB, default '[]'), `metadata` (JSONB, default '{}')

---

### Requirement: Composite scoring for recall

The `memory_recall` tool SHALL use composite scoring combining relevance, importance, recency, and effective confidence with configurable weights. The default weights SHALL be: relevance=0.4, importance=0.3, recency=0.2, confidence=0.1. The confidence component MUST use `effective_confidence` (decayed), not raw `confidence`.

#### Scenario: Composite score formula

- **WHEN** a recall query is executed
- **THEN** the composite score MUST be calculated as: `0.4 * relevance + 0.3 * (importance / 10.0) + 0.2 * recency + 0.1 * effective_confidence`
- **AND** `effective_confidence` MUST be computed as `confidence * exp(-decay_rate * days_since_last_confirmed)` for each result
- **AND** the `relevance` component MUST be derived from the normalized RRF score (capped at 1.0)
- **AND** the `importance` MUST be normalized from 0-10 to 0-1 by dividing by 10
- **AND** results MUST be filtered by `min_confidence` threshold (default 0.2) applied against `effective_confidence`, not raw `confidence`

#### Scenario: Recency score uses exponential decay

- **WHEN** computing the recency score for a memory
- **THEN** the recency MUST follow exponential decay with a 7-day half-life
- **AND** `recency = exp(-ln(2)/7 * days_since_last_referenced)`
- **AND** if `last_referenced_at` is None, recency MUST be 0.0
- **AND** the score MUST be clamped to [0.0, 1.0]

#### Scenario: Recall bumps reference counts

- **WHEN** `recall` returns results
- **THEN** each returned result MUST have its `reference_count` incremented by 1
- **AND** `last_referenced_at` MUST be updated to now

---

### Requirement: Context injection via memory_context

The `memory_context` tool SHALL build a deterministic, sectioned text block for injection into a runtime system prompt. The output SHALL be divided into fixed sections with quota-based budget allocation and stable tie-breaking.

#### Scenario: Context section structure

- **WHEN** `memory_context` is called
- **THEN** the output MUST begin with `# Memory Context\n`
- **AND** the output MUST contain up to four sections in order: `## Profile Facts` (30% of budget), `## Task-Relevant Facts` (35% of budget), `## Active Rules` (20% of budget), and optionally `## Recent Episodes` (15% of budget, only when `include_recent_episodes=True`)
- **AND** each section MUST respect its quota allocation from the total token budget
- **AND** empty sections (no results) MUST be omitted entirely

#### Scenario: Profile facts section

- **WHEN** the `## Profile Facts` section is assembled
- **THEN** it MUST contain facts about the owner entity (identified via `shared.entities WHERE 'owner' = ANY(roles)`)
- **AND** facts MUST be sorted by importance DESC, then created_at DESC, then id ASC
- **AND** each line MUST be formatted as `- [{subject}] [{predicate}]: {content} (confidence: {effective_confidence:.2f})`

#### Scenario: Task-relevant facts section

- **WHEN** the `## Task-Relevant Facts` section is assembled
- **THEN** it MUST contain facts matching the trigger_prompt via composite-scored recall (excluding facts already shown in Profile Facts)
- **AND** facts MUST be sorted by composite_score DESC, created_at DESC, id ASC

#### Scenario: Active rules section

- **WHEN** the `## Active Rules` section is assembled
- **THEN** rules MUST be sorted by maturity rank (proven=3, established=2, candidate=1) DESC, then effectiveness_score DESC, then created_at DESC, then id ASC
- **AND** each line MUST be formatted as `- {content} (maturity: {maturity}, effectiveness: {effectiveness:.2f})`

#### Scenario: Recent episodes section (opt-in)

- **WHEN** `memory_context` is called with `include_recent_episodes=True`
- **THEN** a `## Recent Episodes` section MUST be included containing the most recent episodes for the butler
- **AND** episodes MUST be ordered by `created_at DESC`
- **AND** when `include_recent_episodes` is False or omitted, this section MUST NOT appear

#### Scenario: Token budget enforcement

- **WHEN** `memory_context` assembles the output
- **THEN** the total output MUST NOT exceed `token_budget * 4` characters
- **AND** each section MUST NOT exceed its percentage quota of the total budget
- **AND** items MUST be added within each section in deterministic order until the section quota is exhausted
- **AND** the default token budget MUST come from `config.retrieval.context_token_budget` (default 3000)

#### Scenario: Deterministic tie-breaking

- **WHEN** two items within a section have the same primary sort key
- **THEN** tie-breaking MUST use `created_at DESC`, then `id ASC`
- **AND** the same inputs MUST always produce the same output ordering

#### Scenario: Scope filtering via butler name

- **WHEN** `memory_context` is called with a butler name
- **THEN** the recall query MUST use the butler name as the scope filter

#### Scenario: Request context propagation

- **WHEN** `memory_context` is called with `request_context`
- **THEN** `tenant_id` from request_context MUST scope all retrieval queries
- **AND** `request_id` from request_context MUST be available for trace correlation

---

### Requirement: LLM-driven memory consolidation pipeline

The consolidation pipeline SHALL transform unconsolidated episodes into durable facts and rules via a multi-step process: claim pending episodes via lease, group by source butler, build a prompt with existing context, spawn an LLM CLI session, parse the structured JSON output, and execute the extracted actions against the database. Existing rules for dedup context SHALL be fetched using the `maturity` column (not `status`).

#### Scenario: Episode grouping and prompt construction

- **WHEN** `run_consolidation` is called
- **THEN** episodes MUST be claimed via `FOR UPDATE SKIP LOCKED` ordered by `(tenant_id, butler, created_at, id)`
- **AND** episodes MUST be grouped by `butler` name
- **AND** existing active facts (up to 100) for each butler MUST be fetched with `WHERE validity = 'active' AND source_butler = $1`
- **AND** existing active rules for each butler MUST be fetched with `WHERE maturity NOT IN ('anti_pattern') AND (metadata->>'forgotten')::boolean IS NOT TRUE AND source_butler = $1` (using the `maturity` column, NOT a `status` column)
- **AND** a consolidation prompt MUST be built combining the SKILL.md template with episode content, existing facts, and existing rules

#### Scenario: Consolidation with LLM spawner

- **WHEN** a `cc_spawner` is provided to `run_consolidation`
- **THEN** for each butler group, a runtime session MUST be spawned with `trigger_source='schedule:consolidation'`
- **AND** the runtime output MUST be parsed for a JSON block containing `new_facts`, `updated_facts`, `new_rules`, and `confirmations`
- **AND** partial failures in one butler group MUST NOT block other groups from processing

#### Scenario: Consolidation without spawner (dry run)

- **WHEN** `run_consolidation` is called with `cc_spawner=None`
- **THEN** only episode grouping and counting MUST be performed
- **AND** no actual consolidation MUST occur

#### Scenario: Episode content wrapped in XML tags for prompt injection prevention

- **WHEN** episode content is formatted for the consolidation prompt
- **THEN** each episode's content MUST be wrapped in `<episode_content>` XML tags
- **AND** the SKILL.md MUST contain a security notice instructing the LLM to treat episode content as data only

---

### Requirement: Storage layer CRUD operations

The storage layer SHALL provide async functions for creating episodes, facts, rules, and memory links, as well as retrieving, confirming, soft-deleting (forgetting), and applying feedback to memory items. All functions accept an asyncpg connection pool. Write functions SHALL accept optional `tenant_id` (default `'owner'`) and `request_id` parameters.

#### Scenario: Store episode with embedding and search vector

- **WHEN** `store_episode` is called with content, butler name, and optional `tenant_id` and `request_id`
- **THEN** a new episode row MUST be inserted with a generated UUID, computed embedding, computed tsvector, `tenant_id` (default `'owner'`), `request_id`, and `expires_at` determined by the retention policy for the episode's `retention_class` (falling back to 7 days if no policy exists)
- **AND** the function MUST return the new episode's UUID

#### Scenario: Store fact with supersession check

- **WHEN** `store_fact` is called and an active fact with the same uniqueness key exists within the same `tenant_id`
- **THEN** the existing active fact MUST be marked `validity='superseded'`
- **AND** the new fact MUST have `supersedes_id` set to the old fact's ID
- **AND** a `memory_links` row with `relation='supersedes'` MUST be created linking new to old
- **AND** the entire operation MUST execute within a single database transaction

#### Scenario: Store fact with entity_id validation

- **WHEN** `store_fact` is called with an `entity_id` that does not exist in the `entities` table
- **THEN** a `ValueError` MUST be raised stating the entity does not exist

#### Scenario: Store edge fact with object_entity_id validation

- **WHEN** `store_fact` is called with an `object_entity_id` that does not exist in the `entities` table
- **THEN** a `ValueError` MUST be raised stating the object entity does not exist

#### Scenario: Store edge fact with self-referencing edge rejected

- **WHEN** `store_fact` is called with `entity_id` equal to `object_entity_id`
- **THEN** a `ValueError` MUST be raised stating that self-referencing edges are not allowed

#### Scenario: Store edge fact requires entity_id

- **WHEN** `store_fact` is called with `object_entity_id` set but `entity_id` is NULL
- **THEN** a `ValueError` MUST be raised stating that edge facts require a subject entity (`entity_id`)

#### Scenario: Store rule as candidate

- **WHEN** `store_rule` is called with optional `tenant_id` and `request_id`
- **THEN** a new rule row MUST be inserted with `maturity='candidate'`, `confidence=0.5`, `decay_rate=0.01`, `effectiveness_score=0.0`, all counts set to 0, and the provided `tenant_id` (default `'owner'`)

#### Scenario: Memory link creation with validation

- **WHEN** `create_link` is called
- **THEN** the `relation` MUST be one of `derived_from`, `supports`, `contradicts`, `supersedes`, `related_to`
- **AND** both `source_type` and `target_type` MUST be one of `episode`, `fact`, `rule`
- **AND** the insert MUST use `ON CONFLICT DO NOTHING` for idempotency

#### Scenario: Get memory with reference bump

- **WHEN** `get_memory` is called for a valid memory type and ID
- **THEN** the function MUST atomically increment `reference_count` by 1 and set `last_referenced_at` to now
- **AND** return the updated full row as a dict
- **AND** return `None` if the row does not exist

#### Scenario: Confirm memory resets decay timer

- **WHEN** `confirm_memory` is called for a fact or rule
- **THEN** `last_confirmed_at` MUST be updated to now, effectively resetting the confidence decay timer
- **AND** confirming an episode MUST raise `ValueError`

#### Scenario: Forget episode sets immediate expiry

- **WHEN** `forget_memory` is called with `memory_type='episode'`
- **THEN** the episode's `expires_at` MUST be set to `now()`

#### Scenario: Forget rule sets metadata flag

- **WHEN** `forget_memory` is called with `memory_type='rule'`
- **THEN** the rule's `metadata` MUST have `forgotten` set to `true`

---

### Requirement: MCP tool registration surface

The Memory module SHALL register MCP tools on the hosting butler's MCP server when the module is enabled. Tool closures SHALL inject `pool` and `embedding_engine` from module state so these are not visible in the MCP tool signature.

#### Scenario: Writing tools registered

- **WHEN** the memory module registers tools
- **THEN** tools `memory_store_episode`, `memory_store_fact`, and `memory_store_rule` MUST be available
- **AND** `memory_store_episode` MUST accept `content` (required), `butler` (required), `session_id` (optional), `importance` (optional, default 5.0), `request_context` (optional dict)
- **AND** `memory_store_fact` MUST accept `subject`, `predicate`, `content` (required), `importance` (default 5.0), `permanence` (default 'standard'), `scope` (default 'global'), `valid_at` (optional TIMESTAMPTZ, default NULL), `tags` (optional list), `entity_id` (optional UUID), `object_entity_id` (optional UUID), `request_context` (optional dict), `retention_class` (optional), `sensitivity` (optional)
- **AND** `memory_store_rule` MUST accept `content` (required), `scope` (default 'global'), `tags` (optional list), `request_context` (optional dict), `retention_class` (optional)

#### Scenario: Reading tools registered

- **WHEN** the memory module registers tools
- **THEN** tools `memory_search`, `memory_recall`, and `memory_get` MUST be available
- **AND** `memory_search` MUST accept `query` (required), `types` (optional list), `scope` (optional), `mode` (default 'hybrid'), `limit` (default 10), `min_confidence` (default 0.2), `filters` (optional dict)
- **AND** `memory_recall` MUST accept `topic` (required), `scope` (optional), `limit` (default 10), `filters` (optional dict), `request_context` (optional dict)
- **AND** `memory_get` MUST accept `memory_type` (required) and `memory_id` (required)

#### Scenario: Context tool registered

- **WHEN** the memory module registers tools
- **THEN** tool `memory_context` MUST be available
- **AND** it MUST accept `trigger_prompt` (required), `butler` (required), `token_budget` (default from config), `include_recent_episodes` (default False), `request_context` (optional dict)

#### Scenario: Feedback tools registered

- **WHEN** the memory module registers tools
- **THEN** tools `memory_confirm`, `memory_mark_helpful`, and `memory_mark_harmful` MUST be available
- **AND** `memory_confirm` MUST accept `memory_type` and `memory_id`
- **AND** `memory_mark_helpful` MUST accept `rule_id`
- **AND** `memory_mark_harmful` MUST accept `rule_id` and optional `reason`

#### Scenario: Management tools registered

- **WHEN** the memory module registers tools
- **THEN** tools `memory_forget` and `memory_stats` MUST be available
- **AND** `memory_forget` MUST accept `memory_type` and `memory_id`
- **AND** `memory_stats` MUST accept optional `scope`

#### Scenario: Entity tools registered

- **WHEN** the memory module registers tools
- **THEN** tools `entity_create`, `entity_get`, `entity_update`, `entity_resolve`, `entity_merge`, and `entity_neighbors` MUST be available
- **AND** `entity_create` MUST accept `canonical_name`, `entity_type` (required), `tenant_id` (default `'shared'`), `aliases` (optional), `metadata` (optional)
- **AND** `entity_resolve` MUST accept `name` (required), `tenant_id` (default `'shared'`), `entity_type` (optional), `context_hints` (optional), `enable_fuzzy` (default False)
- **AND** `entity_neighbors` MUST accept `entity_id` (required UUID), `max_depth` (default 1, max 5), `predicate_filter` (optional list), `direction` (default 'both')

#### Scenario: Consolidation and cleanup tools registered

- **WHEN** the memory module registers tools
- **THEN** tools `memory_run_consolidation` and `memory_run_episode_cleanup` MUST be available
- **AND** `memory_run_episode_cleanup` MUST accept `max_entries` (default 10000)
