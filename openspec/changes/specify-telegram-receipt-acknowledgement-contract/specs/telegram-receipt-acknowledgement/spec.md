# Telegram Receipt Acknowledgement

## Purpose

Give the owner a prompt first signal when their Telegram bot message starts a goal. The
acknowledgement belongs to goal admission and the goal reply. The transport connector continues
normalizing updates and has no role in deciding or sending receipts.

## ADDED Requirements

### Requirement: Ack Style Configuration

The goal runtime SHALL consume one owner-channel acknowledgement style with value `typing`,
`reaction`, `both`, or `none`. The default SHALL be `reaction`. This is the
`owner_channel_ack` policy named by bu-z0b0ah.4, retaining the original draft's four style
values and default. The dark first-receipt foundation SHALL use the existing trusted
construction seam without adding or changing a deployed roster setting.

ID: REQ-telegram-receipt-acknowledgement-001
Source: bu-z0b0ah.4 acceptance 7; original receipt draft #4092
Scope: v1-mandatory

#### Scenario: Default requests the first reaction

- **WHEN** the trusted constructor supplies no acknowledgement style for an eligible admission
- **THEN** the effective style is `reaction` and Messenger is asked for the eyes reaction only

#### Scenario: Typing-only style

- **WHEN** the style is `typing` for an eligible admission
- **THEN** Messenger is asked for initial typing only

#### Scenario: Both style

- **WHEN** the style is `both` for an eligible admission
- **THEN** Messenger is asked for the eyes reaction and initial typing

#### Scenario: None style

- **WHEN** the style is `none`
- **THEN** no acknowledgement request is emitted, including for eligible admissions

#### Scenario: Invalid style value

- **WHEN** trusted construction supplies a value outside the four supported styles
- **THEN** it is rejected with a structured error naming the allowed values

### Requirement: Owner-Only Acknowledgement Filtering

The control plane SHALL consider only a new durable goal admitted from the authenticated
`telegram_bot` message of the live owner. The eligible source binding SHALL come from
server-held accepted-ingress evidence verified during admission under RFC 0038 D5. Messenger
SHALL independently resolve the destination chat to the same unique live owner before any
provider action under RFC 0017. An owner sender, matching display name, or primary-route marker
alone SHALL confer no authority to act in a destination chat.

ID: REQ-telegram-receipt-acknowledgement-002
Source: RFC 0038 D5 and D7; RFC 0017 section 2.7; bu-z0b0ah.4 acceptance 5 and 8
Scope: v1-mandatory

#### Scenario: Owner message receives the configured acknowledgement

- **WHEN** a registered Telegram bot contributes a verified owner-originated message, entry
  into a new goal commits, and Messenger resolves the retained reply destination to the same
  unique live owner
- **THEN** the configured first acknowledgement can be attempted against that original target

#### Scenario: Non-owner or unresolved target receives no acknowledgement

- **WHEN** the sender is unverified or is another party, the message identifies a shared chat,
  or the chat binding is missing, ambiguous, changed, or unavailable at dispatch
- **THEN** Messenger applies no typing or reaction and creates no approval request

#### Scenario: System and other channels receive no acknowledgement

- **WHEN** admission originates with a schedule, email, dashboard, Telegram user client,
  system action, or any source other than an authenticated owner Telegram bot message
- **THEN** no Telegram acknowledgement is emitted

#### Scenario: Batched envelope with no owner participant

- **WHEN** a batched envelope carries no verified owner-originated Telegram bot message with an
  exact admitted sender and reply destination
- **THEN** no acknowledgement is emitted

#### Scenario: The owner-only gate uses verified source facts

- **WHEN** envelope or message text asserts the owner role or supplies a reply target
  inconsistent with the authenticated source binding
- **THEN** the assertion cannot make the message or destination eligible

### Requirement: First Acknowledgement at Goal Admission

