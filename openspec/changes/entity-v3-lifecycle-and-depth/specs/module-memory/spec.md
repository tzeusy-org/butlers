# Module Memory — Entity v3 Delta

This delta adds the store-boundary requirement that resolves the two-fact-stores ambiguity (brief open question 11) on the memory-module side. The layering itself is declared in `relationship-entity-lifecycle`; existing memory-module requirements (correction-driven retraction, entity resolution) are unchanged.

## ADDED Requirements

### Requirement: Identity-contact data is out of scope for the memory facts store

The memory-module `facts` table (unqualified table name — it lives in the mounting butler's schema, `relationship` in production) SHALL hold narrative facts, episodes, and non-registry edge-facts only. Identity-contact assertions — data shaped as channel identifiers or identity predicates (`has-email`, `has-phone`, `has-handle`, `has-address`, `has-birthday`, `has-website`, and future contact predicates in `relationship.entity_predicate_registry`) — MUST NOT be stored in the memory-module `facts` table; their single write path is `relationship_assert_fact()` into `relationship.entity_facts`. Conversely, narrative facts MUST NOT be written to `relationship.entity_facts`. The existing no-cross-join rule between the two stores stands (guardrails target the table identities, not a schema prefix); consumers needing both read them separately and layer in application code per `relationship-entity-lifecycle`.

#### Scenario: Extraction routes identity data to the central writer
- **WHEN** the fact-extraction pipeline encounters a new email address for a person
- **THEN** the assertion MUST go through `relationship_assert_fact()` (identity store)
- **AND** the narrative context around it (e.g. "mentioned switching jobs") MAY be stored as a memory-module fact
- **AND** no `has-*` identity predicate content MUST land in the memory-module `facts` table

#### Scenario: Writer-side boundary check rejects identity content
- **WHEN** `memory_store_fact` is called with content shaped as a registry identity predicate
- **THEN** the writer MUST reject the call (or route it to `relationship_assert_fact()`)
- **AND** a test MUST cover this rejection path
