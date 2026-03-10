## MODIFIED Requirements

### Requirement: Storage layer CRUD operations

The storage layer SHALL provide async functions for creating episodes, facts, rules, and memory links, as well as retrieving, confirming, soft-deleting (forgetting), and applying feedback to memory items. All functions accept an asyncpg connection pool.

#### Scenario: Store episode with embedding and search vector

- **WHEN** `store_episode` is called with content and butler name
- **THEN** a new episode row MUST be inserted with a generated UUID, computed embedding, computed tsvector, and `expires_at` derived from the `retention_class` TTL in `memory_policies`
- **AND** the INSERT MUST include `retention_class` and `sensitivity` columns with the caller's values (not only the migration defaults)
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

### Requirement: LLM-driven memory consolidation pipeline

The consolidation pipeline SHALL transform unconsolidated episodes into durable facts and rules via a multi-step process: fetch pending episodes, group by (tenant_id, source butler), build a prompt with existing context, spawn an LLM CLI session, parse the structured JSON output, and execute the extracted actions against the database. All derived facts and rules MUST inherit the tenant context from their source episodes.

#### Scenario: Episode grouping by tenant and butler

- **WHEN** `run_consolidation` is called
- **THEN** episodes with `consolidation_status='pending'` MUST be fetched ordered by `(tenant_id, butler, created_at, id)` with `FOR UPDATE SKIP LOCKED`
- **AND** episodes MUST be grouped by the composite key `(tenant_id, butler)`, not by `butler` alone
- **AND** existing active facts (up to 100) and rules (up to 50) for each butler MUST be fetched for dedup context, scoped to the same `tenant_id`

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
