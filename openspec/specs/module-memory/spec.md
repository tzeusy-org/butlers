# Memory Module

## Purpose

The Memory module is a reusable, opt-in module that hosting butlers load locally, providing persistent storage of episodes, facts, and rules with provenance; low-latency retrieval for runtime context injection; LLM-driven consolidation of episodes into durable knowledge; and lifecycle maintenance including confidence decay, fading/expiry transitions, and episode cleanup.

## ADDED Requirements

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

### Requirement: Fact validity lifecycle states

Facts SHALL progress through validity lifecycle states: `active`, `fading` (tracked via metadata status), `superseded`, `expired`, and `retracted`. The `fading` state is encoded as `metadata->>'status' = 'fading'` while `validity` remains `'active'`. Only facts with `validity = 'active'` SHALL be returned in search and retrieval operations.

#### Scenario: Fact starts as active

- **WHEN** a new fact is stored via `store_fact`
- **THEN** the fact's `validity` MUST be `'active'`
- **AND** the fact's `confidence` MUST be `1.0`

#### Scenario: Fact transitions to superseded (property fact)

- **WHEN** a new property fact (with `object_entity_id IS NULL`) is stored with the same `(subject, predicate)` key as an existing active property fact (when `entity_id` is NULL), or the same `(entity_id, scope, predicate)` key (when `entity_id` is set and `object_entity_id` is NULL)
- **THEN** the existing fact's `validity` MUST be set to `'superseded'`
- **AND** the new fact's `supersedes_id` MUST reference the old fact's `id`
- **AND** a `memory_links` row with `relation='supersedes'` MUST be created

#### Scenario: Fact transitions to superseded (edge fact)

- **WHEN** a new edge fact (with `object_entity_id IS NOT NULL`) is stored with the same `(entity_id, object_entity_id, scope, predicate)` key as an existing active edge fact
- **THEN** the existing edge fact's `validity` MUST be set to `'superseded'`
- **AND** the new fact's `supersedes_id` MUST reference the old fact's `id`
- **AND** a `memory_links` row with `relation='supersedes'` MUST be created

#### Scenario: Temporal fact storage (multi-valued properties with history)

- **WHEN** a fact is stored with `valid_at` set to a non-NULL TIMESTAMPTZ value
- **THEN** the fact is treated as a temporal fact representing the state at that specific point in time
- **AND** multiple temporal facts with the same `(subject, predicate)` or `(entity_id, scope, predicate)` key but different `valid_at` values MAY coexist as active facts
- **AND** temporal facts MUST NOT supersede each other based solely on the uniqueness key — they form a temporal sequence

#### Scenario: Temporal vs property fact supersession distinction

- **WHEN** a new property fact (with `valid_at IS NULL`) is stored with the same uniqueness key as an existing active property fact (also with `valid_at IS NULL`)
- **THEN** the old property fact is superseded (current behavior applies)
- **AND** if a new property fact (valid_at IS NULL) is stored and an active temporal fact (valid_at IS NOT NULL) exists with the same uniqueness key
- **THEN** the temporal fact MUST remain active (not superseded) — property and temporal facts coexist
- **AND** if a new temporal fact (valid_at = T1) is stored and an active temporal fact (valid_at = T2, T2 ≠ T1) exists with the same uniqueness key
- **THEN** both facts remain active — temporal facts do not supersede based on valid_at differences

#### Scenario: Fact transitions to retracted via forget

- **WHEN** `memory_forget` is called with `memory_type='fact'`
- **THEN** the fact's `validity` MUST be set to `'retracted'`

#### Scenario: Fact transitions to expired via decay sweep

- **WHEN** the decay sweep computes effective confidence below 0.05 for a fact
- **THEN** the fact's `validity` MUST be set to `'expired'`

#### Scenario: Fact transitions to fading via decay sweep

- **WHEN** the decay sweep computes effective confidence >= 0.05 and < 0.2 for a fact
- **THEN** the fact's `metadata` MUST have `status` set to `'fading'`
- **AND** the fact's `validity` MUST remain `'active'`

---

### Requirement: Rule maturity progression

Rules SHALL progress through maturity states: `candidate`, `established`, `proven`, and `anti_pattern`. Progression is driven by effectiveness feedback (helpful/harmful marks). New rules always start as `candidate` with `confidence=0.5` and `effectiveness_score=0.0`.

#### Scenario: Rule promotion from candidate to established

- **WHEN** a rule has `success_count >= 5` AND `effectiveness_score >= 0.6`
- **THEN** the rule's `maturity` MUST be promoted to `'established'`

#### Scenario: Rule promotion from established to proven

- **WHEN** a rule has `success_count >= 15` AND `effectiveness_score >= 0.8` AND age >= 30 days
- **THEN** the rule's `maturity` MUST be promoted to `'proven'`

#### Scenario: Rule demotion from established to candidate

- **WHEN** a rule is marked harmful and the recalculated `effectiveness_score < 0.6`
- **THEN** the rule's `maturity` MUST be demoted to `'candidate'`

#### Scenario: Rule demotion from proven to established

- **WHEN** a rule is marked harmful and the recalculated `effectiveness_score < 0.8`
- **THEN** the rule's `maturity` MUST be demoted to `'established'`

#### Scenario: Rule inversion to anti-pattern

- **WHEN** a rule has `harmful_count >= 3` AND `effectiveness_score < 0.3`
- **THEN** the rule MUST be eligible for anti-pattern inversion
- **AND** when inverted, the rule's `maturity` MUST be set to `'anti_pattern'`
- **AND** the `content` MUST be rewritten to `"ANTI-PATTERN: Do NOT {original_content}. This caused problems because: {reasons}"`
- **AND** the original content MUST be preserved in `metadata.original_content`
- **AND** the embedding and search_vector MUST be regenerated from the new content

---

### Requirement: Permanence-to-decay-rate mapping

Facts SHALL support five permanence levels, each mapping to a specific exponential decay rate per day. The permanence level determines how quickly a fact's effective confidence decays from its base confidence when not confirmed.

#### Scenario: Permanence levels and their decay rates

- **WHEN** a fact is stored with `permanence='permanent'`
- **THEN** the `decay_rate` MUST be `0.0` (never decays)
- **AND** `permanence='stable'` MUST map to `decay_rate=0.002`
- **AND** `permanence='standard'` MUST map to `decay_rate=0.008`
- **AND** `permanence='volatile'` MUST map to `decay_rate=0.03`
- **AND** `permanence='ephemeral'` MUST map to `decay_rate=0.1`

#### Scenario: Invalid permanence rejected

- **WHEN** a fact is stored with an unrecognised permanence value
- **THEN** a `ValueError` MUST be raised listing the valid permanence levels

---

### Requirement: Storage layer CRUD operations

The storage layer SHALL provide async functions for creating episodes, facts, rules, and memory links, as well as retrieving, confirming, soft-deleting (forgetting), and applying feedback to memory items. All functions accept an asyncpg connection pool. Write functions SHALL accept optional `tenant_id` (default `'owner'`) and `request_id` parameters.

#### Scenario: Store episode with embedding and search vector

- **WHEN** `store_episode` is called with content, butler name, and optional `tenant_id` and `request_id`
- **THEN** a new episode row MUST be inserted with a generated UUID, computed embedding, computed tsvector, `tenant_id` (default `'owner'`), `request_id`, and `expires_at` determined by the retention policy for the episode's `retention_class` (falling back to 7 days if no policy exists)
- **AND** the INSERT MUST include `retention_class` and `sensitivity` columns with the caller's values (not only the migration defaults)
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

### Requirement: Scoping with global and role-local namespaces

Memory reads and writes SHALL be scoped. Facts and rules have a `scope` column (default `'global'`). When a scope filter is applied in search, facts and rules in `'global'` scope AND the specified scope MUST both be returned. Episodes use the `butler` column for scope filtering.

#### Scenario: Scope filter on facts and rules

