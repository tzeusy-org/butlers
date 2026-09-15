# Telegram Receipt Acknowledgement

## Purpose

Defines the contract for acknowledging a Telegram owner message immediately on receipt — before
classification or routing completes — so the owner has some signal the system is working rather than
10-60 seconds of silence. Covers the configurable ack style (typing indicator, reaction, both, or
none), the timing bound and keepalive cadence for the typing indicator, terminal cleanup for both
ack styles, owner-only filtering, and the ownership boundary between the transport connector, the
Telegram module, and Switchboard's core ingest wiring. This spec is a prerequisite contract; no
code, configuration, or runtime described here exists yet.

## ADDED Requirements

### Requirement: Ack Style Configuration

`[modules.telegram.ack]` SHALL accept an optional `style` field with value `"typing"`,
`"reaction"`, `"both"`, or `"none"`. When absent, `style` defaults to `"reaction"`.

#### Scenario: Default preserves today's shipped behavior

- **WHEN** `[modules.telegram.ack]` is not present in a butler's configuration
- **THEN** the effective ack style is `"reaction"`, matching the unconditional reaction dispatch
  already shipped, so no currently deployed butler's behavior changes without an explicit
  configuration change

#### Scenario: Typing-only style

- **WHEN** `style = "typing"` is configured
- **THEN** an owner-originated message triggers only the typing-indicator keepalive (see Typing
  Indicator Timing Bound and Keepalive) — no reaction is set at any point in the message's lifecycle

#### Scenario: Both style

- **WHEN** `style = "both"` is configured
- **THEN** an owner-originated message triggers the typing-indicator keepalive and the reaction
  lifecycle concurrently, each following its own contract independently

#### Scenario: None style

- **WHEN** `style = "none"` is configured
- **THEN** no typing indicator and no reaction are dispatched for any message, regardless of sender

#### Scenario: Invalid style value

- **WHEN** `[modules.telegram.ack].style` is set to a value other than `typing`, `reaction`, `both`,
  or `none`
- **THEN** configuration validation SHALL reject it with a structured error naming the allowed
  values

### Requirement: Owner-Only Acknowledgement Filtering

Acknowledgement dispatch (typing indicator, reaction, or both) SHALL be gated on the message's
sender resolving to the owner, using a fast lookup independent of the full classification-time
identity resolution pipeline. A message whose sender does not resolve to the owner — including
system/automated triggers with no real Telegram sender, and a batched envelope with no owner
participant — SHALL receive no acknowledgement of any configured style.

#### Scenario: Owner message receives the configured ack

- **WHEN** an incoming `telegram_bot` message's sender resolves to the owner's registered Telegram
  identity via the fast pre-pipeline lookup
- **THEN** acknowledgement dispatch proceeds per the configured ack style

#### Scenario: Non-owner message receives no ack

- **WHEN** an incoming `telegram_bot` message's sender does not resolve to the owner (e.g. another
  participant in a shared or group chat)
- **THEN** no typing indicator and no reaction are dispatched for that message, regardless of the
  configured ack style

#### Scenario: System-originated trigger receives no ack

- **WHEN** a `telegram_bot`-channel processing request has no real Telegram sender identity to
  resolve (e.g. an internally synthesized buffer reference)
- **THEN** no acknowledgement of any configured style is dispatched

#### Scenario: Batched envelope with no owner participant

- **WHEN** a batched envelope's `sender.participants` contains no identity matching
  `sender.owner_sender_id` as an actual message author in that batch
- **THEN** no acknowledgement of any configured style is dispatched for that batch

#### Scenario: Owner-only gate does not wait on classification

- **WHEN** the owner-only gate evaluates a message
- **THEN** it uses a single fast, indexed lookup keyed on the raw sender channel identity (e.g.
  `identity.resolve_owner_channel_via_definer` or an equivalent pipeline-independent check) and
  SHALL NOT wait on or depend on `pipeline.process()`'s own classification-time identity resolution

### Requirement: Typing Indicator Timing Bound

When the configured ack style includes `typing`, the local dispatch of the typing-indicator
`sendChatAction` call SHALL be initiated within 1 second of the pre-processing lifecycle hook firing
(the same point in `_buffer_process`/the ingest background task where the existing in-progress
reaction is fired today, before `pipeline.process()` runs). This bounds only the local call
initiation; it is not a guarantee that Telegram renders or delivers the indicator to the owner
within that same second — network conditions, Telegram-side availability, and the connector's
existing rate-limit/backoff handling are outside this contract's control.

#### Scenario: Typing dispatch initiated promptly

- **WHEN** the pre-processing lifecycle hook fires for an owner-originated message with a
  typing-inclusive ack style configured
- **THEN** the `sendChatAction` HTTP call is initiated within 1 second of that hook firing

#### Scenario: Timing bound is a local dispatch attempt, not a delivery guarantee

- **WHEN** Telegram is slow to process, rate-limits, or otherwise delays rendering the dispatched
  typing indicator to the owner
- **THEN** this requirement is still satisfied, because it bounds only when the local call is
  initiated — a delivery or rendering guarantee is explicitly out of scope

