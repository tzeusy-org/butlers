# module-memory

## ADDED Requirements

### Requirement: Registry-relational edges are out of scope for the memory facts store

The memory module's `object_entity_id` edge-facts SHALL represent **non-registry, narrative**
relationships only (episodic or coordination context that references two entities). A call to
`memory_store_fact()` whose predicate is a registry-relational predicate from
`relationship.entity_predicate_registry` — or a known underscore alias of one (e.g. `friend_of`,
`works_at`, `child_of`) — SHALL be rejected, mirroring the existing identity-contact carve-out,
and the caller SHALL be directed to `relationship_assert_fact()`. Narrative edge-facts remain
legal and continue to back `memory_entity_neighbors`.

#### Scenario: Registry-relational predicate is rejected by the memory writer
- **WHEN** `memory_store_fact()` is called with `object_entity_id` set and a predicate that
  resolves to a registry-relational predicate (e.g. `friend_of`, `works_at`, `child_of`)
- **THEN** a `ValueError` MUST be raised directing the caller to `relationship_assert_fact()`
- **AND** no row MUST be inserted into `{schema}.facts`

#### Scenario: Narrative edge-fact is still accepted
- **WHEN** `memory_store_fact()` is called with `object_entity_id` set and a non-registry
  narrative predicate (e.g. `planned_dinner_with`)
- **THEN** the edge-fact MUST be stored in `{schema}.facts`
- **AND** it MUST remain discoverable via `memory_entity_neighbors`

## Source References
- `relationship-facts` spec (Requirement: Single home for registry-relational edges)
- RFC 0006 (schema isolation)
