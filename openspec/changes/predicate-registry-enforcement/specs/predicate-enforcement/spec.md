## ADDED Requirements

### Requirement: Predicate normalization before storage and registry lookup

All predicates passing through the MCP `memory_store_fact` tool MUST be normalized to a canonical form before storage and before any registry lookup. The normalized form is what gets stored in the `facts` table and what gets matched against `predicate_registry`.

#### Scenario: Lowercase normalization

- **WHEN** `memory_store_fact` is called with predicate `"Birthday"` or `"BIRTHDAY"`
- **THEN** the predicate MUST be normalized to `"birthday"` before storage and registry lookup

#### Scenario: Hyphen and space replacement

- **WHEN** `memory_store_fact` is called with predicate `"job-title"` or `"job title"`
- **THEN** the predicate MUST be normalized to `"job_title"` before storage and registry lookup

#### Scenario: Strip leading is_ prefix

- **WHEN** `memory_store_fact` is called with predicate `"is_parent_of"`
- **THEN** the predicate MUST be normalized to `"parent_of"` before storage and registry lookup

#### Scenario: Combined normalization

- **WHEN** `memory_store_fact` is called with predicate `"Is-Parent Of"`
- **THEN** the predicate MUST be normalized to `"parent_of"` (lowercase → replace hyphens/spaces → strip `is_` prefix)

#### Scenario: Already-canonical predicates are unchanged

- **WHEN** `memory_store_fact` is called with predicate `"parent_of"`
- **THEN** the predicate MUST remain `"parent_of"` (no transformation applied to canonical forms)

#### Scenario: Internal callers bypass normalization

- **WHEN** `store_fact()` in `storage.py` is called directly by butler tools (not via the MCP tool)
- **THEN** no normalization MUST be applied — the predicate is stored as-is
- **AND** this preserves backward compatibility for internal tools that already use canonical snake_case predicates

---

### Requirement: Registry enforcement of is_edge constraint at write time

When a predicate exists in `predicate_registry` with `is_edge = true`, the `store_fact()` storage function MUST require `object_entity_id` to be set. This enforces that relationship predicates produce proper edge-facts instead of malformed property facts.

#### Scenario: Edge predicate without object_entity_id is rejected

- **WHEN** `store_fact()` is called with predicate `"parent_of"` (registered with `is_edge = true`) and `object_entity_id` is NULL
- **THEN** a `ValueError` MUST be raised with a message stating that the predicate requires `object_entity_id` because it is an edge predicate
- **AND** the error message MUST name the predicate and suggest calling `memory_entity_resolve()` to resolve the target entity

#### Scenario: Edge predicate with object_entity_id succeeds

- **WHEN** `store_fact()` is called with predicate `"parent_of"` (registered with `is_edge = true`) and a valid `object_entity_id`
- **THEN** the fact MUST be stored as an edge-fact with both `entity_id` and `object_entity_id` set

#### Scenario: Non-edge predicate without object_entity_id succeeds

- **WHEN** `store_fact()` is called with predicate `"birthday"` (registered with `is_edge = false`) and `object_entity_id` is NULL
- **THEN** the fact MUST be stored normally as a property fact

#### Scenario: Unregistered predicate without object_entity_id succeeds

- **WHEN** `store_fact()` is called with a predicate NOT in the registry and `object_entity_id` is NULL
- **THEN** the fact MUST be stored normally — registry enforcement only applies to registered predicates

---

### Requirement: Registry enforcement of is_temporal constraint at write time

When a predicate exists in `predicate_registry` with `is_temporal = true`, the `store_fact()` storage function MUST require `valid_at` to be set. This prevents temporal predicates from being silently stored as property facts, which would cause supersession to destroy historical data.

#### Scenario: Temporal predicate without valid_at is rejected

- **WHEN** `store_fact()` is called with predicate `"interaction"` (registered with `is_temporal = true`) and `valid_at` is NULL
- **THEN** a `ValueError` MUST be raised with a message stating that the predicate requires `valid_at` because it is a temporal predicate
- **AND** the error message MUST explain that omitting `valid_at` would cause supersession to destroy previous records

#### Scenario: Temporal predicate with valid_at succeeds

- **WHEN** `store_fact()` is called with predicate `"interaction"` (registered with `is_temporal = true`) and `valid_at` is set
- **THEN** the fact MUST be stored as a temporal fact with coexistence semantics (no supersession)

#### Scenario: Non-temporal predicate without valid_at succeeds

- **WHEN** `store_fact()` is called with predicate `"birthday"` (registered with `is_temporal = false`) and `valid_at` is NULL
- **THEN** the fact MUST be stored normally as a property fact with supersession semantics

#### Scenario: Unregistered predicate without valid_at succeeds

- **WHEN** `store_fact()` is called with a predicate NOT in the registry and `valid_at` is NULL
- **THEN** the fact MUST be stored normally — registry enforcement only applies to registered predicates

---

### Requirement: Fuzzy matching suggests canonical predicates for novel predicates

When a predicate is not found in the registry after normalization, the system MUST check for similar registered predicates and include suggestions in the success response. This helps LLMs converge on canonical predicates without blocking creative new ones.

#### Scenario: Close match by edit distance

- **WHEN** `store_fact()` succeeds with predicate `"parnet_of"` (not in registry) and `"parent_of"` exists in the registry with edit distance <= 2
- **THEN** the response MUST include a `"suggestions"` key containing a list of similar registered predicates
- **AND** each suggestion MUST include the predicate name and description

