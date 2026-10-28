# Notify Confirm Interaction

## Purpose

Defines the generic wire contract behind `notify(intent="confirm")`: a caller-defined,
bounded-option question delivered to a resolved recipient, answered via a Telegram inline
keyboard (or rendered as plain text on non-interactive channels), and reported back to the
asking goal through verified goal admission, or to a registered deterministic domain resolver. This capability is deliberately
separate from, and carries none of, the approvals subsystem's authority (`module-approvals`,
RFC 0021): a confirm answer is information, never an approval decision. This spec is a
prerequisite contract; no code, migration, credential, or runtime described here exists yet.

## ADDED Requirements

### Requirement: Confirm Envelope Options and Timeout Bounds

ID: REQ-notify-confirm-interaction-001
Source: RFC 0038 D7/D13/D18; specify-telegram-confirm-interaction-contract/design.md
Scope: v1-mandatory

A `notify(intent="confirm")` call SHALL accept an `options` list of 2 to 4
`{value, label}` entries, an optional `timeout_seconds` bounded to `[30, 86400]` (default
600), and an optional `on_timeout_value` that MUST match one of `options[].value` when
supplied. `request_context` is OPTIONAL for this intent (unlike `reply`/`react`).

#### Scenario: Valid confirm envelope accepted

- **WHEN** `notify(intent="confirm", options=[{value:"yes",label:"Yes"},
  {value:"no",label:"No"}], message="Proceed?")` is called
- **THEN** the call is accepted and a new `pending_confirms` row is created with
  `status="pending"`, the two options, and `expires_at` 600 seconds from now (the default)

#### Scenario: Options list bounds are enforced

- **WHEN** `notify(intent="confirm", options=[...])` is called with fewer than 2 or more
  than 4 entries
- **THEN** the tool returns a structured validation error and no `pending_confirms` row is
  created

#### Scenario: Timeout bounds are enforced

- **WHEN** `notify(intent="confirm", ..., timeout_seconds=5)` or `timeout_seconds=200000`
  is called
- **THEN** the tool returns a structured validation error naming the allowed `[30, 86400]`
  range

#### Scenario: on_timeout_value must reference an offered option

- **WHEN** `notify(intent="confirm", options=[{value:"a",...},{value:"b",...}],
  on_timeout_value="c")` is called
- **THEN** the tool returns a structured validation error — `on_timeout_value` MUST be one
  of the supplied `options[].value`

#### Scenario: request_context is optional for confirm

- **WHEN** `notify(intent="confirm", options=[...], message="...")` is called with no
  `request_context`
- **THEN** the call proceeds normally — reply-to-origin routing (see the Reply-to-Origin
  Reentry requirement) uses an immutable server-held prompt/delivery reference established by the
  authenticated producer; caller text cannot supply goal identity

### Requirement: Non-Interactive Channel Fallback

ID: REQ-notify-confirm-interaction-002
Source: RFC 0038 D7/D13/D18; specify-telegram-confirm-interaction-contract/design.md
Scope: v1-mandatory

A confirm envelope delivered over a channel with no interactive tap affordance (e.g.
`email`) SHALL render as plain text listing each option's `label`, with no automatic
answer-capture mechanism. Such a delivery MUST NOT create a `pending_confirms` row that
any callback path can resolve.

#### Scenario: Email confirm renders as a plain-text prompt

- **WHEN** `notify(channel="email", intent="confirm", options=[{value:"a",label:"Option A"},
  {value:"b",label:"Option B"}], message="Which do you prefer?")` is called
- **THEN** the delivered email body includes the message and both option labels as plain
  text
- **AND** no `pending_confirms` row is created, and the response reports `status="ok"`
  with no resolvable `confirm_id`

### Requirement: Durable Confirm Record and Cross-Butler Storage

ID: REQ-notify-confirm-interaction-003
Source: RFC 0038 D7/D13/D18; specify-telegram-confirm-interaction-contract/design.md
Scope: v1-mandatory

Each accepted interactive confirm SHALL have one durable immutable prompt record keyed by
`confirm_id`, with authenticated producer identity, optional asking goal/effect and delivery
references, exact resolved recipient identity/channel, question, ordered options, expiry,
registered sink identity and opaque sink reference where applicable, and one terminal outcome.
Public storage is a coordination location, not authorization to read or write: only restricted
service roles may create/resolve records. Connectors MUST NOT receive direct mutation rights.
Model arguments MUST NOT select the sink, actor, goal, or recipient authority.

#### Scenario: Confirm row is queryable independent of the origin butler's own schema

- **WHEN** an authenticated connector submits a typed confirm callback
- **THEN** the confirm service resolves the durable record under its restricted role without
  reading the originating butler's schema; any domain mutation uses its registered service
  endpoint, never foreign-schema SQL

