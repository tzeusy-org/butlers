## MODIFIED Requirements

### Requirement: Attention Ledger Recording at the notify() Boundary
Every terminal decision the `notify()` owner-default quiet-hours gate makes SHALL be recorded to `public.attention_ledger` with a closed outcome vocabulary (`delivered`, `coalesced`, `deferred`, `suppressed`, `failed`) and a machine-readable `reason`. A ledger-write failure MUST NOT block or fail the notification it describes (best-effort, fail-open).

`deferred` and `failed` are distinct and MUST NOT be conflated: `deferred` is a benign, chosen hold that resolves on its own (a quiet-hours window ending, a coalescing flush tick) with no caller action required. `failed` is a genuine terminal failure at this attempt — no recipient configured, a transport/delivery error, an unexpected exception — that nothing automatically retries unless the caller explicitly enqueues a retry envelope (e.g. via `insert_deferred_notification`) and records the resulting row id as `notification_ref`. This distinction applies identically to every caller that composes the same gating/dispatch primitives `notify()` uses from outside a butler daemon's own MCP closure (e.g. `butlers.jobs.secrets_lifecycle`, `butlers.jobs.home._send_notify`, `butlers.jobs.decision_review._deliver`) — see those modules' docstrings for why each is a process-boundary-forced consumer rather than a direct `notify()` caller.

#### Scenario: A genuine delivery failure is recorded as failed, not deferred
- **WHEN** any notify-boundary caller (the `notify()` tool itself, or a process-boundary-forced consumer composing the same primitives) cannot resolve a recipient, or the underlying `deliver()` dispatch returns `status="failed"`, or an unexpected exception occurs mid-dispatch
- **THEN** a `public.attention_ledger` row is written with `outcome="failed"` and a `reason` identifying the failure class (e.g. `"no_recipient_configured"`, `"delivery_error:<detail>"`, `"unexpected_error:<ExceptionType>"`)
- **AND** this row is NEVER written with `outcome="deferred"` — that value is reserved for a benign hold the system will retry on its own

#### Scenario: A retried failed delivery records its retry envelope
- **WHEN** a caller enqueues a retry envelope for a transport-failed delivery (e.g. via `insert_deferred_notification` on a `deferred_notifications` table that a scheduler tick will flush)
- **THEN** the corresponding `outcome="failed"` ledger row's `notification_ref` is set to the enqueued row's id, so the failure is traceable to its retry attempt rather than a dead end

#### Scenario: An unexpected exception retries only when enough state is resolved
- **WHEN** a process-boundary consumer's per-credential dispatch raises an unexpected exception AFTER the message AND recipient have been resolved (e.g. `deliver()` itself raises instead of returning `status="failed"`, or a ledger write faults post-resolution)
- **THEN** the failure is treated as retryable: a retry envelope is enqueued on the SAME single deferral path the transport-failed case uses (`_enqueue_deferred_envelope` / `insert_deferred_notification`, with supersede-at-enqueue dedup), and the `outcome="failed"` ledger row records `reason="unexpected_error_retry:<ExceptionType>"` with `notification_ref` set to the enqueued row's id
- **AND WHEN** the exception raises BEFORE the message/recipient are resolvable (e.g. inside the last-notified-state or suppression lookups)
- **THEN** there is nothing safe to enqueue, so the row is stamped honestly as `reason="unexpected_error:<ExceptionType>"` with `notification_ref` null — never a half-built or mis-addressed envelope
- **AND** the retry's `deliver_at` honors any resolved quiet-hours deferral so the retry is not redelivered inside quiet hours (the flush path gates purely on `deliver_at`), and the debounce marker is NOT advanced on the retry path (only a confirmed direct delivery advances it)

#### Scenario: Owner-default policy deferral is linked in the ledger
- **WHEN** `notify()` parks an eligible owner-default call because of
  `public.approvals_policy` quiet hours
- **THEN** it writes `outcome="deferred"`, `reason="policy_quiet_hours"`, and
  the inserted deferred row id as `notification_ref`

