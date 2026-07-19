# Core Notify — Delta

## REMOVED Requirements

### Requirement: Owner-Default-Page Suppression (Drop, Not Defer)

**Reason**: Discarding an eligible routine owner notification after it has been
fully composed loses owner-facing content and creates a ledger-only dead end.

**Migration**: Callers that receive a direct eligible owner-default policy or
context hold now receive the established `deferred` result shape and can use its
`notification_id` and `deliver_at`; they MUST NOT expect
`suppressed_quiet_hours` or `suppressed_context_bus` for those paths.

## ADDED Requirements

### Requirement: Owner-Default-Page Deferred Delivery

After the earlier `delivery_preferences` gate, the `notify()` tool SHALL
durably defer an eligible routine owner-default notification when the
`approvals_policy` quiet-hours window or an active suppressing context applies.
Eligibility is exactly: no `entity_id`, no explicit `recipient`, intent `send`
or `insight`, priority other than `high`, and an available notification pool.
The originating butler's `deferred_notifications` table SHALL store the full
resolved `notify.v1` envelope; message content SHALL NOT be copied into
`public.attention_ledger`.

The approvals-policy check SHALL run before the context check. A policy hold
uses the first whole local hour after the existing inclusive quiet-window end as
its UTC `deliver_at`. A context-only hold uses the latest expiry among all
active `dnd`/`sleeping` suppressors as its UTC `deliver_at`; DND remains the
deterministic ledger reason when both signals are active. When policy and
context holds coexist, the chosen `deliver_at` SHALL be the later of their two
anchors so the stored envelope cannot flush while either hold remains active. A
queued result SHALL return the existing `deferred` response shape with
`notification_id`, `deliver_at`, `channel`, and `priority`.

The earlier `delivery_preferences` mechanism SHALL remain first and unchanged.
High-priority, explicitly targeted, and other-intent notifications SHALL retain
their existing immediate behavior. Policy/context read failures SHALL retain
the existing fail-open immediate path.

#### Scenario: Approvals-policy quiet hours parks the full envelope

- **WHEN** `notify(message="Heads up", priority="medium")` is called with no
  `entity_id` and no `recipient`
- **AND** the current time falls inside the `approvals_policy` quiet-hours
  window
- **THEN** the fully resolved `notify.v1` envelope is inserted into the
  originating butler's `deferred_notifications` table with `status="pending"`
  and a policy-derived UTC `deliver_at`
- **AND** the tool returns `{"status": "deferred", "notification_id": "<uuid>",
  "deliver_at": "<ISO timestamp>", ...}` without calling Switchboard

#### Scenario: Context hold parks until every active suppressor expires

- **WHEN** an eligible `notify()` call is outside approvals-policy quiet hours
- **AND** both a DND signal and a sleeping signal are active with different
  expiry times
- **THEN** the tool stores the full envelope with `deliver_at` equal to the
  later expiry
- **AND** its linked ledger reason is `context_bus:dnd`
- **AND** the tool returns `status="deferred"` without immediate delivery

#### Scenario: High priority and targeted notifications remain exempt

- **WHEN** `notify(..., priority="high")` is called, OR `entity_id` or
  `recipient` is provided, OR intent is neither `send` nor `insight`
- **THEN** this requirement does not apply and the existing delivery path is
  preserved

#### Scenario: Delivery preferences remain the earlier unchanged gate

- **WHEN** the earlier `delivery_preferences` quiet-hours gate defers an
  eligible notification
- **THEN** it keeps its existing delivery-preferences anchor and ledger reason
- **AND** the approvals-policy and context checks are not evaluated

#### Scenario: Concurrent policy and context holds use the later anchor

- **WHEN** approvals-policy quiet hours and an active DND/sleeping signal both
  select a durable hold
- **THEN** the context bus is consulted after the policy check
- **AND** the row's `deliver_at` is the later of the policy and context anchors
- **AND** the ledger reason records both active hold reasons

