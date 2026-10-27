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
presentation. The evidence key SHALL be derived by the server from the durable
operation identity (the decision, acknowledgement, or verified receipt record);
a caller SHALL NOT supply it. Provider send acceptance, page reads/prefetch/
polling, unrelated owner activity, system expiry, connector traffic, and
caller-supplied actor or evidence-key fields SHALL NOT acknowledge an action.
A rejected submission SHALL record only a reason from the closed set
`stale_evidence`, `wrong_action_or_generation`, `non_owner`,
`caller_asserted_actor`, `unbound_receipt`.

ID: REQ-approval-attention-reminders-001
Source: RFC-0035
Scope: v1-mandatory

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
- **AND** a safe rejection reason from the closed set is recorded without
  persisting raw provider or identity data

#### Scenario: Evidence identity cannot be chosen by the caller

- **WHEN** a submission carries its own evidence key or idempotency token
- **THEN** the key is ignored and the server-derived operation identity decides
  uniqueness
- **AND** varying a caller token cannot create a second record or reopen an
  acknowledged episode

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

The system SHALL derive owner reachability from evidence younger than 24
hours (age strictly below 24 hours, measured on database time; an event exactly
24 hours old is stale, matching the homecoming gap rule) over a complete reachability observation inventory. That inventory SHALL
contain each current `telegram` or `email` channel present in the Switchboard
trusted-recovery notify registry with an enabled Messenger adapter and one
unambiguous active owner reachability fact. It SHALL ignore owner preference
and whether an attention episode already used the channel. In evaluation order,
the system SHALL report `reachable` for fresh qualifying owner activity;
`degraded` for no fresh activity plus a fresh provider confirmation on an
inventory channel; `unreachable` only when the inventory is non-empty and every
member has a fresh definitive no-effect/unavailable result with no fresh
confirmation or ambiguity; and `unknown` otherwise. Reachability SHALL be
observational and SHALL NOT itself authorize a reminder.

ID: REQ-approval-attention-reminders-002
Source: RFC-0035
Scope: v1-mandatory

#### Scenario: Owner-authenticated activity is reachable

- **WHEN** an accepted owner-resolved ingress, successful owner authentication
  ceremony, or successful authenticated owner mutation is younger than 24
  hours on database time
- **THEN** reachability is `reachable`
- **AND** background reads, polling, probes, connectors, schedules, and
  unresolved/non-owner ingress do not satisfy the rule

#### Scenario: Confirmed delivery without activity is degraded

- **WHEN** no qualifying owner activity is fresh and a channel in the
  reachability observation inventory has a provider-confirmed presentation
  younger than 24 hours, even if that episode already used the channel
- **THEN** reachability is `degraded`, not `reachable` or `unreachable`
- **AND** that label does not imply acknowledgement or disengagement

#### Scenario: All channels definitively unavailable is unreachable

- **WHEN** no qualifying activity is fresh and a non-empty reachability
  observation inventory has fresh definitive pre-provider/no-effect
  unavailable evidence for every member
- **THEN** reachability is `unreachable` only if no inventory channel has fresh
  confirmed or ambiguous evidence

#### Scenario: Missing, stale, or ambiguous evidence is unknown

- **WHEN** the reachability observation inventory or required evidence is
  incomplete, 24 hours old or older, contradictory, or includes an unresolved
  ambiguous provider effect without a higher-priority fresh confirmation
- **THEN** reachability is `unknown`
- **AND** passage of time never converts unknown evidence into unreachable

#### Scenario: The 24-hour boundary is single-valued

- **WHEN** the newest qualifying owner activity is exactly 24 hours old
- **THEN** it is not fresh for reachability and a following qualifying activity
  starts a new presence epoch
- **AND** the two rules never disagree about the same instant

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
resolved only at egress from the owner's own active reachability fact, SHALL
NOT be stored in policy state, and SHALL NOT be accepted from a caller, a
stored action argument, or any non-owner identity. Egress SHALL re-verify
eligibility and fail with a no-effect result, never fall back to another
channel, when the channel or fact is no longer eligible; a supplied non-owner
destination is a hard no-effect refusal and is never retried. The first four
predicates SHALL form the reachability observation inventory. For a direct
reminder or reminder digest member, the unused-channel predicate SHALL be
bound to that action's current attention episode. For a homecoming summary,
the unused-channel predicate SHALL instead be bound to the singleton owner-
presence epoch that owns the summary, not to any member action's attention
episode. Prior original or reminder channel use by a member SHALL NOT exclude
that member from the homecoming summary. These episode-local predicates SHALL
NOT filter observational reachability evidence. All four inventory predicates,
egress re-verification, ambiguity exclusions, and the fixed channel order SHALL
still apply to the summary.

