# Core Notify — Attention Ledger + Context-Bus Gating

## ADDED Requirements

### Requirement: Attention Ledger Recording at the notify() Boundary
Every terminal decision the `notify()` owner-default quiet-hours gate makes SHALL be recorded to `public.attention_ledger` with a closed outcome vocabulary (`delivered`, `deferred`, `suppressed`) and a machine-readable `reason`. A ledger-write failure MUST NOT block or fail the notification it describes (best-effort, fail-open).

#### Scenario: Quiet-hours suppression is recorded
- **WHEN** `notify()`'s owner-default path is suppressed by `public.approvals_policy` quiet hours
- **THEN** a `public.attention_ledger` row is written with `outcome="suppressed"` and `reason="quiet_hours"`
- **AND** the notify() call still returns `{"status": "suppressed_quiet_hours", ...}` to the caller unchanged

#### Scenario: Context-bus suppression is recorded
- **WHEN** `notify()`'s owner-default path is suppressed because an active `dnd` or `sleeping` context-bus signal is present (and quiet hours did not already suppress)
- **THEN** a `public.attention_ledger` row is written with `outcome="suppressed"` and `reason="context_bus:<signal_type>"`
- **AND** the notify() call returns `{"status": "suppressed_context_bus", "channel": ..., "context_signal": "<signal_type>"}`

#### Scenario: Delivery-preferences defer is recorded
- **WHEN** `notify()` defers a notification via the existing per-butler `delivery_preferences` quiet-hours mechanism
- **THEN** a `public.attention_ledger` row is written with `outcome="deferred"` and `reason="delivery_preferences_quiet_hours"`, and `notification_ref` set to the `deferred_notifications` row id

#### Scenario: Successful delivery is recorded
- **WHEN** `notify()` successfully delivers a notification (either via direct Switchboard self-delivery or via the switchboard client)
- **THEN** a `public.attention_ledger` row is written with `outcome="delivered"`, and `notification_ref` set to the delivery's `notification_id` when the delivery result provides one

#### Scenario: Ledger write failure never blocks delivery
- **WHEN** the `public.attention_ledger` table is unavailable (e.g. an unmigrated database) or the INSERT otherwise fails
- **THEN** `notify()` proceeds exactly as it would without this requirement — the ledger write is logged at WARNING and swallowed, never raised

### Requirement: Context-Bus Gating at the notify() Owner-Default Path
The `notify()` owner-default quiet-hours gate SHALL also consult the situational context bus (`public.user_context`, RFC 0009) for an active `dnd` or `sleeping` signal, deterministically (no LLM in the read path), before delivering. This check applies under the same scope as the existing quiet-hours gate: no explicit `entity_id`/`recipient`, intent in `{send, insight}`, and priority not `high`.

#### Scenario: Active dnd signal suppresses an owner-default send
- **WHEN** `notify(channel="telegram", message="...")` is called with no `entity_id`/`recipient`
- **AND** `public.user_context` has an active `dnd` signal (not expired, not superseded)
- **AND** approvals_policy quiet hours do NOT already suppress
- **THEN** the notification is suppressed with `{"status": "suppressed_context_bus", "context_signal": "dnd"}`

#### Scenario: Context-bus check is skipped when quiet hours already suppressed
- **WHEN** approvals_policy quiet hours already suppress the notification
- **THEN** the context-bus signal is not queried (avoids a redundant DB round-trip on an already-decided path)

#### Scenario: priority="high" bypasses both quiet hours and the context bus
- **WHEN** `notify(priority="high", ...)` is called during active quiet hours AND an active `dnd` signal
- **THEN** the notification is delivered immediately; neither gate suppresses it

### Requirement: Priority Normalization for Ledger Comparability
`notify()`'s 3-level `priority` enum (`high`/`medium`/`low`) SHALL be normalized onto the same 1-100 `priority_score` scale the insight pipeline uses (RFC 0011 Priority Scoring Convention), so `public.attention_ledger` rows from both boundaries are comparable. `"high"` MUST normalize to a score at or above `URGENT_PRIORITY_THRESHOLD` (90).

#### Scenario: high/medium/low map to comparable scores
- **WHEN** a ledger row is recorded for `notify(priority="high")`, `notify(priority="medium")`, and `notify(priority="low")`
- **THEN** the recorded `priority_score` values are 90, 50, and 20 respectively, and `priority_label` preserves the original string