- **WHEN** a search is performed with `scope='relationship'`
- **THEN** the query MUST include facts/rules WHERE `scope IN ('global', 'relationship')`

#### Scenario: Scope filter on episodes

- **WHEN** a search is performed on episodes with a scope value
- **THEN** the query MUST filter episodes WHERE `butler = <scope_value>`

#### Scenario: No scope filter returns all

- **WHEN** a search is performed with `scope=None`
- **THEN** the query MUST NOT apply any scope filter and MUST return results across all scopes

---

### Requirement: Hybrid search combining semantic and keyword modes

The search layer SHALL support three search modes: `semantic` (vector cosine similarity via pgvector), `keyword` (PostgreSQL full-text search via tsvector/tsquery), and `hybrid` (Reciprocal Rank Fusion combining both). Hybrid mode is the default.

#### Scenario: Semantic search returns cosine similarity

- **WHEN** `semantic_search` is called with a query embedding and table name
- **THEN** the results MUST include a `similarity` field computed as `1 - (embedding <=> query_embedding)`
- **AND** results MUST be ordered by similarity descending
- **AND** facts MUST be filtered to `validity = 'active'`
- **AND** rules MUST be filtered to exclude forgotten (metadata->'forgotten' IS NOT TRUE)

#### Scenario: Keyword search uses plainto_tsquery

- **WHEN** `keyword_search` is called with a query string
- **THEN** the query MUST use `plainto_tsquery('english', ...)` for safe free-form input handling
- **AND** results MUST include a `rank` field from `ts_rank`
- **AND** results MUST be ordered by rank descending
- **AND** an empty or falsy query MUST return an empty list

#### Scenario: Hybrid search uses Reciprocal Rank Fusion

- **WHEN** `hybrid_search` is called
- **THEN** both semantic and keyword searches MUST be executed
- **AND** results MUST be fused using RRF with constant k=60
- **AND** the RRF score for each result MUST be `1/(60 + semantic_rank) + 1/(60 + keyword_rank)`
- **AND** results appearing in only one list MUST use `rank = limit + 1` for the missing dimension
- **AND** the final results MUST be ordered by `rrf_score` descending with `semantic_rank` ascending as tiebreaker

#### Scenario: General search across multiple memory types

- **WHEN** `memory_search` is called with `types=None`
- **THEN** all three types (episode, fact, rule) MUST be searched
- **AND** each result MUST be tagged with a `memory_type` field
- **AND** results with `confidence < min_confidence` MUST be filtered out

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

### Requirement: Confidence decay with exponential formula

Effective confidence SHALL decay exponentially over time since the last confirmation. The formula SHALL be `effective_confidence = confidence * exp(-decay_rate * days_since_last_confirmed)`.

#### Scenario: Permanent facts never decay

- **WHEN** effective confidence is computed for a fact with `decay_rate=0.0`
- **THEN** the effective confidence MUST equal the base confidence unchanged

#### Scenario: Unconfirmed memory returns zero confidence

- **WHEN** effective confidence is computed for a memory with `last_confirmed_at=None`
- **THEN** the effective confidence MUST be 0.0

#### Scenario: Decay sweep threshold transitions

- **WHEN** the decay sweep runs across all active facts and rules (excluding permanent ones)
- **THEN** facts with effective confidence < 0.05 MUST have `validity` set to `'expired'`
- **AND** facts with effective confidence >= 0.05 and < 0.2 MUST have `metadata.status` set to `'fading'`
- **AND** facts previously marked as fading that recover above 0.2 MUST have `metadata.status` cleared
- **AND** rules with effective confidence < 0.05 MUST have `metadata.forgotten` set to `true`
- **AND** rules with effective confidence >= 0.05 and < 0.2 MUST have `metadata.status` set to `'fading'`

---

### Requirement: Embedding generation via sentence-transformers

The module SHALL use the `all-MiniLM-L6-v2` model from sentence-transformers to generate 384-dimensional embedding vectors. The EmbeddingEngine SHALL be a singleton, lazy-loaded on first use, and shared across all tool invocations.

#### Scenario: Single text embedding

- **WHEN** `embed` is called with a text string
- **THEN** the result MUST be a list of 384 floats
- **AND** None or empty strings MUST be normalized to a single space before encoding

#### Scenario: Batch embedding

- **WHEN** `embed_batch` is called with a list of texts
- **THEN** the result MUST be a list of 384-dimensional float vectors, one per input
- **AND** an empty input list MUST return an empty list

#### Scenario: Lazy singleton initialization

- **WHEN** `get_embedding_engine` is called for the first time
- **THEN** a new `EmbeddingEngine` instance MUST be created and cached as a module-level singleton
- **AND** subsequent calls MUST return the same instance

---

### Requirement: Text preprocessing for search vectors

All text stored in `search_vector` tsvector columns SHALL be preprocessed to handle edge cases. The preprocessing pipeline SHALL: remove NUL bytes (which PostgreSQL rejects), collapse consecutive whitespace into single spaces, strip leading/trailing whitespace, and truncate to 1 MB on a valid codepoint boundary.

#### Scenario: NUL bytes removed

- **WHEN** text containing `\x00` bytes is preprocessed
- **THEN** all NUL bytes MUST be removed from the output

#### Scenario: Whitespace normalization

- **WHEN** text with tabs, newlines, or multiple consecutive spaces is preprocessed
- **THEN** all whitespace sequences MUST be collapsed to a single space

#### Scenario: Search query preprocessing

- **WHEN** a user search query is preprocessed via `preprocess_search_query`
- **THEN** NUL bytes MUST be removed and whitespace MUST be normalized
- **AND** truncation MUST NOT be applied (queries are typically short)
- **AND** an empty or falsy query MUST return an empty string

---

### Requirement: LLM-driven memory consolidation pipeline

The consolidation pipeline SHALL transform unconsolidated episodes into durable facts and rules via a multi-step process: claim pending episodes via lease, group by (tenant_id, source butler), build a prompt with existing context, spawn an LLM CLI session, parse the structured JSON output, and execute the extracted actions against the database. All derived facts and rules MUST inherit the tenant context from their source episodes. Existing rules for dedup context SHALL be fetched using the `maturity` column (not `status`).

#### Scenario: Episode grouping by tenant and butler

- **WHEN** `run_consolidation` is called
- **THEN** episodes with `consolidation_status='pending'` MUST be fetched ordered by `(tenant_id, butler, created_at, id)` with `FOR UPDATE SKIP LOCKED`
- **AND** episodes MUST be grouped by the composite key `(tenant_id, butler)`, not by `butler` alone
- **AND** existing active facts (up to 100) and rules (up to 50) for each butler MUST be fetched for dedup context, scoped to the same `tenant_id`

#### Scenario: Episode grouping and prompt construction

- **WHEN** `run_consolidation` is called
- **THEN** episodes MUST be claimed via `FOR UPDATE SKIP LOCKED` ordered by `(tenant_id, butler, created_at, id)`
- **AND** episodes MUST be grouped by `butler` name
- **AND** existing active facts (up to 100) for each butler MUST be fetched with `WHERE validity = 'active' AND source_butler = $1`
- **AND** existing active rules for each butler MUST be fetched with `WHERE maturity NOT IN ('anti_pattern') AND (metadata->>'forgotten')::boolean IS NOT TRUE AND source_butler = $1` (using the `maturity` column, NOT a `status` column)
- **AND** a consolidation prompt MUST be built combining the SKILL.md template with episode content, existing facts, and existing rules

#### Scenario: Consolidation with LLM spawner

- **WHEN** a `cc_spawner` is provided to `run_consolidation`
- **THEN** for each `(tenant_id, butler)` group, a runtime session MUST be spawned with `trigger_source='schedule:consolidation'`
- **AND** the runtime output MUST be parsed for a JSON block containing `new_facts`, `updated_facts`, `new_rules`, and `confirmations`
- **AND** partial failures in one group MUST NOT block other groups from processing

