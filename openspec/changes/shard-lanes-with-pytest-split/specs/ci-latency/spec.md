## MODIFIED Requirements

### Requirement: Bounded timestamp evidence for the CI latency objective

The advisory observer SHALL report nearest-rank p95 required completion below 420 seconds across at least 20 complete first-attempt successful merge-group samples. Required completion SHALL use run creation through the latest completion of alls-green and guards; a run from before the bu-29wkwj.3 cutover, which still carries the check mirror, SHALL use the latest completion of check, guards and frontend. Workflow tail SHALL use actual terminal job completion. Scheduling delay SHALL use each executed job's own creation and start, independent of guards/inventory dependency topology. Failure, cancellation, in-progress and retried-run counts SHALL remain visible outside the success-only latency objective. Rolling windows SHALL disclose mixed rollouts; explicit after-since cohorts SHALL require a complete bounded index. This objective SHALL NOT weaken existing full merge-queue execution, required contexts or finite watchdogs.

ID: REQ-ci-latency-001
Source: owner 2026-10-15; bu-q1hzqn; bu-q1hzqn.4; bu-29wkwj.3 (ruleset cutover to alls-green)
Scope: v1-mandatory

#### Scenario: Complete successful cohort establishes the observed objective
- **WHEN** at least 20 clean successes have complete matching run/attempt/head jobs
- **THEN** the report compares nearest-rank p95 of required completion with 420 seconds and reports PASS only below the objective
- **AND** a rolling mixed-rollout window cannot attribute a gain to a specific change

#### Scenario: Missing or insufficient evidence cannot pass
- **WHEN** required data is malformed, incomplete, truncated, duplicated or mismatched, or fewer than 20 eligible successes exist
- **THEN** the report distinguishes collection ERROR from INSUFFICIENT_SAMPLES and neither reports a passing verdict
- **AND** retried successes do not increase the clean sample population

#### Scenario: Breaches remain visible outside required gates
- **WHEN** the scheduled read-only observer detects a breach or lacks complete evidence
- **THEN** the run summary and retained JSON/Markdown artifact display that result even when the observer exits nonzero
- **AND** required queue checks perform no extra test execution or observer work

#### Scenario: Required completion follows the ruleset's contexts
- **WHEN** a merge-group run carries alls-green and no check mirror
- **THEN** its required completion ends at the later of alls-green and guards
- **AND** a run that still carries check, guards and frontend is measured against those three
