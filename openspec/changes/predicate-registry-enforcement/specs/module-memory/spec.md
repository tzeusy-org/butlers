## MODIFIED Requirements

### Requirement: Optional predicate registry for consistency guidance

The module MUST maintain a `predicate_registry` table that enforces `is_edge` and `is_temporal` constraints at write time for registered predicates. LLM extractors SHOULD prefer known predicates from the registry but MAY use novel predicates. Novel predicates MUST be auto-registered after successful storage. The registry MUST NOT block writes for unregistered predicates.

#### Scenario: predicate_registry table schema

- **WHEN** the predicate registry migration runs
- **THEN** the `predicate_registry` table MUST be created with columns:
  - `name` (TEXT PRIMARY KEY) — the predicate string
  - `expected_subject_type` (TEXT, nullable) — suggested entity_type for subject
  - `expected_object_type` (TEXT, nullable) — suggested entity_type for object (NULL for property-facts)
  - `is_edge` (BOOLEAN, default false) — whether this predicate requires `object_entity_id`
  - `is_temporal` (BOOLEAN, default false) — whether this predicate requires `valid_at`
  - `description` (TEXT, nullable) — human-readable description of the predicate
  - `created_at` (TIMESTAMPTZ, default now())

#### Scenario: Seed predicates

- **WHEN** the predicate registry is initialized
- **THEN** it SHOULD be seeded with existing property predicates from the fact-extraction taxonomy (e.g., `birthday`, `occupation`, `preference`, `allergy`)
- **AND** edge predicates SHOULD be seeded: `knows`, `works_at`, `lives_with`, `manages`, `parent_of`, `sibling_of`, `lives_in`, `member_of`
- **AND** edge predicates MUST have `is_edge = true` and appropriate `expected_subject_type`/`expected_object_type` values
- **AND** temporal predicates SHOULD be seeded with `is_temporal = true` to indicate predicates that form time series (e.g., `meal_breakfast`, `meal_lunch`, `meal_dinner`, `meal_snack` for meal facts with nutrition metadata at different times)
- **AND** domain CRUD-to-SPO migration predicates MUST be seeded per the taxonomy defined in `openspec/specs/predicate-taxonomy.md` (bu-ddb epic), covering health, relationship, finance, and home domains

#### Scenario: Temporal predicates guidance

- **WHEN** a predicate is marked with `is_temporal = true` in the registry
- **THEN** `store_fact()` MUST require `valid_at` to be set when this predicate is used
- **AND** if `valid_at` is NULL, `store_fact()` MUST raise a `ValueError` explaining that the predicate is temporal and requires a timestamp
- **AND** multiple facts with the same subject/predicate but different `valid_at` values represent a temporal sequence and MUST NOT supersede each other

#### Scenario: Edge predicates enforcement

- **WHEN** a predicate is marked with `is_edge = true` in the registry
- **THEN** `store_fact()` MUST require `object_entity_id` to be set when this predicate is used
- **AND** if `object_entity_id` is NULL, `store_fact()` MUST raise a `ValueError` explaining that the predicate is an edge predicate and requires a target entity

#### Scenario: predicate_list tool

- **WHEN** the `predicate_list` MCP tool is called
- **THEN** all rows from `predicate_registry` MUST be returned
- **AND** each row MUST include: `name`, `expected_subject_type`, `expected_object_type`, `is_edge`, `is_temporal`, `description`
- **AND** results MUST be ordered by `name ASC`

#### Scenario: predicate_list with edge filter

- **WHEN** `predicate_list` is called with `edges_only=true`
- **THEN** only predicates with `is_edge = true` MUST be returned

#### Scenario: Registry enforcement applies only to registered predicates

- **WHEN** `store_fact` is called with a predicate NOT in the registry
- **THEN** the fact MUST still be stored successfully regardless of `object_entity_id` or `valid_at` values
- **AND** no `is_edge` or `is_temporal` enforcement error MUST be raised

#### Scenario: Novel predicates are auto-registered

- **WHEN** `store_fact()` succeeds with a predicate not in the registry
- **THEN** the predicate MUST be auto-inserted into `predicate_registry` with `is_edge` and `is_temporal` inferred from the call parameters
- **AND** the insert MUST use `ON CONFLICT (name) DO NOTHING`

## ADDED Requirements

### Requirement: memory_predicate_search MCP tool

The module MUST expose a `memory_predicate_search` MCP tool that allows LLMs to search the predicate registry by name prefix and description text before creating facts.

#### Scenario: Search returns matching predicates

- **WHEN** `memory_predicate_search(query="parent")` is called
- **THEN** predicates matching by name prefix or description text MUST be returned
- **AND** each result MUST include `name`, `is_edge`, `is_temporal`, `description`, `expected_subject_type`, `expected_object_type`

#### Scenario: Search with empty query returns all

- **WHEN** `memory_predicate_search(query="")` is called
- **THEN** all registered predicates MUST be returned

### Requirement: Structured error responses from memory_store_fact MCP tool

The `memory_store_fact` MCP tool MUST catch all `ValueError` exceptions from the storage layer and return structured error dicts instead of letting exceptions propagate as MCP `isError=true` frames.

#### Scenario: All validation errors return structured dict

- **WHEN** `memory_store_fact` is called and the underlying `store_fact()` raises a `ValueError`
- **THEN** the MCP tool MUST return a dict with keys `"error"`, `"message"`, and `"recovery"`
- **AND** the `"recovery"` key MUST contain specific next steps the LLM should take to fix the problem
- **AND** the MCP response MUST NOT have `isError = true`

#### Scenario: Recovery steps are context-specific

- **WHEN** the error is about a missing entity
- **THEN** recovery MUST suggest `memory_entity_resolve()` or `memory_entity_create()`
- **WHEN** the error is about a missing `object_entity_id` for an edge predicate
- **THEN** recovery MUST suggest resolving the target entity and retrying with `object_entity_id`
- **WHEN** the error is about a missing `valid_at` for a temporal predicate
- **THEN** recovery MUST suggest providing an ISO-8601 timestamp
