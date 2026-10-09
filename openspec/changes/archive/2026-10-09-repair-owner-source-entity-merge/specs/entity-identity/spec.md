## MODIFIED Requirements

### Requirement: Entity merge preserves roles

When two entities are merged via `entity_merge()`, the target entity MUST inherit all roles from the source entity (union, deduplicated).

**Implementation note:** the relationship-owned `merge_entity_pair()` service implements role
union. `memory_entity_merge` is a compatibility dispatch to that single authority.

#### Scenario: Merge source with roles into target

- **WHEN** source entity has `roles = ['trusted']` and target has `roles = ['owner']`
- **THEN** after merge, target MUST have `roles = ['owner', 'trusted']`

#### Scenario: Merge entities with no roles

- **WHEN** both source and target have `roles = []`
- **THEN** after merge, target MUST have `roles = []`

#### Scenario: Owner source becomes a tombstone without duplicating the owner

- **WHEN** the source is the singleton owner and an authorized merge keeps a different live target
- **THEN** the target MUST receive the ordered, deduplicated role union including owner
- **AND** the source tombstone MUST retain its other roles and MUST no longer hold owner
- **AND** the owner singleton constraint MUST remain enabled without a committed ownerless state

#### Scenario: Downstream merge failure restores the original owner

- **WHEN** a merge fails after owner roles and references have moved inside its transaction
- **THEN** the original source and target roles, metadata, aliases and references MUST be restored
- **AND** no successful merge audit, rebind receipt cohort or post-commit fleet event MUST be emitted