#### Scenario: Consolidation without spawner (dry run)

- **WHEN** `run_consolidation` is called with `cc_spawner=None`
- **THEN** only episode grouping and counting MUST be performed
- **AND** no actual consolidation MUST occur

#### Scenario: Episode content wrapped in XML tags for prompt injection prevention

- **WHEN** episode content is formatted for the consolidation prompt
- **THEN** each episode's content MUST be wrapped in `<episode_content>` XML tags
- **AND** the SKILL.md MUST contain a security notice instructing the LLM to treat episode content as data only

---

### Requirement: Consolidation output parsing

The consolidation parser SHALL extract a JSON block from LLM text output (supporting both fenced code blocks and bare JSON objects), validate each action's required fields, and return a `ConsolidationResult` with `new_facts`, `updated_facts`, `new_rules`, `confirmations`, and `parse_errors`. Malformed data SHALL be reported via `parse_errors` rather than raising exceptions.

#### Scenario: Parse fenced JSON code block

- **WHEN** the LLM output contains a ```` ```json ... ``` ```` fenced code block
- **THEN** the parser MUST extract and decode the JSON from within the fences

#### Scenario: Parse bare JSON object

- **WHEN** the LLM output contains a bare `{...}` JSON object without fences
- **THEN** the parser MUST find the outermost balanced braces and decode the JSON

#### Scenario: New fact validation

- **WHEN** parsing a `new_facts` entry
- **THEN** `subject`, `predicate`, and `content` MUST all be present and non-empty
- **AND** `permanence` MUST default to `'standard'` if missing or invalid
- **AND** `importance` MUST be clamped to [1.0, 10.0]
- **AND** `tags` MUST default to an empty list if missing or not a list

#### Scenario: Updated fact validation

- **WHEN** parsing an `updated_facts` entry
- **THEN** `target_id`, `subject`, `predicate`, and `content` MUST all be present
- **AND** `target_id` MUST be a valid UUID string

#### Scenario: Confirmation validation

- **WHEN** parsing a `confirmations` entry
- **THEN** each entry MUST be a valid UUID string
- **AND** invalid UUIDs MUST be skipped and reported in `parse_errors`

#### Scenario: No JSON found

- **WHEN** no JSON block is found in the LLM output
- **THEN** the parser MUST return an empty `ConsolidationResult` with `parse_errors` containing "No JSON block found in consolidation output"

---

### Requirement: Consolidation executor with per-action error isolation

The consolidation executor SHALL apply parsed consolidation results to the database. Each action (new fact, updated fact, new rule, confirmation) SHALL be wrapped in its own try/except block so that one failure does not prevent remaining actions from executing. The executor MUST propagate tenant_id and request_id from the source episode group to all derived writes.

#### Scenario: New facts stored with tenant context and derived_from links

- **WHEN** the executor processes a `new_facts` entry
- **THEN** `store_fact` MUST be called with the entry's fields, `source_butler` set to the butler name, and `tenant_id` set to the episode group's tenant_id
- **AND** a `derived_from` link MUST be created from the new fact to each source episode

#### Scenario: Updated facts trigger supersession with tenant context

- **WHEN** the executor processes an `updated_facts` entry
- **THEN** `store_fact` MUST be called with `tenant_id` from the episode group (which auto-supersedes the existing fact via the uniqueness key)
- **AND** a `derived_from` link MUST be created from the new fact to each source episode

#### Scenario: New rules stored with tenant context

- **WHEN** the executor processes a `new_rules` entry
- **THEN** `store_rule` MUST be called with `tenant_id` set to the episode group's tenant_id

#### Scenario: Source episodes marked as consolidated

- **WHEN** all actions for a group have been executed
- **THEN** all source episodes MUST be marked with `consolidated=true` and `consolidation_status='consolidated'` with leases cleared

#### Scenario: Individual action failures do not block others

- **WHEN** storing one new fact fails with an exception
- **THEN** the error MUST be logged and added to the `errors` list
- **AND** subsequent actions MUST still be attempted

#### Scenario: Memory events include tenant_id

- **WHEN** consolidation emits memory_events (success or failure)
- **THEN** the INSERT MUST include `tenant_id` from the episode group being processed
- **AND** the INSERT MUST include `actor_butler` with the butler name

---

### Requirement: Episode cleanup with TTL and capacity limits

The episode cleanup process SHALL delete expired episodes and enforce a capacity limit on the episodes table. Unconsolidated episodes that have not expired SHALL be protected from capacity-based deletion.

#### Scenario: Expired episodes deleted

- **WHEN** `run_episode_cleanup` is called
- **THEN** all episodes with `expires_at < now()` MUST be deleted

#### Scenario: Capacity enforcement targets consolidated episodes

- **WHEN** the remaining episode count exceeds `max_entries` (default 10,000) after expiry deletion
- **THEN** the oldest `consolidated=true` episodes MUST be deleted until within budget
- **AND** unconsolidated episodes MUST NOT be deleted by capacity enforcement

#### Scenario: Cleanup returns statistics

- **WHEN** cleanup completes
- **THEN** the result MUST contain `expired_deleted`, `capacity_deleted`, and `remaining` counts

---

### Requirement: Entity identity registry

The module SHALL maintain an `entities` table providing stable identity anchors for recurring subjects referenced in facts. Entities solve the disambiguation problem where a raw string may refer to multiple distinct people, places, or organizations.

#### Scenario: Entity schema

- **WHEN** an entity is stored
- **THEN** the `entities` table row MUST contain: `id` (UUID PK), `tenant_id` (TEXT NOT NULL), `canonical_name` (VARCHAR NOT NULL), `entity_type` (VARCHAR, constrained to 'person', 'organization', 'place', 'other'), `aliases` (TEXT[] NOT NULL, default '{}'), `metadata` (JSONB, default '{}'), `created_at` (TIMESTAMPTZ), `updated_at` (TIMESTAMPTZ)

#### Scenario: Uniqueness constraint on entities

- **WHEN** an entity is created with a `(tenant_id, canonical_name, entity_type)` tuple that already exists
- **THEN** the creation MUST fail with a `ValueError` indicating the duplicate

#### Scenario: Entity type validation

- **WHEN** `entity_create` is called with an entity_type not in `{person, organization, place, other}`
- **THEN** a `ValueError` MUST be raised listing the valid types

#### Scenario: Entity update semantics

- **WHEN** `entity_update` is called
- **THEN** `canonical_name` MUST use replace semantics (overwrites current value)
- **AND** `aliases` MUST use replace-all semantics (pass the full desired list)
- **AND** `metadata` MUST use merge semantics (keys merged into existing metadata)
- **AND** `updated_at` MUST be set to now

---

### Requirement: Entity resolution with tiered candidate discovery

The `entity_resolve` tool SHALL resolve an ambiguous string to a ranked list of entity candidates using tiered candidate discovery. Tombstoned entities (those with `metadata->>'merged_into'` set) SHALL be excluded from resolution results.

The tool accepts two mutually exclusive lookup modes:
- **`name`** (legacy): Name-only lookup using tiers 1–4 (exact canonical, exact alias, prefix/substring, optional fuzzy).
- **`identifier`**: Waterfall lookup — tries a case-insensitive role match (tier 0) first, then falls through to name-based tiers 1–4 using the same string.

Providing both `name` and `identifier` SHALL raise a `ValueError`. Providing neither (or empty/whitespace) SHALL return an empty list.

#### Scenario: Identifier role match (tier 0)

- **WHEN** `entity_resolve` is called with `identifier` and an entity's `roles` array contains a case-insensitive match for the identifier string (e.g., `identifier='Owner'` matches `roles=['owner']`)
- **THEN** the candidate MUST be returned with `name_match='role'` and base score 120.0
- **AND** the role tier MUST take priority over all name-based tiers for the same entity

#### Scenario: Identifier falls through to name tiers

