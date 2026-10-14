## MODIFIED Requirements

### Requirement: Physical Actuation Risk Gate

Every `ha_call_service` invocation MUST be classified by the repository-owned
`(domain, service)` physical-risk map as `safe`, `reversible`, `consequential`,
or `protected`. Unknown pairs MUST fail closed as `protected`. Consequential and
protected calls MUST be durably parked, carrying goal lineage (goal, invocation,
depth, exposure), before any Home Assistant request, and MUST execute only
through owner per-event approval and the approvals executor's lineage read from
the verified call assertion. No standing rule SHALL approve a consequential or
protected call.

A call whose service can deliver content to a person (the `notify`, `tts`,
`persistent_notification` and other messaging or announcement service domains)
SHALL additionally be `communicate`: `ha_call_service` SHALL declare its target
(entity, device or notify service) as `recipient_params` and the message,
title and other recipient-visible service data as content-bearing arguments,
so the template-pinned test can apply; such a call is owner communication
only when every target resolves uniquely to an owner device or the owner's
notification service; any other target, including a shared speaker, a guest
device or a target it cannot resolve, is non-owner communication and SHALL
follow the non-owner rule (`REQ-capability-authorization-016`) as well as the
risk map.

Source: RFC 0038 D4, D7, D13 and owner decision 9
Scope: v1-mandatory

#### Scenario: Consequential call is parked without physical execution

- **WHEN** a goal invocation requests `lock.unlock` with no owner approval
- **THEN** the call SHALL be parked as a pending `ha_call_service` approval carrying the goal id, invocation id, depth and exposure
- **AND** it SHALL return `pending_approval` without sending a request to Home Assistant
- **AND** if the module receives such a call whose verified assertion carries no `approval_ref`, it SHALL NOT send a request to Home Assistant

#### Scenario: Approval grant permits one execution attempt

- **WHEN** an authenticated owner approves the parked action
- **THEN** the approved stored call (or the owner's edited version) SHALL be executed, never a call the model re-issues
- **AND** the approvals executor SHALL bind the action id, originating goal lineage from the verified assertion, and decided-by actor while invoking the original Home Assistant handler
- **AND** the handler SHALL perform one new, independently receipted actuation attempt

#### Scenario: Standing rule cannot approve a consequential call

- **WHEN** an owner standing rule matches a consequential or protected `ha_call_service` call
- **THEN** the call SHALL still be parked for per-event owner approval
- **AND** no Home Assistant request SHALL be sent before that approval

#### Scenario: A notification to a guest's device is non-owner communication

- **WHEN** a goal invocation calls `ha_call_service` for `notify.mobile_app_<guest_phone>` or a `tts` announcement on a shared speaker
- **THEN** the call SHALL be parked for per-event owner approval unless an owner-created standing rule matches it
- **AND** no Home Assistant request SHALL be sent before that

### Requirement: Honest Actuation Receipts

Before a Home Assistant service request leaves the process, the module MUST
insert a uniquely identified `attempting` receipt. It MUST then settle that same
receipt to `succeeded`, `failed`, or `unverified`, recording actor, goal lineage
(goal id, invocation id, depth, exposure) and approval lineage read from the
verified call assertion, requested and observed state, and any rollback hint. A
receipt write failure MUST refuse the physical call rather than act without
evidence.

Source: RFC 0038 D7, D12
Scope: v1-mandatory

#### Scenario: Verified post-condition is required for success

- **WHEN** Home Assistant accepts a service call
- **THEN** the module SHALL read every explicit entity target back from Home Assistant
- **AND** it SHALL record `succeeded` only when the declared post-condition matches
- **AND** a missing verifier, unreadable observation, or mismatch SHALL record `unverified`, require attention, and never claim success

#### Scenario: Definitive Home Assistant rejection is a failed attempt

- **WHEN** connection establishment definitively fails or Home Assistant returns an HTTP error
- **THEN** the module SHALL settle the pre-existing receipt to `failed`
- **AND** it SHALL re-raise the error after preserving the receipt

#### Scenario: Post-dispatch uncertainty is unverified and fenced from retry

- **WHEN** a request may have left the process but its response times out, resets, or cannot be parsed
- **THEN** the module SHALL attempt the declared live state read-back
- **AND** it SHALL settle the receipt to `unverified` even when the read-back matches
- **AND** the same approval id SHALL NOT issue another physical request while a prior receipt remains `attempting` or `unverified`; the owner must reconcile it and create a new approved action
- **AND** a prior `succeeded` receipt MAY be replayed to finalize approval bookkeeping without another physical request

#### Scenario: Reversible action records a rollback hint

- **WHEN** the risk map classifies an action as `reversible`
- **THEN** its receipt SHALL contain an explicit inverse service or a restore-previous-state instruction
- **AND** each retry SHALL create a distinct attempt id rather than deduplicating physical execution

### Requirement: Home Assistant Database Schema

The module SHALL provide Alembic migrations for its home-domain tables. Actuation
receipts SHALL be able to record goal lineage.

Source: RFC 0038 D7, D12
Scope: v1-mandatory

#### Scenario: Migration creates tables

- **WHEN** the Alembic migrations run
- **THEN** `ha_entity_snapshot` (entity_id TEXT PK, state TEXT, attributes JSONB, last_updated TIMESTAMPTZ, captured_at TIMESTAMPTZ) SHALL be created as the active bounded live-state cache written by the Home Assistant module
- **AND** `ha_source_health` (source TEXT PK, status TEXT, last_success_at TIMESTAMPTZ, last_error_at TIMESTAMPTZ, last_error TEXT, updated_at TIMESTAMPTZ) SHALL be created for the module's source-health lease
- **AND** `ha_command_log` (id BIGSERIAL PK, domain TEXT, service TEXT, target JSONB, data JSONB, result JSONB, context_id TEXT, issued_at TIMESTAMPTZ) SHALL be created
- **AND** `maintenance_items` SHALL be created (backing the maintenance tool suite)
- **AND** the `ha_state` predicate SHALL be seeded into `predicate_registry`
- **AND** index `ix_ha_command_log_issued_at` on `ha_command_log(issued_at)` SHALL be created
- **AND** the command log SHALL carry nullable legacy-compatible actuation receipt columns for attempt id, risk, actor, goal id, invocation id, depth, exposure, approval id, requested/observed state, status, rollback hint, failure reason, and completion time, with session id retained as a nullable legacy column

#### Scenario: Migration branch label

- **WHEN** `migration_revisions()` is called
- **THEN** it SHALL return `"home"` as the Alembic branch label