ID: REQ-approval-attention-reminders-003
Source: RFC-0035
Scope: v1-mandatory

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

#### Scenario: Egress re-verifies the destination and recipient

- **WHEN** a channel or the owner's reachability fact changes between admission
  and provider handoff, or a destination other than the owner's own fact is
  supplied
- **THEN** egress fails with a pre-provider no-effect result on the same
  presentation key and selects no other channel or recipient
- **AND** a channel or fact that is no longer eligible leaves the episode in
  `safe_hold(no_eligible_reminder_channel)`, `channel_inventory_unavailable`
  being reserved for an inventory that cannot be read completely
- **AND** a supplied non-owner destination is refused outright and is not retried

#### Scenario: Admission snapshot is replay-stable

- **WHEN** concurrent schedulers select a channel for the same reminder ordinal
- **THEN** one ordered eligibility snapshot and one selected channel commit
- **AND** the loser replays that result without creating a second presentation

#### Scenario: Exhausted action channels do not reopen reminder authority

- **WHEN** an action's original Telegram presentation and Email reminder were
  confirmed, the action is safely held, and a qualifying owner return creates
  a new presence epoch
- **THEN** a homecoming summary may include that action using a channel that
  satisfies all four inventory predicates and is unused by that presence epoch
- **AND** the action's attention episode, used-channel evidence, and spent
  reminder ordinals remain unchanged
- **AND** repeated activity or an uncertain summary outcome cannot create a
  second summary key or select another channel for the same epoch

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
Scope: v1-mandatory

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

#### Scenario: Late reconciliation does not restore reminder authority

- **WHEN** an ambiguous presentation is later reconciled as confirmed or as
  no-effect
- **THEN** the same-key result is recorded and the episode keeps its
  `safe_hold(ambiguous_delivery)` with no reminder re-anchored on the
  reconciled time
- **AND** a later authenticated defer successor starts a new episode only
  through its own confirmed handoff

#### Scenario: Admitted ordinal stays spent when cancelled before handoff

- **WHEN** an admitted reminder is cancelled before provider handoff by
  acknowledgement, decision, expiry, or goal Stop
- **THEN** its ordinal remains spent and no replacement ordinal is created
- **AND** a later defer episode inherits only the unspent ordinals

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
burst shape. Every digest creator and joining member SHALL be compatible with
the digest channel under that action's locked reminder-eligibility snapshot. A
release at or after expiry SHALL create no handoff. Any action status other
than `pending` (decision, expiry, abandonment, or the `cancelled` status a goal
Stop gives a parked effect under RFC 0038) SHALL fence every unstarted
reminder. An approval collapsed into a burst after that burst's digest was
emitted starts its reminder clock at the digest's confirmed delivery, with the
same two-reminder limit.

ID: REQ-approval-attention-reminders-005
Source: RFC-0035
Scope: v1-mandatory

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
- **AND** an expired/rejected/approved/executed/abandoned/cancelled action
  cannot become pending or sendable again

#### Scenario: Quiet-hours release is exact and not re-gated

- **WHEN** a due reminder falls inside configured quiet hours
- **THEN** its first eligible instant is the exact configured end of that
  end-exclusive window
- **AND** a later policy edit neither advances nor delays the stored release

#### Scenario: Concurrent due reminders preserve burst control

- **WHEN** more than three approval attention presentations become due inside
  one schema's ten-minute window
- **THEN** the first three are direct, the fourth creates one cohort-owned
  digest on its action's eligible channel, and a later reminder joins only when
  that digest channel is eligible and unused for the joining action
- **AND** each admitted membership consumes its action's ordinal without
  another direct send

#### Scenario: Incompatible action cannot join an existing digest

- **WHEN** a later due reminder encounters the window's digest but that digest
  channel was already used in its attention episode or lacks a current active
  owner reachability fact at that member's admission
- **THEN** the episode enters
  `safe_hold(burst_digest_channel_incompatible)` without recording membership
  or consuming the reminder ordinal
