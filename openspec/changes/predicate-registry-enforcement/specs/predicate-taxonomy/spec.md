## MODIFIED Requirements

### Requirement: Predicate registry is an enforced contract

The `predicate_registry` table transitions from advisory documentation to an enforced contract. The `is_edge` and `is_temporal` columns become write-time constraints checked by `store_fact()`. Novel predicates are auto-inserted after first successful use to keep the registry current.

#### Scenario: is_edge enforcement at write time

- **WHEN** `store_fact()` is called with a predicate registered as `is_edge = true`
- **THEN** if `object_entity_id` is NULL, the write MUST be rejected with a `ValueError`
- **AND** the error message MUST name the predicate and explain that it requires a target entity

#### Scenario: is_temporal enforcement at write time

- **WHEN** `store_fact()` is called with a predicate registered as `is_temporal = true`
- **THEN** if `valid_at` is NULL, the write MUST be rejected with a `ValueError`
- **AND** the error message MUST explain that temporal predicates require a timestamp to prevent data loss via supersession

#### Scenario: Unregistered predicates remain unrestricted

- **WHEN** `store_fact()` is called with a predicate NOT in `predicate_registry`
- **THEN** the fact MUST be stored successfully regardless of `object_entity_id` or `valid_at` values
- **AND** no enforcement error MUST be raised

#### Scenario: Auto-registration of novel predicates

- **WHEN** `store_fact()` succeeds with a predicate not in the registry
- **THEN** the predicate MUST be inserted into `predicate_registry` with:
  - `is_edge` inferred as `true` if `object_entity_id` was provided, `false` otherwise
  - `is_temporal` inferred as `true` if `valid_at` was provided, `false` otherwise
  - `expected_subject_type` inferred from the entity's `entity_type` if `entity_id` was provided
  - `description` set to NULL (auto-registered, not manually documented)
- **AND** the insert MUST use `ON CONFLICT (name) DO NOTHING` to handle concurrent writes safely

#### Scenario: Auto-registered predicates are enforced on subsequent writes

- **WHEN** a predicate is auto-registered with `is_edge = true` from its first usage
- **AND** a subsequent `store_fact()` call uses the same predicate without `object_entity_id`
- **THEN** the write MUST be rejected with the same enforcement error as manually-seeded edge predicates

#### Scenario: Predicate normalization before registry lookup

- **WHEN** the MCP `memory_store_fact` tool normalizes a predicate (lowercase, underscores, strip `is_` prefix)
- **THEN** the normalized form MUST be used for registry lookup
- **AND** the normalized form MUST be what gets stored in the `facts` table
- **AND** the normalized form MUST match against existing registry entries (e.g., `"Is-Parent Of"` normalizes to `"parent_of"` which matches the seeded registry entry)
