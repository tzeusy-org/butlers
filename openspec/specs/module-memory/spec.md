# Memory Module

## Purpose

The Memory module is a reusable, opt-in module that hosting butlers load locally, providing persistent storage of episodes, facts, and rules with provenance; low-latency retrieval for runtime context injection; LLM-driven consolidation of episodes into durable knowledge; and lifecycle maintenance including confidence decay, fading/expiry transitions, and episode cleanup.

## ADDED Requirements

### Requirement: Three memory types with distinct schemas and lifecycles

The module SHALL support three primary memory types: episodes (high-volume, short-lived session observations), facts (durable subject-predicate-content semantic knowledge), and rules (behavioral guidance learned from repeated outcomes). Each type SHALL have its own PostgreSQL table with type-specific columns, lifecycle states, and retrieval semantics.

#### Scenario: Episode schema and defaults

- **WHEN** an episode is stored
- **THEN** the `episodes` table row MUST contain: `id` (UUID PK), `butler` (TEXT NOT NULL), `session_id` (UUID nullable), `content` (TEXT NOT NULL), `embedding` (vector(384)), `search_vector` (tsvector), `importance` (FLOAT, default 5.0), `reference_count` (INTEGER, default 0), `consolidated` (BOOLEAN, default false), `consolidation_status` (VARCHAR(20), default 'pending'), `retry_count` (INTEGER, default 0), `last_error` (TEXT nullable), `created_at` (TIMESTAMPTZ), `last_referenced_at` (TIMESTAMPTZ nullable), `expires_at` (TIMESTAMPTZ, default now + 7 days), `metadata` (JSONB, default '{}')

#### Scenario: Fact schema and defaults

- **WHEN** a fact is stored
- **THEN** the `facts` table row MUST contain: `id` (UUID PK), `subject` (TEXT NOT NULL), `predicate` (TEXT NOT NULL), `content` (TEXT NOT NULL), `embedding` (vector(384)), `search_vector` (tsvector), `importance` (FLOAT, default 5.0), `confidence` (FLOAT, default 1.0), `decay_rate` (FLOAT, default 0.008), `permanence` (TEXT, default 'standard'), `source_butler` (TEXT nullable), `source_episode_id` (UUID FK to episodes, ON DELETE SET NULL), `supersedes_id` (UUID self-FK, ON DELETE SET NULL), `entity_id` (UUID FK to entities, ON DELETE RESTRICT, nullable), `validity` (TEXT, default 'active'), `scope` (TEXT, default 'global'), `reference_count` (INTEGER, default 0), `created_at` (TIMESTAMPTZ), `last_referenced_at` (TIMESTAMPTZ nullable), `last_confirmed_at` (TIMESTAMPTZ nullable), `tags` (JSONB, default '[]'), `metadata` (JSONB, default '{}')

#### Scenario: Rule schema and defaults

- **WHEN** a rule is stored
- **THEN** the `rules` table row MUST contain: `id` (UUID PK), `content` (TEXT NOT NULL), `embedding` (vector(384)), `search_vector` (tsvector), `scope` (TEXT, default 'global'), `maturity` (TEXT, default 'candidate'), `confidence` (FLOAT, default 0.5), `decay_rate` (FLOAT, default 0.008), `permanence` (TEXT, default 'standard'), `effectiveness_score` (FLOAT, default 0.0), `applied_count` (INTEGER, default 0), `success_count` (INTEGER, default 0), `harmful_count` (INTEGER, default 0), `source_episode_id` (UUID FK to episodes), `source_butler` (TEXT nullable), `created_at` (TIMESTAMPTZ), `last_applied_at` (TIMESTAMPTZ nullable), `last_evaluated_at` (TIMESTAMPTZ nullable), `last_confirmed_at` (TIMESTAMPTZ nullable), `reference_count` (INTEGER, default 0), `last_referenced_at` (TIMESTAMPTZ nullable), `tags` (JSONB, default '[]'), `metadata` (JSONB, default '{}')

---

### Requirement: Fact validity lifecycle states

Facts SHALL progress through validity lifecycle states: `active`, `fading` (tracked via metadata status), `superseded`, `expired`, and `retracted`. The `fading` state is encoded as `metadata->>'status' = 'fading'` while `validity` remains `'active'`. Only facts with `validity = 'active'` SHALL be returned in search and retrieval operations.

#### Scenario: Fact starts as active

- **WHEN** a new fact is stored via `store_fact`
- **THEN** the fact's `validity` MUST be `'active'`
- **AND** the fact's `confidence` MUST be `1.0`

#### Scenario: Fact transitions to superseded

