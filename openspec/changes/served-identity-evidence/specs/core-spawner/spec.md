## MODIFIED Requirements

### Requirement: Runtime Failure Classification
- The spawner SHALL classify runtime failures before deciding whether automatic model failover is safe.
- A strict typed terminal is_error=true or provider equivalent is failure even at process exit0 and even with result text. Its dispatch outcome is runtime_failure as required by bu-s11n0s.6; eligibility remains separately default closed for max-turns/budget/permission/unknown and merges existing daemon tool-call evidence. This narrow correction is the explicit exception to evidence-only no-routing-change: false success no longer resets route health. Unknown ordinary exceptions and all previous side-effect/guardrail/allowance/cancellation gates retain their existing outcomes and retry rules.

ID: REQ-core-spawner-009
Source: bu-s11n0s.6 original outcome; protected 6cfc4eeac9003c0321a892d02b9865113cdccb72; serving protocol P1-P9
Scope: v1-mandatory

#### Scenario: Systemic runtime failure is eligible
- **WHEN** a runtime adapter fails before any side-effect-capable work is observed
- **AND** the failure is classified as systemic infrastructure or provider failure
- **THEN** the spawner MAY attempt same-tier model failover if another eligible
  candidate exists

#### Scenario: Empty normal return is classified after merging tool-call evidence
- **WHEN** a runtime adapter returns normally without result text
- **THEN** the spawner SHALL merge adapter-reported tool calls with daemon-captured
  runtime-session tool calls before classifying the attempt
- **AND** when the merged records contain no non-command MCP tool call, the spawner
  SHALL treat the attempt as an empty-response failure even if token usage was reported
- **AND** when the merged records contain a confirmed non-command MCP tool call, the
  tool-only attempt SHALL remain successful and SHALL NOT trigger model failover

#### Scenario: Captured tool calls make failure ineligible
- **WHEN** captured tool calls for the failed attempt are non-empty
- **THEN** the spawner SHALL classify the failure as not failover-eligible
- **AND** it SHALL NOT start a second model attempt for the same logical session

#### Scenario: Classifier defaults closed
- **WHEN** the classifier receives an unknown exception type, ambiguous adapter error,
  or incomplete process metadata
- **THEN** it SHALL classify the failure as not failover-eligible

### Requirement: Logical Session Attempt Orchestration
- The spawner SHALL keep automatic model failover attempts bounded and auditable.
- Each provider invocation SHALL produce attempt-grained spend evidence: the spawner writes the dispatch-attempt row first, then writes exactly one token-usage row referencing that attempt. Parseable provider usage is `measured`; absence of parseable usage is `unmeasurable` with NULL token buckets.
- Exactly one aggregate ledger row remains the quota/operational-ceiling route total. An invoked attempt additionally commits its bounded served record and zero or more separately keyed model_served_usage rows atomically, tagged provider_breakdown. No total plus breakdown is charged twice. Stable owned attempt_key replay returns the same attempt id only after exact receipt comparison; conflicting replay is zero-effect/degraded. Each internal execution has separate evidence before first-info overwrite. Missing/partial/cumulative-without-baseline usage or reported cost remains explicitly unknown. Persistence failure does not alter the runtime verdict or failover safety. Every mutable evidence producer SHALL use an invocation-owned worker/collector and SHALL copy its immutable evidence before persistence awaits or worker reuse. Discretion SHALL instantiate a fresh existing create_worker() for each invocation instead of consuming the cached parent's mutable last-call metadata; the spawner SHALL preserve its exclusive pooled worker lifecycle. Competing, cancelled, early-failed and late-final invocations SHALL NOT exchange metadata or refill an unknown record from another call.

ID: REQ-core-spawner-010
Source: bu-s11n0s.6 original outcome; protected 6cfc4eeac9003c0321a892d02b9865113cdccb72; serving protocol P1-P9
Scope: v1-mandatory

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

#### Scenario: Timeout without usage remains visible
- **WHEN** a provider invocation times out and no token usage can be parsed
- **THEN** the spawner SHALL write the invocation's dispatch-attempt provenance
- **AND** SHALL write one linked `usage_source='unmeasurable'` ledger row
- **AND** monthly spend surfaces SHALL identify that attempt as unpriced rather than presenting the measured subtotal as complete

