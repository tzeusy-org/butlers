## ADDED Requirements

### Requirement: Singular Dashboard Terminal-Action Journal

The system SHALL create at most one durable terminal action for each immutable
dashboard user message. When a dashboard bug-report or dead-letter lane wins,
it SHALL persist the immutable action kind, canonical action payload and hash,
request identity, planned child effects, and action-level Stop state before any
visible effect is invoked. The action kind and canonical payload SHALL NOT
change after intent is recorded.

ID: REQ-dashboard-terminal-action-recovery-001
Source: heart-and-soul/vision.md § Non-Negotiable Rule 1; RFC 0005 § Workflow and Recovery Telemetry; design.md Decision 2
Scope: v1-mandatory

#### Scenario: Terminal lane first wins

- **WHEN** a dashboard bug-report or dead-letter lane first wins its terminal
  action claim for a user message
- **THEN** the system SHALL persist one immutable parent action and its planned
  child effects before it invokes the QA relay, dead-letter capture, or owner
  reply

#### Scenario: Duplicate delivery reaches a terminal lane

- **WHEN** a retry, restart, or duplicate delivery reaches the same dashboard
  user message
- **THEN** the system SHALL recover the existing parent action and SHALL NOT
  choose a second action kind or replace its canonical payload

### Requirement: Per-Effect Durable Receipts

The system SHALL model every independently visible terminal-lane effect as a
child of the singular action. A bug-report action SHALL have `qa_report` and
`conversation_reply` effects; a dead-letter action SHALL have
`dead_letter_capture` and `conversation_reply` effects. Each child SHALL have
its own stable idempotency key, state, attempt history, receipt/reference, and
reconciliation lease evidence. Internal child states SHALL be exactly `planned`,
`attempt_started`, `completed`, `failed`, `cancelled`, or `ambiguous`; the
owner-facing conversation projection SHALL map `planned` and `attempt_started`
to `pending_reconciliation`. The parent SHALL become completed only after every
planned child has a durable completed receipt; it SHALL preserve each child state
when it becomes failed or ambiguous.

ID: REQ-dashboard-terminal-action-recovery-002
Source: butler-switchboard § Dashboard Chat-Widget Classification Lanes; dashboard-conversations § Conversation Reply Channel; design.md Decisions 2 and 4
Scope: v1-mandatory

#### Scenario: First child effect commits before a later child

- **WHEN** a terminal action crashes after one child effect has a durable
  receipt but before another planned child effect completes
- **THEN** recovery SHALL preserve the completed child receipt and invoke only
  the missing child effect under its own idempotency key

#### Scenario: Receiver cannot prove an earlier child effect

- **WHEN** a child has an `attempt_started` record but its receiver cannot
  prove that the effect occurred and cannot enforce its idempotency key
- **THEN** recovery SHALL mark that child and parent action `ambiguous`
- **AND** it SHALL NOT automatically invoke the child effect again

### Requirement: Fenced and Bounded Reconciliation

The Switchboard daemon SHALL own a supervised terminal-action reconciler. It
SHALL run once at startup and thereafter at a persisted cadence no greater than
60 seconds, claim a 60-second fenced lease, and heartbeat the lease at least
every 20 seconds. A lease expiry alone SHALL NOT authorize another irreversible
call: the receiver's idempotency key or a durable receipt lookup must establish
that retry is safe. Each action SHALL have a persisted maximum of five attempts
and a `reconcile_deadline_at` no later than 15 minutes after intent; on reaching
either limit without proof, the unresolved effect and action SHALL become
`ambiguous`.

ID: REQ-dashboard-terminal-action-recovery-003
Source: heart-and-soul/vision.md § Non-Negotiable Rule 1; RFC 0005 § Workflow and Recovery Telemetry; design.md Decision 3
Scope: v1-mandatory

#### Scenario: Reconciler restarts after an effect call

- **WHEN** the Switchboard daemon restarts after a child effect wrote
  `attempt_started` but before its receipt was persisted