- **WHEN** a new fact is stored with the same `(subject, predicate)` key as an existing active fact (when `entity_id` is NULL), or the same `(entity_id, scope, predicate)` key (when `entity_id` is set)
- **THEN** the existing fact's `validity` MUST be set to `'superseded'`
- **AND** the new fact's `supersedes_id` MUST reference the old fact's `id`
- **AND** a `memory_links` row with `relation='supersedes'` MUST be created

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

The storage layer SHALL provide async functions for creating episodes, facts, rules, and memory links, as well as retrieving, confirming, soft-deleting (forgetting), and applying feedback to memory items. All functions accept an asyncpg connection pool.

#### Scenario: Store episode with embedding and search vector

- **WHEN** `store_episode` is called with content and butler name
- **THEN** a new episode row MUST be inserted with a generated UUID, computed embedding, computed tsvector, and `expires_at` set to 7 days from now
- **AND** the function MUST return the new episode's UUID

#### Scenario: Store fact with supersession check

- **WHEN** `store_fact` is called and an active fact with the same uniqueness key exists
- **THEN** the existing active fact MUST be marked `validity='superseded'`
- **AND** the new fact MUST have `supersedes_id` set to the old fact's ID
- **AND** a `memory_links` row with `relation='supersedes'` MUST be created linking new to old
- **AND** the entire operation MUST execute within a single database transaction

#### Scenario: Store fact with entity_id validation

- **WHEN** `store_fact` is called with an `entity_id` that does not exist in the `entities` table
- **THEN** a `ValueError` MUST be raised stating the entity does not exist

#### Scenario: Store rule as candidate

- **WHEN** `store_rule` is called
- **THEN** a new rule row MUST be inserted with `maturity='candidate'`, `confidence=0.5`, `decay_rate=0.01`, `effectiveness_score=0.0`, and all counts set to 0

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

The `memory_recall` tool SHALL use composite scoring combining relevance, importance, recency, and effective confidence with configurable weights. The default weights SHALL be: relevance=0.4, importance=0.3, recency=0.2, confidence=0.1.

#### Scenario: Composite score formula

- **WHEN** a recall query is executed
- **THEN** the composite score MUST be calculated as: `0.4 * relevance + 0.3 * (importance / 10.0) + 0.2 * recency + 0.1 * effective_confidence`
- **AND** the `relevance` component MUST be derived from the normalized RRF score (capped at 1.0)
- **AND** the `importance` MUST be normalized from 0-10 to 0-1 by dividing by 10
- **AND** results MUST be filtered by `min_confidence` threshold (default 0.2)

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

The consolidation pipeline SHALL transform unconsolidated episodes into durable facts and rules via a multi-step process: fetch pending episodes, group by source butler, build a prompt with existing context, spawn an LLM CLI session, parse the structured JSON output, and execute the extracted actions against the database.

#### Scenario: Episode grouping and prompt construction

- **WHEN** `run_consolidation` is called
- **THEN** episodes with `consolidated=false` MUST be fetched ordered by `created_at ASC`
- **AND** episodes MUST be grouped by `butler` name
- **AND** existing active facts (up to 100) and rules (up to 50) for each butler MUST be fetched for dedup context
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

The consolidation executor SHALL apply parsed consolidation results to the database. Each action (new fact, updated fact, new rule, confirmation) SHALL be wrapped in its own try/except block so that one failure does not prevent remaining actions from executing.

#### Scenario: New facts stored with derived_from links

- **WHEN** the executor processes a `new_facts` entry
- **THEN** `store_fact` MUST be called with the entry's fields and `source_butler` set to the butler name
- **AND** a `derived_from` link MUST be created from the new fact to each source episode

#### Scenario: Updated facts trigger supersession

- **WHEN** the executor processes an `updated_facts` entry
- **THEN** `store_fact` MUST be called (which auto-supersedes the existing fact via the uniqueness key)
- **AND** a `derived_from` link MUST be created from the new fact to each source episode

#### Scenario: Source episodes marked as consolidated

- **WHEN** all actions for a butler group have been executed
- **THEN** all source episodes MUST be marked with `consolidated=true` via `UPDATE episodes SET consolidated = true WHERE id = ANY($1)`

#### Scenario: Individual action failures do not block others

- **WHEN** storing one new fact fails with an exception
- **THEN** the error MUST be logged and added to the `errors` list
- **AND** subsequent actions MUST still be attempted

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

The `entity_resolve` tool SHALL resolve an ambiguous name string to a ranked list of entity candidates using four-tier candidate discovery: exact canonical name match, exact alias match, prefix/substring match, and optional fuzzy match (edit distance via pg_trgm). Tombstoned entities (those with `metadata->>'merged_into'` set) SHALL be excluded from resolution results.

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

