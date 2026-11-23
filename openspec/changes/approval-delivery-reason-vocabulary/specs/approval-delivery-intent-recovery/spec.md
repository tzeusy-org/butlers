## MODIFIED Requirements

### Requirement: Closed presentation state, reason, and ownership boundary
The system SHALL keep a stable action-intent root classified as `single`, `cohort_anchor`, or `collapsed`, and persist each direct-action or cohort-owned delivery presentation only as `ready`, `claimed`, `handoff_started`, `retry_wait`, `delivered`, `collapsed`, `cancelled`, `superseded`, or `ambiguous`. Persisted reasons SHALL use the versioned closed vocabulary `quiet_hours`, `owner_recipient_unavailable`, `callback_secret_unavailable`, `transport_unavailable`, `provider_preflight_failed`, `provider_outcome_unknown`, `action_approved`, `action_rejected`, `action_expired`, `action_abandoned`, `defer_rescheduled`, `cohort_empty`, `action_cancelled`, and `owner_mode`; new codes require an explicit vocabulary revision and unknown codes SHALL be rejected. Durable recovery state, recovery API projections, audit errors, and metric labels SHALL retain only bounded safe metadata, without recipient, callback token/secret, raw provider payload, raw exception, rendered message, or action arguments. `stuck` SHALL be derived rather than a writable terminal state. The worker SHALL be deterministic notification infrastructure with no model invocation or domain-action mutation authority; `pending_actions.status` SHALL remain the sole decision/execution gate.

ID: REQ-approval-delivery-intent-recovery-002
Source: RFC 0023 §Decision (notification-only authority and safe evidence)
Scope: v1-mandatory

#### Scenario: Unknown state or reason is rejected
- **WHEN** a writer attempts to create or transition a presentation with a state or reason outside the closed vocabulary
- **THEN** schema and repository validation reject the write
- **AND** API, metric, and log projections expose only the safe normalized value

#### Scenario: Worker has notification-only authority
- **WHEN** the approval-delivery worker processes an associated pending action
- **THEN** it may read that action/cohort and write only its local presentation, attempt, and safe audit-observability records
- **AND** it has no path to approve, reject, expire, defer, execute, edit, or otherwise mutate `pending_actions`

#### Scenario: Recovery dependencies preserve schema ownership
- **WHEN** a daemon constructs its recovery worker
- **THEN** the worker receives only its schema-local pool and recovery repository, deterministic renderer, current owner-recipient/callback-secret resolvers, and narrow authenticated notify/reconciliation client
- **AND** it receives no peer pool, DSN, cross-schema grant, approval operations/executor, entity/fact service, generic scheduler writer, or dashboard defer capability

#### Scenario: Owner-mode hold has its own reason
- **WHEN** the owner's active mode holds a card's push and the worker defers it before any provider handoff
- **THEN** the presentation SHALL retry with reason `owner_mode`, never `quiet_hours`
- **AND** a stop-cancelled action SHALL be fenced as `action_cancelled`, distinct from an owner rejection
