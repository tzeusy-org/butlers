## MODIFIED Requirements

### Requirement: Relationship Butler Tool Surface

The implementation SHALL provide the behavior described by this requirement.
The relationship butler exposes a comprehensive personal CRM tool set with
module-owned canonical MCP names.

#### Scenario: Tool inventory
- **WHEN** a runtime instance is spawned for the relationship butler
- **THEN** it has access to 40+ tools including: contact CRUD (`contact_create`, `contact_update`, `contact_get`, `contact_search`, `contact_archive`, `contact_resolve`), relationship management (`relationship_add`, `relationship_list`, `relationship_remove`), date tracking (`date_add`, `date_list`, `upcoming_dates`), notes (`note_create`, `note_list`, `note_search`), interactions (`interaction_log`, `interaction_list`), reminders (`reminder_create`, `reminder_list`, `reminder_dismiss`), gifts (`gift_add`, `gift_update_status`, `gift_list`), loans (`loan_create`, `loan_settle`, `loan_list`), groups (`group_create`, `group_add_member`, `group_list`, `group_members`), labels (`label_create`, `label_assign`, `contact_search_by_label`), facts (`fact_set`, `fact_list`), the ungrouped registry-relational edge writer (`relationship_assert_fact`), Relationship entity reads (`entity_resolve`, `entity_get`, `entity_neighbors`, `relationship_fact_evidence`, `relationship_predicate_coverage`, `relationship_lookup`), feed (`feed_get`), memory-owned entity creation (`memory_entity_create`), memory (`memory_store_fact`), and calendar tools
- **AND** no bare `entity_create` MCP tool or alias is registered

> NOTE: `feed_get` is specified but not yet implemented in the relationship module (no `feed_get` tool or library function exists as of this audit). It remains in scope as intent; a remediation issue tracks building it.

## ADDED Requirements

### Requirement: Read-only Relationship entity tool group

The Relationship module's `entity` tool group SHALL contain exactly six
read-only MCP tools: `entity_resolve`, `entity_get`, `entity_neighbors`,
`relationship_fact_evidence`, `relationship_predicate_coverage`, and
`relationship_lookup`. The mutating tools `entity_update` and
`relationship_record_coverage` MUST belong to a separate `entity_write` group,
which the Relationship roster MUST leave inactive. The approved-action handler
`relationship_assert_fact` MUST remain registered independently of both groups
and retain its existing approval behavior.

The memory module's group taxonomy is independent. Entity creation MUST remain
owned by that module under the canonical MCP name `memory_entity_create`; the
Relationship module MUST NOT register a bare `entity_create` alias.

#### Scenario: Selecting the read group registers the exact six reads

- **WHEN** Relationship module tools are registered on FastMCP with
  `groups = ["entity"]`
- **THEN** `entity_resolve`, `entity_get`, `entity_neighbors`,
  `relationship_fact_evidence`, `relationship_predicate_coverage`, and
  `relationship_lookup` MUST be registered
- **AND** `entity_update` and `relationship_record_coverage` MUST NOT be
  registered
- **AND** `relationship_assert_fact` MUST remain registered independently
- **AND** a bare `entity_create` tool MUST NOT be registered

#### Scenario: Omitting both entity groups prunes every grouped entity tool

- **WHEN** Relationship module tools are registered with an explicit group
  allowlist containing neither `entity` nor `entity_write`
- **THEN** all six read-group tools MUST be absent from FastMCP
- **AND** `entity_update` and `relationship_record_coverage` MUST be absent
- **AND** `relationship_assert_fact` MUST remain registered for approved replay

#### Scenario: Roster activation has an exact relevant inventory

- **WHEN** the separately approved implementation activates `entity` but not
  `entity_write` in `[modules.relationship].groups`
- **THEN** the six read-group tools MUST be present on the Relationship module
  surface and the two grouped writes MUST be absent
- **AND** the separately configured memory module MAY expose
  `memory_entity_create` under that prefixed canonical name
- **AND** the combined daemon surface MUST NOT contain a bare `entity_create`
  alias

#### Scenario: Missing evidence and unchecked coverage stay explicit

- **WHEN** `relationship_fact_evidence` is called with an unknown fact id
- **THEN** it MUST return
  `{fact: null, provenance: null, evidence: []}` rather than claim that a known
  fact has no evidence
- **WHEN** `relationship_predicate_coverage` finds no coverage receipt for an
  available entity and requested predicate
- **THEN** that predicate's state MUST be `unknown`, never `absent_proven`
- **AND** a missing or merged-away target MUST be reported as `unavailable`,
  never as proof that the predicate is absent

#### Scenario: Read failures remain distinguishable from domain misses

- **WHEN** a backing store or handler needed by any of the six read-group tools
  fails
- **THEN** the MCP call MUST surface a failure through the normal error path
- **AND** it MUST NOT return a fabricated successful miss, empty collection,
  `unknown`, `unavailable`, or `absent_proven` result

## Source References

- Non-Negotiable Rule 2 (modules only add tools)
- Non-Negotiable Rule 5 (git-based config defines butler identity)
- Non-Negotiable Rule 6 (the Relationship manifesto governs scope)
- RFC 0002 (MCP tool surface, module registration, and tool groups)
- RFC 0004 (entity and contact resolution)
- `relationship-facts` spec (fact evidence and predicate coverage semantics)
- Owner decision `bu-uedyz` Choice C