- **WHEN** `entity_resolve` is called with `identifier` and no entity's roles match the string
- **THEN** the identifier string MUST be used as the lookup for name-based tiers 1–4 (same behavior as `name`)

#### Scenario: Exact canonical name match (tier 1)

- **WHEN** `entity_resolve` is called and a non-tombstoned entity has `LOWER(canonical_name) = LOWER(name)`
- **THEN** the candidate MUST be returned with `name_match='exact'` and base score 100.0

#### Scenario: Exact alias match (tier 2)

- **WHEN** an entity's aliases contain a case-insensitive exact match for the name
- **THEN** the candidate MUST be returned with `name_match='alias'` and base score 80.0

#### Scenario: Prefix/substring match (tier 3)

- **WHEN** an entity's canonical name or aliases match via `LIKE (name || '%')` or `LIKE ('%' || name || '%')` but not via exact match
- **THEN** the candidate MUST be returned with `name_match='prefix'` and base score 50.0

#### Scenario: Fuzzy match (tier 4, opt-in)

- **WHEN** `enable_fuzzy=True` and the name has more than 2 characters
- **THEN** `pg_trgm` similarity search MUST be performed with threshold 0.3
- **AND** fuzzy candidates not found in earlier tiers MUST be included with `name_match='fuzzy'` and base score 20.0
- **AND** if pg_trgm is not available, fuzzy search MUST degrade gracefully (return empty list, no error)

#### Scenario: Graph neighborhood scoring with context_hints

- **WHEN** `context_hints` are provided with a `topic` or `mentioned_with` field
- **THEN** facts associated with each candidate entity MUST be fetched (up to 500)
- **AND** Jaccard keyword overlap between fact predicates/content and hint terms MUST be computed
- **AND** a boost of up to 20.0 MUST be added to the candidate's score based on overlap

#### Scenario: Domain scores from context_hints

- **WHEN** `context_hints` contains a `domain_scores` dict mapping entity_id to numeric score
- **THEN** each matching candidate's score MUST be increased by the domain score value

#### Scenario: Resolution ordering

- **WHEN** results are returned from `entity_resolve`
- **THEN** results MUST be ordered by `score DESC`, then `canonical_name ASC`
- **AND** each result MUST contain: `entity_id`, `canonical_name`, `entity_type`, `score` (rounded to 4 decimal places), `name_match`, `aliases`

#### Scenario: No candidates found

- **WHEN** no entities match the name string
- **THEN** an empty list MUST be returned (entity_resolve MUST NOT auto-create entities)

---

### Requirement: Entity merge with fact conflict resolution

The `entity_merge` tool SHALL merge a source entity into a target entity within an atomic database transaction. Facts referencing the source MUST be re-pointed to the target, with uniqueness conflicts resolved via supersession. The source entity MUST be tombstoned.

#### Scenario: Fact re-pointing without conflict

- **WHEN** a source entity's fact has no conflicting active fact on the target entity (same scope + predicate)
- **THEN** the fact's `entity_id` MUST be updated to the target entity's ID

#### Scenario: Fact conflict resolution by confidence

- **WHEN** a source entity's fact conflicts with a target entity's fact on `(scope, predicate)`
- **THEN** the fact with lower confidence MUST be set to `validity='superseded'`
- **AND** if the source fact has higher confidence, it MUST be re-pointed to the target AND the target's fact MUST be superseded
- **AND** if the target fact has higher or equal confidence, the source fact MUST be superseded

#### Scenario: Alias and metadata merging

- **WHEN** entities are merged
- **THEN** source aliases MUST be appended to target aliases (deduplicated, case-insensitive)
- **AND** source metadata MUST be merged into target metadata (target wins on key conflict)

#### Scenario: Source entity tombstoning

- **WHEN** merge completes
- **THEN** the source entity's metadata MUST have `merged_into` set to the target entity ID string
- **AND** source `updated_at` MUST be set to now

#### Scenario: Audit event for merge

- **WHEN** merge completes
- **THEN** a `memory_events` row with `event_type='entity_merge'` MUST be inserted
- **AND** the payload MUST contain `source_entity_id`, `target_entity_id`, `facts_repointed`, `facts_superseded`, `aliases_added`

#### Scenario: Merge validation

- **WHEN** `entity_merge` is called with identical source and target IDs
- **THEN** a `ValueError` MUST be raised
- **AND** if the source entity is already tombstoned, a `ValueError` MUST be raised
- **AND** if either entity is not found for the tenant, a `ValueError` MUST be raised

#### Scenario: Row-level locking during merge

- **WHEN** entity merge executes
- **THEN** both source and target entity rows MUST be locked with `SELECT ... FOR UPDATE` within a transaction

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
- **AND** `entity_resolve` MUST accept `name` (optional), `identifier` (optional), `tenant_id` (default `'shared'`), `entity_type` (optional), `context_hints` (optional), `enable_fuzzy` (default False). Exactly one of `name` or `identifier` MUST be provided.
- **AND** `entity_neighbors` MUST accept `entity_id` (required UUID), `max_depth` (default 1, max 5), `predicate_filter` (optional list), `direction` (default 'both')

#### Scenario: Consolidation and cleanup tools registered

- **WHEN** the memory module registers tools
- **THEN** tools `memory_run_consolidation` and `memory_run_episode_cleanup` MUST be available
- **AND** `memory_run_episode_cleanup` MUST accept `max_entries` (default 10000)

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

### Requirement: Rule effectiveness feedback with asymmetric weighting

Rule feedback SHALL use asymmetric weighting where harmful evidence carries a 4x penalty compared to helpful evidence. The effectiveness score formulas SHALL differ between helpful and harmful marks.

#### Scenario: Mark helpful increments counts and recalculates effectiveness

- **WHEN** `memory_mark_helpful` is called for a rule
- **THEN** `applied_count` MUST increment by 1
- **AND** `success_count` MUST increment by 1
- **AND** `last_applied_at` MUST be updated to now
- **AND** `effectiveness_score` MUST be recalculated as `success_count / applied_count`
- **AND** maturity promotion MUST be evaluated

#### Scenario: Mark harmful increments counts with 4x penalty formula

- **WHEN** `memory_mark_harmful` is called for a rule
- **THEN** `applied_count` MUST increment by 1
- **AND** `harmful_count` MUST increment by 1
- **AND** `last_applied_at` MUST be updated to now
- **AND** `effectiveness_score` MUST be recalculated as `success_count / (success_count + 4 * harmful_count + 0.01)`
- **AND** maturity demotion MUST be evaluated
- **AND** if a reason is provided, it MUST be appended to `metadata.harmful_reasons` list

#### Scenario: Anti-pattern inversion trigger

- **WHEN** `harmful_count >= 3` AND `effectiveness_score < 0.3` after a harmful mark
- **THEN** `metadata.needs_inversion` MUST be set to `true`

---

### Requirement: Memory stats for system health monitoring

The `memory_stats` tool SHALL return aggregate counts across all memory types, broken down by lifecycle state.

#### Scenario: Episode stats

- **WHEN** `memory_stats` is called
- **THEN** the response MUST include `episodes.total`, `episodes.unconsolidated` (consolidation_status='pending'), and `episodes.backlog_age_hours` (hours since oldest pending episode)

#### Scenario: Fact stats

- **WHEN** `memory_stats` is called
- **THEN** the response MUST include `facts.active` (validity='active' AND not fading), `facts.fading` (validity='active' AND metadata status='fading'), `facts.superseded`, and `facts.expired`

#### Scenario: Rule stats

- **WHEN** `memory_stats` is called
- **THEN** the response MUST include `rules.candidate`, `rules.established`, `rules.proven`, `rules.anti_pattern`, and `rules.forgotten`

#### Scenario: Scope filtering on stats

- **WHEN** `memory_stats` is called with a scope
- **THEN** fact and rule counts MUST be filtered to `scope IN ('global', <scope>)`

---

