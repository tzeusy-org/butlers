## MODIFIED Requirements

### Requirement: Canonical condition identity

The system SHALL identify an infrastructure condition by a canonical producer
`source` plus a SHA-256 `fingerprint` of a versioned, deterministically sorted
identity payload. The source SHALL be an explicit producer domain and SHALL
NOT be inferred from a `QaFinding.source_type`, connector provider/channel,
`healing_dispatch_events.butler_name`, exception fingerprint, timestamp, age,
or error prose. The identity payload SHALL include the canonical source, a
version, and only stable source-defined identity facts; object keys and
set-valued collections SHALL be recursively sorted before deterministic UTF-8
serialization and hashing. An infrastructure producer SHALL retain its
declared identity-payload version as condition evidence. When its first
successor under a strictly higher version explicitly names a predecessor
fingerprint, a complete snapshot SHALL retain reciprocal predecessor/successor
references and a `superseded_by_identity_version_bump` terminal reason; it
SHALL NOT rewrite the predecessor fingerprint.

#### Scenario: Stable evidence produces one identity
- **WHEN** a producer observes the same condition with updated timestamps,
  age text, or sanitized diagnostic prose
- **THEN** it uses the same canonical source and fingerprint
- **AND** those mutable values are retained only as evidence or metadata, not
  fingerprint input

#### Scenario: A producer changes its identity contract
- **WHEN** a producer must change the meaning or shape of condition identity
- **THEN** it increments the identity-payload version before computing the
  sorted SHA-256 payload
- **AND** it does not reinterpret prior episode identity from free-form
  evidence

#### Scenario: Complete snapshot records an explicit version successor
- **WHEN** an active v1 episode is absent from a complete snapshot containing
  a v2 observation that explicitly names the v1 fingerprint as predecessor
- **THEN** the v1 episode resolves with
  `superseded_by_identity_version_bump` and its v2 successor reference
- **AND** the first v2 episode retains the reciprocal v1 predecessor reference
- **AND** repeated v2 observations preserve that correlation without creating
  another episode

#### Scenario: Unlinked or incomplete observations do not invent supersession
- **WHEN** a versioned successor omits explicit predecessor lineage, or its
  snapshot is incomplete
- **THEN** the system does not infer a predecessor/successor correlation
- **AND** an incomplete snapshot does not resolve the v1 episode by absence

#### Scenario: Mutable drift revisions remain evidence
- **WHEN** a migration-drift condition continues while expected or actual
  revision values change
- **THEN** its identity is based on the affected stable schema/chain set
- **AND** the revision values remain evidence rather than causing a new
  fingerprint solely because the diagnostic text changed

## Source References
- Non-Negotiable Rule 4 (deterministic daemon infrastructure and ephemeral LLM intelligence)
- RFC 0006 (database schema and isolation)
- RFC 0007 (dashboard and API surface)
