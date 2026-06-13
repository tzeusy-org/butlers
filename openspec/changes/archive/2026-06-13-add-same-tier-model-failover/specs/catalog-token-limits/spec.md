## MODIFIED Requirements

### Requirement: Hard Block on Quota Exhaustion
The system SHALL hard-block session spawning when a catalog entry's token quota is
exhausted and no eligible same-tier fallback candidate is available.

#### Scenario: Spawner fails over on quota exhausted with same-tier candidate
- **WHEN** the spawner checks quota for a catalog-resolved model before invocation
- **AND** `check_token_quota()` returns `allowed=False`
- **AND** another eligible model exists in the same effective complexity tier
- **THEN** the spawner SHALL skip the exhausted candidate without invoking its adapter
- **AND** retry pre-spawn checks with the next eligible same-tier candidate
- **AND** record quota-skip provenance for the exhausted candidate

#### Scenario: Spawner blocks on quota exhausted without same-tier candidate
- **WHEN** the spawner checks quota for a catalog-resolved model before invocation
- **AND** `check_token_quota()` returns `allowed=False`
- **AND** no other eligible model exists in the same effective complexity tier
- **THEN** the spawner SHALL NOT invoke any adapter
- **AND** it SHALL return a `SpawnerResult` with `success=False`
- **AND** the error message SHALL identify which quota window is exhausted and current
  usage versus limit

#### Scenario: Discretion dispatcher remains hard-blocked
- **WHEN** the discretion dispatcher resolves a model and `check_token_quota()` returns
  `allowed=False`
- **THEN** the dispatcher SHALL preserve the existing hard-block behavior
- **AND** it SHALL NOT use spawner model failover
