## MODIFIED Requirements

### Requirement: Status Transition Contract

The approval lifecycle MUST allow `pending -> approved|rejected|expired`,
`approved -> executed|abandoned`, and no transition from
`rejected|expired|executed|abandoned`. Invalid transitions raise
`InvalidTransitionError`.

#### Scenario: Approve a pending action

- **WHEN** `approve_action` is called with a valid action_id and authenticated
  human actor context
- **THEN** a compare-and-set UPDATE transitions status from `pending` to
  `approved`
- **AND** an `action_approved` audit event is recorded
- **AND** an available owning executor MAY then run the original tool function
- **AND** status advances to `executed` only after that execution persists its
  result and success audit event.

#### Scenario: Approve with concurrent race

- **WHEN** two concurrent approve calls target the same pending action
- **THEN** the compare-and-set ensures only one succeeds (WHERE status =
  'pending')
- **AND** the losing call receives a transition error with the current status.

#### Scenario: Reject a pending action

- **WHEN** `reject_action` is called with a valid action_id and authenticated
  human actor
- **THEN** status transitions from `pending` to `rejected` with `decided_by`
  set to `human:<actor_id> (reason: <escaped_reason>)`
- **AND** an `action_rejected` audit event is recorded.

#### Scenario: Expire stale actions

- **WHEN** `expire_stale_actions` is called
- **THEN** all pending actions where `expires_at < now()` are transitioned to
  `expired`
- **AND** an `action_expired` audit event is recorded for each.

#### Scenario: Abandon an approved unexecuted action

- **WHEN** an authenticated dashboard actor requests abandonment with a
  non-blank reason for an action whose status is `approved` and execution
  result is null
- **THEN** a compare-and-set UPDATE transitions it to `abandoned`
- **AND** an immutable `action_abandoned` event records the actor and exact
  reason in the same transaction
- **AND** the action cannot subsequently execute or return to an eligible
  recovery state.

#### Scenario: Invalid abandonment source state is rejected

- **WHEN** abandonment targets an action that is pending, rejected, expired,
  executed, abandoned, or has a non-null execution result
- **THEN** no action state or event is written
- **AND** the caller receives a transition error describing the durable current
  state.

#### Scenario: Retry and abandonment race

- **WHEN** retry dispatch and abandonment concurrently target the same approved
  action with a null execution result
- **THEN** the executor acquires a database row lock before any handler is
  invoked, and abandonment's compare-and-set waits for that lock
- **AND** only the winning terminal outcome is durably recorded
- **AND** the loser returns the current durable state without appending another
  terminal event.

#### Scenario: Already-executed action is replayed

- **WHEN** the executor is called for an action that is already `executed`
- **THEN** the stored `execution_result` is returned (idempotent replay)
- **AND** no second execution occurs.

### Requirement: Immutable Audit Events

The `approval_events` table MUST be an append-only audit log. Events include
`event_type`, `action_id`, `rule_id`, `actor`, `reason`, `event_metadata`
(JSONB), and `occurred_at`. A database trigger prevents UPDATE and DELETE
operations. An event's `action_id` and `rule_id` are immutable historical
provenance rather than deletion-blocking foreign keys, so terminal-action and
inactive-rule retention do not mutate or delete the event. A newly inserted
non-null action or rule reference MUST still resolve to its live row when the
event is written.

#### Scenario: Audit event creation for all state transitions

- **WHEN** any approval state transition occurs (queued, auto-approved,
  approved, rejected, expired, abandoned, execution succeeded, execution
  failed, rule created, rule revoked)
- **THEN** an immutable event row is inserted with the corresponding
  `ApprovalEventType` value
- **AND** actor, action_id/rule_id, reason, and metadata are captured.

#### Scenario: Audit event types

- **WHEN** events are recorded
- **THEN** the following canonical event types are used: `action_queued`,
  `action_auto_approved`, `action_approved`, `action_rejected`,
  `action_expired`, `action_abandoned`, `action_execution_succeeded`,
  `action_execution_failed`, `rule_created`, `rule_revoked`.

#### Scenario: Audit event immutability

- **WHEN** an UPDATE or DELETE is attempted on `approval_events`
- **THEN** the database trigger rejects the operation
- **AND** the row remains unchanged.

#### Scenario: Historical audit references preserve new-write validation

- **WHEN** a new event is inserted with a non-null `action_id` or `rule_id`
- **THEN** that identifier must reference a live pending action or approval
  rule when the event is written
- **AND** a retained event may continue to expose that identifier after its
  referenced terminal action or inactive rule is cleaned under the shorter
  retention policy.

### Requirement: Retention Policy

The module MUST support configurable retention windows for approvals data:
`pending_actions_retention_days` (default 90),
`approval_rules_retention_days` (default 180), and
`approval_events_retention_days` (default 365).

#### Scenario: Cleanup old actions

- **WHEN** `cleanup_old_actions` runs
- **THEN** only terminal-status actions (`rejected`, `expired`, `executed`,
  `abandoned`) older than the retention window are deleted
- **AND** `approved` actions remain retained and retryable, including old rows
  with a null `execution_result`
- **AND** related immutable action events remain unchanged with their historical
  `action_id` until their own event-retention window
- **AND** standing rules created from those terminal actions remain unchanged
  with their historical `created_from` until their separate rule-retention
  policy applies
- **AND** pending actions are never cleaned up automatically
- **AND** a dry-run mode returns counts without deleting.

#### Scenario: Cleanup old rules

- **WHEN** `cleanup_old_rules` runs
- **THEN** only inactive rules (`active=false`) older than the retention window
  are deleted
- **AND** immutable rule events remain unchanged with their historical `rule_id`
  until their separate 365-day event-retention window
- **AND** rerunning the cleanup after an eligible rule is deleted returns no
  additional rule deletion and never mutates or deletes the retained event.

#### Scenario: Cleanup old events requires privilege

- **WHEN** `cleanup_old_events` is called without `privileged=True`
- **THEN** a `PermissionError` is raised
- **AND** no events are deleted
- **WHEN** `cleanup_old_events` is called with `privileged=True`
- **THEN** events older than the retention window are deleted (bypasses
  immutability trigger).