### Requirement: Module configuration via butler.toml

The Memory module SHALL be configured under `[modules.memory]` in each hosting butler's `butler.toml`. The configuration model SHALL be `MemoryModuleConfig` with a nested `RetrievalConfig`.

#### Scenario: Default configuration values

- **WHEN** no `[modules.memory]` configuration is provided
- **THEN** `retrieval.context_token_budget` MUST default to 3000
- **AND** `retrieval.default_limit` MUST default to 20
- **AND** `retrieval.default_mode` MUST default to 'hybrid'
- **AND** `retrieval.score_weights` MUST default to `{relevance: 0.4, importance: 0.3, recency: 0.2, confidence: 0.1}`

#### Scenario: Custom configuration overrides

- **WHEN** a butler's `butler.toml` specifies `[modules.memory.retrieval]` with custom values
- **THEN** the provided values MUST override the defaults
- **AND** the config MUST be available to tools at registration time

---

### Requirement: Fail-open policy for memory operations

Memory retrieval and storage failures MUST NOT block primary runtime execution. The module SHALL log errors and continue. Integrity-invariant violations (invalid state transitions, broken schema constraints, provenance corruption) SHALL fail-closed.

#### Scenario: Memory retrieval failure during session start

- **WHEN** `memory_context` encounters a database error during retrieval
- **THEN** the error MUST be logged
- **AND** the runtime session MUST NOT be blocked

#### Scenario: Memory storage failure during session completion

- **WHEN** `memory_store_episode` encounters a database error
- **THEN** the error MUST be logged
- **AND** the runtime session MUST NOT be blocked

#### Scenario: Consolidation group failure isolation

- **WHEN** consolidation fails for one butler group
- **THEN** the error MUST be logged and added to the errors list
- **AND** other butler groups MUST still be processed

---

### Requirement: Database schema with indexes and constraints

The memory module SHALL manage its database schema through Alembic migrations in three revisions: `mem_001` (baseline tables), `mem_002` (entities), `mem_003` (memory_events). The module uses pgvector for embedding storage and PostgreSQL full-text search for keyword matching.

#### Scenario: Required PostgreSQL extensions

- **WHEN** the baseline migration runs
- **THEN** `CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public` MUST be executed
- **AND** `CREATE EXTENSION IF NOT EXISTS "uuid-ossp" WITH SCHEMA public` MUST be executed

#### Scenario: Episodes table indexes

- **WHEN** the episodes table is created
- **THEN** indexes MUST exist for: `(butler, created_at DESC)`, `(expires_at)` WHERE expires_at IS NOT NULL, `(butler, created_at)` WHERE consolidation_status='pending', `search_vector` (GIN), `embedding` (IVFFlat with vector_cosine_ops, lists=20)

#### Scenario: Facts table indexes

- **WHEN** the facts table is created
- **THEN** indexes MUST exist for: `(scope, validity)` WHERE validity='active', `(subject, predicate)`, `search_vector` (GIN), `tags` (GIN), `embedding` (IVFFlat with vector_cosine_ops, lists=20), `object_entity_id` (partial, WHERE object_entity_id IS NOT NULL)

#### Scenario: Entity-keyed fact uniqueness (partial unique indexes)

- **WHEN** the entities migration runs
- **THEN** a partial unique index on `(entity_id, scope, predicate)` WHERE `entity_id IS NOT NULL AND object_entity_id IS NULL AND validity = 'active' AND valid_at IS NULL` MUST be created (property-fact uniqueness)
- **AND** a partial unique index on `(scope, subject, predicate)` WHERE `entity_id IS NULL AND validity = 'active' AND valid_at IS NULL` MUST be created (legacy subject-keyed uniqueness)
- **AND** a partial unique index on `(entity_id, object_entity_id, scope, predicate)` WHERE `object_entity_id IS NOT NULL AND validity = 'active' AND valid_at IS NULL` MUST be created (edge-fact uniqueness)
- **AND** all three constraints MUST coexist and apply only to property facts (valid_at IS NULL); temporal facts (valid_at IS NOT NULL) have no DB-level uniqueness constraint and rely on application-layer deduplication

#### Scenario: Memory links table

- **WHEN** the memory_links table is created
- **THEN** the primary key MUST be `(source_type, source_id, target_type, target_id)`
- **AND** a CHECK constraint MUST enforce `relation IN ('derived_from', 'supports', 'contradicts', 'supersedes', 'related_to')`
- **AND** an index MUST exist on `(target_type, target_id)` for reverse lookups

#### Scenario: Entities table indexes and constraints

- **WHEN** the entities table is created
- **THEN** a UNIQUE constraint MUST exist on `(tenant_id, canonical_name, entity_type)`
- **AND** a CHECK constraint MUST enforce `entity_type IN ('person', 'organization', 'place', 'other')`
- **AND** indexes MUST exist for: `(tenant_id, canonical_name)`, `aliases` (GIN), `metadata` (GIN)
- **AND** the `facts.entity_id` FK MUST reference `entities(id)` with `ON DELETE RESTRICT`

#### Scenario: Memory events table

- **WHEN** the memory_events table is created
- **THEN** the table MUST have columns: `id` (UUID PK), `event_type` (TEXT NOT NULL), `actor` (TEXT nullable), `tenant_id` (TEXT nullable), `payload` (JSONB, default '{}'), `created_at` (TIMESTAMPTZ NOT NULL)
- **AND** indexes MUST exist for: `(event_type, created_at DESC)`, `(tenant_id, created_at DESC)`

---

### Requirement: Memory links for provenance tracking

The `memory_links` table SHALL store directional edges between memory items for provenance tracking. Supported relation types are: `derived_from`, `supports`, `contradicts`, `supersedes`, and `related_to`.

#### Scenario: Link query by direction

- **WHEN** `get_links` is called with `direction='outgoing'`
- **THEN** only links where the memory item is the source MUST be returned
- **AND** `direction='incoming'` MUST return only links where the memory item is the target
- **AND** `direction='both'` (default) MUST return links in both directions

#### Scenario: Idempotent link creation

- **WHEN** `create_link` is called for a link that already exists
- **THEN** the operation MUST succeed without error (ON CONFLICT DO NOTHING)

---

### Requirement: JSON serialization for MCP tool responses

All MCP tool responses SHALL use JSON-serializable types. UUID values MUST be converted to strings, datetime values MUST be converted to ISO 8601 format strings, and the embedding vectors MUST NOT be included in tool responses (they are excluded at the search/retrieval layer by not being selected or by being stripped).

#### Scenario: UUID serialization

- **WHEN** a tool response includes a UUID value
- **THEN** the value MUST be converted to its string representation via `str(uuid)`

#### Scenario: Datetime serialization

- **WHEN** a tool response includes a datetime value
- **THEN** the value MUST be converted to ISO 8601 format via `datetime.isoformat()`

---

### Requirement: Module lifecycle integration

The Memory module SHALL implement the `Module` abstract base class with proper lifecycle hooks. The module name SHALL be `"memory"`, it SHALL have no dependencies on other modules, and it SHALL declare the `"memory"` Alembic branch label for its migrations.

#### Scenario: Module startup

- **WHEN** `on_startup` is called with a config and database reference
- **THEN** the module MUST store the database reference for pool access
- **AND** if the config is a `MemoryModuleConfig` instance, it MUST be stored for later use

#### Scenario: Module shutdown

- **WHEN** `on_shutdown` is called
- **THEN** the database and embedding engine references MUST be cleared to None

#### Scenario: Lazy embedding engine initialization

- **WHEN** a tool requires the embedding engine
- **THEN** `_get_embedding_engine` MUST lazy-load the engine on first access
- **AND** the engine MUST be shared across all subsequent tool calls

---

### Requirement: Edge fact storage via object_entity_id