- **THEN** the startup reconciler SHALL query the receiver's durable receipt or
  receiver-enforced idempotency result before it considers another call

#### Scenario: Action exceeds its bounded recovery policy

- **WHEN** an action reaches its persisted retry budget or reconciliation
  deadline without a proven result
- **THEN** the system SHALL mark its unresolved child effects and parent action
  `ambiguous` and stop automatic delivery attempts

#### Scenario: Legacy in-progress turn is migrated

- **WHEN** the migration encounters a pre-existing
  `external_action_in_progress` dashboard turn without a durable per-effect
  receipt
- **THEN** it SHALL create an explicit ambiguous action record for that turn
- **AND** it SHALL NOT enqueue or replay the historical effect automatically

### Requirement: Terminal-Action Stop Linearization

The system SHALL persist action-level Stop intent even when a terminal action
has already been claimed. Before every child effect invocation, the reconciler
SHALL fence and record `attempt_started` under its lease after checking that
Stop intent. A confirmed cancellation SHALL mean that no child effect was
invoked.

ID: REQ-dashboard-terminal-action-recovery-004
Source: dashboard-chat-ui § Stream cancellation is a real server-side stop; dashboard-conversations REQ-dashboard-conversations-004; design.md Decision 6
Scope: v1-mandatory

#### Scenario: Stop arrives before a child starts

- **WHEN** the owner requests Stop after action intent but before a child effect
  records `attempt_started`
- **THEN** the action and dashboard turn SHALL become cancelled and the child
  effect SHALL NOT be invoked

#### Scenario: Stop arrives after a child starts

- **WHEN** the owner requests Stop after a child effect records
  `attempt_started`
- **THEN** the cancellation response SHALL report
  `pending_reconciliation` or `ambiguous`, not confirmed cancellation
- **AND** recovery SHALL establish the child result before it makes any terminal
  owner-facing claim

### Requirement: Action State, Turn Mapping, and Manual Resolution

The parent action state SHALL be exactly `pending_reconciliation`, `completed`,
`failed`, `cancelled`, or `ambiguous`; it is an aggregation over independently
preserved child-effect state. The following mapping SHALL be exhaustive:
| Parent action outcome | Required durable evidence | Dashboard-turn outcome |
| --- | --- | --- |
| `completed` / `bug_report` | every planned effect completed | `completed` |
| `completed` / `dead_letter` | every planned effect completed | `failed` |
| `failed` | a required effect reached a proven terminal failure, or the owner manually resolved an ambiguity as failed | `failed` |
| `cancelled` | Stop won before any child recorded `attempt_started` | `cancelled` |
| `ambiguous` | an attempted effect has no safe receipt/idempotency proof before the recovery bound | explicit `ambiguous` |
`ambiguous` is closed to automatic delivery but is not silently final: an
owner-only manual-resolution operation MAY append immutable `completed` or
`failed` resolution evidence with a required sanitized note, and only that
operation may resolve an ambiguous parent. A manual `completed` resolution of a
bug report maps to `completed`; a manual `completed` resolution of a dead letter
maps to `failed`; a manual `failed` resolution maps to `failed` for either kind.
Manual resolution SHALL retain, rather than overwrite, every child-effect state
and SHALL never invoke a relay or child effect. Completed, failed, and cancelled
outcomes SHALL otherwise be monotonic. The system SHALL provide `GET
/api/dashboard/terminal-actions/{id}` for owner-only inspection and `POST
/api/dashboard/terminal-actions/{id}/resolve` for manual resolution; the resolve
request SHALL accept only `completed` or `failed` plus a required sanitized note.

ID: REQ-dashboard-terminal-action-recovery-005
Source: heart-and-soul/vision.md § Non-Negotiable Rule 1; dashboard-conversations § Conversation Messages List; design.md Decision 5
Scope: v1-mandatory

#### Scenario: All planned child effects complete

