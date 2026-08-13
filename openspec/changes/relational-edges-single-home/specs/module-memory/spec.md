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

### Requirement: Consolidation narrative edges use an exact local allowlist

For newly consolidated facts only, the storage boundary SHALL admit an
`object_entity_id` edge only after its literal predicate is classified by
the versioned local v1 allowlist: `planned_dinner_with`,
`wake_coordination`, and `social_exchange_with`. The storage boundary MUST
reject every other predicate and an unavailable or missing classification
before that artifact can write a fact or evidence link. This consolidation-only
guard SHALL NOT query or write `relationship.entity_predicate_registry` or
`relationship.entity_facts`, and SHALL NOT change generic
`memory_store_fact()` admission behavior.

#### Scenario: Approved consolidation narrative edge persists
- **WHEN** the consolidation executor submits a new fact with
  `object_entity_id` and predicate `planned_dinner_with`, `wake_coordination`,
  or `social_exchange_with`
- **THEN** the storage boundary MUST persist the edge in `{schema}.facts`
- **AND** the existing evidence, tenant, cardinality, retry, lease, and
  idempotence behavior MUST remain in effect

#### Scenario: Unapproved or unavailable consolidation edge is rejected
- **WHEN** the consolidation executor submits a new fact with
  `object_entity_id` whose predicate is not in the local allowlist, or the
  consolidation edge classification is unavailable
- **THEN** the storage boundary MUST raise `ValueError` before inserting a fact
- **AND** it MUST NOT write an evidence link for that rejected artifact
- **AND** it MUST preserve the executor's established group lifecycle policy
- **AND** the generic `memory_store_fact()` path MUST remain unaffected

## Source References
- `relationship-facts` spec (Requirement: Single home for registry-relational edges)
- RFC 0006 (schema isolation)
