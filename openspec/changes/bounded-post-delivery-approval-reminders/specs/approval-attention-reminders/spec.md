# Approval Attention Reminders

## Purpose

Defines the proposed successor policy for action-specific acknowledgement,
bounded post-delivery reminders, reachability truth, safe hold, and homecoming
after RFC 0023 has provider-confirmed an approval presentation. This is a
planning contract only. Nothing in this spec is implemented or activated by
the draft.

## ADDED Requirements

### Requirement: Action-specific owner acknowledgement evidence

The system SHALL persist append-only acknowledgement evidence bound to the
approval action and presentation generation. It SHALL derive the owner actor
from the authenticated server boundary and SHALL accept only a successful
authenticated approve, reject, defer, explicit attention acknowledgement, or a
provider-native verified-owner read receipt whose adapter binds it to the exact
presentation. Provider send acceptance, page reads/prefetch/polling, unrelated
owner activity, system expiry, connector traffic, and caller-supplied actor
fields SHALL NOT acknowledge an action.

ID: REQ-approval-attention-reminders-001
Source: RFC-0035
Scope: proposed

#### Scenario: Explicit acknowledgement closes attention without deciding

- **WHEN** the authenticated owner explicitly acknowledges the current
  presentation of a still-pending action
- **THEN** one action/generation-bound acknowledgement is recorded and the
  attention episode becomes `acknowledged`
- **AND** action status, arguments, expiry, decision provenance, and execution
  state remain unchanged

#### Scenario: Decision or defer is qualifying evidence

- **WHEN** an authenticated approve, reject, or defer succeeds for the action
- **THEN** its server-derived actor and action/generation identity qualify as
  acknowledgement evidence
- **AND** defer acknowledges only the old generation; a later confirmed defer
  successor starts a new episode using only the action's unspent lifetime
  reminder ordinals

#### Scenario: Provider delivery is not acknowledgement

- **WHEN** Telegram or email confirms provider acceptance for a presentation
- **THEN** the presentation may become `delivered` but no owner
  acknowledgement is recorded
- **AND** no current Telegram/email read receipt is promoted to acknowledgement

#### Scenario: Stale, unrelated, or caller-asserted evidence is rejected

- **WHEN** evidence predates its presentation, names another action/generation,
  comes from a non-owner, or supplies an actor in caller-controlled data
- **THEN** it has no acknowledgement or scheduling effect
- **AND** a safe rejection reason is recorded without persisting raw provider
  or identity data

#### Scenario: Acknowledgement replay is idempotent

- **WHEN** the same semantic acknowledgement is submitted more than once
- **THEN** the first durable record is returned and no duplicate record,
  reminder, or action transition is created

#### Scenario: Missing acknowledgement evidence fails closed

- **WHEN** the scheduler cannot complete its acknowledgement-evidence read
- **THEN** the episode enters `safe_hold` with
  `ack_evidence_unavailable`
- **AND** absence is not inferred and no provider call is attempted

### Requirement: Fresh reachability truth

The system SHALL derive owner reachability from evidence no older than 24
hours. It SHALL report `reachable` for fresh qualifying owner activity;
`degraded` for no fresh activity plus a fresh eligible-channel provider
confirmation; `unreachable` only when the eligible-channel set is non-empty and
every member has a fresh definitive no-effect/unavailable result with no fresh
confirmation or ambiguity; and `unknown` otherwise. Reachability SHALL be
observational and SHALL NOT itself authorize a reminder.

ID: REQ-approval-attention-reminders-002
Source: RFC-0035
Scope: proposed

#### Scenario: Owner-authenticated activity is reachable

- **WHEN** an accepted owner-resolved ingress, successful owner authentication
  ceremony, or successful authenticated owner mutation occurred within 24
  hours
- **THEN** reachability is `reachable`
- **AND** background reads, polling, probes, connectors, schedules, and
  unresolved/non-owner ingress do not satisfy the rule

#### Scenario: Confirmed delivery without activity is degraded

- **WHEN** no qualifying owner activity is fresh and an eligible channel has a
  provider-confirmed presentation within 24 hours
- **THEN** reachability is `degraded`, not `reachable` or `unreachable`
- **AND** that label does not imply acknowledgement or disengagement

#### Scenario: All channels definitively unavailable is unreachable

- **WHEN** no qualifying activity is fresh and a non-empty currently eligible
  channel set has fresh definitive pre-provider/no-effect unavailable evidence
  for every member
- **THEN** reachability is `unreachable` only if no eligible channel has fresh
  confirmed or ambiguous evidence

#### Scenario: Missing, stale, or ambiguous evidence is unknown