### Requirement: Domain-Separated Callback Token

ID: REQ-notify-confirm-interaction-004
Source: RFC 0038 D7/D13/D18; specify-telegram-confirm-interaction-contract/design.md
Scope: v1-mandatory

Resolution over an interactive channel SHALL use a callback token of the form
`cfm1:<confirm_id>:<opt_idx>:<hmac>`, where `opt_idx` is the option's position in the
envelope's `options` list and the HMAC is computed over a domain-tagged message
(`"cfm1|<confirm_id>|<opt_idx>|<recipient_chat_id>"`) so a `cfm1:` token can never verify as an
`apr1:` or `cgi:` token, or vice versa, even when the signing key is shared.

#### Scenario: Token fits Telegram's callback_data limit

- **WHEN** a `cfm1:` token is minted for any `confirm_id` (36-character UUID) and any
  `opt_idx` in `[0, 3]` (the 2-4 option bound)
- **THEN** the resulting `callback_data` string is at most 64 bytes

#### Scenario: Cross-domain token rejection

- **WHEN** a valid `apr1:` or `cgi:` token is presented to the confirm-resolution handler
  (or a valid `cfm1:` token is presented to the approvals or gap-interview handler)
- **THEN** the wrong-domain token is rejected as invalid — no cross-domain
  replay succeeds even under a shared signing key

### Requirement: Authenticated Callback Identity by Recipient Match

ID: REQ-notify-confirm-interaction-005
Source: RFC 0038 D7/D13/D18; specify-telegram-confirm-interaction-contract/design.md
Scope: v1-mandatory

The confirm-resolution handler SHALL authenticate a tap by comparing the tapping update's
authenticated sender and chat identity against the exact recipient binding the confirm was sent to
(recorded on the durable prompt at send time), with an exact sender check. A group chat
match alone is
insufficient identity proof. Owner status is required only for owner-targeted prompts.
HMAC verification (see Domain-Separated Callback Token) MUST pass before any database
lookup, as defense-in-depth against a forged or tampered token.

#### Scenario: Matching recipient chat resolves the confirm

- **WHEN** a `cfm1:` callback arrives from the exact sender and chat recorded as the
  recipient, whether the recipient is the owner or another resolved entity
- **THEN** the tap is authenticated and processing proceeds to the Atomic Replay-Fenced
  Resolution requirement

#### Scenario: Non-matching chat is ignored

- **WHEN** a `cfm1:` callback with a syntactically and cryptographically valid token
  arrives from a chat that does not match the confirm's recorded recipient chat
- **THEN** the callback is answered generically, the event is logged, and no state change
  occurs; a wrong sender in the correct group chat is refused identically

#### Scenario: Invalid or tampered token

- **WHEN** a `cfm1:`-shaped callback arrives whose HMAC fails to verify
- **THEN** the callback is answered generically, the failure is logged, and no state
  change occurs, and no `public.pending_confirms` row is read

### Requirement: Atomic Replay-Fenced Resolution

ID: REQ-notify-confirm-interaction-006
Source: RFC 0038 D7/D13/D18; specify-telegram-confirm-interaction-contract/design.md
Scope: v1-mandatory

Resolving a confirm SHALL include a single atomic conditional update
(`status='pending' -> status='answered'`, or the equivalent for expiry) that succeeds for
at most one caller per `confirm_id`. After acquiring the row lock, acceptance SHALL recheck
`expires_at > clock_timestamp()` using authoritative database time. An expired row still
marked pending cannot accept a choice even if the sweep has not run.
A resolution attempt against a row that is no longer
`pending` MUST NOT change the recorded choice or create another dispatch obligation.
Recovery may advance the existing application/rendering receipt under its own idempotent fence.

#### Scenario: First valid tap wins

- **WHEN** two authenticated, valid taps for the same `confirm_id` (e.g. a double-tap, or
  concurrent requests) race
- **THEN** exactly one atomically transitions the row to `answered` and records one durable
  answer-dispatch obligation
- **AND** the other observes zero rows affected and receives the "already handled" toast

#### Scenario: Tap on an already-resolved confirm

- **WHEN** a valid, authenticated tap arrives for a `confirm_id` whose row is already
  `answered` or `expired`
- **THEN** the callback is answered with a non-destructive "already handled" notice
  reflecting the row's current terminal state, the message is edited to show that state,
  and no duplicate dispatch obligation is created

#### Scenario: Late tap before the expiry sweep is refused

- **WHEN** a valid tap locks a still-pending prompt whose deadline has already passed
- **THEN** no choice is accepted or applied; the service records the single expiry outcome
  and its timeout obligation and returns an expired toast
- **AND** a later expiry sweep cannot create a second outcome or timeout obligation

### Requirement: Expiry Semantics and Timeout Reentry

