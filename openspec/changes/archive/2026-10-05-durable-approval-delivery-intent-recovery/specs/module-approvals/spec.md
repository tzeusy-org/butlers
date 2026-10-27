The recovery protocol is owned by [approval-delivery-intent-recovery](../../../../../specs/approval-delivery-intent-recovery/spec.md). The requirements below bind this capability’s integration seam; they do not define a separate recovery protocol.

## MODIFIED Requirements

### Requirement: Pending Actions Queue
The `pending_actions` table MUST provide a durable queue and audit log for approval-gated tool invocations. It stores `id`, `tool_name`, `tool_args` (JSONB), `status`, `requested_at`, and optional fields `agent_summary`, `session_id`, `expires_at`, `decided_by`, `decided_at`, `execution_result`, `approval_rule_id`, `why`, `evidence`, and producer-opt-in `deduplication_key`. When durable admission is enabled under the canonical additive rollout contract, every newly admitted `pending` row SHALL commit in the same transaction as one unique, schema-local approval delivery-intent root whose immutable logical action key and admission classification are safe for end-to-end notification recovery. The first three ordinary actions receive direct presentations; the fourth `cohort_anchor` joins and creates the cohort-owned digest; later ordinary `collapsed` actions record a terminal non-sendable local presentation and join that cohort. A digest-only `origin='prepared'` action uses the same durable representation with one standalone terminal `collapsed` action presentation, no cohort membership, no burst-count effect, and no provider or defer activation.

ID: REQ-module-approvals-001
Source: RFC-0021,RFC-0023
Scope: v1-mandatory

#### Scenario: Pending action rationale fields

- **WHEN** the `pending_actions` table is migrated or created fresh
- **THEN** nullable column `why TEXT` is available for human-readable rationale
- **AND** non-null column `evidence JSONB DEFAULT '[]'::jsonb` is available for cited evidence
- **AND** legacy rows without rationale data remain readable with `why = NULL` and `evidence = '[]'::jsonb`

#### Scenario: Semantic key serializes active equivalent actions

- **WHEN** concurrent producers park actions with the same non-null
  `deduplication_key`
- **THEN** a partial unique constraint MUST allow at most one row with that
  key in `pending`, `approved`, `rejected`, or `abandoned` status
- **AND** null historic keys MUST remain allowed, while an `expired` action
  MUST not block a newly surfaced action with the same key

#### Scenario: Atomic pending admission creates one intent
- **WHEN** a producer admits a new action with status `pending` and durable admission enabled
- **THEN** the pending row and one foreign-keyed intent with the action's immutable logical key plus its required direct presentation, cohort anchor/digest, or collapsed terminal presentation and membership commit together or both roll back
- **AND** an unavailable notification runtime does not permit a pending row without its intent

#### Scenario: Prepared action admission stays durable and silent
- **WHEN** a relationship or travel producer admits a digest-only action with `origin='prepared'`
- **THEN** the pending row, intent, and standalone terminal `collapsed` presentation commit atomically when admission is enabled
- **AND** it creates no cohort membership, due work, legacy emission, provider call, or defer successor
- **AND** default-off admission commits only the established prepared pending row

#### Scenario: List pending actions with status filter
- **WHEN** `list_pending_actions` is called with an optional status filter and limit
- **THEN** matching rows are returned ordered by `requested_at DESC`
- **AND** an invalid status value returns an error dict

#### Scenario: Show pending action detail
- **WHEN** `show_pending_action` is called with an action_id
- **THEN** the full PendingAction row is returned as a serialized dict
- **AND** its safe delivery projection is included without unsafe recovery detail
- **AND** an invalid UUID or missing action returns an error dict

#### Scenario: Count pending actions by status

- **WHEN** `pending_action_count` is called
- **THEN** a dict with `total` and `by_status` counts is returned
- **AND** delivery backlog metrics remain a separate safe aggregation rather than action payload data

### Requirement: Status Transition Contract

The approval lifecycle MUST allow `pending -> approved|rejected|expired`,
`approved -> executed|abandoned`, and no transition from
`rejected|expired|executed|abandoned`. Invalid transitions raise
`InvalidTransitionError`. Every transition out of `pending` SHALL atomically fence or cancel its nonterminal approval-delivery presentations without granting the notification worker domain-action mutation authority. Each authenticated dashboard defer remains a pending-state operation and appends exactly one bounded successor presentation generation through its own shared transaction path.

ID: REQ-module-approvals-002
Source: RFC-0021,RFC-0023
Scope: v1-mandatory

#### Scenario: Approve a pending action

- **WHEN** `approve_action` is called with a valid action_id and authenticated
  human actor context
- **THEN** a compare-and-set UPDATE transitions status from `pending` to
  `approved`
- **AND** an `action_approved` audit event is recorded
- **AND** an available owning executor MAY then run the original tool function
- **AND** status advances to `executed` only after that execution persists its
  result and success audit event.
- **AND** the same transaction atomically cancels its sendable delivery presentations

#### Scenario: Approve with concurrent race
- **WHEN** two concurrent approve calls target the same pending action
- **THEN** the compare-and-set ensures only one succeeds with `WHERE status = 'pending'`
- **AND** the losing call receives a transition error with the current status and cannot revive a cancelled presentation

#### Scenario: Dashboard abandons a stalled approved action

- **WHEN** a dashboard actor supplies a non-blank reason for an action whose
  status is `approved` and whose `execution_result` is null
- **THEN** a compare-and-set update transitions that action to `abandoned`
- **AND** an immutable `action_abandoned` event stores the actor and reason in
  the same transaction
- **AND** no MCP, Telegram callback, automatic, bulk, or scheduled path can
  invoke abandonment
- **AND** an action outside that exact predicate remains unchanged.

#### Scenario: Reject a pending action

- **WHEN** `reject_action` is called with a valid action_id and authenticated
  human actor
- **THEN** status transitions from `pending` to `rejected` with `decided_by`
  set to `human:<actor_id> (reason: <escaped_reason>)`
- **AND** an `action_rejected` audit event is recorded.
- **AND** the same transaction atomically cancels delivery recovery; a started handoff may only append late evidence and never changes the rejected action

#### Scenario: Expire stale actions

- **WHEN** `expire_stale_actions` is called
- **THEN** all pending actions where `expires_at < now()` are transitioned to
  `expired`
- **AND** an `action_expired` audit event is recorded for each.
- **AND** the same transaction cancels each action’s sendable presentations and marks its cohort membership ineligible without cancelling a shared eligible digest
- **AND** a worker that merely observes an expired action cannot perform the domain expiry transition itself

#### Scenario: Send-start and decision race
- **WHEN** a worker and a terminal decision contend for the same pending action
- **THEN** the committed fenced handoff-start marker is the final cancellation-safe boundary under a fixed action-then-intent-presentation lock order
- **AND** whichever transaction loses cannot send or revive future recovery, while a decision after send-start stops only future recovery

#### Scenario: Dashboard defer schedules a guarded re-presentation
- **WHEN** an authenticated dashboard actor defers a still-pending action for `1 ≤ hours ≤ 168`
- **THEN** the same action/intent transaction extends expiry, supersedes an unstarted current presentation, and appends the next presentation generation for `now + hours`
- **AND** it keeps the logical action key, cannot route through generic notifications, and does not grant the worker ability to defer or schedule future presentations

#### Scenario: Already-executed action is replayed

- **WHEN** the executor is called for an action that is already `executed`
- **THEN** the stored `execution_result` is returned (idempotent replay)
- **AND** no second execution occurs.
- **AND** no second notification delivery occurs

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
