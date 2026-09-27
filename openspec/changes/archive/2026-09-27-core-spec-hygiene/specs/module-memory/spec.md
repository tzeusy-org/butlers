## MODIFIED Requirements

### Requirement: Consolidation executor with per-action error isolation

The consolidation executor SHALL apply parsed consolidation results to the database. Each action (new fact, updated fact, new rule, confirmation) SHALL be wrapped in its own try/except block so that one valid action failure does not prevent remaining valid actions from executing. Before any action is attempted for a non-empty source group, the executor MUST validate every fact and rule artifact's exact episode evidence. The executor MUST propagate tenant_id and request_id from the source episode group to all derived writes.

ID: REQ-module-memory-006

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

### Requirement: Consolidation narrative edges use an exact local allowlist

For newly consolidated facts only, the storage boundary SHALL admit an
`object_entity_id` edge only after its literal predicate is classified by the
versioned local v1 allowlist: `planned_dinner_with`, `wake_coordination`, and
`social_exchange_with`. The storage boundary MUST reject every other predicate
and an unavailable or missing classification before that artifact can write a
fact or evidence link. This consolidation-only guard SHALL NOT query or write
`relationship.entity_predicate_registry` or `relationship.entity_facts`, and
SHALL NOT change generic `memory_store_fact()` admission behavior.

ID: REQ-module-memory-012
Source: [Observed] PR #3728; `openspec/changes/archive/2026-09-27-relational-edges-single-home/landed-b5-b6-transfer.md`

#### Scenario: Approved consolidation narrative edge persists

- **WHEN** the consolidation executor submits a new fact with
  `object_entity_id` and predicate `planned_dinner_with`, `wake_coordination`,
  or `social_exchange_with`
- **THEN** the storage boundary MUST persist the edge in `{schema}.facts`
- **AND** the existing evidence, tenant, cardinality, retry, lease, and
  idempotence behavior MUST remain in effect

#### Scenario: Consolidation preserves a well-formed edge target

- **WHEN** consolidation output contains a well-formed `object_entity_id`
- **THEN** the executor MUST preserve that target and forward the new edge to
  the consolidation storage boundary for exact-allowlist classification
- **AND** when the target is malformed, it MUST reject that new fact rather
  than silently downgrading it to a property fact or the generic writer path

#### Scenario: Unapproved or unavailable consolidation edge is rejected

- **WHEN** the consolidation executor submits a new fact with
  `object_entity_id` whose predicate is not in the local allowlist, or the
  consolidation edge classification is unavailable
- **THEN** the storage boundary MUST raise `ValueError` before inserting a fact
- **AND** it MUST NOT write an evidence link for that rejected artifact
- **AND** it MUST preserve the executor's established group lifecycle policy
- **AND** the generic `memory_store_fact()` path MUST remain unaffected

### Requirement: memory_entity_resolve Raises on Invalid Input
The `memory_entity_resolve` MCP tool SHALL raise `ValueError` when invoked with invalid input. The tool accepts a unified `identifier` argument (preferred) or a legacy `name` argument; exactly one must be supplied with a usable value. Invalid input includes: the resolved lookup string being `null`/`None`, missing, or empty/whitespace-only; and both `name` and `identifier` being provided together. The tool SHALL NOT return an empty list in these cases. The "no candidates found" empty-list return is reserved for a well-formed non-empty lookup string that simply does not match any entity under any tier.

Returning `[]` for a null lookup would be indistinguishable from a valid-query-no-match and can drive a caller into a retry loop, so invalid input and no-match are distinct outcomes.

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