With the goal-runtime switch enabled, the control plane SHALL initiate the configured first
acknowledgement for an eligible newly committed admission before launching its lead. The
admission transaction SHALL finish before calling Messenger. Duplicate attempts or source
replays SHALL NOT initiate another acknowledgement or overtake the original admission's
bounded handoff on the ordinary launch path. An interruption MAY lose the acknowledgement.
Recovery of an admitted goal SHALL remain possible through the existing supervisor without
replaying a possibly delivered signal. A provider wait SHALL NOT outlive the bounded
handoff. Under the `both` style, each selected action may be attempted once and an
uncertain reaction does not authorize a second reaction.

ID: REQ-telegram-receipt-acknowledgement-003
Source: RFC 0038 D1, D5, D7, D14, and D17; bu-z0b0ah.4 acceptance 1, 8, 9, and 10
Scope: v1-mandatory

#### Scenario: First acknowledgement precedes the lead

- **WHEN** trusted admission commits a new eligible goal and the enabled runtime handles its
  result
- **THEN** the configured local Messenger request is initiated before the runner can launch the
  lead, without waiting indefinitely for a provider response

#### Scenario: A concurrent duplicate cannot acknowledge or start first

- **WHEN** attempts for the same durable Telegram source return the same decision, including a
  replay concurrent with the first result
- **THEN** the initial request is attempted at most once and a duplicate does not start a lead
  ahead of the first admission's bounded handoff

#### Scenario: Joined, refused, and disabled paths emit nothing

- **WHEN** an input is joined to an existing goal or refused, or the goal-runtime switch is off
- **THEN** there is no acknowledgement intent or provider action

#### Scenario: Acknowledgement failure does not block launch

- **WHEN** Messenger refuses or the first acknowledgement misses its deadline or has an
  unknown transport outcome
- **THEN** the lead is still eligible to run and the uncertain acknowledgement is not repeated

#### Scenario: Recovery makes no speculative acknowledgement

- **WHEN** the daemon restarts after admission commit and before the initial acknowledgement or
  lead launch can be confirmed
- **THEN** the supervisor can recover the admitted goal without replaying first acknowledgement

#### Scenario: Acknowledgement telemetry is content-free

- **WHEN** the control plane emits a receipt span for the goal
- **THEN** the goal ID is its only data attribute and it carries no message, sender, chat,
  credential, provider payload or transport exception detail

### Requirement: Typing Indicator Timing Bound

For an eligible admission using a typing-inclusive style, Messenger SHALL initiate the local
`sendChatAction` request within one second of the post-commit prelaunch hook. This bounds the
local attempt only. It carries no Telegram render or delivery guarantee. The separate
user-visible latency target belongs to M7 under bu-mq929j.

ID: REQ-telegram-receipt-acknowledgement-004
Source: bu-z0b0ah.4 acceptance 1; original receipt draft #4092; signed bu-mq929j
Scope: v1-mandatory

#### Scenario: Typing dispatch initiated promptly

- **WHEN** the post-commit prelaunch hook fires for an eligible typing-inclusive admission
- **THEN** a fake clock at the Messenger transport boundary observes the call initiated within
  one second of the hook

#### Scenario: Timing bound applies to local initiation

- **WHEN** Telegram delays rendering after an on-time local attempt
- **THEN** the measurement remains the time of local initiation and makes no claim about rendering

### Requirement: Typing Indicator Keepalive

The later lifecycle work in bu-z0b0ah.4 SHALL refresh typing for an eligible in-flight goal
every four seconds, before Telegram's documented expiry of up to five seconds. The dark
first-receipt foundation SHALL request initial typing without starting a recurring task.

ID: REQ-telegram-receipt-acknowledgement-005
Source: RFC 0038 D11 and D14; bu-z0b0ah.4 acceptance 2
Scope: v1-mandatory

#### Scenario: Keepalive re-issues before expiry

- **WHEN** eligible work remains in flight for four seconds under a typing-inclusive style
- **THEN** the lifecycle service refreshes that chat's typing, subject to the owner gate

#### Scenario: Keepalive stops once the goal ends

- **WHEN** reply delivery settles, a goal fails, the owner stops it, or its lease lapses
- **THEN** the goal contributes no further typing refreshes

### Requirement: Per-Chat Typing Keepalive Reference Counting

The lifecycle service SHALL maintain one keepalive per eligible chat, counting active goal
holds. Recurring work starts when the first hold is acquired and stops when the last is
released. It SHALL recheck live owner eligibility before provider actions and release lost or
expired holds without creating a retry on admission.

