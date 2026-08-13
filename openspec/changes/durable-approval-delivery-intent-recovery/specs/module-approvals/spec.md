## MODIFIED Requirements

### Requirement: Pending Actions Queue
The `pending_actions` table SHALL provide a durable queue and audit log for approval-gated tool invocations, storing `id`, `tool_name`, `tool_args` (JSONB), `status`, `requested_at`, and optional `agent_summary`, `session_id`, `expires_at`, `decided_by`, `decided_at`, `execution_result`, `approval_rule_id`, `why`, `evidence`, and producer-opt-in `deduplication_key`; every newly admitted `pending` row SHALL commit in the same transaction as one unique, schema-local approval delivery-intent root whose immutable logical action key and admission classification are safe for end-to-end notification recovery. The first three actions receive direct presentations; the fourth `cohort_anchor` joins and creates the cohort-owned digest; later `collapsed` actions record a terminal non-sendable local presentation and join that cohort.

ID: REQ-module-approvals-001
Source: RFC-0021,RFC-0023
Scope: v1-mandatory

#### Scenario: Pending action rationale fields
- **WHEN** the `pending_actions` table is migrated or created fresh
- **THEN** nullable `why TEXT` and non-null `evidence JSONB DEFAULT '[]'::jsonb` are available
- **AND** legacy rows without rationale remain readable with `why = NULL` and `evidence = '[]'::jsonb`

#### Scenario: Semantic key serializes active equivalent actions
- **WHEN** concurrent producers park actions with the same non-null `deduplication_key`
- **THEN** a partial unique constraint allows at most one row with that key in `pending`, `approved`, `rejected`, or `abandoned` status
- **AND** null historic keys remain allowed while an `expired` action does not block a newly surfaced action with the same key

#### Scenario: Atomic pending admission creates one intent
- **WHEN** a producer admits a new action with status `pending`
- **THEN** the pending row and one foreign-keyed intent with the action's immutable logical key plus its required direct presentation, cohort anchor/digest, or collapsed terminal presentation and membership commit together or both roll back
- **AND** an unavailable notification runtime does not permit a pending row without its intent

#### Scenario: List pending actions with status filter
- **WHEN** `list_pending_actions` is called with an optional status filter and limit
- **THEN** matching rows are returned ordered by `requested_at DESC`
- **AND** an invalid status value returns an error dict

#### Scenario: Show pending action detail
- **WHEN** `show_pending_action` is called with an action_id
- **THEN** the full PendingAction row and safe delivery projection are returned as serialized data
- **AND** an invalid UUID or missing action returns an error dict

#### Scenario: Count pending actions by status
- **WHEN** `pending_action_count` is called
- **THEN** it returns a dict with `total` and `by_status` counts
- **AND** delivery backlog metrics remain a separate safe aggregation rather than action payload data

### Requirement: Status Transition Contract
The approval lifecycle SHALL allow `pending -> approved|rejected|expired` and `approved -> executed|abandoned`, with `rejected|expired|executed|abandoned` terminal and invalid transitions raising `InvalidTransitionError`; every transition out of `pending` SHALL atomically fence or cancel its nonterminal approval-delivery presentations without granting the notification worker domain-action mutation authority. Each authenticated dashboard defer remains a pending-state operation and appends exactly one bounded successor presentation generation through its own shared transaction path.

ID: REQ-module-approvals-002
Source: RFC-0021,RFC-0023
Scope: v1-mandatory

#### Scenario: Approve a pending action
- **WHEN** `approve_action` is called with a valid action_id and authenticated human actor context
- **THEN** a compare-and-set update transitions status from `pending` to `approved`, records `action_approved`, and atomically cancels its sendable delivery presentations
- **AND** an available owning executor may then run the original tool function and advances to `executed` only after its result persists

#### Scenario: Approve with concurrent race
- **WHEN** two concurrent approve calls target the same pending action
- **THEN** the compare-and-set ensures only one succeeds with `WHERE status = 'pending'`
- **AND** the losing call receives a transition error with the current status and cannot revive a cancelled presentation

#### Scenario: Dashboard abandons a stalled approved action
- **WHEN** a dashboard actor supplies a non-blank reason for an action whose status is `approved` and whose `execution_result` is null
- **THEN** a compare-and-set update transitions it to `abandoned` and appends immutable actor/reason evidence in the same transaction
- **AND** no MCP, Telegram callback, automatic, bulk, or scheduled path can invoke abandonment

#### Scenario: Reject a pending action
- **WHEN** `reject_action` is called with a valid action_id and authenticated human actor
- **THEN** status transitions from `pending` to `rejected` with escaped decision provenance, records `action_rejected`, and atomically cancels delivery recovery
- **AND** a previously started handoff may only append late attempt evidence and never changes the rejected action

#### Scenario: Expire stale actions
- **WHEN** the canonical stale-expiry operation finds a pending action whose `expires_at < now()`
- **THEN** it transitions the action to `expired`, records `action_expired`, cancels its sendable action presentations, and atomically marks its cohort membership ineligible without cancelling a shared eligible cohort digest
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
- **THEN** the stored `execution_result` is returned idempotently
- **AND** no second execution or notification delivery occurs
