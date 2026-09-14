## Purpose

Defines the authoritative inbound source-channel and provider-pair catalog without granting any
outbound delivery capability.

## ADDED Requirements

### Requirement: Inbound Source Catalog Authority
The system SHALL authorize `ingest.v1` source identity from an owner-controlled catalog of
canonical channel/provider pairs. Catalog admission SHALL authorize inbound envelope validation
only and MUST NOT register, enable, or imply an outbound notify adapter, approval rule, recipient,
or delivery route.

ID: REQ-source-channel-catalog-001
Source: RFC 0033 §Inbound catalog authority (Proposed; owner sign-off required)
Scope: v1-mandatory

#### Scenario: Registered inbound pair is accepted
- **WHEN** an enabled catalog pair receives a syntactically valid `ingest.v1` envelope
- **THEN** Switchboard SHALL allow the envelope to proceed to the existing deduplication and ingestion pipeline
- **AND** acceptance SHALL grant no outbound delivery authority for either token

#### Scenario: Inbound registration cannot enable delivery
- **WHEN** a migration registers an enabled `sms/<provider>` or other catalog pair
- **THEN** the pair MAY become eligible for inbound validation after the enforcement cutover
- **AND** `notify(channel="sms")` and every other outbound channel without a separately registered adapter SHALL remain unsupported

#### Scenario: Connector cannot self-register
- **WHEN** a connector runtime attempts to insert, update, or delete a catalog row
- **THEN** the database SHALL refuse the write
- **AND** no connector heartbeat, startup declaration, or accepted envelope SHALL create or enable a pair

### Requirement: Bounded Syntax and Authoritative Pair Validation
Channel and provider tokens SHALL match lowercase ASCII `[a-z][a-z0-9_]{0,63}` before semantic
validation. Switchboard SHALL validate the exact enabled pair against one atomically loaded catalog
snapshot before deduplication or persistence and SHALL return a bounded machine-readable rejection
without echoing payload, sender, recipient, credential, or provider-response content.

ID: REQ-source-channel-catalog-002
Source: RFC 0033 §Two-stage validation and bounded cache (Proposed; owner sign-off required)
Scope: v1-mandatory

#### Scenario: Malformed token is rejected before lookup
- **WHEN** a channel or provider token is empty, longer than 64 characters, mixed-case, or contains a character outside the canonical syntax
- **THEN** Switchboard SHALL reject the envelope with `error_code="invalid_source_syntax"`
- **AND** it SHALL perform no deduplication or event persistence

#### Scenario: Unknown or mismatched pair fails closed
- **WHEN** both tokens are syntactically valid but their exact pair is absent or disabled in the authoritative snapshot
- **THEN** Switchboard SHALL reject the envelope with `error_code="unknown_source_pair"` or `error_code="disabled_source_pair"`
- **AND** it SHALL perform no deduplication, event persistence, classification, or routing

#### Scenario: Semantic error is content-blind
- **WHEN** any catalog validation fails
- **THEN** the result and structured audit metadata MAY identify the bounded error code and canonical channel/provider tokens
- **AND** they MUST NOT contain the envelope payload, normalized text, sender identity, endpoint identity, recipient, credential, or raw provider response

### Requirement: Bounded Last-Known-Good Catalog Cache
Switchboard SHALL begin a full catalog refresh no later than 60 seconds after the preceding
successful load, swap snapshots atomically, and use a last successfully loaded snapshot only while
its age is strictly less than 60 seconds. A read failure MUST NOT admit an unseen pair, partially
replace the snapshot, extend the last-success time, or permit acceptance at or after snapshot expiry.

ID: REQ-source-channel-catalog-003
Source: RFC 0033 §Two-stage validation and bounded cache (Proposed; owner sign-off required)
Scope: v1-mandatory

#### Scenario: Fresh last-known-good snapshot preserves a known pair
- **WHEN** a catalog refresh fails and the last successful full snapshot is less than 60 seconds old
- **THEN** an enabled pair present in that snapshot SHALL remain valid
- **AND** a pair absent from that snapshot SHALL still be rejected

#### Scenario: Stale or absent snapshot stops ingestion safely
- **WHEN** no successful full snapshot exists or its age reaches 60 seconds
- **THEN** Switchboard SHALL reject every envelope before persistence with `error_code="source_catalog_unavailable"` and `retryable=true`
- **AND** it SHALL expose degraded catalog availability without presenting the cached pair set as current

