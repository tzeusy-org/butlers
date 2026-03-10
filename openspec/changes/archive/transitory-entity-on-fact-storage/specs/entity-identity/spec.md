## ADDED Requirements

### Requirement: Transitory entity convention via metadata.unidentified

Entities with `metadata->>'unidentified' = 'true'` SHALL be treated as **transitory entities** — pending user approval for promotion to confirmed entities. This convention is the canonical mechanism for surfacing auto-discovered entities in the dashboard for review.

Transitory entities are full `shared.entities` rows — they have a valid UUID, can be referenced by `entity_id` in facts and contacts, and participate in entity resolution and graph traversal. The only distinction is the metadata flag, which controls dashboard presentation (shown in the "Unidentified Entities" section with a visual badge).

#### Scenario: Transitory entity created by contacts system

- **WHEN** a message arrives from an unknown sender and `create_temp_contact()` is called
- **THEN** the auto-created entity MUST have `metadata` containing `{"unidentified": true, "source_channel": "<type>", "source_value": "<value>"}`
- **AND** the entity MUST appear in the dashboard "Unidentified Entities" section

#### Scenario: Transitory entity created by memory fact storage

- **WHEN** an agent stores a fact about an entity not found via `memory_entity_resolve`
- **THEN** the auto-created entity MUST have `metadata` containing `{"unidentified": true, "source": "fact_storage", "source_butler": "<butler>", "source_scope": "<scope>"}`
- **AND** the entity MUST appear in the dashboard "Unidentified Entities" section

#### Scenario: Transitory entity participates in entity resolution

- **WHEN** `memory_entity_resolve` is called with a name matching a transitory entity's `canonical_name`
- **THEN** the transitory entity MUST be returned as a candidate (same scoring as any other entity)
- **AND** the `metadata.unidentified` flag MUST NOT exclude it from resolution results

#### Scenario: Promoting a transitory entity

- **WHEN** the owner edits a transitory entity via the dashboard and removes the `unidentified` flag from metadata (or the system clears it upon confirmation)
- **THEN** the entity MUST no longer appear in the "Unidentified Entities" section
- **AND** the entity MUST continue to be a valid, confirmed entity with all its linked facts intact

#### Scenario: Merging a transitory entity into a confirmed entity

- **WHEN** the owner merges transitory entity T into confirmed entity C via `memory_entity_merge(source=T, target=C)`
- **THEN** all facts with `entity_id = T` MUST be re-pointed to `entity_id = C`
- **AND** entity T MUST be tombstoned (`metadata.merged_into = C`)
- **AND** the transitory entity MUST no longer appear in the "Unidentified Entities" section

#### Scenario: Deleting a transitory entity

- **WHEN** the owner deletes a transitory entity via the dashboard
- **THEN** all facts anchored to the entity MUST be handled per the `ON DELETE RESTRICT` FK constraint
- **AND** the entity cannot be deleted while facts reference it — facts must be retracted or re-pointed first

#### Scenario: Dashboard query for unidentified entities

- **WHEN** the dashboard fetches entities for the "Unidentified Entities" section
- **THEN** the query MUST filter on `metadata->>'unidentified' = 'true'`
- **AND** results MUST include entities created by both the contacts system and the memory fact storage system
- **AND** tombstoned entities (`metadata->>'merged_into' IS NOT NULL`) MUST be excluded