- **WHEN** every planned child effect has a durable completed receipt
- **THEN** the parent action and dashboard turn SHALL transition according to
  the action-kind mapping exactly once

#### Scenario: Primary effect completed but acknowledgement failed

- **WHEN** a `qa_report` or `dead_letter_capture` child has a completed receipt
  but its required `conversation_reply` child reaches a proven failure
- **THEN** the parent action SHALL become `failed` and preserve both effect
  states and the primary-effect safe reference
- **AND** the conversation projection SHALL permit the UI to distinguish a filed
  report or captured dead letter from a failed acknowledgement

#### Scenario: Action reaches a proven terminal failure

- **WHEN** a required child effect reaches a proven terminal failure and no
  ambiguity remains
- **THEN** the parent action and dashboard turn SHALL become `failed` according
  to the mapping table

#### Scenario: Owner resolves an ambiguity

- **WHEN** the owner submits a manual completed or failed resolution with a
  required sanitized note for an ambiguous action
- **THEN** `POST /api/dashboard/terminal-actions/{id}/resolve` SHALL append
  immutable resolution evidence and expose the resolved outcome without
  invoking any relay or child effect

### Requirement: Reconciliation Observability and Safe Evidence

The system SHALL expose low-cardinality counts of pending, stale, failed, and
ambiguous terminal actions, and SHALL make an owner-only inspection resource
available for an action ID. Conversation and UI read models SHALL expose only
sanitized reason codes and safe references; raw relay, database, credential, or
unbounded exception text SHALL NOT be exposed.

ID: REQ-dashboard-terminal-action-recovery-006
Source: heart-and-soul/vision.md § Non-Negotiable Rule 1; RFC 0005 § Workflow and Recovery Telemetry; design.md Decisions 5-6
Scope: v1-mandatory

#### Scenario: Action requires owner inspection

- **WHEN** an action is stale or ambiguous
- **THEN** the dashboard SHALL provide its owner-only action-inspection
  reference and sanitized reason code
- **AND** the reconciliation metrics SHALL count it without storing a raw error
  payload as an owner-visible value

### Requirement: Reconciler Operational Modes

The Switchboard SHALL own a persisted, owner-only operational setting named
`terminal_action_reconciler.mode` whose only values are `observe` and `active`.
Its deployment default SHALL be `observe`. In `observe`, the reconciler SHALL
inspect/claim actions, look up receipts, expose owner-visible state and metrics,
and mark only a bounded unprovable action `ambiguous`; it SHALL NOT invoke a
missing child effect or automatically retry external delivery. In `active`, it
MAY invoke a missing child only after the receipt/idempotency proof required by
this capability. Promotion from `observe` to `active` SHALL be an audited
owner-authorized settings change after the kill/restart canary and a review of
pending/stale metrics. A rollback SHALL return the setting to `observe`, stop new
automatic child-effect invocation, and retain action/effect rows, leases, and
pending/ambiguous state as inspectable evidence.

ID: REQ-dashboard-terminal-action-recovery-007
Source: heart-and-soul/vision.md § Non-Negotiable Rule 1; dashboard-terminal-action-recovery design.md Decision 7
Scope: v1-mandatory

#### Scenario: Fresh deployment begins in observe mode

- **WHEN** the recovery schema and Switchboard reconciler first deploy
- **THEN** the persisted mode SHALL be `observe`
- **AND** a missing child effect SHALL not be invoked automatically

#### Scenario: Owner promotes the reconciler after validation

- **WHEN** the owner authorizes promotion after a compose-backed kill/restart
  canary and review of pending/stale metrics
- **THEN** the system SHALL record an audited transition to `active`
- **AND** only then may the reconciler invoke a safely retryable missing child
  effect

#### Scenario: Owner rolls back an active reconciler

- **WHEN** the owner changes an active reconciler back to `observe`
- **THEN** it SHALL make no new automatic child-effect calls after the transition
- **AND** it SHALL retain pending, ambiguous, and lease evidence for inspection
