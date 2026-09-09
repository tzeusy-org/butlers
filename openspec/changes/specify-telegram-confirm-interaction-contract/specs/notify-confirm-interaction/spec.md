# Notify Confirm Interaction

## Purpose

Defines the generic wire contract behind `notify(intent="confirm")`: a caller-defined,
bounded-option question delivered to a resolved recipient, answered via a Telegram inline
keyboard (or rendered as plain text on non-interactive channels), and reported back to the
originating butler as an ordinary conversational turn. This capability is deliberately
separate from, and carries none of, the approvals subsystem's authority (`module-approvals`,
RFC 0021): a confirm answer is information, never an approval decision. This spec is a
prerequisite contract; no code, migration, credential, or runtime described here exists yet.

## ADDED Requirements

### Requirement: Confirm Envelope Options and Timeout Bounds

A `notify(intent="confirm")` call SHALL accept an `options` list of 2 to 8
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
  than 8 entries
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
  Reentry requirement) depends only on `origin_butler`, which every envelope already
  carries automatically

### Requirement: Non-Interactive Channel Fallback

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

Each accepted confirm SHALL be persisted as one row in `public.pending_confirms`
(cross-butler, per this repository's `public`-schema convention for state with no
per-butler privileged authority — see design.md D3), keyed by a generated `confirm_id`,
storing `origin_butler`, the resolved `channel`/`recipient`, the full `options` list, the
optional `on_timeout_value`, `status` (`pending`/`answered`/`expired`), and `expires_at`.

#### Scenario: Confirm row is queryable independent of the origin butler's own schema

- **WHEN** the `telegram_bot` connector or the confirm-resolution API route needs to
  resolve a `cfm1:` callback
- **THEN** it reads and writes `public.pending_confirms` directly, without needing to know
  or query the origin butler's own per-butler schema

### Requirement: Domain-Separated Callback Token

Resolution over an interactive channel SHALL use a callback token of the form
`cfm1:<confirm_id>:<opt_idx>:<hmac>`, where `opt_idx` is the option's position in the
envelope's `options` list and the HMAC is computed over a domain-tagged message
(`"cfm1|<confirm_id>|<opt_idx>|<requested_at>"`) so a `cfm1:` token can never verify as an
`apr1:` or `cgi:` token, or vice versa, even when the signing key is shared.

#### Scenario: Token fits Telegram's callback_data limit

- **WHEN** a `cfm1:` token is minted for any `confirm_id` (36-character UUID) and any
  `opt_idx` in `[0, 7]` (the 2-8 option bound)
- **THEN** the resulting `callback_data` string is at most 64 bytes

#### Scenario: Cross-domain token rejection

- **WHEN** a valid `apr1:` or `cgi:` token is presented to the confirm-resolution handler
  (or a valid `cfm1:` token is presented to the approvals or gap-interview handler)
- **THEN** HMAC verification fails and the token is rejected as invalid — no cross-domain
  replay succeeds even under a shared signing key

### Requirement: Authenticated Callback Identity by Recipient Match

The confirm-resolution handler SHALL authenticate a tap by comparing the tapping update's
originating chat identity against the exact recipient chat the confirm was sent to
(recorded on the `pending_confirms` row at send time), NOT by checking for owner status.
HMAC verification (see Domain-Separated Callback Token) MUST pass before any database
lookup, as defense-in-depth against a forged or tampered token.

#### Scenario: Matching recipient chat resolves the confirm

- **WHEN** a `cfm1:` callback arrives from the exact chat the confirm's envelope resolved
  as its recipient (whether that recipient is the system owner or another resolved entity)
- **THEN** the tap is authenticated and processing proceeds to the Atomic Replay-Fenced
  Resolution requirement

#### Scenario: Non-matching chat is ignored

- **WHEN** a `cfm1:` callback with a syntactically and cryptographically valid token
  arrives from a chat that does not match the confirm's recorded recipient chat
- **THEN** the callback is answered generically, the event is logged, and no state change
  occurs — this is authenticated identity, not merely "any owner-verified channel," because
  a confirm's recipient is not always the owner

#### Scenario: Invalid or tampered token

- **WHEN** a `cfm1:`-shaped callback arrives whose HMAC fails to verify
- **THEN** the callback is answered generically, the failure is logged, and no state
  change occurs, and no `public.pending_confirms` row is read

### Requirement: Atomic Replay-Fenced Resolution

Resolving a confirm SHALL be a single atomic conditional update
(`status='pending' -> status='answered'`, or the equivalent for expiry) that succeeds for
at most one caller per `confirm_id`. A resolution attempt against a row that is no longer
`pending` MUST NOT mutate state and MUST NOT re-trigger reply-to-origin reentry.

#### Scenario: First valid tap wins

- **WHEN** two authenticated, valid taps for the same `confirm_id` (e.g. a double-tap, or
  concurrent requests) race
- **THEN** exactly one atomically transitions the row to `answered` and triggers reentry
- **AND** the other observes zero rows affected and receives the "already handled" toast