- **WHEN** required inventory/evidence is incomplete, older than 24 hours,
  contradictory, or includes an unresolved ambiguous provider effect
- **THEN** reachability is `unknown`
- **AND** passage of time never converts unknown evidence into unreachable

#### Scenario: Concurrent evidence derives one state

- **WHEN** owner activity and a channel outcome commit concurrently
- **THEN** derivation from their durable commit times yields one state under a
  consistent database snapshot
- **AND** replay does not create another activity or channel event

### Requirement: Closed registered-channel eligibility and order

An automatic reminder channel SHALL be eligible only when it is `telegram` or
`email`, is present in the Switchboard trusted-recovery notify registry, has an
enabled Messenger adapter, has one unambiguous active owner reachability fact,
and is unused by any provider-started or confirmed presentation in the current
episode. Eligible unused channels SHALL be ordered by eligible owner
`prefers-channel`, then `telegram`, then `email`. Private destinations SHALL be
resolved only at egress and SHALL NOT be stored in policy state.

ID: REQ-approval-attention-reminders-003
Source: RFC-0035
Scope: proposed

#### Scenario: Preferred registered channel is selected first

- **WHEN** the owner prefers an eligible unused channel from
  `{telegram,email}`
- **THEN** that channel is selected before the fixed fallback order
- **AND** only its channel name, not its destination, is persisted

#### Scenario: Unsupported preference is skipped

- **WHEN** the owner prefers WhatsApp, Discord, an unregistered future channel,
  or a channel without an active owner reachability fact
- **THEN** the preference is skipped and the next eligible unused channel is
  considered
- **AND** no adapter is provisioned, invoked, or treated as registered

#### Scenario: Initial channel cannot be repeated

- **WHEN** the confirmed owner-requested presentation used Telegram
- **THEN** Telegram is ineligible for every automatic reminder in that episode
- **AND** email is the only possible current reminder channel if otherwise
  eligible

#### Scenario: Inventory failure creates no send

- **WHEN** registry, adapter, preference, or owner-reachability inventory cannot
  be read completely
- **THEN** the episode enters `safe_hold(channel_inventory_unavailable)`
- **AND** no fallback destination or unapproved channel is invented

#### Scenario: Admission snapshot is replay-stable

- **WHEN** concurrent schedulers select a channel for the same reminder ordinal
- **THEN** one ordered eligibility snapshot and one selected channel commit
- **AND** the loser replays that result without creating a second presentation

### Requirement: Bounded reminder admission separate from recovery

An action SHALL have at most two automatic reminder ordinals over its lifetime.
For each attention episode, candidate ordinals are due at 4 hours and 24 hours
after its owner-requested presentation's confirmed handoff. A reminder SHALL be
admitted only while the action is pending and unexpired, acknowledgement
evidence is complete and empty, no earlier presentation is ambiguous or still
recovering, the ordinal is unused, and a distinct eligible channel exists.
RFC 0023 recovery SHALL keep the same key and channel and SHALL NOT consume a
reminder ordinal.

ID: REQ-approval-attention-reminders-004
Source: RFC-0035
Scope: proposed

#### Scenario: First bounded reminder succeeds

- **WHEN** the +4-hour slot is due for a pending, unexpired action with a
  confirmed anchor, complete no-ack evidence, no unresolved presentation, an
  unused ordinal, and an eligible unused channel
- **THEN** exactly one next presentation generation and reminder ordinal are
  committed on that channel
- **AND** provider handoff proceeds only through RFC 0023's trusted path

#### Scenario: Lifetime count never exceeds two

- **WHEN** any number of scheduler runs, restarts, defer episodes, or safe
  recovery attempts occur for the same action
- **THEN** at most reminder ordinals 1 and 2 can exist over its lifetime
- **AND** an ordinal collapsed into a digest counts toward that bound

#### Scenario: Exact timing is not recovery backoff

- **WHEN** an owner-requested presentation is confirmed at time `T`
- **THEN** the policy due instants are exactly `T+4h` and `T+24h`
- **AND** RFC 0023's 15-second/exponential/15-minute recovery values do not
  alter those instants or establish reachability freshness

#### Scenario: Safe retry keeps one key and channel

- **WHEN** an admitted reminder fails safely before provider effect
- **THEN** RFC 0023 retries the same presentation key on the same selected
  channel under its existing bounded backoff
- **AND** no new ordinal, channel, or presentation key is created

#### Scenario: Ambiguous effect blocks every fresh key

- **WHEN** any action or reminder presentation has an unresolved ambiguous
  provider effect
- **THEN** only reconciliation of that same presentation key is allowed
- **AND** no later reminder, channel switch, homecoming membership, or fresh
  presentation key may be created for the action

