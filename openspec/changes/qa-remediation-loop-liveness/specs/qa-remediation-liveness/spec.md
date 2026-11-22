## ADDED Requirements

### Requirement: QA remediation-loop liveness conditions
The independent control-plane controller SHALL record QA remediation-loop conditions in `public.infra_conditions`, each with a fixed server-derived identity, and SHALL never claim absence without an instrument. QA does not investigate or patch itself.

ID: REQ-qa-remediation-liveness-001
Source: owner gate bu-l34qcx (approved 2026-11-22); RFC0015 D3; RFC0029; dossier docs/redesigns/2026-11-21-qa-self-healing-pursuit.md move 8
Scope: v1-mandatory

#### Scenario: Capability below target
- **WHEN** the most recent launched QA investigation closed on a structural cause, such as `publication_unavailable` (a publisher is not configured)
- **THEN** exactly one active `qa_loop_capability` condition exists, identified by that cause (`publisher_unconfigured`), and a later scan creates no second episode
- **AND** it resolves only after a later launched investigation closes without a structural cause

#### Scenario: Breaker open too long
- **WHEN** the QA breaker has been tripped for more than 6 hours
- **THEN** one `qa_loop_breaker` condition is active, carrying the streak's dominant cause as evidence

#### Scenario: Proposal unadopted
- **WHEN** a retained proposal has no pull request after 7 days
- **THEN** one `qa_loop_proposal` condition is active

#### Scenario: No verified fix
- **WHEN** at least 3 launched investigations in the last 14 days reached the publication stage and none was merged
- **THEN** one `qa_loop_funnel` condition is active
- **AND** with zero such investigations the condition is unmeasurable, not absent

### Requirement: Unmeasurable evidence never resolves
A condition check SHALL be present, absent or unmeasurable. An unreadable table or an empty opportunity set SHALL be unmeasurable and SHALL neither open nor resolve a condition.

ID: REQ-qa-remediation-liveness-002
Source: RFC0029; REQ-butler-control-plane-liveness-007 resolution rule
Scope: v1-mandatory

#### Scenario: Database unavailable
- **WHEN** the controller cannot read the evidence tables
- **THEN** no condition opens or resolves and the tick is marked incomplete

### Requirement: Cause-bound breaker reset
A manual QA breaker reset SHALL name the cause it addresses as `<cause_code>:<reference>` and SHALL be recorded with that cause. While the streak's dominant cause is structural, the reset SHALL name that cause, and a reference whose earlier reset was followed by a failure on the same structural cause SHALL be refused. A liveness condition SHALL never auto-reset, auto-publish, unpause or widen authority.

ID: REQ-qa-remediation-liveness-003
Source: owner gate bu-l34qcx (approved 2026-11-22); dossier move 8
Scope: v1-mandatory

#### Scenario: Reset without a cause
- **WHEN** a reset names no cause, or the dominant cause is structural and the reset names another
- **THEN** it is refused with `refusal_reason` and no `breaker_resets` row is written

#### Scenario: Reset naming the structural cause
- **WHEN** the dominant cause is `publisher_unconfigured` and the reset names `publisher_unconfigured:<reference>` with a fresh reference
- **THEN** the reset is accepted and `cause_code` and `cause_ref` are recorded

#### Scenario: Disproved claim
- **WHEN** the same cause and reference already reset the breaker and the same structural failure followed
- **THEN** a reset with that reference is refused with `structural_cause_unchanged:<cause>`

### Requirement: Engineering-side prerequisite stall watchdog
A watchdog outside the runtime SHALL report QA prerequisite work items that are blocked or unowned for more than 7 days, from a tracker export, and SHALL report `unknown` when it cannot read the evidence. It SHALL NOT write to the tracker.

ID: REQ-qa-remediation-liveness-004
Source: owner gate bu-l34qcx (approved 2026-11-22); runtime-to-tracker deny (bu-g2zuv2)
Scope: v1-mandatory

#### Scenario: Stalled prerequisite
- **WHEN** a watched item is blocked, or unassigned, with no update for more than 7 days
- **THEN** the dry run reports it `stalled` and exits 1

#### Scenario: Unreadable evidence
- **WHEN** the export is unreadable or a watched item has no row
- **THEN** that item is reported `unknown` and nothing is claimed