The `facts` table SHALL support an optional `object_entity_id` column (UUID, nullable) that references `shared.entities(id)` with `ON DELETE RESTRICT`. When `object_entity_id` is set, the fact represents a directed edge from `entity_id` (subject) to `object_entity_id` (object). When `object_entity_id` is NULL, the fact is a property fact (existing behavior). The column is added by a memory module migration and is backward compatible — all existing facts retain `object_entity_id = NULL`.

#### Scenario: object_entity_id column definition

- **WHEN** the edge-fact migration runs
- **THEN** an `object_entity_id UUID` column MUST be added to the `facts` table, nullable, with default NULL
- **AND** a FK constraint `facts_object_entity_id_shared_fkey` MUST reference `shared.entities(id)` with `ON DELETE RESTRICT`
- **AND** a partial index on `object_entity_id` WHERE `object_entity_id IS NOT NULL` MUST be created for efficient edge lookups

#### Scenario: Existing facts unaffected

- **WHEN** the migration completes
- **THEN** all pre-existing facts MUST have `object_entity_id = NULL`
- **AND** no data migration or backfill is required

---

### Requirement: Mandatory entity anchoring for person facts (no generic identities)

All facts about an external entity — whether a person, organization, place, or other — MUST be anchored to a resolved `entity_id`. Storing facts with string-only subjects (no `entity_id`) is a spec violation. The `subject` field is a human-readable label only when `entity_id` is provided; it MUST NOT serve as the primary identity key.

When `memory_entity_resolve` returns zero candidates for a subject name, the agent MUST create a **transitory entity** via `memory_entity_create` with `metadata.unidentified = true` and source provenance, then use the returned `entity_id` to anchor the fact. This ensures the entity is visible in the dashboard's "Unidentified Entities" section for user review (merge, confirm, or delete).

This rule applies to all butlers with the memory module enabled: health, finance, education, travel, home, relationship, and general butlers MUST all follow this contract.

#### Scenario: Sender fact uses entity_id from identity preamble