#### Scenario: Reminder replay is idempotent

- **WHEN** the same due ordinal is evaluated after restart or by concurrent
  workers
- **THEN** uniqueness on `(action_id, reminder_ordinal)` returns the durable
  presentation/membership
- **AND** no duplicate provider handoff is admitted

#### Scenario: Acknowledgement race is fenced

- **WHEN** acknowledgement and reminder admission contend
- **THEN** acknowledgement-first creates no reminder; admission-first may be
  cancelled before handoff; handoff-start-first may finish only that attempt
- **AND** acknowledgement blocks every later ordinal without retracting a
  provider call already started

### Requirement: Expiry, quiet-hours, and burst preservation

Automatic reminder policy SHALL never write `pending_actions.expires_at` or any
action decision field. It SHALL use RFC 0021's exact end-exclusive quiet-hours
release and the same per-schema ten-minute first-three/one-digest/later-collapse
burst shape. A release at or after expiry SHALL create no handoff. Terminal
decision or expiry SHALL fence every unstarted reminder.

ID: REQ-approval-attention-reminders-005
Source: RFC-0035
Scope: proposed

#### Scenario: Original expiry is a hard fence

- **WHEN** a reminder due time or stored quiet-hours release is at or after the
  action's canonical expiry
- **THEN** no reminder is admitted or handed off and the episode records
  `safe_hold(expiry_fence)`
- **AND** the action expires only through the existing canonical expiry writer

#### Scenario: Reminder never extends or revives

- **WHEN** a reminder is admitted, delivered, retried, cancelled, or held
- **THEN** action expiry, status, decision, arguments, and execution state are
  unchanged
- **AND** an expired/rejected/approved/executed/abandoned action cannot become
  pending or sendable again

#### Scenario: Quiet-hours release is exact and not re-gated

- **WHEN** a due reminder falls inside configured quiet hours
- **THEN** its first eligible instant is the exact configured end of that
  end-exclusive window
- **AND** a later policy edit neither advances nor delays the stored release

#### Scenario: Concurrent due reminders preserve burst control

- **WHEN** more than three approval attention presentations become due inside
  one schema's ten-minute window
- **THEN** the first three are direct, the fourth creates one cohort-owned
  digest, and later reminders join that digest as collapsed memberships
- **AND** each membership consumes its action's ordinal without another direct
  send

#### Scenario: Ambiguous digest blocks all member successors

- **WHEN** a reminder cohort digest has an unresolved ambiguous provider effect
- **THEN** every member is blocked from a fresh direct or digest key for that
  episode
- **AND** decision/expiry may still remove a member without replaying the
  digest

### Requirement: Safe hold does not become ActionStatus

The approval attention episode SHALL support monotonic `safe_hold` with a
closed reason vocabulary. `safe_hold` SHALL NOT be added to `ActionStatus` and
SHALL NOT approve, reject, expire, execute, abandon, defer, extend, or revive
an action. A later acknowledgement MAY improve the read projection to
acknowledged but SHALL NOT restore automatic send authority.

ID: REQ-approval-attention-reminders-006
Source: RFC-0035
Scope: proposed

#### Scenario: Reminder budget exhaustion holds attention only

- **WHEN** the action has spent both reminder ordinals without qualifying
  acknowledgement
- **THEN** its attention episode enters
  `safe_hold(reminder_budget_exhausted)`
- **AND** a still-unexpired action remains `pending` until the owner or canonical
  expiry path acts

#### Scenario: No eligible channel holds without mutation

- **WHEN** a due episode has no distinct eligible unused channel
- **THEN** it enters `safe_hold(no_eligible_reminder_channel)`
- **AND** no action field, provider, or unregistered channel is touched

#### Scenario: Safe-hold replay is monotonic

- **WHEN** the same hold condition is observed repeatedly or concurrently
- **THEN** one durable reason/transition is retained and no reminder authority
  returns
- **AND** a late acknowledgement may be shown without deleting hold history

#### Scenario: Expiry after safe hold remains canonical

- **WHEN** a pending safe-held action reaches its expiry
- **THEN** the existing expiry operation transitions it to `expired`
- **AND** attention policy neither performs that transition nor creates a
  replacement action

### Requirement: Bounded homecoming summary

The system SHALL create an owner-presence epoch only when qualifying owner
activity follows at least 24 hours without qualifying activity, serialized
under one singleton record. It SHALL admit at most one homecoming summary per
epoch. The summary SHALL contain no more than 20 pending safe-held actions and
safe-held actions expired within 7 days, plus an overflow count; ambiguous
actions SHALL be excluded from outbound content. Expired entries SHALL be
non-actionable.