### Requirement: Typing Indicator Keepalive

While an owner-originated message remains in-flight (between the pre-processing hook and the
terminal outcome) under a typing-inclusive ack style, the typing indicator SHALL be kept alive by
re-issuing `sendChatAction` at a 4-second cadence, grounded in the Telegram Bot API's documented
behavior that a chat action's status is set for 5 seconds or less.

#### Scenario: Keepalive re-issues before expiry

- **WHEN** an owner-originated message has been in-flight for 4 or more seconds under a
  typing-inclusive ack style
- **THEN** another `sendChatAction` call has already been issued for that chat, before Telegram's
  own documented ≤5-second expiry would otherwise clear the indicator

#### Scenario: Keepalive stops once the message reaches a terminal outcome

- **WHEN** the message's processing reaches success, failure, or a caught exception
- **THEN** no further `sendChatAction` calls are issued for that message's contribution to the
  chat's keepalive (see Per-Chat Typing Keepalive Reference Counting for the multi-message case)

### Requirement: Per-Chat Typing Keepalive Reference Counting

The typing-indicator keepalive SHALL be tracked per chat, not per message, with a reference count of
in-flight owner messages for that chat. The keepalive loop for a chat SHALL start only on the
transition from zero to one in-flight message and SHALL stop only on the transition back to zero.

#### Scenario: Second concurrent message in the same chat does not start a duplicate loop

- **WHEN** a second owner message arrives in a chat that already has one in-flight message under a
  typing-inclusive ack style
- **THEN** the existing keepalive loop for that chat continues unchanged — no second loop is started

#### Scenario: Keepalive continues until the last concurrent message finishes

- **WHEN** two owner messages are in flight in the same chat and the first reaches a terminal
  outcome while the second is still processing
- **THEN** the chat's keepalive loop continues (reference count is 1, not 0) and stops only when the
  second message also reaches a terminal outcome

### Requirement: Terminal Cleanup

Acknowledgement dispatch SHALL leave no acknowledgement state that persists indefinitely beyond the
message's terminal outcome, within the limits of what the Telegram Bot API exposes.

#### Scenario: Reaction and both styles replace the in-progress reaction on terminal outcome

- **WHEN** an owner message under a `reaction`- or `both`-inclusive ack style reaches a terminal
  outcome
- **THEN** the in-progress reaction is replaced by the terminal success or failure reaction (per the
  existing `react_for_ingest` mapping) — this satisfies "no orphaned in-progress signal" via
  replacement, not literal removal (see connector-telegram-bot's Lifecycle Reactions requirement and
  design.md D5 for why replacement is specified instead of removal)

#### Scenario: Typing and both styles stop dispatching on terminal outcome

- **WHEN** an owner message under a `typing`- or `both`-inclusive ack style reaches a terminal
  outcome and no other message keeps that chat's reference count above zero
- **THEN** no further `sendChatAction` calls are issued for that chat, and any residual client-side
  typing status clears via Telegram's own documented behavior (≤5-second expiry, or immediately once
  the butler's actual reply is sent into that chat) — the Bot API defines no explicit "clear typing"
  call, so this bounded, documented tail is the full extent of what this requirement can promise

#### Scenario: Cleanup runs on both success and failure

- **WHEN** `pipeline.process()` either completes successfully or raises an exception caught by the
  existing call-site handling
- **THEN** the terminal cleanup for the configured ack style runs in both cases — a failure does not
  leave the ack in its in-progress state

### Requirement: Ownership Boundary for Acknowledgement Dispatch

The transport connector (`connector-telegram-bot`) SHALL NOT send or track any acknowledgement.
`module-telegram` SHALL own the low-level Telegram API primitives for both ack styles, called
unconditionally by whatever invokes them. Switchboard's core ingest wiring (the existing
pre/post-`pipeline.process()` call sites) SHALL own reading the configured ack style, evaluating the
owner-only gate, and invoking the appropriate `module-telegram` primitives before and after
processing.

#### Scenario: Connector remains unaware of acknowledgement dispatch

- **WHEN** the Telegram bot connector processes an inbound update
- **THEN** it performs no acknowledgement dispatch and reads no ack-style configuration, consistent
  with its existing transport-only role

#### Scenario: Module primitives are unconditional

- **WHEN** `module-telegram`'s typing-indicator or reaction primitives are invoked
- **THEN** they perform the requested Telegram API action without independently checking ack-style
  configuration or owner identity — those decisions are made by the caller

#### Scenario: Core wiring makes the gating decisions

- **WHEN** Switchboard's core ingest wiring processes an owner-originated message
- **THEN** it is the single place that reads `[modules.telegram.ack].style`, evaluates the
  owner-only gate, and decides which `module-telegram` primitives to call before and after
  `pipeline.process()`

## Source References

- `about/heart-and-soul/vision.md` Non-Negotiable Rule 3 (MCP-only inter-butler communication):
  unaffected — this contract adds no inter-butler call, only a same-process call from Switchboard's
  core wiring into a loaded module instance, the same pattern the existing reaction lifecycle uses.
- No RFC governs this capability; see design.md D1 for why none is proposed.
