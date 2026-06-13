## Purpose

The memory module provides tiered, durable memory for butlers — storing episodes, facts, and rules; consolidating episodes into durable knowledge via an LLM pipeline; supporting correction-driven retraction; and exposing dashboard surfaces for retention policy, compaction history, and memory inspection.

## Requirements

### Requirement: Correction-Driven Memory Retraction
The memory module SHALL support retraction of memories (facts, episodes, and rules) initiated by the correction system. Correction-driven retraction SHALL use the existing `memory_forget` mechanism to set validity to `retracted`, and SHALL additionally record correction provenance in the memory's metadata.

#### Scenario: Fact retracted via correction
- **WHEN** the `correct` tool processes a `memory_deletion` correction for a fact
- **THEN** the memory module's `memory_forget` SHALL be called with the fact's `memory_id` and `memory_type=fact`
- **AND** the fact's `metadata` SHALL be updated to include `correction_id` (the UUID of the correction record) and `correction_reason` (the user's description of why the memory is wrong)
- **AND** the fact's `validity` SHALL be set to `retracted`

#### Scenario: Episode retracted via correction
- **WHEN** the `correct` tool processes a `memory_deletion` correction for an episode
- **THEN** the memory module's `memory_forget` SHALL be called with the episode's `memory_id` and `memory_type=episode`
- **AND** the episode's `metadata` SHALL be updated to include `correction_id` and `correction_reason`

#### Scenario: Rule retracted via correction
- **WHEN** the `correct` tool processes a `memory_deletion` correction for a rule
- **THEN** the memory module's `memory_forget` SHALL be called with the rule's `memory_id` and `memory_type=rule`
- **AND** the rule's `metadata` SHALL be updated to include `correction_id` and `correction_reason`

#### Scenario: Already-retracted memory cannot be corrected
- **WHEN** a `memory_deletion` correction targets a memory whose validity is already `retracted`
- **THEN** the correction SHALL fail with `status=failed` and a summary explaining that the memory is already retracted

#### Scenario: Superseded memory cannot be corrected via deletion
- **WHEN** a `memory_deletion` correction targets a fact whose validity is `superseded`
- **THEN** the correction SHALL fail with `status=failed` and a summary explaining that the memory has been superseded by a newer version, and suggesting the user correct the newer version instead

#### Scenario: Correction provenance in memory events
- **WHEN** a memory is retracted via correction
- **THEN** a `memory_events` row SHALL be inserted with event type indicating correction-driven retraction
- **AND** the event's metadata SHALL include the `correction_id` for audit linkage

---

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

---

### Requirement: Memory Page — Dispatch Fold-In
The existing `/memory` page SHALL fold in the `MemoryExpanded` design from `pr/overview/settings-refactor/settings-expanded.jsx`, with sections for the tier flow, retention policy, compaction log, and memory-inspect search.

#### Scenario: Page structure post-fold-in
- **WHEN** a user navigates to `/memory`
- **THEN** the page renders sections in this order:
  - **§1 Tier flow** — visual hint of `events → mid-term → long-term` with counts and last-compaction timestamps.
  - **§2 Retention policy** — table keyed by `kind` (`event|fact|preference|summary|transcript|embedding`) with editable `ttl_days` and `max_rows` cells; mutations call `PUT /api/memory/retention-policies/{kind}`.
  - **§3 Compaction log** — feed of recent compaction events with `ts`, `kind`, `rows_removed`, `bytes_freed`.
  - **§4 Inspect** — search bar over memory contents (`q`, `kind`) returning paginated hits.

---

### Requirement: Memory Retention Policies API
The dashboard SHALL expose CRUD over per-kind retention policies.

#### Scenario: Read all policies
- **WHEN** `GET /api/memory/retention-policies` is called
- **THEN** the response is `ApiResponse[RetentionPolicy[]]` with one row per `kind` containing `kind`, `ttl_days: int | null`, `max_rows: bigint | null`, `updated_at`, `updated_by`.