- **AND** no direct presentation, alternate-channel send, or second digest is
  created; an incomplete inventory read instead uses
  `safe_hold(channel_inventory_unavailable)`

#### Scenario: Ambiguous digest blocks all member successors

- **WHEN** a reminder cohort digest has an unresolved ambiguous provider effect
- **THEN** every admitted member is blocked from a fresh direct or digest key
  for that episode
- **AND** decision/expiry may still remove a member without replaying the
  digest

#### Scenario: Late-collapsed approval is still reminded

- **WHEN** an approval is collapsed into a burst after that burst's digest was
  emitted
- **THEN** its reminder clock starts at that digest's confirmed delivery
- **AND** it receives at most two reminders

### Requirement: Safe hold does not become ActionStatus

The approval attention episode SHALL support monotonic `safe_hold` with exactly
this closed reason vocabulary: `reminder_budget_exhausted`,
`no_eligible_reminder_channel`, `ack_evidence_unavailable`,
`channel_inventory_unavailable`, `burst_digest_channel_incompatible`,
`ambiguous_delivery`, `expiry_fence`. A new reason requires an adopted
amendment. When every allowlisted channel (REQ-003's first three predicates: `telegram` or `email`, in the registry, adapter enabled) has been used by the episode, or both
ordinals are spent, the episode SHALL enter safe hold at that confirmation
rather than waiting for the next due instant. `safe_hold` SHALL NOT be added to `ActionStatus` and
SHALL NOT approve, reject, expire, execute, abandon, defer, extend, or revive
an action. A later acknowledgement MAY improve the read projection to
acknowledged but SHALL NOT restore automatic send authority.

ID: REQ-approval-attention-reminders-006
Source: RFC-0035
Scope: v1-mandatory

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

#### Scenario: Exhausted channels hold at once, not at the next slot

- **WHEN** a reminder is confirmed and every allowlisted channel has now been
  used by the episode
- **THEN** the episode enters `safe_hold(no_eligible_reminder_channel)` at that
  confirmation, so a returning owner's homecoming summary includes the action
- **AND** a transient lack of a reachability fact, rather than a used channel,
  does not hold early and is judged at the due instant

#### Scenario: Ambiguous delivery holds attention only

- **WHEN** any presentation of the action has an unresolved ambiguous effect
- **THEN** the episode enters `safe_hold(ambiguous_delivery)` and only
  same-key reconciliation continues
- **AND** the action stays `pending` and decidable by the owner

#### Scenario: Owner sees the hold truthfully

- **WHEN** the authenticated owner opens a pending action's detail
- **THEN** the dashboard shows the episode state, the closed hold reason as a
  fixed human label, remaining unspent ordinals, reachability state with its
  evidence time, and ambiguity when present
- **AND** no raw provider data, destination, or callback material is shown

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
activity follows at least 24 hours (on database time) without qualifying activity, serialized
under one singleton record. It SHALL admit at most one homecoming summary per
epoch. The summary SHALL contain no more than 20 pending safe-held actions and
safe-held actions expired within 7 days, plus an overflow count; ambiguous
actions SHALL be excluded from outbound content. Expired entries SHALL be
non-actionable. The summary's channel-use identity SHALL be its owner-presence
epoch as defined in REQ-approval-attention-reminders-003. It SHALL NOT be a new
action attention episode or a restored reminder ordinal. A provider-started
summary SHALL retain its selected channel and key through recovery; ambiguous
effects SHALL permit only same-key reconciliation.

ID: REQ-approval-attention-reminders-007
Source: RFC-0035
Scope: v1-mandatory

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

- **WHEN** any presentation of an action (not only its latest) has an
  unresolved ambiguous effect
- **THEN** it remains visible as ambiguous on the authenticated dashboard but
  is absent from outbound homecoming content, the overflow count, and any
  outbound disclosure of how many items are held back
- **AND** homecoming cannot mint a fresh key for it

#### Scenario: No eligible rows sends nothing

- **WHEN** a new presence epoch starts and no pending safe-held or recently
  expired safe-held action qualifies
- **THEN** no summary is admitted for that epoch
- **AND** the epoch is not retried and later activity in it cannot create one

#### Scenario: Overflow is counted and linked

- **WHEN** more than 20 qualifying actions exist
- **THEN** the summary lists 20 and states the number of further qualifying
  rows with one dashboard link to the full list
- **AND** the count excludes ambiguous actions

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
Scope: v1-mandatory

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

#### Scenario: Silence and reachability never adapt policy

- **WHEN** approval acknowledgement rate, safe holds, or reachability labels
  accumulate over time
- **THEN** reminder count, timing, channels, and the proactive-insight
  auto-off ratchet are unchanged by them
- **AND** owner absence never lowers any engagement ratio through approval
  traffic

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
Scope: v1-mandatory

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

### Requirement: Content-blind attention state and reminder content

Attention episodes, acknowledgement records, reminder ordinals, presence
epochs, hold records, logs, metrics, and audit payloads SHALL store only
identifiers, closed enumerations, generations, ordinals, channel names,
timestamps, and bounded counts (the homecoming overflow count and aggregate
metric values). They SHALL NOT store tool names, arguments, lineage content,
destinations, callback material, or raw provider payloads. A reminder or
homecoming presentation SHALL be a fixed content-blind template that identifies
itself as a reminder, states the pending-since and expiry times, and links to
the authenticated dashboard detail. It SHALL NOT include tool arguments or a
recipient, and it SHALL NOT carry approve, reject, or defer controls.

ID: REQ-approval-attention-reminders-010
Source: RFC-0035
Scope: v1-mandatory

#### Scenario: Reminder on a different channel discloses no action content

- **WHEN** a reminder is presented on email after the original used Telegram
- **THEN** it contains only the fixed template, times, and dashboard link
- **AND** the owner must open the authenticated dashboard to see the action

#### Scenario: Stored attention fields are an allowlist

- **WHEN** any attention table, log line, metric label, or audit payload is
  written
- **THEN** it holds only allowlisted identifiers, enumerations, and timestamps
- **AND** a contract guard rejects any other field

### Requirement: Attention policy confers no authority and is not model-reachable

Acknowledgement, reminder admission, safe hold, reachability derivation, and
homecoming SHALL run only as deterministic daemon infrastructure and through
owner-authenticated surfaces that use the approvals module's existing owner
authentication. They SHALL NOT be registered in any goal-visible capability
catalog, SHALL NOT be grantable to a model session, goal, or child invocation,
and SHALL NOT be callable with a broker assertion. A model session SHALL NOT be
able to acknowledge, read attention state, or change a reminder. No attention
operation SHALL decide, edit, execute, or cancel an action.

ID: REQ-approval-attention-reminders-011
Source: RFC-0035
Scope: v1-mandatory

#### Scenario: Model session cannot acknowledge

- **WHEN** a goal or session attempts to acknowledge attention, read episode
  state, or alter a reminder through any tool
- **THEN** no such tool exists in its catalog and no grant can add one
- **AND** the owner-authenticated surface remains the only acknowledgement path

#### Scenario: No scheduler invokes a model

- **WHEN** the reminder scheduler or homecoming projector runs
- **THEN** it spawns no model session and calls no model function
- **AND** its decisions derive only from durable state and database time

### Requirement: Approval-keyed identity under the goal runtime

Every attention identity SHALL be keyed by the approval (`pending_actions`)
identifier and its presentation generation, never by session, goal, invocation,
or effect identifier. An action whose parked effect carries RFC 0038 goal
lineage, a `legacy` goal origin, or no session lineage SHALL behave identically
for acknowledgement, reminders, safe hold, and homecoming. A fresh owner
approval required to replay an `unknown` effect is a new approval with its own
episode and lifetime ordinals. Lineage MAY be joined by approval id for the
owner surface only and SHALL NOT be copied into attention state.

ID: REQ-approval-attention-reminders-012
Source: RFC-0035
Scope: v1-mandatory

#### Scenario: Session-less and lineage-bearing approvals behave the same

- **WHEN** one pending action has no session lineage and another is a parked
  goal effect
- **THEN** both are keyed and bounded identically by approval id
- **AND** no policy path reads a session, goal, or invocation id

#### Scenario: Stopped goal fences reminders

- **WHEN** owner Stop cancels a parked effect while a reminder is admitted but
  unstarted
- **THEN** the reminder is cancelled before handoff and its ordinal stays spent
- **AND** the cancelled action is never described as actionable in a reminder
  or homecoming summary

#### Scenario: Fresh approval does not inherit a spent budget silently

- **WHEN** an unknown effect requires a fresh owner approval
- **THEN** the new pending action starts its own episode and ordinals
- **AND** the per-schema burst window still bounds how many presentations the
  owner receives
