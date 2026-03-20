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

### Requirement: Predicate registry description and search indexes

Every predicate in the registry MUST have a meaningful description. The registry MUST maintain three complementary search indexes for hybrid retrieval.

#### Scenario: Description is required for seeded predicates

- **WHEN** a predicate is seeded via migration
- **THEN** it MUST have a non-NULL `description` that explains what the predicate represents, when to use it, and common synonyms or related concepts (e.g., `parent_of` description SHOULD mention "father", "mother", "parent", "child relationship")

#### Scenario: Trigram index on predicate name

- **WHEN** the predicate registry schema is created
- **THEN** a GIN trigram index (`pg_trgm`) MUST be created on the `name` column
- **AND** fuzzy name matching MUST use `similarity(name, $query)` with a threshold of 0.3 for candidate retrieval

#### Scenario: Full-text search vector on name and description

- **WHEN** a predicate is inserted or updated in the registry
- **THEN** a `search_vector` tsvector column MUST be maintained as `setweight(to_tsvector('english', name), 'A') || setweight(to_tsvector('english', coalesce(description, '')), 'B')`
- **AND** a GIN index MUST be created on `search_vector` for full-text queries

#### Scenario: Semantic embedding on description

- **WHEN** a predicate is inserted or updated with a non-NULL description
- **THEN** a `description_embedding` vector column MUST be populated using the memory module's embedding engine
- **AND** the embedding MUST enable cosine similarity search for conceptual matching (e.g., query "father" matches `parent_of` even without lexical overlap)

---

### Requirement: Predicate search MCP tool with hybrid retrieval

A new `memory_predicate_search` MCP tool MUST be provided for LLMs to discover canonical predicates before inventing new ones. The tool MUST use hybrid retrieval combining trigram, full-text, and semantic signals via Reciprocal Rank Fusion (RRF).

#### Scenario: Hybrid search pipeline

- **WHEN** `memory_predicate_search(query="father")` is called
- **THEN** the system MUST execute three parallel retrieval paths:
  1. **Trigram**: `SELECT name, similarity(name, $query) AS score FROM predicate_registry WHERE similarity(name, $query) > 0.3 ORDER BY score DESC`
  2. **Full-text**: `SELECT name, ts_rank(search_vector, plainto_tsquery('english', $query)) AS score FROM predicate_registry WHERE search_vector @@ plainto_tsquery('english', $query) ORDER BY score DESC`
  3. **Semantic**: Embed the query, compute cosine similarity against `description_embedding`, rank by similarity
- **AND** results MUST be fused using Reciprocal Rank Fusion: `RRF_score = SUM(1 / (K + rank_i))` where K=60
- **AND** results MUST be returned ordered by fused score descending

#### Scenario: Trigram fuzzy name matching

- **WHEN** `memory_predicate_search(query="parnet_of")` is called (typo)
- **THEN** `parent_of` MUST appear in results via trigram similarity even though there is no exact prefix match

#### Scenario: Semantic conceptual matching

- **WHEN** `memory_predicate_search(query="dad")` is called
- **THEN** `parent_of` MUST appear in results via semantic similarity between the query embedding and the description embedding (which mentions "father", "parent", etc.)
- **AND** this MUST work even when there is zero lexical overlap between query and predicate name

#### Scenario: Full-text description matching

- **WHEN** `memory_predicate_search(query="blood pressure reading")` is called
- **THEN** `measurement_blood_pressure` MUST appear in results via full-text search on the description

#### Scenario: Filter by scope via optional parameter

- **WHEN** `memory_predicate_search(query="measurement", scope="health")` is called
- **THEN** only predicates with `expected_subject_type` matching health domain usage MUST be returned
- **AND** the scope filter MUST be applied before ranking

#### Scenario: Empty query returns all predicates

- **WHEN** `memory_predicate_search(query="")` is called
- **THEN** all registered predicates MUST be returned ordered by name

#### Scenario: Each result includes full metadata

- **WHEN** any search returns results
- **THEN** each result MUST include: `name`, `is_edge`, `is_temporal`, `description`, `expected_subject_type`, `expected_object_type`, and the fused `score`

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

---

### Requirement: Predicate aliases for deterministic synonym resolution

The registry MUST support an `aliases` array per predicate. At write time, alias resolution MUST occur before normalization and registry lookup, deterministically rewriting synonyms to their canonical predicate.

#### Scenario: Alias resolves to canonical predicate

- **WHEN** `store_fact()` is called with predicate `"father_of"` and `parent_of` has `aliases = ['father_of', 'mother_of', 'dad_of']`
- **THEN** the predicate MUST be silently rewritten to `"parent_of"` before storage
- **AND** the response MUST include `"resolved_from": "father_of"` so the LLM learns the canonical form

#### Scenario: Alias resolution takes priority over fuzzy matching

- **WHEN** a predicate exactly matches an alias
- **THEN** alias resolution MUST be used (deterministic, exact) rather than fuzzy matching (probabilistic)

#### Scenario: Aliases are included in search

- **WHEN** `memory_predicate_search(query="dad")` is called and `parent_of` has alias `"dad_of"`
- **THEN** `parent_of` MUST appear in search results via alias matching in addition to trigram/semantic signals

#### Scenario: Alias uniqueness

- **WHEN** an alias is added to predicate_registry
- **THEN** the alias MUST be unique across all predicates — no two predicates can share an alias
- **AND** an alias MUST NOT collide with any canonical predicate name

---

### Requirement: Inverse and symmetric predicates for bidirectional traversal

The registry MUST support `inverse_of` and `is_symmetric` metadata to enable bidirectional entity graph traversal without duplicate fact storage.

#### Scenario: Inverse predicate declaration

