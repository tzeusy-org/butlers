# Core Notify — Same-Window Coalescing at the Deferred-Notification Flush

## ADDED Requirements

### Requirement: Same-Window Coalescing of Deferred Notifications
When the deferred-notification flush pass (`_tick_deferred_notification_pass`) finds more than one due (`status='pending' AND deliver_at <= now`) row targeting the same delivery target (channel + recipient), it SHALL compose them into ONE message and deliver them via a single `notify_fn` call instead of one send per row. A delivery target with exactly one due row SHALL be delivered unchanged (its stored envelope, verbatim).

#### Scenario: Multiple same-target due notifications compose into one send
- **WHEN** the flush pass runs and finds 3 due notifications all addressed to
  the same (channel, recipient) pair
- **THEN** exactly one `notify_fn` call is made, carrying a composed message
  that includes all 3 underlying messages
- **AND** all 3 underlying rows are marked `status='delivered'` with the same
  `delivered_at`

#### Scenario: A solo due notification is delivered unchanged
- **WHEN** the flush pass finds exactly one due notification for a given
  delivery target
- **THEN** `notify_fn` is called with that row's stored envelope, unmodified
- **AND** the row is marked `delivered` exactly as it was before this change

#### Scenario: Different recipients are never coalesced
- **WHEN** two due notifications target different explicit recipients (or one
  targets an explicit recipient and the other targets none, i.e. the owner's
  default channel)
- **THEN** each is delivered via its own `notify_fn` call — never folded into
  one composed message together

#### Scenario: A failed composed send leaves the whole group pending
- **WHEN** `notify_fn` raises for a composed multi-row digest
- **THEN** every row in that group remains `status='pending'` for retry on
  the next tick — no row in the group is marked `delivered` while others are
  not

### Requirement: Attention Ledger Recording at the Deferred-Notification Flush
Every successful flush-time delivery SHALL be recorded to `public.attention_ledger` with `source="notify"`: `outcome="delivered"` for a solo-row send, `outcome="coalesced"` (one ledger row per underlying notification) for a composed digest send. A ledger-write failure MUST NOT block or fail the notification it describes (best-effort, fail-open — same contract as every other `record_attention_event` call site).

#### Scenario: Composed digest records one coalesced row per underlying notification
- **WHEN** the flush pass delivers a composed digest of 3 due notifications
- **THEN** 3 `public.attention_ledger` rows are written, each with
  `source="notify"`, `outcome="coalesced"`, and its own row's
  `notification_ref`
