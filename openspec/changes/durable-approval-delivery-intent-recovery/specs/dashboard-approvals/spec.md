## ADDED Requirements

### Requirement: Truthful approval-delivery recovery projection
The approvals API and dashboard SHALL project a pending action's durable delivery state, mode, safe reason, attempt count, next eligible time, and derived stuck/ambiguous status without exposing recipient identity, callback material, action key, raw provider response, raw error, or a false claim that uncertain delivery was never attempted.

ID: REQ-dashboard-approvals-001
Source: RFC-0023
Scope: v1-mandatory

#### Scenario: Retry and quiet-hours state render honestly
- **WHEN** a pending action has a `ready`, `retry_wait`, or quiet-hours-delayed current delivery presentation
- **THEN** list and detail responses identify its delivery state and safe timing/reason truth
- **AND** the dashboard distinguishes waiting/retrying from confirmed notification delivery

#### Scenario: Ambiguous state is visible without unsafe detail
- **WHEN** an approval delivery presentation is `ambiguous` or derived stuck
- **THEN** the action detail visibly marks notification recovery as uncertain or needing attention
- **AND** neither the API nor UI includes the provider error text, recipient, callback token, secret, or provider payload

#### Scenario: Legacy emission data remains explicitly legacy
- **WHEN** an action predates new intent admission and only has an `approval_push_emissions` outcome
- **THEN** the API identifies that outcome as legacy evidence rather than authoritative recovery state
- **AND** the UI does not claim “never attempted” solely because that legacy outcome is null or failed

### Requirement: Safe delivery-recovery observability
The approvals service SHALL expose aggregate delivery-recovery counts, oldest-due age, expired-lease count, ambiguous count, and safe state/reason dimensions for operator observability without adding a dashboard action that approves, executes, replays, or mutates a parked domain action.

ID: REQ-dashboard-approvals-002
Source: RFC-0023
Scope: v1-mandatory

#### Scenario: Stuck backlog is measurable without sensitive labels
- **WHEN** a schema contains overdue retry work, expired claims, or ambiguous presentations
- **THEN** its metrics/read-model aggregation reports the safe count and age dimensions needed to detect the condition
- **AND** labels and responses exclude tool arguments, message content, recipient, callback material, and raw transport data

#### Scenario: Observability does not create a domain-action bypass
- **WHEN** an operator views delivery-recovery detail or a stuck-presentation aggregate
- **THEN** the surface provides no control that approves, rejects, expires, executes, or replays the parked domain action
- **AND** normal authenticated approval decision endpoints remain the only domain-action path

## MODIFIED Requirements

### Requirement: Approval Verbs
The dashboard SHALL expose explicit verb endpoints for approve, deny, defer,
and dashboard-only abandonment. A defer remains an authenticated pending-state
operation: it extends expiry and atomically resets the approval notification
re-presentation timer to `now + hours` through the delivery-intent
presentation-generation protocol, rather than through generic notification
retry or defer controls.

ID: REQ-dashboard-approvals-003
Source: RFC-0021,RFC-0023
Scope: v1-mandatory

#### Scenario: Defer with bounded hours and a later presentation
- **WHEN** `POST /api/approvals/{id}/defer {hours: int}` is called
- **THEN** the call is rejected with `422` unless `1 ≤ hours ≤ 168`
- **AND** on success, one action/intent/presentation transaction extends
  `expires_at`, supersedes an unstarted current presentation, and schedules
  exactly one successor generation for that successful defer at `now + hours`
- **AND** it retains the logical action key, does not enqueue a generic
  notification/deferred row, and appends
  `audit.append("approval.defer", target=action_id, note=str(hours))`

#### Scenario: Defer races a worker without an unsafe duplicate
- **WHEN** an authenticated defer and a worker handoff-start contend for the
  current presentation
- **THEN** defer-first prevents that generation's provider call, while
  handoff-start-first leaves only historical attempt evidence and schedules
  that successful defer's one successor generation
- **AND** the response/API projection reports only safe timing/state, never a
  key, callback token, recipient, or raw provider result

#### Scenario: Defer preserves other burst-digest members
- **WHEN** an authenticated actor defers a fourth cohort anchor or later
  collapsed member before its shared cohort digest begins handoff
- **THEN** the action's later direct presentation is scheduled for `now + hours`
  and that action's membership is marked ineligible for the unstarted digest
- **AND** other eligible cohort members remain deliverable through their shared
  digest without exposing a recovery key or callback material
