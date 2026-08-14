## MODIFIED Requirements

### Requirement: LLM-driven memory consolidation pipeline

The consolidation pipeline SHALL transform unconsolidated episodes into durable facts and rules via a multi-step process: fetch pending episodes, group by `(tenant_id, source butler)`, build a prompt with existing context, spawn an LLM CLI session, parse the structured JSON output, validate artifact evidence, and execute the extracted actions against the database. All derived facts and rules MUST inherit the tenant context from their source episodes.

#### Scenario: Episode grouping by tenant and butler

- **WHEN** `run_consolidation` is called
- **THEN** episodes with `consolidation_status='pending'` MUST be fetched ordered by `(tenant_id, butler, created_at, id)` with `FOR UPDATE SKIP LOCKED`
- **AND** episodes MUST be grouped by the composite key `(tenant_id, butler)`, not by `butler` alone
- **AND** existing active facts (up to 100) and rules (up to 50) for each butler MUST be fetched for dedup context, scoped to the same `tenant_id`

#### Scenario: Consolidation with LLM spawner

- **WHEN** a `cc_spawner` is provided to `run_consolidation`
- **THEN** for each `(tenant_id, butler)` group, a runtime session MUST be spawned with `trigger_source='schedule:consolidation'`
- **AND** the runtime output MUST be parsed for a JSON block containing `new_facts`, `updated_facts`, `new_rules`, and `confirmations`
- **AND** a successful runtime result with missing or blank output MUST fail the group with an actionable error so its episodes remain eligible for retry rather than being marked consolidated
- **AND** partial failures in one group MUST NOT block other groups from processing

#### Scenario: Artifact output names exact episode evidence

- **WHEN** a non-empty episode group is formatted for consolidation
- **THEN** each rendered episode MUST expose its UUID to the runtime session
- **AND** every emitted `new_facts`, `updated_facts`, and `new_rules` entry MUST include a non-empty `evidence_episode_ids` array naming only the claimed episode UUIDs that support that artifact

#### Scenario: Invalid artifact evidence fails the group before persistence

- **WHEN** any emitted fact or rule artifact has absent, empty, malformed, duplicate, or out-of-group `evidence_episode_ids`
- **THEN** the consolidation group MUST use the existing failed/dead-letter retry path
- **AND** no fact, rule, `memory_links` row, or consolidated episode state from that group MAY be persisted before the failure

#### Scenario: Scheduled consolidation uses the catalog-backed daemon spawner

- **WHEN** the deterministic `memory_consolidation` scheduled-job handler runs
- **THEN** it MUST pass the daemon's live `Spawner` to `run_consolidation` rather than using the `cc_spawner=None` dry-run path
- **AND** an empty pending-episode claim MUST spawn no runtime session
- **AND** each non-empty `(tenant_id, butler)` group MUST use `trigger_source='schedule:consolidation'` without overriding model, runtime, or session timeout, so model selection, spend-routing policy, quotas, failover, and timeout remain authoritative in the model catalog and `Spawner`
- **AND** database and embedding resolution MUST use the active memory module's runtime pool and configured embedding-engine lifecycle, including any private `memory_schema`, rather than the daemon's domain pool or the embedding helper's default model
- **AND** the handler's returned consolidation statistics or raised error MUST remain the scheduled task result recorded by the scheduler

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

The consolidation executor SHALL apply parsed consolidation results to the database. Each action (new fact, updated fact, new rule, confirmation) SHALL be wrapped in its own try/except block so that one valid action failure does not prevent remaining valid actions from executing. Before any action is attempted for a non-empty source group, the executor MUST validate every fact and rule artifact's exact episode evidence. The executor MUST propagate tenant_id and request_id from the source episode group to all derived writes.

#### Scenario: New facts stored with tenant context and exact derived_from links

- **WHEN** the executor processes a valid `new_facts` entry
- **THEN** `store_fact` MUST be called with the entry's fields, `source_butler` set to the butler name, and `tenant_id` set to the episode group's tenant_id
- **AND** a `valid_at` value on the entry MUST be forwarded so the fact is stored as a temporal observation
- **AND** exactly one `derived_from` link MUST be created from the new fact to each UUID in that entry's validated `evidence_episode_ids`, and none to another claimed episode
- **AND** the fact write and all of those links MUST commit atomically

#### Scenario: Updated facts trigger supersession with tenant context

- **WHEN** the executor processes a property `updated_facts` entry without `valid_at`
- **THEN** the parser MUST require only `target_id` and replacement `content`, MAY accept `permanence`, and MUST NOT require model-supplied `subject`, `predicate`, `entity_id`, or `scope`
- **AND** unrecognized legacy identity fields MAY be ignored rather than copied into the internal update action
- **AND** it MUST reload the live target fact identified by `target_id`, scoped to the same tenant and source butler
- **AND** the target MUST be a property fact rather than an entity-edge fact
- **AND** it MUST use the target fact's persisted subject, predicate, entity ID, and scope as the supersession identity key
- **AND** temporal-predicate classification MUST use the persisted target predicate, including predicate aliases, rather than the repeated model-output predicate
- **AND** `store_fact` MUST atomically verify that `target_id` remains the current fact for that identity key before superseding it
- **AND** a missing, stale, cross-tenant, cross-source, temporal, or entity-edge target MUST be rejected without preventing later consolidation actions
- **AND** exactly one `derived_from` link MUST be created from the new fact to each UUID in that entry's validated `evidence_episode_ids`, and none to another claimed episode
- **AND** the updated fact write and all of those links MUST commit atomically

#### Scenario: Temporal observations are not updated facts

- **WHEN** consolidation output contains an `updated_facts` entry with a non-null `valid_at`
- **THEN** the parser MUST reject the entry and the executor MUST NOT write it
- **AND** when `valid_at` is omitted but the predicate registry marks the predicate as temporal, the executor MUST reject the entry before calling `store_fact`
- **AND** the consolidation prompt MUST direct temporal observations to `new_facts`, where `valid_at` preserves coexistence rather than supersession

#### Scenario: New rules stored with tenant context and exact derived_from links

- **WHEN** the executor processes a valid `new_rules` entry
- **THEN** `store_rule` MUST be called with `tenant_id` set to the episode group's tenant_id
- **AND** exactly one `derived_from` link MUST be created from the new rule to each UUID in that entry's validated `evidence_episode_ids`, and none to another claimed episode
- **AND** the rule write and all of those links MUST commit atomically

#### Scenario: Source episodes marked as consolidated

- **WHEN** all actions for a group have been executed
- **THEN** all source episodes MUST be marked with `consolidated=true` and `consolidation_status='consolidated'` with leases cleared

#### Scenario: Individual action failures do not block others

- **WHEN** storing one valid new fact fails with an exception
- **THEN** the error MUST be logged and added to the `errors` list
- **AND** subsequent valid actions MUST still be attempted

#### Scenario: Memory events include tenant_id

- **WHEN** consolidation emits memory_events (success or failure)
- **THEN** the INSERT MUST include `tenant_id` from the episode group being processed
- **AND** the INSERT MUST include `actor_butler` with the butler name
