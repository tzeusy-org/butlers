## Purpose

Define the freshness, response and inference-cost guarantees that event-driven features must satisfy from observation through owner-visible outcome. Frequent machine observations remain inexpensive while meaningful transitions can request bounded reasoning under existing authority.

## ADDED Requirements

### Requirement: Routine observations require no model invocation
For an enrolled feature, the system SHALL process routine machine observations, unchanged values, duplicates and historical replay without model invocation. Only a named meaningful transition in its admitted profile MAY request reasoning, subject to existing authorization and all applicable budgets; transport arrival alone MUST NOT qualify.

ID: REQ-event-response-contract-001
Source: heart-and-soul/vision.md Rules 3 and 4; RFC 0038 S2 and S4
Scope: v1-mandatory

#### Scenario: High frequency observations remain model free
- **WHEN** an enrolled feature receives observations that cause no named reasoning transition
- **THEN** ingestion, filtering, state maintenance and condition evaluation produce zero model invocations attributable to those observations
- **AND** model-based discretion, summarization or delegated classification cannot move that cost to an unaccounted path

#### Scenario: Alternate routing retains the same cost boundary
- **WHEN** an observation is promoted or routed to another consumer, including wellness processing
- **THEN** the originating feature retains cost attribution through that route
- **AND** the route either satisfies the same zero-invocation rule or is an explicitly declared reasoning transition admitted under the feature budget

### Requirement: Activation requires a complete response profile
Each enrolled feature SHALL have a validated, versioned response profile declaring its source population, timestamp trust, maximum observation age, sampling or polling bounds, fallback behavior, ordering and gap handling, debounce or dwell, response deadline, inference limits and measurement endpoints. The system MUST meet its declared deadlines under the profile's named operating conditions; a missed deadline MUST become an explicit missed or degraded outcome under a declared finite detection bound, never a late success relabelled timely. Missing or invalid bounds MUST refuse activation; profile changes MUST NOT expand source or action authority.

ID: REQ-event-response-contract-002
Source: heart-and-soul/vision.md Rules 3 and 5; RFC 0038 S3 and S5
Scope: v1-mandatory

#### Scenario: Missing source guarantees refuse a realtime claim
- **WHEN** a feature lacks a validated finite freshness or response bound for its configured source population
- **THEN** that response profile cannot activate
- **AND** any separately admitted degraded profile identifies its weaker freshness and supported outcomes

#### Scenario: Replay cannot refresh old evidence
- **WHEN** old evidence is received again or arrives without a trustworthy observation timestamp
- **THEN** receipt time does not replace its original observation time or fabricate source freshness
- **AND** consumers retain stale or unknown status instead of asserting a current condition

#### Scenario: Source silence expires current knowledge
- **WHEN** a previously valid observation exceeds the profile's maximum age without replacement
- **THEN** current reads immediately treat that evidence as stale or unknown
- **AND** propagation and any required notice occur within the profile's declared response deadline
- **AND** it does not infer a healthy condition from silence

#### Scenario: Missed response deadlines cannot become timely successes
- **WHEN** a feature fails to reach its declared response endpoint by its deadline
- **THEN** it records a missed or degraded outcome within its finite detection bound
- **AND** subsequent recovery retains that miss in response evidence
- **AND** late actions execute only if still valid under the feature's authority, expiry and revision rules

### Requirement: Coalescing preserves meaningful transitions
The system SHALL bound queues and coalescing windows while preserving each enrolled consumer's required transitions and stop signals. It MUST suppress duplicate effects and reasoning admissions, preserve source ordering semantics, and expose unknown or degraded state when a gap prevents reconstruction instead of silently treating dropped data as healthy.

ID: REQ-event-response-contract-003
Source: heart-and-soul/vision.md Rule 4; RFC 0038 S3.2 and S4
Scope: v1-mandatory

#### Scenario: A short transition survives a burst
- **WHEN** a meaningful start and completion or threshold crossing occurs within one coalescing window
- **THEN** replacing intermediate level samples does not erase that transition
- **AND** repeated delivery of its identity creates no additional effect or reasoning admission

#### Scenario: Small changes accumulate across a threshold
- **WHEN** individually small sensor changes collectively cross a feature's declared threshold
- **THEN** generic ingestion significance filtering does not hide the crossing from that feature's deterministic evaluation

