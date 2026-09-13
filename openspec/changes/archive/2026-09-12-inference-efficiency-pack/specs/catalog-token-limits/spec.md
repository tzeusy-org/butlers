## MODIFIED Requirements

### Requirement: Hard Block on Quota Exhaustion
The system SHALL hard-block session spawning or discretion dispatching when a catalog entry's token quota is exhausted and no eligible same-tier fallback candidate is available. When an eligible model exists in the same effective complexity tier, the spawner and discretion dispatcher SHALL fail over to it instead of hard-blocking. Note: the pre-spawn check and post-spawn record are not atomic, so concurrent spawns targeting the same catalog entry can overshoot the limit by up to N sessions' worth of tokens (where N is the number of concurrent spawns). This is accepted — the limit is a guardrail, not a billing boundary.

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
- **AND** the error message SHALL identify which quota window is exhausted and current usage versus limit

#### Scenario: Discretion dispatcher skips a quota-exhausted catalog entry
- **WHEN** the discretion dispatcher checks quota for its selected catalog entry before invocation
- **AND** `check_token_quota()` returns `allowed=False`
- **AND** another eligible model exists in the same effective complexity tier
- **THEN** the dispatcher SHALL skip the exhausted entry without invoking its adapter
- **AND** retry the pre-invocation quota check with the next eligible same-tier candidate
- **AND** the skip SHALL count toward the dispatcher's existing same-tier failover-attempt cap
- **AND** the dispatcher SHALL record only bounded non-sensitive operational provenance, not Spawner session provenance

#### Scenario: Discretion dispatcher exhausts quota skips within its tier
- **WHEN** the discretion dispatcher has no remaining eligible candidate in its effective complexity tier after one or more quota skips
- **THEN** it SHALL NOT invoke a quota-exhausted adapter
- **AND** it SHALL raise `RuntimeError` tagged `same_tier_failover_exhausted`
- **AND** it SHALL NOT cross to another complexity tier

#### Scenario: Error message includes quota details
- **WHEN** a spawn is blocked due to quota exhaustion
- **THEN** the error message includes: the catalog entry alias, which window(s) are exceeded, current usage, and the configured limit

#### Scenario: Discretion dispatcher remains hard-blocked
- **WHEN** the discretion dispatcher resolves a model and `check_token_quota()` returns `allowed=False`
- **THEN** the dispatcher SHALL preserve the existing hard-block behavior (raises `RuntimeError` with a message indicating quota exhaustion)
- **AND** it SHALL NOT use spawner model failover