#### Scenario: Owner-default context deferral is linked in the ledger
- **WHEN** `notify()` parks an eligible owner-default call because of an active
  suppressing context signal
- **THEN** it writes `outcome="deferred"`,
  `reason="context_bus:<signal_type>"`, and the inserted deferred row id as
  `notification_ref`

#### Scenario: Deferred persistence failure is recorded as failed
- **WHEN** policy or context parking is selected but the deferred-row INSERT
  fails
- **THEN** the tool makes a best-effort `outcome="failed"` ledger record with a
  `deferred_persistence_error:<ExceptionType>` reason
- **AND** the ledger attempt cannot convert the result into immediate delivery

#### Scenario: Delivery-preferences defer is recorded
- **WHEN** any notify-boundary caller (the `notify()` tool itself, or a process-boundary-forced consumer composing the same primitives) defers a notification via the per-butler `delivery_preferences` quiet-hours mechanism
- **THEN** a `public.attention_ledger` row is written with `outcome="deferred"` and `reason="delivery_preferences_quiet_hours"`, and `notification_ref` set to the enqueued `deferred_notifications` row id
- **AND** the `delivery_preferences` gate is checked FIRST, ahead of the approvals-policy and context-bus gates, mirroring `notify()`'s own gate ordering
- **AND** a process-boundary consumer that has no butler identity of its own (e.g. `butlers.jobs.secrets_lifecycle`) keys the `delivery_preferences` lookup on the identity it already delivers under (`"switchboard"`) — `delivery_preferences` is a per-schema table, so the lookup uses that identity's own pool

#### Scenario: Successful delivery is recorded
- **WHEN** `notify()` successfully delivers a notification (either via direct Switchboard self-delivery or via the switchboard client)
- **THEN** a `public.attention_ledger` row is written with `outcome="delivered"`, and `notification_ref` set to the delivery's `notification_id` when the delivery result provides one

#### Scenario: Ledger write failure never blocks delivery
- **WHEN** the `public.attention_ledger` table is unavailable (e.g. an unmigrated database) or the INSERT otherwise fails
- **THEN** `notify()` proceeds exactly as it would without this requirement — the ledger write is logged at WARNING and swallowed, never raised

## ADDED Requirements

### Requirement: Confirmed Delivery Cannot Be Reclassified by Bookkeeping

The Switchboard notification route SHALL distinguish the external transport
result from post-send observability work. Once Messenger confirms an external
delivery, the route SHALL return a confirmed-delivery result containing its
safe receipt even if routing-log, registry, notification-log, audit, or
attention-ledger persistence later fails. Such later failures SHALL be caught
and recorded as safe telemetry failures; they SHALL not turn the send into a
retryable delivery failure.

ID: REQ-core-notify-027
Source: heart-and-soul/vision.md Rule 3; RFC 0003; RFC 0011 Amendment 1; design.md Decision 5
Scope: v1-mandatory

#### Scenario: Post-send routing-log ACL failure preserves confirmation

- **WHEN** Messenger returns a successful send receipt and a subsequent
  routing-log write is rejected by database permissions
- **THEN** the caller receives confirmed delivery with the safe receipt
- **AND** the error is logged as non-fatal telemetry associated with the
  delivery attempt
- **AND** the caller does not retry solely because that bookkeeping write
  failed

#### Scenario: Pre-send failure remains distinguishable from uncertain transport

- **WHEN** route construction or recipient resolution fails before external
  transport begins
- **THEN** the route returns a safe not-attempted failure classification
- **AND** it does not claim external delivery or synthesize a receipt
- **WHEN** transport may have begun but no confirmation is available
- **THEN** it returns an explicit uncertain classification rather than a
  generic retryable failure

#### Scenario: Delivery ownership remains Switchboard-mediated

- **WHEN** a non-Switchboard component needs an external notification
- **THEN** it uses the existing Switchboard/Messenger delivery boundary rather
  than querying Switchboard private tables or opening a direct Messenger path
- **AND** a post-send telemetry failure does not widen that component's
  database permissions
