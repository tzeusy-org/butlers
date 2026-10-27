## ADDED Requirements

### Requirement: Owner-asserted person posture

`public.entities` SHALL carry `posture TEXT NOT NULL DEFAULT 'active'` constrained to `active`, `memorial`, `quiet` or `no_contact`, with `posture_since DATE` and `posture_set_by TEXT`. Posture SHALL be asserted only by the owner through the Relationship butler's `entity_set_posture` tool and SHALL NOT be inferred. Posture SHALL be independent of `listed`, and changing it SHALL NOT delete, archive or hide any memory, fact or chronicle. A database trigger SHALL refuse a change to any posture column from a butler runtime role other than `butler_relationship_rw`.

#### Scenario: Default posture is active

- **WHEN** an entity is created without a posture
- **THEN** its posture SHALL be `active`

#### Scenario: Setting the same posture is a no-op

- **WHEN** `entity_set_posture` is called with the posture the entity already has
- **THEN** the entity row SHALL NOT be written and no audit row SHALL be appended

#### Scenario: Posture value is never audited

- **WHEN** `entity_set_posture` changes a posture
- **THEN** the audit row SHALL name the entity and SHALL NOT contain the posture value

#### Scenario: Non-Relationship role cannot change posture

- **WHEN** a butler runtime role other than `butler_relationship_rw` updates a posture column
- **THEN** the update SHALL fail with `insufficient_privilege` while other entity columns stay writable

#### Scenario: Rollback restores behavior

- **WHEN** an entity's posture is set back to `active`
- **THEN** every behavior suppressed by its former posture SHALL resume without data restoration

#### Scenario: Owner entity has no posture

- **WHEN** `entity_set_posture` targets the owner entity
- **THEN** the tool SHALL refuse
