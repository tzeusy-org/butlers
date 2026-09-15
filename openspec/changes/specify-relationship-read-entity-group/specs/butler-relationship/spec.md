## MODIFIED Requirements

### Requirement: Relationship Butler Registered Tool Surface

The relationship butler SHALL expose its currently approved manifesto-owned
personal CRM tool set. Its eight configured relationship-module groups
(`contacts`, `contacts_extended`, `interactions`, `relationships`, `social`,
`notes`, `tracking`, and `management`) own 58 tools. The mandatory
`relationship_assert_fact` approval-dispatch handler registers unconditionally,
for 59 relationship-module handlers in total. The mixed `entity` group SHALL
remain disabled until the adopted six-read/two-write split is implemented; it
MUST NOT be activated while it still exposes both reads and writes.

The separately approved implementation SHALL replace that current inventory
with nine configured groups owning 64 tools, while the mandatory unconditional
handler makes 65 relationship-module handlers in total. The ninth group SHALL
be the read-only `entity` group; the two `entity_write` tools SHALL remain
inactive.

#### Scenario: Exact current registered inventory

- **WHEN** a runtime instance is spawned for the relationship butler
- **THEN** all 58 tools owned by the eight configured groups SHALL be registered
- **AND** the inventory SHALL include `contact_create`, `contact_update`,
  `contact_get`, `contact_search`, `contact_archive`, `contact_resolve`,
  `relationship_add`, `relationship_list`, `relationship_remove`, `date_add`,
  `date_list`, `upcoming_dates`, `note_create`, `note_list`, `note_search`,
  `interaction_log`, `interaction_list`, `fact_set`, `fact_list`, and `feed_get`
- **AND** `relationship_assert_fact` SHALL be the additional mandatory unconditional handler, making 59 relationship-module handlers total
- **AND** `entity_resolve`, `entity_get`, `entity_neighbors`,
  `relationship_fact_evidence`, `relationship_predicate_coverage`,
  `relationship_lookup`, `entity_update`, and `relationship_record_coverage`
  SHALL all be absent
- **AND** no bare `entity_create` MCP tool or alias SHALL be registered
- **AND** the separately configured memory module MAY expose
  `memory_entity_create` under that canonical prefixed name

#### Scenario: Tool inventory after the read-only split

- **WHEN** the separately approved implementation activates the read-only
  Relationship `entity` group
- **THEN** all 64 group-owned relationship tools SHALL be registered, including
  the six Relationship entity reads (`entity_resolve`, `entity_get`,
  `entity_neighbors`, `relationship_fact_evidence`,
  `relationship_predicate_coverage`, and `relationship_lookup`)
- **AND** `relationship_assert_fact` SHALL be the additional mandatory
  unconditional handler, making 65 relationship-module handlers total
- **AND** `entity_update` and `relationship_record_coverage` SHALL remain absent
  from the active surface
- **AND** the separately configured memory module MAY expose
  `memory_entity_create` under that canonical prefixed name
- **AND** no bare `entity_create` MCP tool or alias SHALL be registered

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
