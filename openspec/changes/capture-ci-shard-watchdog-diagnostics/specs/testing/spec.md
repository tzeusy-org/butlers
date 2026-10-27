## ADDED Requirements

### Requirement: CI watchdog diagnostics preserve partial observation and execution authority

When a named CI watchdog investigation is released, the system SHALL capture bounded masked actual worker/item progress and available stack/process observations during execution and finalize separately identified diagnostic artifacts before provider termination. Capture source and actual subject checkout/lineage, repository/workflow/run/attempt/child, fresh nonce, complete expected file/case population and observed multiplicities SHALL remain distinct and bound. Diagnostic artifacts SHALL preserve unobserved, unavailable, truncated, failed or cancelled boundaries as UNKNOWN and SHALL NOT satisfy complete execution, duration ordering, smoke/release, coverage publishing or required gate verification. Existing item/job/UV deadlines, marker/file/loadfile fixture boundaries, worker/coverage/tracer defaults, old callbacks/assertions, finalizers and scoped owned cleanup SHALL remain intact.

ID: REQ-testing-060
Source: bu-ly3lv5.10.2; original bu-ly3lv5.10 C6/C7/P0; REQ-testing-046 actual boundaries/privacy/UNKNOWN; engineering-bar and testing-and-verification.
Scope: v1-mandatory

#### Scenario: Finalized live checkpoints survive terminal cancellation
- **WHEN** a diagnostic step has finalized an immutable masked checkpoint while execution is still active and the provider later cancels the job
- **THEN** fixed same-run/attempt artifact retrieval establishes the persisted checkpoint by its authenticated ID/content digest and source/child bindings
- **AND** an unfinished upload, unreachable later step, expired artifact or missing readback remains UNKNOWN rather than durable capture proof

#### Scenario: Complete expected populations and actual multiplicities remain
- **WHEN** a frozen original-profile investigation records actual collections, item/phase boundaries and worker identities
- **THEN** fresh real collection preserves every original file/case and opaque multiplicity with separate current nonce and source-domain correspondence
- **AND** nested subprocesses, duplicate phases, dropped events and missing boundaries cannot forge top-worker progress or unobserved PASS

#### Scenario: Stack process and transport diagnostics exclude sensitive values
- **WHEN** capture samples a worker or uploads/reads a diagnostic carrier
- **THEN** only approved static source references/line numbers, opaque identities, closed categories and bounded numeric process/resource fields are exposed
- **AND** raw parameters, environment, credentials, provider payload, argv, source lines, arbitrary names/paths, errors and stack locals never enter public output or carriers; nonresponsive or unsupported stacks remain UNKNOWN

#### Scenario: Observer and uploader failures preserve original execution
- **WHEN** observation, queues, clocks, writing, strict validation, upload or helper cleanup fails or is cancelled
- **THEN** fixed failure/gap categories and bounded observed prefixes are retained without changing the original selected tests or deadlines, hiding primary failures or terminating unrelated processes
- **AND** positioned failed/cancelled/resistant-descendant controls and a same-path healthy companion demonstrate real reachability and owned completion

#### Scenario: Diagnostic evidence never replaces a complete execution carrier
- **WHEN** a consumer receives even a plausible whole-population diagnostic snapshot
- **THEN** its diagnostic-only schema and literal incomplete execution status cannot pass success-only execution/timing/release/coverage or required fan-in admission
- **AND** every unobserved item, phase, finalizer and whole gate outcome remains UNKNOWN while the original complete verifier stays unchanged

#### Scenario: Observation controls are separate from workload and delivery proof
- **WHEN** bounded local controls or a harmless hosted artifact-retention control succeed
- **THEN** only their named mechanism and transport scope is qualified
- **AND** the actual original-profile investigation, genuine source/native review, current normal/protected/squash and every parent hard timing/history/reaction outcome remain separate mandatory unearned proof