ID: REQ-notify-confirm-interaction-007
Source: RFC 0038 D7/D13/D18; specify-telegram-confirm-interaction-contract/design.md
Scope: v1-mandatory

A `pending_confirms` row whose `expires_at` has passed with no successful resolution SHALL
transition to `status="expired"` exactly once (idempotent under repeated expiry-sweep
ticks) and SHALL record exactly one durable answer-dispatch obligation
carrying `on_timeout_value` when the caller supplied one, or an explicit no-answer outcome
when it did not.

#### Scenario: Expiry with a declared timeout value

- **WHEN** a confirm with `on_timeout_value="no"` reaches `expires_at` unanswered
- **THEN** the row transitions to `expired`, the Telegram message is edited to show an
  expired state, and the timeout dispatch carries `value="no"`

#### Scenario: Expiry with no declared timeout value

- **WHEN** a confirm with no `on_timeout_value` reaches `expires_at` unanswered
- **THEN** the timeout dispatch carries `{"outcome": "expired", "value": null}`
  rather than a fabricated option that was never offered

#### Scenario: Expiry sweep is idempotent

- **WHEN** an expiry-sweep tick observes a row it already transitioned to `expired` on a
  prior tick (e.g. after a scheduler restart)
- **THEN** it performs no further state change and triggers no duplicate dispatch

### Requirement: Reply-to-Origin Reentry via Verified Goal Admission

ID: REQ-notify-confirm-interaction-008
Source: RFC 0038 D7/D13/D18; specify-telegram-confirm-interaction-contract/design.md
Scope: v1-mandatory

A conversational answer SHALL enter `GoalAdmission.admit_submission` through a trusted
source reader, using only the immutable server-held delivery/prompt reference. It MUST NOT
use `ingest.v1`, `pinned_target`, proximity, or a model-supplied goal ID to join a goal.
An authenticated owner answer joins only a still-input-accepting, unexpired prompt whose
asking goal is joinable under adopted F3. A valid answer to a terminal/nonjoinable goal is
new owner intent, with inherited exposure, never a resurrection. Non-owner replies remain
third_party/exposed and cannot acquire owner origin or owner grants.

#### Scenario: Answered confirm reenters through verified admission

- **WHEN** an authenticated owner answers an active goal's unexpired accepting prompt
- **THEN** a durable source occurrence tied to the stored delivery reference enters goal
  admission once, with source-owned identity evidence and the immutable option value
- **AND** only admission determines whether to join; no model-selected routing is used

#### Scenario: Expired confirm reenters with the system sentinel

- **WHEN** an unanswered confirm expires
- **THEN** its durable timeout outcome is a system event with no owner-message evidence;
  it cannot impersonate the recipient or mint owner-origin authority

#### Scenario: Terminal goal receives new owner intent

- **WHEN** a valid owner answer references a prompt whose asking goal is terminal or no
  longer accepts input
- **THEN** admission applies F3 new-owner-goal behavior and preserves exposure, without
  reopening the prior goal

#### Scenario: Non-owner answer preserves exposure

- **WHEN** an authenticated non-owner recipient answers a prompt
- **THEN** any admission treats the answer as third_party/exposed; recipient verification
  never upgrades it to owner origin

### Requirement: Registered Deterministic Answer Sinks

ID: REQ-notify-confirm-interaction-009
Source: RFC 0038 D7/D13/D18; specify-telegram-confirm-interaction-contract/design.md
Scope: v1-mandatory

A service-plane producer MAY select a sink from a closed code-owned registry instead of
conversational reentry. Each registration binds one producer to a fixed authenticated
domain endpoint and validates the immutable sink reference and offered values. Models,
connector input and callback labels MUST NOT provide a sink name, URL or routing target.
A deterministic confirm need not create a reasoning goal. Unknown sinks fail closed.

The durable answer, sink application and delivery acknowledgments SHALL be distinguished.
Domain application is idempotent by `confirm_id` at the registered domain endpoint. A
crash or ambiguous RPC outcome MUST NOT create a second mutation; retry reconciles that
same key. A sink failure MUST NOT report application success. A first authenticated choice
is immutable even during application retry. Rendering failures never resend a prompt or
reapply an answer. Expiry records no domain choice unless explicitly configured.

#### Scenario: Domain sink applies a choice once

- **WHEN** a verified choice reaches the registered Chronicler gap-interview sink
- **THEN** the sink resolves that stored interview with the existing confirm/correct/dismiss
  semantics, once for the stable application key, and returns durable application status
- **AND** no goal is invented solely to apply the deterministic correction

#### Scenario: Sink failure remains recoverable

- **WHEN** sink application fails or its response is lost after accepting a choice
- **THEN** the service reports an application-pending/error state and retries or reconciles
  the same choice and key, including after restart; it cannot claim applied prematurely

