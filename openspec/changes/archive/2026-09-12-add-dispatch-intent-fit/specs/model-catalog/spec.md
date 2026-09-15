## ADDED Requirements

### Requirement: Model Catalog Capability Envelope
The `public.model_catalog` table SHALL carry a per-entry capability and context
envelope: a `capabilities` JSONB object (NOT NULL, default `{}`), a nullable
`max_context_tokens` integer, and a nullable `max_output_tokens` integer, added by
migration `core_204`.

#### Scenario: Envelope column shape is constrained in the database
- **WHEN** a catalog entry is written
- **THEN** `capabilities` MUST be a JSON object (`chk_model_catalog_capabilities_object`)
- **AND** `max_context_tokens` and `max_output_tokens` MUST be NULL or positive
- **AND** the feature vocabulary itself is validated in application code rather than
  by a CHECK constraint, because the vocabulary lives with the runtime adapters and a
  database constraint would need re-migrating every time it grows

#### Scenario: Existing entries are unaffected
- **WHEN** the migration runs against a populated catalog
- **THEN** no row is backfilled and every existing entry keeps an empty envelope
- **AND** an empty envelope excludes no candidate, because the adapter baseline
  already answers `tool_use` and `session_resume` for every registered runtime type

#### Scenario: Undeclared context window stays undeclared
- **WHEN** `max_context_tokens` is NULL
- **THEN** the window is treated as undeclared and therefore unproven, so a dispatch
  that requires a context floor excludes the entry rather than guessing a value

### Requirement: Fit Before Ranking
When resolution is given a dispatch intent, the system SHALL exclude every candidate
that cannot satisfy the intent's required capabilities, context floor, deadline, or
per-call budget BEFORE selecting the winning tier, before narrowing to the highest
effective priority, and before the tie-break.

#### Scenario: An unusable top-priority entry does not take its tier down
- **WHEN** the highest-priority entry in a tier cannot satisfy the intent and a
  lower-priority entry in the same tier can
- **THEN** the lower-priority entry is selected
- **AND** the excluded entry is recorded on the receipt with its fit findings

#### Scenario: A tier with no fitting candidate is not a winning tier
- **WHEN** every candidate in the requested tier fails hard fit and tier
  fallthrough is allowed
- **THEN** resolution continues to the next canonical tier

#### Scenario: No fitting candidate anywhere returns no selection
- **WHEN** eligible catalog entries exist but none of them fit the intent
- **THEN** resolution yields no selection, and the caller's existing static-fallback
  path applies
- **AND** the receipt records why each candidate was excluded, which a bare "no
  candidates" result cannot express

#### Scenario: An intent requiring nothing resolves exactly as before
- **WHEN** an intent requires no capabilities and sets no context floor, deadline,
  or budget
- **THEN** no candidate is excluded and the selected entry is identical to the one
  the pre-existing resolution path selects
- **AND** priority narrowing, evidence-based scoring, and the round-robin tie-break
  are unchanged for intent-aware resolution

#### Scenario: Quota semantics are preserved
- **WHEN** intent-aware resolution runs quota-aware and any fit-surviving
  top-priority candidate in the winning tier lacks quota headroom
- **THEN** tier quota exhaustion is raised with the same representative contract as
  the pre-existing resolution path, so the caller's sequential quota and same-tier
  failover loop still applies
