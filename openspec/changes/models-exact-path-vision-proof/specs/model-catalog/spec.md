## MODIFIED Requirements

### Requirement: Model Catalog Capability Envelope
The `public.model_catalog` table SHALL carry a per-entry capability and context
envelope: a `capabilities` JSONB object (NOT NULL, default `{}`), a nullable
`max_context_tokens` integer, and a nullable `max_output_tokens` integer, added by
migration `core_204`.
For the Models vision-proof workflow, existing declaration values and newly managed proof bindings SHALL remain distinct. Only unchanged historical declarations retain existing behavior and MUST NOT be cleared or converted by migration. New Enable/Apply/Refresh writes SHALL require an applicable passing receipt and atomic exact-row/envelope/path comparison; a fresh Verify receipt alone MUST NOT modify declaration, applied binding or routing eligibility, including already-true rows. All catalog write paths MUST enforce this application boundary without losing unrelated envelope keys. Explicit disable/unknown edits remain owner actions; a failed probe does not perform them.
Every new or cloned/unbound true write and identity-changing edit retaining true requires applicable proof; grandfather only unchanged historical declarations.

ID: REQ-model-catalog-003
Source: owner-adopted Models vision contract d46d758108064d4ea5d00cab2a94a8ebc82a18f54dea5a2b9393b7f1cc25e11e; canonical model-catalog baseline
Scope: v1-mandatory

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

#### Scenario: Vision capability requires exact-path evidence
- **WHEN** a catalog row declares `capabilities.vision = true`
- **THEN** the declaration is backed by an end-to-end probe of that exact `runtime_type`, `model_id`, runtime/CLI version, and account path
- **AND** the probe proves an MCP image content block reaches inference by requiring an answer available only from the image bytes, not from prompt text or attachment metadata
- **AND** a text-only model verification, model-brand claim, direct `attachment_view()` unit test, or adapter-wide capability assumption is insufficient evidence
- **AND** rows without that evidence retain unknown vision support and remain excluded from image-bearing external dispatches

#### Scenario: Capability envelope is writable through the catalog API
- **WHEN** the owner creates or updates a catalog entry with a `capabilities` object
- **THEN** the object is validated against the `ModelFeature` vocabulary and boolean values before any write, and an unknown key or non-boolean value is rejected with 422
- **AND** an update that includes `capabilities` replaces the entry's whole envelope
- **AND** every catalog entry response includes the stored `capabilities` object

#### Scenario: Applying proof is distinct from verifying a true row

- **WHEN** a declared-true row receives a successful new Verify receipt
- **THEN** its applied binding and routing remain unchanged until explicit matching Apply/Refresh CAS

### Requirement: Fit Before Ranking
When resolution is given a dispatch intent, the system SHALL exclude every candidate
that cannot satisfy the intent's required capabilities, context floor, deadline, or
per-call budget BEFORE selecting the winning tier, before narrowing to the highest
effective priority, and before the tie-break.
For rows explicitly converted to the managed Models vision-proof lifecycle, image-fit SHALL additionally require a matching applied proof identity. Stale managed proof MUST fail closed. Historical unbound declarations retain their existing applicability semantics. The separately authenticated private diagnostic selection MAY bypass only the exact authorized candidate vision prerequisite for its bounded synthetic operation; it MUST retain every other authority, quota and enabled-row constraint and MUST NOT expose this exception to ordinary dispatch callers or mutate capability bits.

ID: REQ-model-catalog-004
Source: owner-adopted Models vision contract d46d758108064d4ea5d00cab2a94a8ebc82a18f54dea5a2b9393b7f1cc25e11e; canonical model-catalog baseline
Scope: v1-mandatory

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
- **THEN** resolution yields no selection and the caller returns a pre-invocation
  `ModelResolutionError` without launching an adapter
- **AND** the receipt records why each candidate was excluded, which a bare "no
  candidates" result cannot express

#### Scenario: Override selection cannot bypass hard fit
- **WHEN** a spend rule or private-content policy selects a different catalog entry after intent-aware resolution
- **THEN** the caller SHALL verify that the replacement candidate was fit-eligible for the original intent and effective tier
- **AND** a candidate recorded as `excluded_hard_fit` SHALL remain non-invocable even when an operator rule or locality policy selects it
- **AND** the caller SHALL preserve the candidate's original fit exclusions rather than projecting it as selected

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

#### Scenario: Diagnostic fitting never grants ordinary image authority

- **WHEN** an ordinary session lacks image capability or attempts to provide a probe bypass flag
- **THEN** it remains excluded; only a verified purpose-bound exact diagnostic operation can exercise the narrow exception