- **WHEN** `parent_of` is registered with `inverse_of = 'child_of'`
- **THEN** querying facts about entity Bob where Bob is `object_entity_id` on a `parent_of` fact MUST be presentable as `child_of(Bob, Alice)` at the query layer

#### Scenario: Symmetric predicate

- **WHEN** `knows` is registered with `is_symmetric = true`
- **THEN** a fact `knows(Alice, Bob)` MUST be discoverable when querying facts about Bob without needing a separate `knows(Bob, Alice)` fact

#### Scenario: Virtual inverse at query time

- **WHEN** the entity detail API returns facts for an entity
- **THEN** facts where the entity is `object_entity_id` MUST also be returned
- **AND** those facts MUST use the `inverse_of` predicate label (or the same label for symmetric predicates)
- **AND** no duplicate facts MUST be stored — inverse resolution is read-path only

---

### Requirement: Predicate lifecycle with status and deprecation

The registry MUST track predicate lifecycle status. Deprecated predicates MUST point to their replacement. Writes to deprecated predicates MUST succeed with a warning.

#### Scenario: Deprecated predicate write succeeds with warning

- **WHEN** `store_fact()` is called with a predicate that has `status = 'deprecated'` and `superseded_by = 'new_predicate'`
- **THEN** the fact MUST still be stored successfully
- **AND** the response MUST include `"warning": "Predicate 'old_name' is deprecated. Use 'new_predicate' instead."`

#### Scenario: Auto-registered predicates start as proposed

- **WHEN** a novel predicate is auto-registered after a successful `store_fact()` write
- **THEN** the new registry entry MUST have `status = 'proposed'`

#### Scenario: Status values

- **WHEN** the `status` column is queried
- **THEN** valid values MUST be `'active'`, `'deprecated'`, or `'proposed'`

---

### Requirement: Predicate scoping by domain

The registry MUST support a `scope` column matching the fact-level scope values, serving as namespace, UI grouping, and search filter.

#### Scenario: Scope values match fact-level scopes

- **WHEN** a predicate is registered with a scope
- **THEN** the scope MUST be one of: `global`, `health`, `relationship`, `finance`, `home`

#### Scenario: Domain predicates scoped to their butler

- **WHEN** health-domain predicates are seeded (e.g., `measurement_weight`)
- **THEN** they MUST have `scope = 'health'`
- **AND** relationship predicates MUST have `scope = 'relationship'`
- **AND** finance predicates MUST have `scope = 'finance'`
- **AND** home predicates MUST have `scope = 'home'`
- **AND** cross-domain predicates (e.g., `knows`, `parent_of`, `preference`) MUST have `scope = 'global'`

#### Scenario: Scope filters search results

- **WHEN** `memory_predicate_search(query="measurement", scope="health")` is called
- **THEN** only predicates with `scope = 'health'` (or `scope = 'global'`) MUST be returned

#### Scenario: Usage tracking per predicate

- **WHEN** `store_fact()` successfully stores a fact
- **THEN** the predicate's `usage_count` MUST be incremented by 1
- **AND** `last_used_at` MUST be set to the current timestamp

---

### Requirement: Domain and range type validation (soft)

When a predicate in the registry specifies `expected_subject_type` and/or `expected_object_type`, `store_fact()` MUST validate the actual entity types against these expectations. Violations produce warnings, not errors — the fact is still stored.

#### Scenario: Subject type mismatch produces warning

- **WHEN** `store_fact()` is called with predicate `parent_of` (registered with `expected_subject_type = 'person'`) and the subject entity has `entity_type = 'organization'`
- **THEN** the fact MUST still be stored successfully
- **AND** the response MUST include a `"warning"` stating the expected subject type is `'person'` but the actual entity type is `'organization'`

#### Scenario: Object type mismatch produces warning

- **WHEN** `store_fact()` is called with predicate `works_at` (registered with `expected_object_type = 'organization'`) and the object entity has `entity_type = 'person'`
- **THEN** the fact MUST still be stored successfully
- **AND** the response MUST include a `"warning"` stating the expected object type is `'organization'` but the actual entity type is `'person'`

#### Scenario: NULL expected types skip validation

- **WHEN** a predicate has `expected_subject_type = NULL` or `expected_object_type = NULL`
- **THEN** no type validation MUST be performed for that side
- **AND** no warning MUST be produced

#### Scenario: Matching types produce no warning

- **WHEN** `store_fact()` is called with predicate `parent_of` and both subject and object entities have `entity_type = 'person'`
- **THEN** no type-related warning MUST be produced

#### Scenario: Type validation uses entity lookup already in transaction

- **WHEN** `store_fact()` validates entity types
- **THEN** it MUST reuse the entity existence check already performed within the transaction (no additional query)
- **AND** if `entity_id` is provided, the entity's `entity_type` MUST be fetched in the same query as the existence check

---

### Requirement: Example payloads in predicate registry

Each predicate in the registry SHOULD carry a structured example showing the expected `content` and `metadata` format. This enables LLMs to follow concrete templates when creating facts.

#### Scenario: example_json column stores sample payload

- **WHEN** a predicate is seeded via migration
- **THEN** it SHOULD include an `example_json` JSONB column containing a sample `{"content": "...", "metadata": {...}}` payload
- **AND** the example MUST use realistic values, not placeholders

#### Scenario: Predicate search returns examples

- **WHEN** `memory_predicate_search()` returns results
- **THEN** each result MUST include the `example_json` field if non-NULL

#### Scenario: Auto-registered predicates have no example

- **WHEN** a novel predicate is auto-registered
- **THEN** `example_json` MUST be NULL (no example can be inferred from a single usage)
