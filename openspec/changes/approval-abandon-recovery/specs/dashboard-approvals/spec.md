## MODIFIED Requirements

### Requirement: Approvals Flat List API

The dashboard SHALL expose `GET /api/approvals?state=waiting|decided|all|stalled`
as a flat-list view complementing the existing `GET /api/approvals/actions`
paginated list.

#### Scenario: Filter by state

- **WHEN** `GET /api/approvals?state=waiting` is called
- **THEN** the response is `ApiResponse[ApprovalSummary[]]` containing only
  actions in `pending` state, ordered `created_at DESC`.
- **WHEN** `GET /api/approvals?state=decided` is called
- **THEN** the response contains actions in `approved | rejected | expired |
  executed | abandoned` states.
- **WHEN** `GET /api/approvals?state=all` is called or `state` is omitted
- **THEN** all states are included.

### Requirement: Whole-population stalled approval radar

The flat `GET /api/approvals` endpoint SHALL accept `state=stalled` in
addition to its existing states. A stalled approval SHALL be derived only when
its persisted `status` is exactly `approved` and its `execution_result` is
`NULL`; stalled SHALL NOT be persisted as a new status or inferred from a time
threshold.

Every flat approvals response, regardless of requested state, offset, or
limit, SHALL include `meta.stalled_count`: the count of all currently stalled
actions across the endpoint's eligible approval-source population. The list
filter and the aggregate SHALL use the same per-pool eligibility and exact
stalled predicate.

#### Scenario: Stalled filter selects only approved actions without execution

- **WHEN** `GET /api/approvals?state=stalled` reads a population containing
  approved actions with null and non-null execution results plus other statuses
- **THEN** it returns only actions whose status is `approved` and whose
  `execution_result` is null
- **AND** it does not return an `executed`, `pending`, `rejected`, `expired`,
  `abandoned`, or approved action with a non-null execution result.

#### Scenario: Stalled metadata is independent of the page window

- **WHEN** `GET /api/approvals?state=decided&limit=30` returns a bounded
  history page while more than 30 older/newer rows exist
- **THEN** `meta.stalled_count` equals the count of every eligible stalled
  approval, not the count of rows on that page
- **AND** the same count is returned for `state=waiting`, `state=decided`,
  `state=all`, and `state=stalled` requests over the same healthy population.

#### Scenario: Degraded approval sources cannot imply an all-clear

- **WHEN** any eligible approval source cannot supply its list or stalled
  aggregate contribution
- **THEN** the flat response identifies that source in `meta.sources_degraded`
- **AND** any returned `meta.stalled_count` is treated as observed partial
  coverage rather than proof that no stalled approvals exist.

### Requirement: Approval Verbs

The dashboard SHALL expose explicit verb endpoints for approve, deny, defer,
and dashboard-only abandonment.

#### Scenario: Approve with optional edits

- **WHEN** `POST /api/approvals/{id}/approve {edits?: object}` is called
- **THEN** the action is approved with any supplied `edits` applied to its
  arguments
- **AND** `audit.append("approval.approve", target=action_id,
  note=json.dumps(edits))` is invoked
- **AND** the underlying tool is executed via the shared executor (existing
  module-approvals behavior).

#### Scenario: Deny with reason

- **WHEN** `POST /api/approvals/{id}/deny {reason?: str}` is called
- **THEN** the action transitions to `rejected`
- **AND** `audit.append("approval.deny", target=action_id, note=reason)` is
  invoked.

#### Scenario: Defer with bounded hours

- **WHEN** `POST /api/approvals/{id}/defer {hours: int}` is called
- **THEN** the call is rejected with `422` unless `1 ≤ hours ≤ 168`
- **AND** on success, the action's `expires_at` is extended by `hours` and the
  notification re-presentation timer is reset to `now + hours`
- **AND** `audit.append("approval.defer", target=action_id, note=str(hours))`
  is invoked.

#### Scenario: Dashboard abandons an eligible stalled action

- **WHEN** `POST /api/approvals/{id}/abandon {reason: string}` is called by an
  authenticated dashboard actor for an action whose status is `approved` and
  execution result is null
- **THEN** the action transitions to `abandoned` with an immutable
  `action_abandoned` event carrying the actor and non-blank reason
- **AND** the response reports the terminal status
- **AND** the action is absent from stalled results and has no Retry affordance.

#### Scenario: Dashboard rejects invalid abandonment without mutation

- **WHEN** the abandon endpoint receives a blank reason or an action outside
  the exact approved/null-execution predicate
- **THEN** it returns a validation or transition error without writing an event
  or changing the action.

#### Scenario: Abandonment has no alternate invocation path

- **WHEN** an approval is surfaced through an MCP tool, Telegram callback,
  automatic workflow, scheduled cleanup, or bulk operation
- **THEN** that path does not expose or invoke abandonment.