#### Scenario: Close match by common prefix

- **WHEN** `store_fact()` succeeds with predicate `"parent_relationship"` (not in registry) and `"parent_of"` exists in the registry sharing a prefix of 5+ characters
- **THEN** the response MUST include `"parent_of"` in the suggestions list

#### Scenario: No close matches

- **WHEN** `store_fact()` succeeds with predicate `"custom_domain_metric"` (not in registry) and no registered predicate has edit distance <= 2 or a shared prefix of 5+ characters
- **THEN** the response MUST NOT include a `"suggestions"` key (or it MUST be an empty list)

#### Scenario: Suggestions are non-blocking

- **WHEN** `store_fact()` is called with a novel predicate that has close matches
- **THEN** the fact MUST still be stored successfully
- **AND** the response MUST include both the `"id"` of the stored fact and the `"suggestions"` list

---

### Requirement: Auto-registration of novel predicates after successful write

When a fact is stored with a predicate not in the registry, the system MUST auto-register it in `predicate_registry` with flags inferred from the call parameters.

#### Scenario: Auto-register with inferred is_edge

- **WHEN** `store_fact()` succeeds with a predicate not in the registry and `object_entity_id` is set
- **THEN** a new row MUST be inserted into `predicate_registry` with `name` = the predicate, `is_edge = true`, and `is_temporal` inferred from `valid_at`
- **AND** the insert MUST use `ON CONFLICT (name) DO NOTHING` for concurrent-safety

#### Scenario: Auto-register with inferred is_temporal

- **WHEN** `store_fact()` succeeds with a predicate not in the registry and `valid_at` is set
- **THEN** the auto-registered row MUST have `is_temporal = true`

#### Scenario: Auto-register with inferred expected_subject_type

- **WHEN** `store_fact()` succeeds with a predicate not in the registry and `entity_id` resolves to an entity with `entity_type = 'person'`
- **THEN** the auto-registered row MUST have `expected_subject_type = 'person'`

#### Scenario: Concurrent auto-registration is safe

- **WHEN** two concurrent `store_fact()` calls both use the same novel predicate for the first time
- **THEN** exactly one row MUST be inserted into `predicate_registry` (the first writer wins)
- **AND** the second writer MUST NOT raise an error

---

### Requirement: Predicate search MCP tool

A new `memory_predicate_search` MCP tool MUST be provided for LLMs to discover canonical predicates before inventing new ones.

#### Scenario: Search by prefix

- **WHEN** `memory_predicate_search(query="parent")` is called
- **THEN** the result MUST include all registered predicates whose name starts with `"parent"` (e.g., `parent_of`)

#### Scenario: Search by description text

- **WHEN** `memory_predicate_search(query="father")` is called and the description of `parent_of` contains the word "father" or "parent"
- **THEN** `parent_of` MUST be included in the results

#### Scenario: Filter by scope via optional parameter

- **WHEN** `memory_predicate_search(query="measurement", scope="health")` is called
- **THEN** only predicates with `expected_subject_type` matching health domain usage MUST be returned

#### Scenario: Empty query returns all predicates

- **WHEN** `memory_predicate_search(query="")` is called
- **THEN** all registered predicates MUST be returned (equivalent to `memory_predicate_list()`)

---

### Requirement: Consistent structured error responses from MCP memory_store_fact tool

All validation failures from the `memory_store_fact` MCP tool MUST return structured JSON dicts instead of raw exceptions. Every error response MUST include a `"recovery"` field with specific next steps.

#### Scenario: Invalid entity_id returns structured error with recovery

- **WHEN** `memory_store_fact` is called with an `entity_id` that does not exist
- **THEN** the tool MUST return `{"error": "...", "message": "...", "recovery": "Call memory_entity_resolve(identifier=<name>) to resolve the entity, or memory_entity_create() to create a new one."}`
- **AND** the MCP response MUST have `isError = false` (it is a structured dict, not an exception)

#### Scenario: Edge predicate missing object_entity_id returns structured error

- **WHEN** `memory_store_fact` is called with an edge predicate and no `object_entity_id`
- **THEN** the tool MUST return a structured dict with `"recovery"` explaining: resolve the target entity via `memory_entity_resolve()`, then retry with `object_entity_id`

#### Scenario: Temporal predicate missing valid_at returns structured error

- **WHEN** `memory_store_fact` is called with a temporal predicate and no `valid_at`
- **THEN** the tool MUST return a structured dict with `"recovery"` explaining: provide an ISO-8601 `valid_at` timestamp for when the fact was true

#### Scenario: Self-referencing edge returns structured error

- **WHEN** `memory_store_fact` is called with `entity_id` equal to `object_entity_id`
- **THEN** the tool MUST return a structured dict with `"recovery"` explaining the entities must be different

#### Scenario: UUID embedded in content returns structured error

- **WHEN** `memory_store_fact` is called with a UUID in content and no `object_entity_id`
- **THEN** the tool MUST return a structured dict with `"recovery"` explaining: use `object_entity_id` instead of embedding UUIDs in content

#### Scenario: Invalid permanence returns structured error

- **WHEN** `memory_store_fact` is called with an invalid permanence value
- **THEN** the tool MUST return a structured dict listing the valid permanence values in `"recovery"`