#### Scenario: Tap on an already-resolved confirm

- **WHEN** a valid, authenticated tap arrives for a `confirm_id` whose row is already
  `answered` or `expired`
- **THEN** the callback is answered with a non-destructive "already handled" notice
  reflecting the row's current terminal state, the message is edited to show that state,
  and no further reentry is triggered

### Requirement: Expiry Semantics and Timeout Reentry

A `pending_confirms` row whose `expires_at` has passed with no successful resolution SHALL
transition to `status="expired"` exactly once (idempotent under repeated expiry-sweep
ticks) and SHALL trigger exactly one reply-to-origin reentry (see Reply-to-Origin Reentry)
carrying `on_timeout_value` when the caller supplied one, or an explicit no-answer outcome
when it did not.

#### Scenario: Expiry with a declared timeout value

- **WHEN** a confirm with `on_timeout_value="no"` reaches `expires_at` unanswered
- **THEN** the row transitions to `expired`, the Telegram message is edited to show an
  expired state, and the reentry to the origin butler carries `value="no"`

#### Scenario: Expiry with no declared timeout value

- **WHEN** a confirm with no `on_timeout_value` reaches `expires_at` unanswered
- **THEN** the reentry to the origin butler carries `{"outcome": "expired", "value": null}`
  rather than a fabricated option that was never offered

#### Scenario: Expiry sweep is idempotent

- **WHEN** an expiry-sweep tick observes a row it already transitioned to `expired` on a
  prior tick (e.g. after a scheduler restart)
- **THEN** it performs no further state change and triggers no duplicate reentry

### Requirement: Reply-to-Origin Reentry via Deterministic Pinned Routing

A resolved (answered or expired) confirm SHALL be delivered back to the calling butler as
one ordinary `ingest.v1` envelope with `control.pinned_target` set to that confirm's
`origin_butler`, reusing the existing `IngestControlV1.pinned_target` field
(`connector-base-spec`) rather than a new session-resume mechanism. `sender.identity` on an
answered confirm SHALL be the authenticated recipient's own channel identity; on an expired
confirm it SHALL be a fixed `"system:confirm-timeout"` sentinel, never the recipient's own
identity.

#### Scenario: Answered confirm reenters as a normal pinned message

- **WHEN** a confirm is answered by its authenticated recipient
- **THEN** exactly one `ingest.v1` envelope is submitted with `control.pinned_target` equal
  to the confirm's `origin_butler`, `sender.identity` equal to the recipient's channel
  identity, and `payload.normalized_text` naming the chosen option's `label`
- **AND** the origin butler receives this precisely as it would any other inbound message
  pinned to it — no new tool-call authority accompanies it

#### Scenario: Expired confirm reenters with the system sentinel

- **WHEN** a confirm expires unanswered
- **THEN** the reentry envelope's `sender.identity` is `"system:confirm-timeout"`, never the
  resolved recipient's own identity, so the origin butler can distinguish "no one answered"
  from "the recipient said X"

### Requirement: Separation from Approval Authority

The confirm-resolution code path (tap handling and expiry-sweep handling alike) MUST NOT
call, invoke, or otherwise trigger the approvals decision surface (`approve_action`,
`reject_action`, any dashboard approve/reject route), the approved-action executor, or any
write to `pending_actions` or `approval_events`. A confirm's resolved `value` MUST NOT be
interpreted as approval, rejection, or any other pre-authorization for a gated tool call by
any component other than the origin butler's own subsequently spawned session applying its
normal reasoning and the ordinary approval gate to whatever it decides to do next.

#### Scenario: Confirm resolution never touches approvals state

- **WHEN** any confirm (answered or expired) resolves
- **THEN** no row in `pending_actions` or `approval_events` is created, read, or modified as
  part of that resolution
- **AND** no approvals executor invocation occurs as a direct effect of the resolution

#### Scenario: A confirm answer does not pre-authorize a later gated call

- **WHEN** the origin butler's session, having received a confirm answer via reentry,
  subsequently calls a gated tool (e.g. `notify()` to a non-owner recipient)
- **THEN** that call is evaluated by the ordinary approval gate exactly as if no confirm had
  ever occurred — the confirm answer supplies no bypass, no implicit `_why`, and no standing
  authorization

### Requirement: Independent Resolution of Concurrent Confirms

Multiple `pending_confirms` rows MAY be `status="pending"` simultaneously, including more
than one addressed to the same recipient chat. Resolution SHALL be keyed exclusively by the
`confirm_id` embedded in the tapped token; no shared "most recent confirm" state, cursor, or
single-outstanding-confirm assumption SHALL affect resolution.

#### Scenario: Two concurrent confirms in the same chat resolve independently

- **WHEN** confirm A and confirm B are both `status="pending"` in the same chat and the
  recipient taps confirm B's button
- **THEN** only confirm B's row transitions and only confirm B's reentry is triggered
- **AND** confirm A remains `pending`, unaffected, until its own button is tapped or it
  expires on its own schedule

### Requirement: Strict Additivity to Existing Telegram Interaction Surfaces

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