#### Scenario: Committed disablement has a sixty-second acceptance bound
- **WHEN** a pair is disabled immediately after a complete catalog snapshot loads
- **THEN** Switchboard SHALL stop accepting that pair on the next successful refresh or when that snapshot reaches 60 seconds of age, whichever occurs first
- **AND** a clock-controlled test SHALL prove no event for the disabled pair is accepted at or after that deadline

#### Scenario: Failed refresh preserves one coherent generation
- **WHEN** a refresh reads only part of the catalog or raises before the full snapshot is validated
- **THEN** Switchboard SHALL discard that candidate snapshot
- **AND** every concurrent validator SHALL observe either the preceding complete generation or the next complete generation, never a mixture

### Requirement: Migration-Only Registration and Content-Blind Read Projection
Catalog writes SHALL remain migration-owned for this change. Runtime roles SHALL receive only the
least privilege required for Switchboard to read the catalog, and the owner API SHALL expose a
Switchboard-backed read-only projection with catalog availability while offering no create, edit,
enable, disable, or delete mutation.

ID: REQ-source-channel-catalog-004
Source: RFC 0033 §Writer and operator boundaries (Proposed; owner sign-off required)
Scope: v1-mandatory

#### Scenario: Runtime grants are read-only
- **WHEN** catalog migrations and grants are inspected on real PostgreSQL
- **THEN** Switchboard's runtime role SHALL be able to select catalog rows
- **AND** connector, butler, dashboard runtime, and Messenger roles SHALL be denied direct `SELECT`, `INSERT`, `UPDATE`, `DELETE`, `TRUNCATE`, `REFERENCES`, and `TRIGGER` privileges on every catalog table defined by this change

#### Scenario: Read API distinguishes unavailable from empty
- **WHEN** the owner requests `GET /api/ingestion/source-catalog`
- **THEN** the dashboard API SHALL obtain the bounded projection from Switchboard and return the canonical pair rows with `source_available=true` when that read succeeds
- **AND** a failed catalog read SHALL return an empty data array with `source_available=false`, never a truthful-looking empty catalog

#### Scenario: Projection reveals capability facts only
- **WHEN** the catalog read succeeds
- **THEN** each row SHALL expose only `channel`, `provider`, and `enabled`
- **AND** the response MUST NOT expose endpoint identities, connector configuration, credentials, message bodies, sender or recipient identities, health evidence, or outbound-delivery capability

### Requirement: Additive Legacy-Pair Rollout and Non-Destructive Rollback
The catalog rollout SHALL preserve every exact tuple in RFC 0033 `LEGACY_SOURCE_PAIRS_V1` and its meaning
before any static validation is relaxed. Cutover and rollback MUST NOT replay, rewrite, delete, or
reinterpret previously accepted events.

ID: REQ-source-channel-catalog-005
Source: RFC 0033 §Additive rollout and rollback (Proposed; owner sign-off required)
Scope: v1-mandatory

#### Scenario: Seed is complete before propagation
- **WHEN** the catalog representation is introduced
- **THEN** the enabled catalog tuple set SHALL equal RFC 0033 `LEGACY_SOURCE_PAIRS_V1`, the runtime Literal projections, and the complete static pair matrix
- **AND** the equality check SHALL block propagation or enforcement for a missing, extra, disabled, duplicated, or substituted tuple even when the compared sets have the same count

#### Scenario: New pair requires no validator code edit after cutover
- **WHEN** a migration adds an enabled catalog-only test channel and provider after semantic enforcement is active
- **THEN** a syntactically valid envelope for that pair SHALL pass source validation without editing or redeploying a channel/provider literal or static pair matrix

#### Scenario: Rollback refuses catalog-only pairs
- **WHEN** operators disable catalog enforcement and return to the compatible static validator
- **THEN** every legacy seeded pair SHALL retain its pre-change behavior
- **AND** catalog-only pairs SHALL be refused until a catalog-aware binary returns

#### Scenario: Accepted history retains original identity
- **WHEN** a catalog pair is later disabled, removed by a forward migration, or unavailable during rollback
- **THEN** existing ingestion rows SHALL retain their stored channel/provider values and lineage
- **AND** the rollback SHALL not replay provider payloads or rewrite historical events to a legacy pair
