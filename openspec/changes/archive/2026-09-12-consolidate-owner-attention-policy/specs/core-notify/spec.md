## MODIFIED Requirements

### Requirement: Owner-Default-Page Deferred Delivery
After the earlier `delivery_preferences` gate, `notify()` SHALL durably defer
an eligible routine owner-default notification when the global Owner Attention
Policy (`public.approvals_policy`) quiet window or an active suppressing context
applies. Eligibility is exactly: no `entity_id`, no explicit `recipient`,
intent `send` or `insight`, priority other than `high`, and an available
notification pool. The originating butler's `deferred_notifications` table
SHALL store the full resolved `notify.v1` envelope; message content SHALL NOT
be copied into `public.attention_ledger`.

The Owner Attention Policy SHALL be evaluated in its stored IANA timezone as
the end-exclusive interval `[quiet_start_hour, quiet_end_hour)`. A policy hold
uses the exact configured local end as its UTC `deliver_at`; a local instant at
the end is not quiet. A context-only hold uses the latest expiry among all
active `dnd`/`sleeping` suppressors as its UTC `deliver_at`. When both holds
apply, the later anchor SHALL win so the envelope cannot flush while either
hold remains active. A queued result SHALL return `status="deferred"` with its
`notification_id`, `deliver_at`, `channel`, and `priority`.

The earlier `delivery_preferences` mechanism SHALL remain first and unchanged.
High-priority, explicitly targeted, and other-intent notifications SHALL retain
their existing behavior. Missing, incomplete, invalid, or unreadable Owner
Attention Policy data SHALL retain the existing fail-open immediate path.

#### Scenario: Owner Attention Policy quiet hours parks the full envelope
- **WHEN** `notify(message="Heads up", priority="medium")` is called with no
  `entity_id` and no `recipient`
- **AND** the current local time falls inside the Owner Attention Policy
  quiet-hours window
- **THEN** the fully resolved `notify.v1` envelope is inserted into the
  originating butler's `deferred_notifications` table with `status="pending"`
  and the exact local policy end converted to UTC as `deliver_at`
- **AND** the tool returns `{"status": "deferred", "notification_id": "<uuid>",
  "deliver_at": "<ISO timestamp>", ...}` without calling Switchboard

#### Scenario: Exact policy end resumes immediate delivery
- **WHEN** an eligible routine owner-default `notify()` call occurs at exactly
  `quiet_end_hour` in the policy timezone
- **THEN** the call is outside the end-exclusive policy interval
- **AND** no policy-derived durable hold is created

#### Scenario: Context hold parks until every active suppressor expires
- **WHEN** an eligible `notify()` call is outside Owner Attention Policy quiet
  hours
- **AND** both a DND signal and a sleeping signal are active with different
  expiry times
- **THEN** the tool stores the full envelope with `deliver_at` equal to the
  later expiry and a deterministic `context_bus:dnd` reason
- **AND** the tool returns `status="deferred"` without immediate delivery

#### Scenario: Concurrent policy and context holds use the later anchor
- **WHEN** Owner Attention Policy quiet hours and an active DND/sleeping signal
  both select a durable hold
- **THEN** the context bus is consulted after the policy check
- **AND** the row's `deliver_at` is the later of the policy and context anchors
- **AND** the ledger reason records both active hold reasons

#### Scenario: High priority and targeted notifications are exempt
- **WHEN** `notify(..., priority="high")` is called, OR `entity_id` or
  `recipient` is provided, OR intent is neither `send` nor `insight`
- **THEN** this requirement does not apply and the existing delivery path is
  preserved

#### Scenario: Deferred persistence failure is retryable and never sends
- **WHEN** an eligible policy or context hold is selected but inserting the
  deferred row fails
- **THEN** `notify()` returns `status="error"` with `retryable=true`
- **AND** it does not call Switchboard or return a suppressed status

#### Scenario: Ledger failure after queueing preserves the hold
- **WHEN** the deferred row is inserted successfully but its ledger write fails
- **THEN** the queued row remains pending and `notify()` still returns the
  deferred result
