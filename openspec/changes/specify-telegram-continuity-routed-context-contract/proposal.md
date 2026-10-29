# Specify Telegram conversation continuity at goal admission

## Why

The original draft coupled Telegram sticky target routing with bounded target-side
history. RFC 0038 D1/D2 removes classification, target affinity and route dispatch:
Switchboard admits reasoning ingress as goals, and domains expose capabilities
rather than starting reasoning sessions. The adopted collision disposition for
this change therefore retires its sticky-target, TTL-reclassification and misroute
re-dispatch behavior while carrying conversation continuity into goal admission.

This amends the existing packet before archive under `bu-u5euwb.16`. It implements
no runtime behavior and makes no RFC decision. Canonical-conforming text needs no
owner exact-text sign-off; independent review and separately authorized future
implementation, live access, switch release and archive remain distinct.

## What Changes

- Conform `telegram-continuity-routed-context` to goal-admission conversation
  continuity. Retire the affinity HIT/MISS machinery, the Telegram 24-hour affinity
  TTL, TTL-triggered classification, target fallback, conflict-driven reclassification
  and correction re-pinning. No prior domain choice selects a target or grants access.
- Preserve deterministic recent history: at most the most recent 30 messages in the
  most recent 15-minute window of the same `external_conversation_id`. Conversation
  continuity is goal input/context, never a routed domain prompt or authority to join
  a goal merely because a message came from the same chat.
- Follow the already-adopted identity split. Telegram uses
  `event.external_conversation_id` (`telegram:<chat_id>`, with the forum-topic suffix
  only for `is_topic_message`) separately from `event.reply_target_ref`.
  `event.external_thread_id` is retired. History must not select by reply target or
  `source_thread_identity`; there is no new wire field or identity table.
- Preserve the untrusted-data banner and per-message code fences, sender provenance
  and per-item stamps. Third-party or tainted history exposes its reader; no history
  text, prior pin or successful admission authenticates a sender or enlarges a grant.
  Private-custody content stays unreachable. Context is not a new direct database or
  cross-domain access path.
- Preserve connector/ingress idempotency and the canonical request reference. Duplicate
  accepted envelopes return the existing admission/goal rather than producing a second
  goal, routing decision, correction dispatch or domain session.
- Replace this packet's competing `butler-switchboard` MODIFIED Misroute Correction
  Re-dispatch block with the legitimate REMOVED disposition already adopted by
  generalist. The entire tool contract retires, including expired-event fallback,
  target-registry dispatch, replacement-session IDs, correction metadata and Telegram
  re-pinning. Historical outcomes remain accounted for in the preservation record;
  they are not unsafe positive target-state obligations.

## Capabilities

### New Capabilities

- `telegram-continuity-routed-context`: bounded, conversation-keyed goal continuity,
  its trust/identity/idempotency boundaries, and explicit retirement of routing affinity.

### Modified Capabilities

- `butler-switchboard`: remove Misroute Correction Re-dispatch in agreement with
  generalist's D1 disposition. Wrong goal outcomes use the existing new-goal/Stop
  contract; a lead chooses authorized domain capabilities within its existing goal.

## Scope and Impact

Only this packet's proposal, design, tasks and two deltas change. No runtime code,
tests, migration, credentials, provider call, live Telegram access, archive, activation
or deployment is performed. Tests: `+0 ~0 -0`.

`bu-p6v8k`'s sticky-routing objective is obsolete at the switch, as the collision ledger
already records. The continuity need historically tracked by `bu-d2ft5` survives in
goal context; no implementation or lifecycle claim is made for either bead. The larger
`bu-0ynlk.15` thread-spine work is not implemented or made a prerequisite here. This
packet creates no competing conversation store or chat-to-goal binding protocol.
Email's current runtime is untouched by this prose amendment; after cutover, its
classification and affinity disposition follows generalist, not a legacy exception
created by this Telegram packet. Existing buffered traffic and release authority stay
under RFC 0038's hard-cutover contract.