ID: REQ-approval-attention-reminders-007
Source: RFC-0035
Scope: proposed

#### Scenario: Returning owner receives one bounded summary

- **WHEN** qualifying owner activity starts a new presence epoch and eligible
  safe-held actions exist
- **THEN** one summary keyed to that epoch is admitted with pending rows first,
  then expired rows, capped at 20 plus overflow
- **AND** it carries dashboard links only, no raw arguments, destinations,
  callback material, or direct action controls

#### Scenario: Expired entry cannot act

- **WHEN** an action expired within the 7-day homecoming window
- **THEN** it may appear only as historical, explicitly non-actionable context
- **AND** no callback, approval verb, revival, or replacement action is created

#### Scenario: Ambiguous action is excluded

- **WHEN** an action's latest presentation has an unresolved ambiguous effect
- **THEN** it remains visible as ambiguous on the authenticated dashboard but
  is absent from outbound homecoming content
- **AND** homecoming cannot mint a fresh key for it

#### Scenario: Concurrent activity deduplicates one epoch

- **WHEN** multiple qualifying activity events arrive concurrently after the
  24-hour gap
- **THEN** the singleton lock creates one epoch and uniqueness admits one
  summary key
- **AND** later activity/restart replays that epoch rather than sending again

#### Scenario: Homecoming honors channel, quiet hours, and burst

- **WHEN** a summary is admitted
- **THEN** it uses the activity channel only if eligible, otherwise the
  preferred/Telegram/email order, stores the exact quiet-hours release, and
  counts as one burst-window presentation
- **AND** an ambiguous handoff is reconciled by the same key without resend

### Requirement: Disengagement denominator isolation

Approval delivery, reminders, safe holds, and homecoming summaries SHALL NOT
insert `insight_engagement`, increment `attention_daily_rollup` insight counts,
mark an insight engaged, or change the proactive-insight 14-day auto-off
denominator. Approval-specific acknowledgement analytics, if exposed, SHALL
use a separate denominator of provider-confirmed direct/reminder membership
with complete evidence while the action was pending and SHALL have no action or
insight-policy authority.

ID: REQ-approval-attention-reminders-008
Source: RFC-0035
Scope: proposed

#### Scenario: Approval delivery does not become an insight opportunity

- **WHEN** an initial approval, reminder, digest membership, or homecoming
  summary is confirmed
- **THEN** no proactive-insight engagement row or rollup insight count is
  written
- **AND** approval silence cannot lower insight engagement through a new
  denominator row

#### Scenario: Owner acknowledgement does not mark insights engaged

- **WHEN** the owner acknowledges or decides an approval
- **THEN** only approval attention evidence is updated
- **AND** unrelated insight rows remain unchanged unless the existing
  owner-ingress writer independently qualifies

#### Scenario: Approval-specific denominator excludes uncertain evidence

- **WHEN** an approval acknowledgement rate is calculated
- **THEN** ambiguous, safe-retry, unconfirmed collapsed membership,
  homecoming, and already-expired observations are excluded
- **AND** only confirmed eligible membership with a complete evidence read can
  enter the denominator

#### Scenario: Reachability changes preserve historical evidence

- **WHEN** current reachability moves among reachable, degraded, unreachable,
  and unknown
- **THEN** historic insight and approval records are neither deleted nor
  rewritten
- **AND** changing the insight denominator for unreachable periods requires a
  separate adopted change

### Requirement: No authority before exact adoption and activation

The RFC 0035/OpenSpec successor SHALL remain planning-only until independent
security and product review pass and the owner adopts the exact reviewed
artifact. Adoption SHALL authorize neither implementation nor activation.
Implementation, migration, deployment, rollout configuration, credentials,
provider access, notification sends, and live action effects SHALL remain
separate gates.

ID: REQ-approval-attention-reminders-009
Source: RFC-0035
Scope: proposed

#### Scenario: Draft merge has no runtime effect

- **WHEN** this specification draft is committed or merged
- **THEN** no worker, migration, provider, notification, action transition, or
  runtime configuration changes
- **AND** Direction B and prior RFC 0023 authority are not broadened

#### Scenario: Review is not adoption

- **WHEN** independent security/product review passes an exact commit
- **THEN** the artifact remains unadopted until the owner explicitly adopts
  that exact reviewed version
- **AND** any semantic edit invalidates the prior review/adoption target

#### Scenario: Adoption is not activation

- **WHEN** the owner adopts the exact successor
- **THEN** future implementation may be planned under its own authority
- **AND** migration, rollout, deployment, provider use, and live sends remain
  disabled until separately authorized and verified