#### Scenario: Arbitrary sink is refused

- **WHEN** a model, callback or unregistered producer supplies a sink or endpoint
- **THEN** the request is refused before any domain mutation

### Requirement: Separation from Approval Authority

ID: REQ-notify-confirm-interaction-010
Source: RFC 0038 D7/D13/D18; specify-telegram-confirm-interaction-contract/design.md
Scope: v1-mandatory

The confirm-resolution code path (tap handling and expiry-sweep handling alike) MUST NOT
call, invoke, or otherwise trigger the approvals decision surface (`approve_action`,
`reject_action`, any dashboard approve/reject route), the approved-action executor, or any
write to `pending_actions` or `approval_events`. A confirm's resolved `value` MUST NOT be
interpreted as approval, rejection, or any other pre-authorization for a gated tool call by
the normal goal runtime and broker evaluating any later effect independently.

#### Scenario: Confirm resolution never touches approvals state

- **WHEN** any confirm (answered or expired) resolves
- **THEN** no row in `pending_actions` or `approval_events` is created, read, or modified as
  part of that resolution
- **AND** no approvals executor invocation occurs as a direct effect of the resolution; registered sinks cannot mint grants or approve effects

#### Scenario: A confirm answer does not pre-authorize a later gated call

- **WHEN** the origin butler's session, having received a confirm answer via verified admission,
  subsequently calls a gated tool (e.g. `notify()` to a non-owner recipient)
- **THEN** that call is evaluated by the ordinary approval gate exactly as if no confirm had
  ever occurred — the confirm answer supplies no bypass, no implicit `_why`, and no standing
  authorization

### Requirement: Independent Resolution of Concurrent Confirms

ID: REQ-notify-confirm-interaction-011
Source: RFC 0038 D7/D13/D18; specify-telegram-confirm-interaction-contract/design.md
Scope: v1-mandatory

Multiple `pending_confirms` rows MAY be `status="pending"` simultaneously, including more
than one addressed to the same recipient chat. Resolution SHALL be keyed exclusively by the
`confirm_id` embedded in the tapped token; no shared "most recent confirm" state, cursor, or
single-outstanding-confirm assumption SHALL affect resolution.

#### Scenario: Two concurrent confirms in the same chat resolve independently

- **WHEN** confirm A and confirm B are both `status="pending"` in the same chat and the
  recipient taps confirm B's button
- **THEN** only confirm B's row transitions and only confirm B's dispatch is triggered
- **AND** confirm A remains `pending`, unaffected, until its own button is tapped or it
  expires on its own schedule

### Requirement: Strict Additivity to Existing Telegram Interaction Surfaces

ID: REQ-notify-confirm-interaction-012
Source: RFC 0038 D7/D13/D18; specify-telegram-confirm-interaction-contract/design.md
Scope: v1-mandatory

Every callback-handling, rendering, and validation addition this capability introduces
SHALL be a new, independently guarded code path. No existing `apr1:`/`cgi:` callback
handling, approval inline-keyboard rendering, or non-confirm `notify()` intent's validation
or delivery behavior SHALL change as a result of this capability's introduction.

#### Scenario: Non-confirm notify traffic is unaffected

- **WHEN** `notify()` is called with any intent other than `confirm` (`send`, `reply`,
  `react`, `insight`, or the target-state `draft`)
- **THEN** its validation, gating, and delivery behavior is byte-for-byte unchanged by this
  capability's introduction

#### Scenario: Existing callback prefixes are unaffected

- **WHEN** a `callback_query` arrives with an `apr1:` or `cgi:` prefixed `callback_data`
- **THEN** it is routed exactly per its own existing requirement, with no interaction with
  the `cfm1:` handling path

### Requirement: Shared Immutable Request Card Rendering

ID: REQ-notify-confirm-interaction-013
Source: RFC 0038 D7/D13/D18; specify-telegram-confirm-interaction-contract/design.md
Scope: v1-mandatory

Telegram and dashboard SHALL render the same persisted question, options and authoritative
server facts, with labelled assistant claims and escaped/inert quoted input. Rendering MUST
NOT select a recipient, alter stored prompt identity/options, select a sink or confer effect
authority. Dashboard answer submissions require owner authentication and the same resolver
and application-state read model as the Telegram path.

#### Scenario: Both surfaces preserve facts and inert quotes

- **WHEN** the stored card includes authoritative facts, an assistant claim and hostile quoted text
- **THEN** both surfaces preserve the same facts, distinguish the claim and render the quote inert
- **AND** a changed display label cannot change recipient, option identity, sink or authority

#### Scenario: Application pending is visible

- **WHEN** an answer is accepted but its registered sink has not confirmed application
- **THEN** both surfaces show application pending/error rather than claiming applied