- **WHEN** a butler receives a routed message with preamble `[Source: Owner (contact_id: abc, entity_id: def), via telegram]`
- **AND** the message contains a preference or attribute of the sender (e.g., "I liked that meal")
- **THEN** the butler MUST call `memory_store_fact` with `entity_id="def"` (from the preamble)
- **AND** `subject` MUST be a human-readable label (e.g., `"Owner"`, the sender's name)
- **AND** the butler MUST NOT store the fact with `subject="user"` and no `entity_id`

#### Scenario: Unidentified sender fact uses auto-created entity_id

- **WHEN** a butler receives a routed message with preamble `[Source: Unknown sender (contact_id: abc, entity_id: def), via telegram -- pending disambiguation]`
- **AND** the message contains information about the sender
- **THEN** the butler MUST call `memory_store_fact` with `entity_id="def"` (the auto-created entity)
- **AND** the fact is retrievable later even after the entity is merged into a known entity

#### Scenario: Unidentified entity lifecycle

- **WHEN** a temporary contact is created for an unknown sender
- **THEN** the system auto-creates a `shared.entities` entry with `metadata.unidentified = true`
- **AND** the entity appears in the dashboard entities list (`/butlers/entities`) with an "Unidentified" badge
- **AND** the owner MAY:
  1. **Flesh out**: Update the entity's `canonical_name` and `aliases` via the entity detail page
  2. **Merge into**: Merge the unidentified entity into an existing entity via contact merge (`POST /contacts/{id}/merge`) or the `memory_entity_merge` MCP tool — all facts are re-pointed to the target entity
  3. **Delete**: Retract the entity if the sender was spam or irrelevant

#### Scenario: Entity merge re-points all facts

- **WHEN** the owner merges unidentified entity U into known entity K via `memory_entity_merge(source=U, target=K)`
- **THEN** all facts with `entity_id = U` MUST be re-pointed to `entity_id = K`
- **AND** uniqueness conflicts (same `(entity_id, scope, predicate)`) MUST be resolved via supersession (higher-confidence fact wins)
- **AND** entity U MUST be tombstoned (`metadata.merged_into = K`)

#### Scenario: Mentioned person fact uses resolved entity_id

- **WHEN** a message mentions a person other than the sender (e.g., "Sarah likes coffee")
- **THEN** the butler MUST call `memory_entity_resolve("Sarah")` before storing the fact
- **AND** if resolved, MUST pass the resolved `entity_id` to `memory_store_fact`
- **AND** if unresolved (zero candidates), MUST call `memory_entity_create` with `metadata.unidentified = true` first, then use the new `entity_id`

#### Scenario: Fact stored without entity_id is a spec violation

- **WHEN** a butler calls `memory_store_fact` with a fact about an external entity (person, organization, place, or other)
- **AND** `entity_id` is `None`
- **THEN** this is a spec violation — facts about external entities MUST always be entity-anchored
- **AND** the shared `butler-memory` skill instructs all runtime instances to follow this rule

#### Scenario: Transitory entity for unknown organization from email processing

- **WHEN** a butler processes an email referencing a merchant or organization (e.g., "Nutrition Kitchen SG")
- **AND** `memory_entity_resolve("Nutrition Kitchen SG", entity_type="organization")` returns an empty list
- **THEN** the butler MUST call `memory_entity_create` with:
  - `canonical_name="Nutrition Kitchen SG"`
  - `entity_type="organization"`
  - `metadata={"unidentified": true, "source": "fact_storage", "source_butler": "<butler_name>", "source_scope": "<scope>"}`
- **AND** MUST pass the returned `entity_id` to `memory_store_fact`
- **AND** the entity MUST appear in the dashboard "Unidentified Entities" section

#### Scenario: Transitory entity for unknown place

- **WHEN** a butler stores a fact referencing a place not yet in the entity graph (e.g., "Marina Bay Sands")
- **AND** `memory_entity_resolve("Marina Bay Sands", entity_type="place")` returns an empty list
- **THEN** the butler MUST call `memory_entity_create` with:
  - `canonical_name="Marina Bay Sands"`
  - `entity_type="place"`
  - `metadata={"unidentified": true, "source": "fact_storage", "source_butler": "<butler_name>", "source_scope": "<scope>"}`
- **AND** MUST use the returned `entity_id` for the fact

#### Scenario: Idempotent entity creation on duplicate name

- **WHEN** `memory_entity_create` raises a unique constraint violation (because the entity already exists for this `(tenant_id, canonical_name, entity_type)`)
- **THEN** the agent MUST catch the error and call `memory_entity_resolve` to obtain the existing entity's `entity_id`
- **AND** MUST use that `entity_id` to anchor the fact
- **AND** MUST NOT treat the duplicate as a failure

#### Scenario: Entity type inference from context

- **WHEN** a butler creates a transitory entity during fact storage
- **THEN** the butler SHOULD infer the `entity_type` from context:
  - Merchant/company/service names → `organization`
  - Person names → `person`
  - Location names → `place`
  - Unknown/ambiguous → `other`

---

### Requirement: Entity anchoring via request_context

When a routed message arrives at a downstream butler via a `route.v1` envelope, the `request_context` carries resolved sender identity as `source_sender_entity_id` and `source_sender_contact_id`. Butlers MUST use `source_sender_entity_id` directly as the `entity_id` when storing facts about the message sender — they MUST NOT call `memory_entity_resolve` or `memory_entity_create` for the sender when this field is already populated.

This short-circuits the resolve-or-create protocol for the sender specifically. The Switchboard has already resolved (or provisioned) the sender entity; downstream butlers consume it as-is.

#### Scenario: Butler uses source_sender_entity_id for sender facts

- **WHEN** a butler receives a `route.v1` message with `request_context.source_sender_entity_id = 'def-456'`
- **AND** the message contains information about the sender (e.g., "I had lunch at 2pm today")
- **THEN** the butler MUST call `memory_store_fact` with `entity_id='def-456'`
- **AND** MUST NOT call `memory_entity_resolve` or `memory_entity_create` for the sender entity

#### Scenario: request_context entity_id takes precedence over preamble parsing

- **WHEN** `request_context.source_sender_entity_id` is present
- **THEN** the butler MUST use this value as the authoritative sender entity anchor
- **AND** MUST NOT attempt to extract entity identity from the text preamble string (which is a legacy display format)

#### Scenario: Missing source_sender_entity_id falls back to resolve-or-create

- **WHEN** a butler receives a message where `request_context.source_sender_entity_id` is absent or null
- **THEN** the butler MUST fall back to the standard resolve-or-create-transitory protocol for the sender entity
- **AND** MUST call `memory_entity_resolve` and, if empty, `memory_entity_create` with `metadata.unidentified = true`

#### Scenario: memory_store_fact auto-fallback from routing context

- **WHEN** a butler runtime calls `memory_store_fact` with `entity_id = None`
- **AND** the runtime session's routing context contains `source_entity_id` (set by the Switchboard identity pipeline)
- **THEN** `memory_store_fact` MUST automatically use the routing context's `source_entity_id` as the effective `entity_id`
- **AND** the fact MUST be anchored to the sender's entity without requiring explicit LLM extraction

**Implementation note:** `memory_store_fact` in `src/butlers/modules/memory/__init__.py` reads `source_entity_id` from `get_current_runtime_session_routing_context()` when `entity_id` is None. The routing context is populated by `route_to_butler` in `src/butlers/daemon.py` from the identity resolution result.

#### Scenario: Explicit entity_id takes precedence over routing context

- **WHEN** a butler runtime calls `memory_store_fact` with an explicit `entity_id` (non-None)
- **AND** the routing context also contains `source_entity_id`
- **THEN** the explicit `entity_id` MUST be used (routing context is not consulted)

#### Scenario: entity_resolve still needed for third-party mentions

- **WHEN** a message mentions a person other than the sender (e.g., owner says "Sarah likes coffee")
- **THEN** the sender auto-resolution provides the owner's entity_id, NOT Sarah's
- **AND** the butler MUST call `memory_entity_resolve("Sarah")` to resolve Sarah's entity_id before storing the fact about Sarah
- **AND** the auto-resolved sender entity_id is only appropriate for facts about the sender themselves

---

### Requirement: Entity neighbors graph traversal

The `entity_neighbors` tool SHALL traverse the entity graph by following edge facts (facts where `object_entity_id IS NOT NULL`). The implementation uses recursive CTEs on PostgreSQL. No external graph database is required.

#### Scenario: entity_neighbors tool parameters

- **WHEN** `entity_neighbors` is called
- **THEN** the following parameters MUST be accepted:
  - `entity_id` (UUID, required) — the starting entity
  - `max_depth` (INTEGER, default 1, max 5) — maximum traversal hops
  - `predicate_filter` (list of TEXT, optional) — restrict traversal to these predicate types
  - `direction` (TEXT, default 'both') — one of `'outgoing'`, `'incoming'`, `'both'`

#### Scenario: entity_neighbors result schema

- **WHEN** `entity_neighbors` returns results
- **THEN** each result MUST contain: `entity_id` (UUID string), `canonical_name`, `entity_type`, `predicate` (the edge predicate), `direction` (`'outgoing'` or `'incoming'` relative to the traversal source at that hop), `content` (the edge fact's content), `depth` (1-indexed hop distance from start), `fact_id` (UUID string of the edge fact)

#### Scenario: entity_neighbors cycle detection

- **WHEN** the graph contains cycles
- **THEN** traversal MUST NOT revisit entities already seen at a shallower depth
- **AND** the recursive CTE MUST track visited entity IDs to break cycles

#### Scenario: entity_neighbors validates entity existence

- **WHEN** `entity_neighbors` is called with an `entity_id` that does not exist
- **THEN** a `ValueError` MUST be raised stating the entity was not found

#### Scenario: entity_neighbors max_depth clamping

- **WHEN** `entity_neighbors` is called with `max_depth > 5`
- **THEN** the depth MUST be clamped to 5
- **AND** a warning SHOULD be included in the response metadata

---

### Requirement: Optional predicate registry for consistency guidance

The module MAY maintain a `predicate_registry` table that guides consistent predicate usage across fact extraction. The registry is advisory — it does not enforce constraints on fact storage. LLM extractors SHOULD prefer known predicates from the registry but MAY use novel predicates.

#### Scenario: predicate_registry table schema

- **WHEN** the predicate registry migration runs
- **THEN** the `predicate_registry` table MUST be created with columns:
  - `name` (TEXT PRIMARY KEY) — the predicate string
  - `expected_subject_type` (TEXT, nullable) — suggested entity_type for subject
  - `expected_object_type` (TEXT, nullable) — suggested entity_type for object (NULL for property-facts)
  - `is_edge` (BOOLEAN, default false) — whether this predicate is intended for edge facts
  - `is_temporal` (BOOLEAN, default false) — whether this predicate is typically used with `valid_at` timestamps to represent temporal sequences
  - `description` (TEXT, nullable) — human-readable description of the predicate
  - `created_at` (TIMESTAMPTZ, default now())

#### Scenario: Seed predicates

- **WHEN** the predicate registry is initialized
- **THEN** it SHOULD be seeded with existing property predicates from the fact-extraction taxonomy (e.g., `birthday`, `occupation`, `preference`, `allergy`)
- **AND** edge predicates SHOULD be seeded: `knows`, `works_at`, `lives_with`, `manages`, `parent_of`, `sibling_of`, `lives_in`, `member_of`
- **AND** edge predicates MUST have `is_edge = true` and appropriate `expected_subject_type`/`expected_object_type` values
- **AND** temporal predicates SHOULD be seeded with `is_temporal = true` to indicate predicates that form time series (e.g., `meal_breakfast`, `meal_lunch`, `meal_dinner`, `meal_snack` for meal facts with nutrition metadata at different times)
- **AND** domain CRUD-to-SPO migration predicates MUST be seeded per the taxonomy defined in `openspec/specs/predicate-taxonomy.md` (bu-ddb epic), covering health, relationship, finance, and home domains

#### Scenario: Temporal predicates guidance

- **WHEN** a predicate is marked with `is_temporal = true` in the registry
- **THEN** this indicates the predicate is typically used with `valid_at` timestamps to represent historical sequences
- **AND** LLM extractors SHOULD use `valid_at` when storing facts with temporal predicates
- **AND** multiple facts with the same subject/predicate but different `valid_at` values represent a temporal sequence and MUST NOT supersede each other

#### Scenario: predicate_list tool

- **WHEN** the `predicate_list` MCP tool is called
- **THEN** all rows from `predicate_registry` MUST be returned
- **AND** each row MUST include: `name`, `expected_subject_type`, `expected_object_type`, `is_edge`, `is_temporal`, `description`
- **AND** results MUST be ordered by `name ASC`

#### Scenario: predicate_list with edge filter

- **WHEN** `predicate_list` is called with `edges_only=true`
- **THEN** only predicates with `is_edge = true` MUST be returned

#### Scenario: Registry does not enforce constraints

- **WHEN** `store_fact` is called with a predicate NOT in the registry
- **THEN** the fact MUST still be stored successfully
- **AND** no error or warning MUST be raised by the storage layer

---

### Requirement: CRUD-to-SPO domain predicate taxonomy (bu-ddb)

Domain butlers (health, relationship, finance, home) migrate dedicated CRUD tables to temporal SPO facts. This requires a domain predicate taxonomy, entity resolution contract, and standardized metadata schemas. The full specification is in `openspec/specs/predicate-taxonomy.md`.

#### Scenario: Domain scope isolation

- **WHEN** a domain butler stores a fact for a CRUD-migrated table
- **THEN** the fact MUST use the domain-specific `scope` value: `health` for health butler, `relationship` for relationship butler, `finance` for finance butler, `home` for home butler
- **AND** the scope MUST prevent predicate collisions across butler domains (e.g. `gift` in relationship scope is distinct from any future `gift` in another scope)

#### Scenario: Entity anchoring — no bare string subjects

- **WHEN** any domain butler stores a CRUD-migrated fact
- **THEN** the fact MUST carry a resolved `entity_id` UUID — never NULL for domain facts
- **AND** the `subject` field MAY contain a human-readable label but MUST NOT be the sole identifier
- **AND** the string `"user"` MUST NOT be used as the `entity_id` value or as a bare subject for any migrated fact
- **AND** self-data (health, finance) MUST use the owner entity resolved via `entity_resolve(identifier='Owner')` (which matches `roles=['owner']` via tier 0) or from `shared.contacts WHERE roles @> '["owner"]'`
- **AND** contact-data (relationship) MUST use the resolved entity for each contact via `shared.contacts.entity_id`
- **AND** HA device data (home) MUST use an entity created with `entity_type='other'` and `metadata.entity_class='ha_device'` per distinct HA entity ID string

#### Scenario: Temporal vs property fact dispatch by predicate

- **WHEN** a domain wrapper tool stores a fact
- **THEN** if the predicate has `is_temporal = true` in `predicate_registry`, the fact MUST be stored with `valid_at` set to the source event timestamp (e.g. `measured_at`, `occurred_at`, `posted_at`)
- **AND** if the predicate has `is_temporal = false`, the fact MUST be stored without `valid_at` (NULL), relying on supersession for updates
- **AND** temporal facts with the same `(entity_id, scope, predicate)` key but different `valid_at` values MUST coexist as active facts — they MUST NOT supersede each other

#### Scenario: NUMERIC amounts as strings in metadata

- **WHEN** a domain fact carries a monetary amount (finance transactions, loans, subscriptions, bills)
- **THEN** the amount MUST be stored as a string in `metadata` (e.g. `"1234.99"`) to preserve `NUMERIC(14,2)` precision
- **AND** aggregation queries MUST cast via `(metadata->>'amount')::NUMERIC` to recover the numeric value
- **AND** the string representation MUST NOT use scientific notation

#### Scenario: Transaction deduplication in finance wrapper

- **WHEN** `record_transaction` is called to store a finance transaction fact
- **THEN** the wrapper MUST check for an existing active fact matching `(entity_id, predicate, scope, metadata->>'source_message_id', metadata->>'merchant', metadata->>'amount', valid_at)` before inserting
- **AND** if a matching fact exists, the insert MUST be skipped and the existing fact ID returned
- **AND** this check MUST be equivalent in coverage to the original `uq_transactions_dedupe` partial unique index on the deprecated `transactions` table

#### Scenario: Backward-compatible tool response shapes

- **WHEN** a CRUD-migrated MCP tool returns a response
- **THEN** the response structure MUST be identical to the response structure returned by the original CRUD-table implementation
- **AND** all field names, types, and optional/required semantics MUST be preserved
- **AND** callers MUST observe no behavioral difference between the CRUD and fact-wrapper implementations
- **AND** the mapping from fact fields to legacy response fields MUST follow the wrapper API contract documented in `openspec/specs/predicate-taxonomy.md` Part 5

---

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

The consolidation pipeline SHALL use a lease-based claiming model with `FOR UPDATE SKIP LOCKED` to support concurrent consolidation workers. Episodes SHALL progress through a strict state machine: `pending` -> `consolidated` | `failed` | `dead_letter`. Every episode MUST reach exactly one terminal state.

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

### Requirement: [TARGET-STATE] Tenant-bounded isolation

All retrieval and lifecycle operations SHALL be tenant-bounded by default. Worker execution SHALL preserve deterministic ordering within `(tenant_id, butler)` shard keys.

**Note: The current code implementation does not consistently enforce `tenant_id` scoping across all operations. The `episodes`, `facts`, and `rules` tables in code do not have a `tenant_id` column. Tenant isolation is partially implemented in the entity subsystem only. The docs specify this as a normative requirement for target state.**

#### Scenario: Tenant-scoped fact storage

- **WHEN** a fact is stored
- **THEN** the `tenant_id` MUST be set from the authenticated request context
- **AND** queries MUST scope all reads to the caller's tenant

#### Scenario: Cross-tenant access prevention

- **WHEN** a memory retrieval request is made
- **THEN** the query MUST include a `tenant_id` filter matching the caller's tenant
- **AND** no data from other tenants MUST be returned

---

### Requirement: [TARGET-STATE] Rule application tracking

[TARGET-STATE] The module SHALL maintain a `rule_applications` table recording per-application outcomes for rules. This table is specified in the normative docs under provenance/audit surfaces but is not yet implemented in code or migrations.

#### Scenario: Rule application recorded

- **WHEN** a rule is applied (helpful or harmful)
- **THEN** a `rule_applications` row MUST be created recording the rule_id, outcome, timestamp, and optional context

---

### Requirement: [TARGET-STATE] Embedding version tracking

[TARGET-STATE] The module SHALL maintain an `embedding_versions` table tracking the model and version used for embeddings, supporting re-embed migrations when the embedding model changes. This table is specified in the normative docs under provenance/audit surfaces but is not yet implemented in code or migrations.

#### Scenario: Embedding model version recorded

- **WHEN** an embedding is generated
- **THEN** the model name and version MUST be recorded in `embedding_versions`

#### Scenario: Re-embed migration support

- **WHEN** the embedding model is upgraded
- **THEN** a migration process MUST be available to re-embed all existing memories with the new model

---

### Requirement: [TARGET-STATE] Decay and hygiene scheduled workers

[TARGET-STATE] The module SHALL provide scheduled workers for decay sweeps and episode cleanup that run on a configurable cron schedule. The normative docs specify this as a mandatory runtime flow. The decay sweep function (`run_decay_sweep`) exists in code, but its integration as a scheduled worker with cron configuration is not yet wired.

#### Scenario: Daily decay sweep

- **WHEN** the decay sweep cron fires
- **THEN** all active facts and rules with `decay_rate > 0.0` MUST be evaluated
- **AND** threshold transitions (fading/expired) MUST be applied

#### Scenario: Episode cleanup on schedule

- **WHEN** the episode cleanup cron fires
- **THEN** expired episodes MUST be deleted and capacity limits MUST be enforced

---

### Requirement: [TARGET-STATE] Memory events for all mutations

[TARGET-STATE] The normative docs specify that `memory_events` SHALL be an append-only audit stream for ALL memory mutations and lifecycle transitions. Currently, only `entity_merge` writes to this table. All other mutation operations (store, forget, confirm, mark_helpful, mark_harmful, supersession, decay transitions) do not yet emit memory_events records.

#### Scenario: Fact stored event

- **WHEN** a new fact is stored
- **THEN** a `memory_events` row with `event_type='fact_created'` MUST be inserted

#### Scenario: Memory forgotten event

- **WHEN** `memory_forget` is called
- **THEN** a `memory_events` row with the appropriate event type MUST be inserted

#### Scenario: Lifecycle transition event

- **WHEN** a decay sweep transitions a fact to fading or expired
- **THEN** a `memory_events` row MUST be inserted recording the transition

---

### Requirement: [TARGET-STATE] Deterministic context assembly with stable ordering

[TARGET-STATE] The normative docs specify that `memory_context` output must be deterministic and sectioned with stable ordering tie-breakers: `score DESC`, then `created_at DESC`, then `id ASC`. The current implementation sorts by composite score descending but does not enforce the full tie-breaking chain with `created_at` and `id`.

#### Scenario: Deterministic ordering with tie-breaking

- **WHEN** two memories have identical composite scores
- **THEN** the memory with a later `created_at` MUST appear first
- **AND** if `created_at` is also identical, the memory with the lower `id` MUST appear first

#### Scenario: Deterministic section quotas

- **WHEN** the context is assembled
- **THEN** section quotas (facts vs rules) MUST be deterministic and configurable
- **AND** the tokenizer used for budget enforcement MUST be deterministic
