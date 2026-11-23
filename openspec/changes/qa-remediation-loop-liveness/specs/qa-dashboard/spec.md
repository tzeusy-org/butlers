## ADDED Requirements

### Requirement: Honest staffer status over remediation-loop conditions
`GET /api/qa/summary` SHALL NOT return `staffer_status` of `healthy` while a QA remediation-loop liveness condition is active, SHALL return `unknown` when those conditions cannot be read, and SHALL list the active conditions.

ID: REQ-qa-dashboard-801
Source: owner gate bu-l34qcx (approved 2026-11-22); REQ-qa-remediation-liveness-001
Scope: v1-mandatory

#### Scenario: Condition active
- **WHEN** a patrol is clean and a `qa_loop_capability` condition is active
- **THEN** `staffer_status` is `degraded` and `loop_conditions` names the cause

#### Scenario: Conditions unreadable
- **WHEN** the condition ledger cannot be read
- **THEN** `staffer_status` is `unknown` and `loop_conditions_known` is false

### Requirement: Reset names its cause
`POST /api/qa/circuit-breaker/reset` SHALL accept `{ cause_ref }` and apply REQ-qa-remediation-liveness-003. A refusal SHALL return `{ reset: false, message, refusal_reason }` without writing a row.

ID: REQ-qa-dashboard-802
Source: owner gate bu-l34qcx (approved 2026-11-22); REQ-qa-remediation-liveness-003
Scope: v1-mandatory

#### Scenario: Dashboard dialog
- **WHEN** an operator opens the reset dialog
- **THEN** the confirm action stays disabled until a `cause_code:reference` is entered
