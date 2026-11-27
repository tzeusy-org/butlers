# Telegram receipt acknowledgement design

## Context

The original receipt draft attached to `pipeline.process()`. The RFC 0038 collision
disposition assigned bu-z0b0ah.4 to move it to committed goal admission and actual reply.
The receipt foundation in bu-z0b0ah.9 has a smaller review surface than the later progress
and lifecycle work. The design retains the original receipt outcomes while allocating the
first safe, bounded handoff to that prerequisite.

RFC 0038 D1 places admission with the control plane. D5 requires verified owner origin. D7
places provider egress with Messenger. D11 makes actual reply delivery a prerequisite for
successful completion. D14 governs Stop and recovery, and D17 keeps telemetry content-free.
RFC 0017 section 2.7 supplies live, uniquely resolved owner-channel authorization.

## Goals and non-goals

- A newly admitted owner Telegram bot message gets the configured initial eyes reaction
  and/or typing before the lead launches. The first foundation uses an injected Messenger
  boundary and the runtime default remains off.
- The full parent outcome still owns delayed deterministic progress, its bounded edits,
  terminal result mapping, restart reconciliation and the M6 rollout.
- Scope excludes standalone connector actions, legacy pipeline changes, third-party or
  shared chat activity, model-written progress, Stop buttons, and live provider access.
  The owner assigned the first-visible p95 target and model drafts to M7 in bu-mq929j.

## Decisions

### D1. The goal control plane orders receipt and Messenger owns egress

The control plane chooses the acknowledgement style and initiates the first request after
admission commits and before the ordinary lead launch. Messenger verifies the destination
before Telegram module actions. Any future deployed service crossing uses the approved
Messenger MCP boundary. The connector remains unaware of acknowledgement state, style
and transport calls.

The admitted record and its private result must distinguish newly committed admissions from
replayed decisions. Existing attempts may return the same decision ID, goal ID and public
result even when they race to observe the commit. Use a dark local handoff tested under
concurrent duplicate attempts. Keep network activity out of the admission transaction, and
do not let a replay launch ahead of an in-flight first acknowledgement on this normal path.
The ordinary supervisory recovery path can start a committed goal when the first caller is
lost. It does not reconstruct or resend a possibly delivered acknowledgement. Preserve the
existing admission result and the unchanged behavior for ordinary goals without a receipt.

### D2. One second bounds local typing initiation

Measure the interval from the post-commit prelaunch hook to the local Messenger dispatch
initiation for an eligible typing-inclusive style under a fake clock. A provider reply is
not needed to launch the lead after the bounded handoff. A refusal, local timeout or
uncertain outcome is best effort and leaves launch possible without a second attempt.
Receipt telemetry carries goal ID only and no transport exception, identifiers for people
or destinations, or private payload.

### D3. Four seconds remains the later typing cadence

Telegram's residual typing can expire after at most five seconds. Later lifecycle work
refreshes at four-second intervals for an eligible running goal. The first-receipt
foundation has no recurring task. An unqualified destination or ended goal contributes no
further refresh.

### D4. One policy retains four styles and the reaction default

The original draft's `typing`, `reaction`, `both` and `none` semantics and reaction default
carry forward to the owner-channel policy called `owner_channel_ack` on bu-z0b0ah.4.
The control plane gets it through trusted construction in the dark foundation. There is
no second module selector and no new deployed configuration in that foundation. Deployment
configuration and switch authority stay with the rollout package.

### D5. The settled owner reply and explicit terminal outcomes drive cleanup

Eyes remain the initial reaction. Actual owner reply delivery replaces eyes with thumbs-up
and closes any progress message. Failure or a partial outcome uses the failure line and
alien reaction. Stop displays the specified Stopped line. The later progress work keeps
exactly one delayed, reply-bound message, updates it with templated ledger facts and retains
the original edit interval bound. This contract records those parent commitments without
adding progress behavior to the first-receipt foundation.

### D6. Concurrent lifecycle work shares one per-chat typing loop

A verified goal contributes an individual hold to the owner's chat. The first hold starts
the later keepalive and the last valid release stops it. A replay cannot acquire a second
hold for the same goal. Missing or stale owner bindings fail closed for new provider
actions during refresh.

### D7. Verify source and destination independently

The source facts come from retained authenticated Telegram bot ingress, including the
message's original reply target under `REQ-connector-telegram-bot-001`. Only actual owner
identity established by admission counts. Text assertions, borrowed references, metadata
from other sources and a primary route flag confer no authority. The first signal targets
the original message in a chat that resolves uniquely to the same live owner. Refuse a
shared or changed chat, inconsistent source binding, unknown identities, ambiguity or
unavailable authority. A fake Messenger exercises this distinct destination check with
positive and negative cases.

### D8. Stop refresh and let residual typing expire

The Telegram Bot API provides no separate clear-typing method. Stop new refreshes on
reply delivery, failure, Stop or lease lapse and let the client expire the last signal.
Retain the original requirement to stop only when the last valid hold for that chat ends.

### D9. Recover without replaying uncertain provider actions

Restart or lease lapse must not leave an orphaned background task. The first foundation
has no persistent typing state. Later lifecycle work must use the persisted progress ID to
settle the progress message after restart. An uncertain initial signal is never replayed
from the admission result. First receipt must leave the goal and reply delivery ledger
semantics intact.

## Test strategy

Use the accepted source fixture and real PostgreSQL in
`tests/switchboard/test_goal_ledger_postgres.py` for immutable decisions, owner evidence,
replay and concurrent duplicate controls. Extend `tests/goal_runner/test_goal_runtime.py`
at the real admission/start seam with a fake Messenger and controlled timing. Cover each
style, off switch, other source, refused and joined admission, ambiguous chat, refusal or
timeout, first handoff ordering, and telemetry. Use no actual provider or live owner data.
The later progress acceptance keeps its own real lifecycle, fake-clock and restart proofs.

## Delivery gates

Independent exact-head review and separate owner adoption naming the reviewed proposal are
required before any source implementation. The first-receipt implementation, if adopted,
has its own independent review and verified protected merge. The original rollout,
progress acceptance and switch release remain open. Report only the measured per-hop
baseline at the M6 switch, as authorized by bu-mq929j. An exact RFC cutover-gate amendment
requires separate owner approval.
