# Proactive Insight Engine — Priority-Urgent Bypass + Attention Ledger

## ADDED Requirements

### Requirement: Priority-Urgent Bypass of Quiet Hours and the Context Bus
Neither the hour-based quiet-hours check (`public.insight_settings`) nor a context-bus `dnd`/`sleeping` signal SHALL suppress a candidate whose `priority` is at or above `URGENT_PRIORITY_THRESHOLD` (90 — RFC 0011's "time-critical" floor). When at least one such candidate is pending during what would otherwise be a fully-suppressed cycle, the delivery cycle proceeds for urgent candidates only; candidates below the threshold remain `status='pending'`, untouched, for a later non-suppressed cycle.

#### Scenario: Urgent candidate delivered during quiet hours, routine candidate untouched
- **WHEN** the delivery cycle runs during active quiet hours
- **AND** one pending candidate has `priority=95` and another has `priority=70`
- **THEN** the `priority=95` candidate is delivered (or included in a digest)
- **AND** the `priority=70` candidate's status remains `'pending'` — it is neither delivered nor marked `filtered`/`expired` by this cycle

#### Scenario: Fully suppressed cycle when no candidate is urgent
- **WHEN** the delivery cycle runs during active quiet hours (or an active context-bus `dnd`/`sleeping` signal)
- **AND** every pending candidate has `priority < 90`
- **THEN** the cycle returns `skipped=True` and delivers nothing, exactly as before this change
- **AND** one `public.attention_ledger` row is written with `outcome="suppressed"` and the triggering `reason`

#### Scenario: Expiry runs regardless of suppression
- **WHEN** the delivery cycle would otherwise be fully suppressed (quiet hours or context bus, no urgent candidate)
- **THEN** the expiry step (marking `expires_at`-past candidates as `expired`) still runs unconditionally before the suppression check

### Requirement: Context-Bus Gating of the Delivery Cycle
The delivery cycle SHALL consult the situational context bus (`public.user_context`) for an active `dnd` or `sleeping` signal, deterministically, as an additional suppression input alongside the existing hour-based quiet-hours check defined in `public.insight_settings`. This closes RFC 0011's Integration note that context-bus consultation was "optional, deferred to a follow-up."

#### Scenario: dnd signal suppresses when no quiet hours are configured
- **WHEN** `public.insight_settings.quiet_start`/`quiet_end` are NULL (quiet hours not configured or not active)
- **AND** `public.user_context` has an active `dnd` signal
- **AND** no pending candidate is priority>=90
- **THEN** the cycle is suppressed exactly as if quiet hours were active, with `reason="context_bus:dnd"`

### Requirement: Seeded Owner-Level Quiet Hours
`public.insight_settings` SHALL be seeded with a sane owner-level quiet-hours default (23:00-08:00 Asia/Singapore) on migration, applied only when the row is currently unconfigured (`quiet_start`, `quiet_end`, and `quiet_timezone` all NULL). An owner who has already configured quiet hours before this migration runs MUST NOT have their configuration overwritten.

#### Scenario: Fresh install gets seeded defaults
- **WHEN** a database has never had `insight_settings.quiet_start`/`quiet_end`/`quiet_timezone` configured
- **AND** the `core_160` migration runs
- **THEN** `quiet_start=23`, `quiet_end=8`, `quiet_timezone='Asia/Singapore'`

#### Scenario: Existing configuration is preserved
- **WHEN** `insight_settings` already has a non-NULL `quiet_start`/`quiet_end`/`quiet_timezone` (owner-configured)
- **AND** the seed migration (or a re-run of its guarded UPDATE) executes
- **THEN** the existing values are unchanged

### Requirement: Attention Ledger Recording of Delivered/Coalesced Candidates
Every candidate the delivery cycle successfully delivers SHALL be recorded to `public.attention_ledger`. A single-candidate delivery is recorded with `outcome="delivered"`; when multiple candidates are folded into one digest message (`deliver_count > 1`), each candidate in the digest is recorded with `outcome="coalesced"` — distinguishing "sent alone" from "sent as part of a composed batch" for later dashboard/audit use.

#### Scenario: Standalone delivery recorded as delivered
- **WHEN** the delivery cycle selects exactly one candidate and delivers it
- **THEN** one `public.attention_ledger` row is written with `outcome="delivered"`, `dedup_key` set to the candidate's `dedup_key`, and `notification_ref` set to the candidate's id

#### Scenario: Digest delivery recorded as coalesced, one row per candidate
- **WHEN** the delivery cycle selects 3 candidates and delivers them as one digest message
- **THEN** 3 `public.attention_ledger` rows are written, each with `outcome="coalesced"` and its own candidate's `dedup_key`/id

#### Scenario: Failed delivery is not recorded to the ledger
- **WHEN** the `notify_fn` call for a selected candidate fails
- **THEN** no `public.attention_ledger` row is written for that cycle's delivery attempt — the existing `delivery_attempt_count`/3-strikes `filtered` mechanism (unchanged by this requirement) remains the record of the failure
