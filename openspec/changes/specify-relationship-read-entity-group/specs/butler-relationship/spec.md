## ADDED Requirements

### Requirement: Read-only Relationship entity tool group

The Relationship module's `entity` tool group SHALL contain exactly six
read-only MCP tools: `entity_resolve`, `entity_get`, `entity_neighbors`,
`relationship_fact_evidence`, `relationship_predicate_coverage`, and
`relationship_lookup`. The mutating tools `entity_update`,
`relationship_record_coverage`, and `entity_set_posture` MUST belong to a separate `entity_write` group,
which the Relationship roster MUST leave inactive. The approved-action handler
`relationship_assert_fact` MUST remain registered independently of both groups
and retain its existing approval behavior.

The memory module's group taxonomy is independent. Entity creation MUST remain
owned by that module under the canonical MCP name `memory_entity_create`; the
Relationship module MUST NOT register a bare `entity_create` alias.

ID: REQ-butler-relationship-003
Source: Closed bu-t89d9m option A (2026-10-20 14:36 UTC), RFC 0002 and relationship-facts read contracts
Scope: v1-mandatory

#### Scenario: Selecting the read group registers the exact six reads

- **WHEN** Relationship module tools are registered on FastMCP with
  `groups = ["entity"]`
- **THEN** `entity_resolve`, `entity_get`, `entity_neighbors`,
  `relationship_fact_evidence`, `relationship_predicate_coverage`, and
  `relationship_lookup` MUST be registered
- **AND** `entity_update`, `relationship_record_coverage`, and `entity_set_posture` MUST NOT be
  registered
- **AND** `relationship_assert_fact` MUST remain registered independently
- **AND** a bare `entity_create` tool MUST NOT be registered

#### Scenario: Omitting both entity groups prunes every grouped entity tool

- **WHEN** Relationship module tools are registered with an explicit group
  allowlist containing neither `entity` nor `entity_write`
- **THEN** all six read-group tools MUST be absent from FastMCP
- **AND** `entity_update`, `relationship_record_coverage`, and `entity_set_posture` MUST be absent
- **AND** `relationship_assert_fact` MUST remain registered for approved replay

#### Scenario: Roster activation has an exact relevant inventory

- **WHEN** the implementation approved by closed `bu-t89d9m` option A
  (2026-10-20 14:36 UTC) activates `entity` but not
  `entity_write` in `[modules.relationship].groups`
- **THEN** the six read-group tools MUST be present on the Relationship module
  surface and the three grouped writes MUST be absent
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
- Owner decision `bu-uedyz` Choice C; closed implementation decision
  `bu-t89d9m` option A (2026-10-20 14:36 UTC)
