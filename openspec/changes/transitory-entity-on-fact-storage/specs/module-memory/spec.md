## MODIFIED Requirements

### Requirement: Mandatory entity anchoring for person facts (no generic identities)

All facts about an external entity — whether a person, organization, place, or other — MUST be anchored to a resolved `entity_id`. Storing facts with string-only subjects (no `entity_id`) is a spec violation. The `subject` field is a human-readable label only when `entity_id` is provided; it MUST NOT serve as the primary identity key.

When `memory_entity_resolve` returns zero candidates for a subject name, the agent MUST create a **transitory entity** via `memory_entity_create` with `metadata.unidentified = true` and source provenance, then use the returned `entity_id` to anchor the fact. This ensures the entity is visible in the dashboard's "Unidentified Entities" section for user review (merge, confirm, or delete).

This rule applies to all butlers with the memory module enabled: health, finance, education, travel, home, relationship, and general butlers MUST all follow this contract.

#### Scenario: Sender fact uses entity_id from identity preamble

- **WHEN** a butler receives a routed message with preamble `[Source: Owner (contact_id: abc, entity_id: def), via telegram]`
- **AND** the message contains a preference or attribute of the sender (e.g., "I liked that meal")
- **THEN** the butler MUST call `memory_store_fact` with `entity_id="def"` (from the preamble)
- **AND** `subject` MUST be a human-readable label (e.g., `"Owner"`, the sender's name)
- **AND** the butler MUST NOT store the fact with `subject="user"` and no `entity_id`

#### Scenario: Unidentified sender fact uses auto-created entity_id

- **WHEN** a butler receives a routed message with preamble `[Source: Unknown sender (contact_id: abc, entity_id: def), via telegram -- pending disambiguation]`
- **AND** the message contains information about the sender
- **THEN** the butler MUST call `memory_store_fact` with `entity_id="def"` (the auto-created entity)
- **AND** the fact is retrievable later even after the entity is merged into a known entity

#### Scenario: Unidentified entity lifecycle

- **WHEN** a temporary contact is created for an unknown sender
- **THEN** the system auto-creates a `shared.entities` entry with `metadata.unidentified = true`
- **AND** the entity appears in the dashboard entities list (`/butlers/entities`) with an "Unidentified" badge
- **AND** the owner MAY:
  1. **Flesh out**: Update the entity's `canonical_name` and `aliases` via the entity detail page
  2. **Merge into**: Merge the unidentified entity into an existing entity via contact merge (`POST /contacts/{id}/merge`) or the `memory_entity_merge` MCP tool — all facts are re-pointed to the target entity
  3. **Delete**: Retract the entity if the sender was spam or irrelevant

#### Scenario: Entity merge re-points all facts

- **WHEN** the owner merges unidentified entity U into known entity K via `memory_entity_merge(source=U, target=K)`
- **THEN** all facts with `entity_id = U` MUST be re-pointed to `entity_id = K`
- **AND** uniqueness conflicts (same `(entity_id, scope, predicate)`) MUST be resolved via supersession (higher-confidence fact wins)
- **AND** entity U MUST be tombstoned (`metadata.merged_into = K`)

#### Scenario: Mentioned person fact uses resolved entity_id

- **WHEN** a message mentions a person other than the sender (e.g., "Sarah likes coffee")
- **THEN** the butler MUST call `memory_entity_resolve("Sarah")` before storing the fact
- **AND** if resolved, MUST pass the resolved `entity_id` to `memory_store_fact`
- **AND** if unresolved (zero candidates), MUST call `memory_entity_create` with `metadata.unidentified = true` first, then use the new `entity_id`

#### Scenario: Fact stored without entity_id is a spec violation

- **WHEN** a butler calls `memory_store_fact` with a fact about an external entity (person, organization, place, or other)
- **AND** `entity_id` is `None`
- **THEN** this is a spec violation — facts about external entities MUST always be entity-anchored
- **AND** the shared `butler-memory` skill instructs all runtime instances to follow this rule

#### Scenario: Transitory entity for unknown organization from email processing

- **WHEN** a butler processes an email referencing a merchant or organization (e.g., "Nutrition Kitchen SG")
- **AND** `memory_entity_resolve("Nutrition Kitchen SG", entity_type="organization")` returns an empty list
- **THEN** the butler MUST call `memory_entity_create` with:
  - `canonical_name="Nutrition Kitchen SG"`
  - `entity_type="organization"`
  - `metadata={"unidentified": true, "source": "fact_storage", "source_butler": "<butler_name>", "source_scope": "<scope>"}`
- **AND** MUST pass the returned `entity_id` to `memory_store_fact`
- **AND** the entity MUST appear in the dashboard "Unidentified Entities" section

#### Scenario: Transitory entity for unknown place

- **WHEN** a butler stores a fact referencing a place not yet in the entity graph (e.g., "Marina Bay Sands")
- **AND** `memory_entity_resolve("Marina Bay Sands", entity_type="place")` returns an empty list
- **THEN** the butler MUST create a transitory entity with `entity_type="place"` and `metadata.unidentified = true`
- **AND** MUST use the returned `entity_id` for the fact

#### Scenario: Idempotent entity creation on duplicate name

- **WHEN** `memory_entity_create` raises a unique constraint violation (because the entity already exists for this `(tenant_id, canonical_name, entity_type)`)
- **THEN** the agent MUST catch the error and call `memory_entity_resolve` to obtain the existing entity's `entity_id`
- **AND** MUST use that `entity_id` to anchor the fact
- **AND** MUST NOT treat the duplicate as a failure

#### Scenario: Entity type inference from context

- **WHEN** a butler creates a transitory entity during fact storage
- **THEN** the butler SHOULD infer the `entity_type` from context:
  - Merchant/company/service names → `organization`
  - Person names → `person`
  - Location names → `place`
  - Unknown/ambiguous → `other`