ID: REQ-telegram-receipt-acknowledgement-006
Source: RFC 0038 D14; bu-z0b0ah.4 acceptance 2 and 6
Scope: v1-mandatory

#### Scenario: Second concurrent goal in the same chat does not stack loops

- **WHEN** two eligible typing-inclusive goals overlap in a chat
- **THEN** the second hold adds no additional keepalive loop

#### Scenario: Keepalive continues until the last concurrent goal ends

- **WHEN** one eligible goal releases its hold and another still holds the same chat
- **THEN** the keepalive continues until the last hold is released

### Requirement: Terminal Cleanup

Lifecycle cleanup SHALL follow the settled goal outcome and release the goal's typing hold
when reply delivery, terminal failure, owner Stop, or lease lapse ends its contribution.
Success requires actual owner reply delivery under RFC 0038 D11. The first-receipt foundation
SHALL NOT advance a reply or goal terminal marker.

ID: REQ-telegram-receipt-acknowledgement-007
Source: RFC 0038 D11 and D14; bu-z0b0ah.4 acceptance 4 and 6
Scope: v1-mandatory

#### Scenario: Reply delivery replaces eyes with a success reaction

- **WHEN** the actual owner reply is delivered for a reaction-inclusive goal and the destination
  still resolves uniquely to the same owner
- **THEN** the eyes reaction is replaced by thumbs-up and the later lifecycle closes any progress
  message as specified by bu-z0b0ah.4

#### Scenario: Failure replaces eyes with a failure reaction

- **WHEN** a reaction-inclusive goal fails or settles partial and its chat still qualifies
- **THEN** the eyes reaction is replaced by the alien reaction and the later lifecycle uses the
  failure status in any progress message

#### Scenario: Typing and both styles stop dispatching when the final hold ends

- **WHEN** the last goal for a chat releases its typing hold
- **THEN** no further refresh is issued and Telegram clears residual typing by its ordinary
  expiration or receipt of the actual reply

#### Scenario: Stop or restart leaves no orphaned loop

- **WHEN** the goal is stopped, the lease lapses, or Messenger or the control plane restarts
- **THEN** stale typing work cannot continue and the later lifecycle reconciles any persisted
  progress message as required by bu-z0b0ah.4

### Requirement: Ownership Boundary for Acknowledgement Dispatch

The transport connector SHALL continue accepting and normalizing Telegram updates. The goal
runtime SHALL own admission-stage orchestration and style selection. Messenger SHALL own
authorized acknowledgement egress using the Telegram module and SHALL revalidate the destination
before sending. The Telegram module SHALL apply only owner-eligible typing and reactions under
that boundary. The legacy classify-and-route lifecycle remains frozen under the existing
switch. The transport and any enabled route require the separate authority recorded on the
release outcome.

ID: REQ-telegram-receipt-acknowledgement-008
Source: RFC 0038 D1 and D7; bu-z0b0ah.4 acceptance 9 and 11
Scope: v1-mandatory

#### Scenario: Connector remains unaware of acknowledgement dispatch

- **WHEN** the Telegram bot connector processes an inbound update
- **THEN** it sends no acknowledgement, tracks no receipt state, and reads no style policy

#### Scenario: Messenger permits only the verified owner destination

- **WHEN** Messenger receives a typed first acknowledgement request with a stored reply target
- **THEN** it verifies that transport destination before requesting Telegram module actions

#### Scenario: The control plane makes the orchestration decision

- **WHEN** verified admission of a new owner Telegram bot goal commits
- **THEN** the control plane selects the configured prelaunch action and Messenger owns delivery

## Source References

- RFC 0038 D1, D5, D7, D11, D14 and D17.
- RFC 0017 section 2.7 and the active `generalist-lead-focused-delegation` Telegram module delta.
- The Telegram receipt row of `generalist-lead-focused-delegation/collision-dispositions.md`.
- bu-z0b0ah.4 and its dark first-receipt prerequisite bu-z0b0ah.9.
- Signed bu-mq929j and the separate M6 switch release bu-enbrs2.
