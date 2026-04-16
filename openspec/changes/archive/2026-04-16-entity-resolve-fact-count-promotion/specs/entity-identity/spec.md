## ADDED Requirements

### Requirement: Entity resolve case-insensitive exact tier with fact-count promotion

`memory_entity_resolve(name, entity_type=...)` SHALL treat case-insensitive matches against `canonical_name` and against any element of `aliases` as a single tier called the **exact tier**. Within the exact tier, candidates SHALL be ranked by `fact_count`, defined as the number of rows in the memory butler's `facts` table where `(entity_id = e.id OR object_entity_id = e.id) AND validity = 'active' AND invalid_at IS NULL`. Candidates whose `fact_count` equals the maximum `fact_count` in the exact tier SHALL be returned with `score = 100`. All other exact-tier candidates SHALL be returned with `score = 80`.

The returned `name_match` field for any exact-tier candidate SHALL be the literal string `"exact"`. The values `"canonical"` and `"alias"` SHALL NOT appear in the returned `name_match` field.

Each returned record SHALL include a `fact_count` integer field reflecting the count described above.

Lower tiers (`prefix`, `fuzzy`, `role`) SHALL retain their existing scoring (`prefix = 50`, `fuzzy = 20`, `role = 120`) and SHALL NOT participate in fact-count promotion.

#### Scenario: Single canonical-name match returns score 100

- **WHEN** `entity_resolve("Acme Corp", entity_type="organization")` is called
- **AND** exactly one entity exists with `canonical_name = "Acme Corp"` and no other entity has `"Acme Corp"` as either a canonical name or an alias (case-insensitive)
- **THEN** the call SHALL return one result with `score = 100`, `name_match = "exact"`, and `fact_count` populated

#### Scenario: Single alias-only match returns score 100

- **WHEN** `entity_resolve("Chloe", entity_type="person")` is called
- **AND** exactly one entity exists whose `canonical_name = "Chloe Wong"` and whose `aliases` array contains `"Chloe"` (case-insensitive)
- **AND** no other entity has `"Chloe"` as canonical name or alias
- **THEN** the call SHALL return one result with `score = 100`, `name_match = "exact"`, `canonical_name = "Chloe Wong"`, and `aliases` containing `"Chloe"`

#### Scenario: Two exact-tier candidates with unequal fact counts

- **WHEN** `entity_resolve("Sam", entity_type="person")` is called
- **AND** entity A has `canonical_name = "Sam"` with `fact_count = 12`
- **AND** entity B has `canonical_name = "Samuel Lin"` with alias `"Sam"` and `fact_count = 47`
- **THEN** the call SHALL return entity B with `score = 100, name_match = "exact"`
- **AND** entity A SHALL be returned with `score = 80, name_match = "exact"`
- **AND** results SHALL be sorted by score descending

#### Scenario: Tied fact counts return multiple results at score 100

- **WHEN** `entity_resolve("Alex", entity_type="person")` is called
- **AND** two entities each have `"Alex"` as canonical name or alias and each have `fact_count = 8`
- **THEN** both SHALL be returned with `score = 100`
- **AND** the runtime LLM is expected to surface this ambiguity to the user (via the `butler-memory` skill disambiguation rule)

#### Scenario: Zero fact counts still return at score 100

- **WHEN** `entity_resolve("NewPerson", entity_type="person")` is called
- **AND** an entity with `canonical_name = "NewPerson"` exists but has `fact_count = 0`
- **THEN** the call SHALL return that entity with `score = 100, fact_count = 0`
- **AND** the candidate SHALL NOT be filtered out solely on account of having zero facts

#### Scenario: Case-insensitive equivalence between canonical and alias

- **WHEN** `entity_resolve("chloe", entity_type="person")` is called
- **AND** entity C has `canonical_name = "Chloe"` (mixed case) with `fact_count = 5`
- **AND** entity D has alias `"CHLOE"` (uppercase) on `canonical_name = "Chloe Wong"` with `fact_count = 5`
- **THEN** both C and D SHALL appear in the exact tier
- **AND** because their `fact_count` is tied at the max, both SHALL be returned with `score = 100, name_match = "exact"`

#### Scenario: Retracted facts do not contribute to fact_count

- **WHEN** `entity_resolve("Riley", entity_type="person")` is called
- **AND** an entity matches in the exact tier and has 10 facts referencing it, of which 4 have `validity != 'active'` or `invalid_at IS NOT NULL`
- **THEN** the returned `fact_count` SHALL be 6, not 10

## MODIFIED Requirements

### Requirement: Google Account entity role

Entities with `'google_account'` in their `roles` array SHALL be treated as companion entities for Google account credential storage. They are infrastructure entities, not identity entities.

#### Scenario: Google account role recognized

- **WHEN** an entity has `roles = ['google_account']`
- **THEN** it SHALL be recognized as a Google account companion entity
- **AND** it SHALL anchor `entity_info` rows (type `google_oauth_refresh`) for that account's credentials

#### Scenario: Google account entities excluded from identity resolution

- **WHEN** `entity_resolve()` searches for entities by name
- **THEN** entities with `'google_account' = ANY(roles)` SHALL be excluded from candidate results
- **AND** they SHALL NOT appear in any resolution tier (role, exact, prefix, or fuzzy)

#### Scenario: Google account entities excluded from graph traversal defaults

- **WHEN** `entity_neighbors()` traverses the entity graph with default parameters
- **THEN** entities with `'google_account' = ANY(roles)` SHALL be excluded from traversal results
- **AND** edge facts pointing to/from google_account entities SHALL NOT be followed

#### Scenario: Google account entities excluded from dashboard entity lists

- **WHEN** the dashboard fetches entities for display (entity list, unidentified entities)
- **THEN** entities with `'google_account' = ANY(roles)` SHALL be filtered out
- **AND** they SHALL NOT appear in entity count statistics