#### Scenario: Update a policy
- **WHEN** `PUT /api/memory/retention-policies/{kind} {ttl_days?, max_rows?}` is called
- **THEN** the row for `kind` is upserted with the supplied fields (null = unlimited)
- **AND** `audit.append("memory.retention", target=kind, note=f"ttl={ttl_days};max={max_rows}")` is invoked.

#### Scenario: Cleanup job consults policy
- **WHEN** the memory cleanup job runs
- **THEN** it loads `memory_retention_policies` and enforces `ttl_days` and `max_rows` per kind
- **AND** each compaction is recorded with `ts, kind, rows_removed, bytes_freed` in the compaction log feed
- **AND** the job runs once daily.

---

### Requirement: Memory Inspect Search API
The dashboard SHALL expose a search endpoint over memory contents.

#### Scenario: Search hits
- **WHEN** `GET /api/memory/inspect?q=<query>&kind=<kind>&limit=<n>` is called
- **THEN** the response is `PaginatedResponse[MemoryHit]` with `id`, `kind`, `summary`, `created_at`, `validity`, `score` (optional)
- **AND** `q` accepts a plain-text query that is matched against `summary` and other indexed fields per the memory module's existing search semantics.

---

### Requirement: Compaction Log Feed API
The dashboard SHALL expose a feed of recent compaction events.

#### Scenario: List compaction events
- **WHEN** `GET /api/memory/compaction-log?limit=50` is called
- **THEN** the response is `PaginatedResponse[CompactionEvent]` ordered `ts DESC`, default `limit=50`, max `500`.

### Requirement: memory_entity_resolve Raises on Invalid Input
The `memory_entity_resolve` MCP tool SHALL raise `ValueError` when invoked with invalid input. The tool accepts a unified `identifier` argument (preferred) or a legacy `name` argument; exactly one must be supplied with a usable value. Invalid input includes: the resolved lookup string being `null`/`None`, missing, or empty/whitespace-only; and both `name` and `identifier` being provided together. The tool SHALL NOT return an empty list in these cases. The "no candidates found" empty-list return is reserved for a well-formed non-empty lookup string that simply does not match any entity under any tier.

This requirement was motivated by a real incident (session `46f18840-4f74-4e0a-a3bf-cafa2b579f3a`, 2026-04-15) in which the lifestyle butler looped 41 times on `memory_entity_resolve` with a null lookup because the tool returned `[]` as a success, indistinguishable from a valid-query-no-match. The tool now distinguishes invalid input from no-match.

This requirement composes with the cross-cutting "MCP Tools Raise on Invalid Input" rule in `core-modules`. The cross-cutting rule is the contract; this requirement is the module-specific expression of that contract for the tool that triggered the incident, so regressions can be caught by module-local tests.

#### Scenario: Null/empty lookup raises
- **WHEN** `memory_entity_resolve` is called such that neither `identifier` nor `name` resolves to a non-empty string (the lookup is `None`, the JSON `null`, absent, `""`, or whitespace-only)
- **THEN** the tool SHALL raise `ValueError`
- **AND** SHALL NOT return an empty list

#### Scenario: Both name and identifier provided raises
- **WHEN** `memory_entity_resolve` is called with both a non-empty `name` and a non-empty `identifier`
- **THEN** the tool SHALL raise `ValueError`
- **AND** SHALL NOT return an empty list

#### Scenario: Well-formed lookup with no match returns empty list
- **WHEN** `memory_entity_resolve` is called with a non-empty `identifier` (or legacy `name`) that does not match any entity under any tier (role, exact, alias, prefix/substring, optional fuzzy)
- **THEN** the tool SHALL return an empty list
- **AND** SHALL NOT raise

## Source References
- PLAN.md §6 Phase 8 — memory fold-in scope.
- `pr/overview/settings-refactor/settings-expanded.jsx :: MemoryExpanded` is the visual reference.
- Reuses `audit.append()` from dashboard-audit-log on policy mutations.
- Existing module-memory requirements (correction-driven retraction, etc.) are unchanged by this delta.

