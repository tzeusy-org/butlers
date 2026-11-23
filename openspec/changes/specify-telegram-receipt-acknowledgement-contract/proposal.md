# Specify Telegram receipt acknowledgement controls

## Why

An owner who starts a goal from Telegram needs a prompt sign that Butlers accepted it. The
original draft in #4092 specified that signal on the old classify-and-route lifecycle. The
RFC 0038 collision disposition directs this contract to goal admission and the actual owner
reply. The connector continues to carry the message and its reply target without taking on
receipt or delivery responsibility.

## What Changes

- Specify the initial eyes reaction and typing at the committed goal admission-to-lead handoff.
  The control plane selects one four-value acknowledgement style and Messenger owns egress.
- Require an authenticated `telegram_bot` message from the live owner and independently
  verify that the destination chat belongs uniquely to the same owner before acting.
- Keep the one-second local typing initiation bound. The user-visible p95 target and temporary
  model drafts belong to M7 under signed bu-mq929j.
- Preserve the existing four-second typing cadence, per-chat active-owner-goal reference counts
  and terminal cleanup for the later lifecycle package. The observed reply must actually reach
  the owner before a success reaction. Preserve every progress and release criterion on
  bu-z0b0ah.4.
- Propose a first-receipt foundation under bu-z0b0ah.9 with fake Messenger evidence. The runtime
  default stays off, the retired routing hooks stay untouched, and production routing or Bot
  API use requires the existing separate authority.

## Capabilities

### New Capabilities

- `telegram-receipt-acknowledgement` defines first admission, owner eligibility, initial style and
  timing, plus the remaining acknowledgement lifecycle.

### Modified Capabilities

- `module-telegram` provides owner-gated typing through Messenger custody and keeps
  acknowledgement style independent of credential configuration.
- `connector-telegram-bot` records the downstream ownership boundary and its transport-only
  responsibility for the verified original reply target.

## Impact

The future foundation's scope is the trusted goal admission-to-lead boundary and an injected
Messenger transport with synthetic owner evidence. Its tests cover durable duplicates,
concurrent launch ordering, disabled and denied inputs, bounded transport refusal, and
content-free telemetry. Remaining keepalive, progress, retirement, restart, and switch proofs
remain recorded on the parent outcome. This proposal changes only the existing OpenSpec
artifacts. It neither implements a runtime change nor grants production, credential,
external delivery or switch authority.

The proposal requires independent semantic review of its exact head and separate owner
approval naming that reviewed artifact before implementation. This is the same adoption
gate recorded in #4092. The release record `bu-enbrs2` retains operating authority for the
switch and its cleanup. This proposal makes no RFC amendment.

Tests: +0 ~0 -0