#### Scenario: Policy or context lookup failure remains fail-open

- **WHEN** the approvals-policy or context lookup raises before a hold is
  selected
- **THEN** `notify()` follows its existing immediate delivery path
- **AND** it does not create a partial deferred row

#### Scenario: Deferred persistence failure is retryable and never sends

- **WHEN** an eligible policy or context hold is selected but inserting the
  deferred row fails
- **THEN** `notify()` returns `status="error"` with `retryable=true`
- **AND** it does not call Switchboard or return a suppressed status
- **AND** it makes a best-effort failed ledger record without message content

#### Scenario: Ledger failure after queueing preserves the hold

- **WHEN** the deferred row is inserted successfully but its ledger write fails
- **THEN** the queued row remains pending
- **AND** `notify()` still returns the deferred result

## MODIFIED Requirements

### Requirement: Attention Ledger Recording at the notify() Boundary

Every terminal decision the `notify()` owner-default gate makes SHALL be
recorded to `public.attention_ledger` with a closed outcome vocabulary
(`delivered`, `coalesced`, `deferred`, `suppressed`, `failed`) and a
machine-readable `reason`. A ledger-write failure MUST NOT block a notification
that was already delivered or durably queued.

`deferred` and `failed` are distinct and MUST NOT be conflated. `deferred` is a
benign, persisted hold with a concrete `deliver_at` that the scheduler will
retry on its own. `failed` is a genuine failure at this attempt. This requirement
continues to govern process-boundary consumers that compose the same primitives;
this change alters only the direct eligible owner-default policy/context branch.

#### Scenario: A genuine delivery failure is recorded as failed, not deferred

- **WHEN** any notify-boundary caller cannot resolve a recipient, receives an
  underlying delivery failure, or raises unexpectedly mid-dispatch
- **THEN** it writes an attention-ledger row with `outcome="failed"` and a
  failure-class reason
- **AND** it does not describe the failure as a benign deferred hold

#### Scenario: A retried failed delivery records its retry envelope

- **WHEN** a caller enqueues a retry envelope for a transport-failed delivery
- **THEN** the corresponding failed ledger row records the queued row id in
  `notification_ref`

#### Scenario: Owner-default policy deferral is linked in the ledger

- **WHEN** `notify()` parks an eligible owner-default call because of
  `public.approvals_policy` quiet hours
- **THEN** it writes `outcome="deferred"`, `reason="policy_quiet_hours"`, and the
  inserted deferred row id as `notification_ref`

#### Scenario: Owner-default context deferral is linked in the ledger

- **WHEN** `notify()` parks an eligible owner-default call because of an active
  suppressing context signal
- **THEN** it writes `outcome="deferred"`, `reason="context_bus:<signal_type>"`,
  and the inserted deferred row id as `notification_ref`

#### Scenario: Deferred persistence failure is recorded as failed

- **WHEN** policy or context parking is selected but the deferred-row INSERT
  fails
- **THEN** the tool makes a best-effort `outcome="failed"` ledger record with a
  `deferred_persistence_error:<ExceptionType>` reason
- **AND** the ledger attempt cannot convert the result into immediate delivery

#### Scenario: Delivery-preferences defer is recorded

- **WHEN** a notify-boundary caller defers a notification through per-butler
  `delivery_preferences`
- **THEN** it writes `outcome="deferred"`,
  `reason="delivery_preferences_quiet_hours"`, and the queued row id
- **AND** that earlier gate remains ahead of approvals-policy and context
  evaluation

#### Scenario: Successful delivery is recorded

- **WHEN** `notify()` successfully delivers a notification directly or through
  Switchboard
- **THEN** it writes `outcome="delivered"` and any provided delivery reference

#### Scenario: Ledger write failure never discards delivery or a queued row

- **WHEN** a ledger INSERT fails after a direct delivery or successful deferred
  enqueue
- **THEN** the failure is logged and swallowed
- **AND** the already chosen delivery or deferred result is preserved
