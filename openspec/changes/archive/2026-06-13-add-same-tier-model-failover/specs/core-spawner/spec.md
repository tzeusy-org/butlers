## MODIFIED Requirements

### Requirement: Dynamic Model Resolution at Spawn Time
The spawner SHALL resolve models dynamically at spawn time using the model catalog and
MAY use same-tier failover only after the initial catalog candidate has been selected.

#### Scenario: Initial catalog candidate establishes failover tier
- **WHEN** `resolve_model()` returns a catalog result for a trigger
- **THEN** the spawner SHALL treat that result's effective complexity tier as the
  failover tier for the logical session
- **AND** subsequent automatic failover attempts SHALL use only that exact tier

#### Scenario: Catalog resolution failure uses static fallback
- **WHEN** initial catalog resolution returns `None` for every eligible tier or raises
  before a catalog candidate is selected
- **THEN** the spawner SHALL use the existing static fallback behavior
- **AND** same-tier model failover SHALL NOT run because no catalog tier was established

### Requirement: Runtime Failure Classification
The spawner SHALL classify runtime failures before deciding whether automatic model
failover is safe.

#### Scenario: Systemic runtime failure is eligible
- **WHEN** a runtime adapter fails before any side-effect-capable work is observed
- **AND** the failure is classified as systemic infrastructure or provider failure
- **THEN** the spawner MAY attempt same-tier model failover if another eligible
  candidate exists

#### Scenario: Captured tool calls make failure ineligible
- **WHEN** captured tool calls for the failed attempt are non-empty
- **THEN** the spawner SHALL classify the failure as not failover-eligible
- **AND** it SHALL NOT start a second model attempt for the same logical session

#### Scenario: Classifier defaults closed
- **WHEN** the classifier receives an unknown exception type, ambiguous adapter error,
  or incomplete process metadata
- **THEN** it SHALL classify the failure as not failover-eligible

### Requirement: Logical Session Attempt Orchestration
The spawner SHALL keep automatic model failover attempts bounded and auditable.

#### Scenario: Successful fallback completes logical session once
- **WHEN** the primary model fails with a failover-eligible error
- **AND** a fallback model succeeds
- **THEN** exactly one logical session completion SHALL be recorded
- **AND** the session's final model SHALL be the successful fallback model
- **AND** provenance SHALL record the failed primary attempt

#### Scenario: Non-eligible failure completes without retry
- **WHEN** a runtime invocation fails with a non-failover-eligible error
- **THEN** the spawner SHALL preserve existing failure behavior
- **AND** it SHALL record no fallback invocation

#### Scenario: Attempt cap prevents infinite retry
- **WHEN** same-tier failover is active
- **THEN** the number of attempts SHALL be bounded by the number of eligible same-tier
  catalog candidates
- **AND** no catalog entry SHALL be invoked more than once for the same logical session
