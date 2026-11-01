## MODIFIED Requirements

### Requirement: Smoke Tests Run In CI As A Fast Gate
The smoke tier SHALL execute in CI (`.github/workflows/ci.yml`) on every merge-group tree and every backend-applicable
pull request as a fast gate, distinct from and faster than the integration tier,
and MUST NOT pull in the E2E suite or any real LLM dependency. The dedicated-selection scenario below and the distinct/faster fast-gate description apply to scoped execution and uncovered full-mode smoke items; full derived mode does not claim a separate smoke job completed faster than the full integration run. Complete full execution MAY use exact-identity derived smoke evidence from the same successful lane population under P8; this explicitly qualifies the previous unconditional dedicated invocation while preserving its command, no-LLM/no-E2E and failure contracts.

ID: REQ-testing-034
Source: bu-ly3lv5.1 preserved smoke outcome; existing bu-r5mnn event policy and testing-and-verification; bu-ly3lv5.6 P8 full-mode proof qualification
Scope: v1-mandatory

#### Scenario: Dedicated smoke selection in CI
- **WHEN** the CI `check-preflight` job runs
- **THEN** smoke tests are selected via `-m smoke` (excluding `e2e` and any real-LLM
  paths) and run alongside the independent unit and integration shards
- **AND** a smoke failure fails the CI run

#### Scenario: No E2E or real-LLM dependency in the smoke gate
- **WHEN** the smoke step runs in CI
- **THEN** it does not require `ANTHROPIC_API_KEY` or the `claude` CLI
- **AND** `tests/e2e` is excluded from the smoke selection, consistent with the
  existing E2E CI-exclusion mechanisms

#### Scenario: Existing post-queue and docs-only smoke skips are explicit
- **WHEN** a successful classifier identifies a docs/spec-only pull request, or the workflow is a push to main after protected merge-group validation
- **THEN** check-preflight may skip under the existing event policy
- **AND** this skip cannot authorize a missing classifier verdict, a failed/cancelled preflight, a backend-applicable pull request, or a merge-group omission


#### Scenario: Exact full-lane smoke reuse preserves the same guarantee
- **WHEN** full-lane receipts prove every item selected by the dedicated smoke command actually completed successfully
- **THEN** preflight emits explicitly derived release evidence with actual commands and exact source/run/attempt identity
- **AND** any uncovered or unavailable selected item requires the original dedicated command or a non-successful preflight

### Requirement: Smoke Run Release Evidence
A smoke run SHALL emit a machine-readable release-evidence record so that a release
can be tied to concrete operational proof. A derived full-lane record SHALL retain every existing field, record actual invoked shard commands and separate smoke selector provenance, and MUST NOT claim an unexecuted dedicated command ran.

ID: REQ-testing-051
Source: bu-ly3lv5.6 P8; complete existing smoke release evidence
Scope: v1-mandatory

#### Scenario: Evidence record fields
- **WHEN** the smoke tier completes (in CI or locally with evidence enabled)
- **THEN** it records, for the run: the exact command invoked, the git commit SHA,
  the wall-clock duration, the pass/fail outcome, and the set of skipped test
  classes (e.g. tests skipped because Docker was unavailable)
- **AND** the record is captured as a CI artifact or log line that can be
  referenced from release notes


#### Scenario: Derived commands are actual execution evidence
- **WHEN** a full-lane smoke record is derived
- **THEN** its exact-command field records the actual executed shard commands and evidence_mode distinguishes derivation from the dedicated smoke command
- **AND** source SHA, actual duration/outcome/skipped classes and artifact references remain available

#### Scenario: Missing smoke provenance cannot be a release proof
- **WHEN** identity, outcomes, markers or actual execution commands are incomplete
- **THEN** the release evidence is unavailable/non-passing and the dedicated command is retained as the safe execution path