The Memory module SHALL register 19 MCP tools on the hosting butler's MCP server when the module is enabled. Tool closures SHALL inject `pool` and `embedding_engine` from module state so these are not visible in the MCP tool signature.

#### Scenario: Writing tools registered

- **WHEN** the memory module registers tools
- **THEN** tools `memory_store_episode`, `memory_store_fact`, and `memory_store_rule` MUST be available
- **AND** `memory_store_episode` MUST accept `content` (required), `butler` (required), `session_id` (optional), `importance` (optional, default 5.0)
- **AND** `memory_store_fact` MUST accept `subject`, `predicate`, `content` (required), `importance` (default 5.0), `permanence` (default 'standard'), `scope` (default 'global'), `tags` (optional list)
- **AND** `memory_store_rule` MUST accept `content` (required), `scope` (default 'global'), `tags` (optional list)

#### Scenario: Reading tools registered

- **WHEN** the memory module registers tools
- **THEN** tools `memory_search`, `memory_recall`, and `memory_get` MUST be available
- **AND** `memory_search` MUST accept `query` (required), `types` (optional list), `scope` (optional), `mode` (default 'hybrid'), `limit` (default 10), `min_confidence` (default 0.2)
- **AND** `memory_recall` MUST accept `topic` (required), `scope` (optional), `limit` (default 10)
- **AND** `memory_get` MUST accept `memory_type` (required) and `memory_id` (required)

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

#### Scenario: Context tool registered

- **WHEN** the memory module registers tools
- **THEN** tool `memory_context` MUST be available
- **AND** it MUST accept `trigger_prompt` (required), `butler` (required), `token_budget` (default from config)

#### Scenario: Entity tools registered

- **WHEN** the memory module registers tools
- **THEN** tools `entity_create`, `entity_get`, `entity_update`, `entity_resolve`, and `entity_merge` MUST be available
- **AND** `entity_create` MUST accept `canonical_name`, `entity_type`, `tenant_id` (all required), `aliases` (optional), `metadata` (optional)
- **AND** `entity_resolve` MUST accept `name` (required), `tenant_id` (default 'default'), `entity_type` (optional), `context_hints` (optional), `enable_fuzzy` (default False)

#### Scenario: Consolidation and cleanup tools registered

- **WHEN** the memory module registers tools
- **THEN** tools `memory_run_consolidation` and `memory_run_episode_cleanup` MUST be available
- **AND** `memory_run_episode_cleanup` MUST accept `max_entries` (default 10000)

---

### Requirement: Context injection via memory_context

The `memory_context` tool SHALL build a structured text block for injection into a runtime system prompt, retrieving the most relevant facts and rules via composite-scored recall and formatting them within a token budget.

#### Scenario: Context structure

- **WHEN** `memory_context` is called
- **THEN** the output MUST begin with `# Memory Context\n`
- **AND** if facts are found, a `## Key Facts` section MUST be included with lines formatted as `- [{subject}] [{predicate}]: {content} (confidence: {confidence:.2f})`
- **AND** if rules are found and budget allows, a `## Active Rules` section MUST be included with lines formatted as `- {content} (maturity: {maturity}, effectiveness: {effectiveness:.2f})`

#### Scenario: Token budget enforcement

- **WHEN** `memory_context` assembles the output
- **THEN** the output MUST NOT exceed `token_budget * 4` characters (1 token ~ 4 chars approximation)
- **AND** items MUST be added in order of composite score descending until the budget is exhausted
- **AND** the default token budget MUST come from `config.retrieval.context_token_budget` (default 3000)

#### Scenario: Scope filtering via butler name

- **WHEN** `memory_context` is called with a butler name
- **THEN** the recall query MUST use the butler name as the scope filter
- **AND** up to 20 results MUST be retrieved from recall

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
- **THEN** indexes MUST exist for: `(scope, validity)` WHERE validity='active', `(subject, predicate)`, `search_vector` (GIN), `tags` (GIN), `embedding` (IVFFlat with vector_cosine_ops, lists=20)

#### Scenario: Entity-keyed fact uniqueness (partial unique indexes)

- **WHEN** the entities migration runs
- **THEN** a partial unique index on `(entity_id, scope, predicate)` WHERE `entity_id IS NOT NULL AND validity = 'active'` MUST be created
- **AND** a partial unique index on `(scope, subject, predicate)` WHERE `entity_id IS NULL AND validity = 'active'` MUST be created
- **AND** both constraints MUST coexist

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