#### Scenario: Overflow and stop remain observable
- **WHEN** overload exceeds the profile's finite queue capacity while an owner stop is received
- **THEN** the stop is honored within its declared bound without waiting for model admission
- **AND** any unrecoverable observation gap makes dependent conclusions unknown or degraded
- **AND** recovery neither replays old effects nor launches one reasoning invocation per buffered sample

### Requirement: Feature budgets bound aggregate inference
Every reasoning-capable profile SHALL impose finite aggregate invocation, input-token and output-token limits over a declared accounting window in addition to per-goal and fleet limits. Admission MUST reserve enforceable upper bounds atomically before invocation and attribute retries, children, model-based preprocessing and alternate routes to the originating feature. Exhausted or unverifiable capacity MUST deny additional inference without disabling observation, owner stop or already authorized deterministic handling.

ID: REQ-event-response-contract-004
Source: heart-and-soul/vision.md Rules 3 and 5; RFC 0038 goal budget contract and S2.2
Scope: v1-mandatory

#### Scenario: Concurrent attempts share one remaining allowance
- **WHEN** concurrent goals or retries compete for one remaining feature invocation allowance
- **THEN** at most one receives an inference reservation
- **AND** reserved input and output token ceilings cannot exceed the feature's remaining token allowances

#### Scenario: Unknown usage cannot refund capacity
- **WHEN** an invocation terminates without authoritative usage settlement
- **THEN** its conservative reservation remains charged until reconciled
- **AND** cached input counts toward the input-token limit without double counting the same reported tokens
- **AND** reservation and settlement remain bound to the fixed accounting window captured at admission, including settlement after rollover

#### Scenario: Restart and configuration changes cannot evade limits
- **WHEN** a process restarts, a new goal is opened or the feature profile is revised during its accounting window
- **THEN** consumed and reserved allowance remains associated with the stable feature identity
- **AND** profile changes cannot reset that window or refund existing consumption

#### Scenario: Exhaustion preserves deterministic care
- **WHEN** a feature exhausts its inference allowance
- **THEN** it continues observation and permitted deterministic handling while exposing inference-held status
- **AND** it honors owner stop without an LLM
- **AND** replenishment does not automatically replay obsolete reasoning requests

### Requirement: Response evidence distinguishes freshness from delivery
An enrolled feature SHALL expose source age and measure source observation, connector receipt, accepted transition, intentional dwell or attention hold, reasoning admission and delivery outcome separately where applicable. Response evidence MUST distinguish provider acceptance, owner acknowledgement and verified physical outcome, retain failure and missing-data counts, and never present unmeasured end-to-end latency as achieved.

ID: REQ-event-response-contract-005
Source: heart-and-soul/vision.md Rule 4; RFC 0038 S3.2 and S6; craft-and-care/performance-discipline.md
Scope: v1-mandatory

#### Scenario: Fast processing cannot hide a slow source
- **WHEN** a polling source reports a change after a long upstream delay
- **THEN** the feature reports source freshness separately from its own processing latency
- **AND** an unknown source occurrence time remains unknown
- **AND** a fast processing measurement does not establish a realtime source-to-owner guarantee

#### Scenario: Holds and failures remain in the report
- **WHEN** response measurements include delayed, held, failed or never-delivered outcomes
- **THEN** reports retain their counts and denominator alongside successful-latency percentiles
- **AND** intentional holds and degraded-source cohorts remain distinguishable without disappearing from totals

#### Scenario: Acknowledgement is not physical success
- **WHEN** a provider accepts a message or a device accepts a requested setpoint
- **THEN** the feature records that specific acknowledgement
- **AND** it claims owner receipt or achieved room comfort only with the separately required evidence

## Source References

- `about/heart-and-soul/vision.md`, Non-Negotiable Rules 3, 4 and 5: capability authority, deterministic infrastructure and runtime tuning.
- RFC 0038, `about/legends-and-lore/rfcs/0038-goal-runtime-and-capability-authorization.md`: existing goal budgets, S2 waiting/admission, S3 perception freshness, S4 deterministic conditions and S6 delivery. This proposal does not activate pending RFC consumers.
- `about/craft-and-care/performance-discipline.md`: measurements and bounded performance claims.
