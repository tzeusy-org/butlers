## ADDED Requirements

### Requirement: Server-Authoritative Replay Safety
The replay status transition SHALL be authorized by a resolved source and
connector replay policy, not by UI eligibility alone.

#### Scenario: Email replay is denied
- **WHEN** an individual or bulk replay request targets an event whose source
  channel is `email`
- **THEN** the request SHALL not transition that event to `replay_pending`
- **AND** the API SHALL return HTTP 409 with a non-sensitive replay-safety
  reason

#### Scenario: Connector policy is explicitly unsafe
- **WHEN** an event resolves to an active connector-registry row with
  `replay_safe=false`
- **THEN** individual replay SHALL return HTTP 409 without a status transition
- **AND** a bulk request containing that event SHALL reject before replaying
  any selected event

#### Scenario: Connector-specific identity is resolved from persisted candidates
- **WHEN** an accepted event retains a generic provider with a
  connector-specific source channel, or a filtered event retains a generic
  connector type with a connector-specific source channel
- **THEN** both persisted candidates SHALL be considered against the active
  registry identity
- **AND** exactly one active registry row must match before replay is safe

#### Scenario: Connector policy cannot be resolved
- **WHEN** an event lacks an unambiguous active connector replay policy
- **OR** its endpoint identity is missing or blank
- **THEN** it SHALL be treated as replay-unsafe and not transitioned
- **AND** a policy lookup infrastructure failure before an individual replay
  or bulk preflight SHALL return HTTP 503 rather than assume a safe default
- **AND** if a bulk request has already started, a later policy-explanation
  failure SHALL be reported for that event without a status transition or a
  safe default

#### Scenario: Safe policy is atomically enforced at mutation
- **WHEN** an event was previously displayed as replay-safe and its replay
  endpoint is invoked
- **THEN** the status-transition statement SHALL require exactly one active,
  nonblank-endpoint connector policy whose `replay_safe` value is true
- **AND** it SHALL lock that resolved policy row with the transition
- **AND** a stale UI state cannot cause an unsafe transition
