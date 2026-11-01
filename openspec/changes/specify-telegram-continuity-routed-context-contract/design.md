# Design: Telegram conversation continuity in the goal runtime

## Context and authority

This is the existing packet's adopted-RFC conformance amendment, not a second shaping
exercise or an implementation. The original draft's nine decisions coupled a
Switchboard routing cache to bounded target-side history. Canonical `bu-u5euwb.16`
and generalist's collision dispositions settle the replacement: sticky target affinity,
TTL reclassification and Misroute Correction Re-dispatch retire; bounded conversation
history and third-party fencing survive as goal-admission continuity.

RFC 0038 D1/D2 removes classification, route dispatch and domain reasoning sessions.
D5 makes admission deterministic and binds origin/cause to server-held evidence. D15
starts the lead with goal input, with third-party spans fenced as data; other context
comes through authorized context/domain reads with provenance. Generalist's
module-pipeline Conversation History for Routing Context already carries the same
15-minute / 30-message bound into goals, per-item stamps and exposure. No new choice
about owners, grants, subscriptions or goal-join policy is made here.

The current connector/pipeline contracts supersede this draft's old
`external_thread_id` extraction: history is selected by `external_conversation_id`,
not a per-message reply target. Topic identity and sender authority are independent.

## Goals and non-goals

Preserve bounded, deterministic, provenance-bearing continuity without reintroducing
a target selector, classifier, correction session or competing identity store.
History remains context, never authentication, a grant or proof of goal ownership.

No runtime implementation, database/migration, provider/credential access, live
Telegram request, task completion, archive, cutover or deployment occurs. The existing
future implementation and release gates remain separate. The spec amendment neither
changes email's live behavior nor exempts post-cutover email from generalist's removal
of thread-affinity routing. Pending thread-spine work is not a prerequisite.

## Conformance of the original decisions

### D1: Conversation identity replaces the affinity lookup key

Retire the `routing_log` target-affinity lookup and its HIT/MISS interpretation. The
existing Telegram field mapping supplies `event.external_conversation_id` as
`telegram:<chat_id>`, suffixed `:topic:<message_thread_id>` only for a message marked
`is_topic_message`. `event.reply_target_ref` identifies the reply target separately;
`source_thread_identity` is its notify-facing compatibility name, not the history key.
`event.external_thread_id` must not be reintroduced. History uses
`request_context ->> 'external_conversation_id'`, so distinct messages share history
inside a conversation, and different chats/topics do not leak into one another.
Existing pre-split history/backfill behavior is inherited, not reimplemented here.

### D2: Retire affinity TTL; preserve the independent history bounds

There is no Telegram 24-hour target pin or affinity settings surface in the goal
runtime, and no expiry that triggers classification. This does not retire the context
window: load only the most recent 30 messages within the most recent 15 minutes.
Neither a prior target nor a long-lived thread binding widens that window. The old
email 30-day affinity comparison is historical, not a post-cutover exception.

### D3: A prior domain's availability cannot select a dispatch path

A removed, disabled or unavailable previously-used domain grants no target affinity
and causes no classification or fallback route. Accepted reasoning ingress follows
ordinary admission. Capability availability and authorization follow the existing
broker/goal contract; no domain session is launched. Missing or unreadable history
cannot be represented as successfully loaded context, inferred from a different
conversation, or used to upgrade sender authority. Existing admission validation and
idempotency still apply; this packet adds no admission-success fallback policy.

### D4: Concurrent turns do not create a pin conflict or guessed goal join

Prior messages using different domains create no target-affinity conflict, queue or
conflict-then-reclassify rule. Independent goals are not forced into one conversation.
The existing owner-reply join contract alone can attach an incoming reply to a live
goal: trusted infrastructure must prove the exact prompt/effect and recipient/channel
binding, lock/recheck liveness and retain the goal's authority and accounting.
Same-chat history is not that proof. Unknown, foreign, mismatched or expired bindings
follow the already-specified ordinary admission outcome, without guessed continuity.
This amendment does not implement a new goal-join protocol.

### D5: Replay keeps the existing ingest and admission identities

The Telegram canonical key remains `tg:<chat_id>:<message_id>`, shared with the
user-client connector; only the existing unavailable-ID fallback uses
`telegram:<endpoint_identity>:<update_id>`. At-least-once delivery and crash-safe
checkpoint advancement after accepted ingest remain unchanged. A duplicate accepted
envelope resolves to the existing goal/admission, with the same canonical request
reference; it is not a second turn that writes affinity or starts another session.
No new dedup store, correction ID or routing log is introduced.

### D6: Remove the entire misroute correction tool contract

The delta uses REMOVED Misroute Correction Re-dispatch, matching generalist. It no
longer carries a competing MODIFIED tool block. All seven original scenarios in this
packet are retired with that mechanism: successful/expired-event/unregistered-target
re-dispatch, copied route context, a replacement session ID, corrected routing outcome,
and Telegram re-pinning. Their full old bodies and dispositions are retained in the
amendment preservation evidence. No original message is re-dispatched, no corrected
domain session is created, and no compatibility correction endpoint is authorized.

The adopted migration is unchanged: for a wrong goal outcome the owner may start a
new goal or Stop the current one; a lead that selected the wrong domain calls the
right capability within its existing grant and goal. The canonical ingest reference,
server-held sender identity and goal/effect lineage remain in their actual goal
contracts; correction IDs and replacement session IDs are not their successors.

### D7: Bounded continuity belongs to the admitted goal

Retire `_dispatch_dashboard_target`, `route.v1 input.context`, classifier-authored
context and the affinity-bypass variant as carriers of this feature. Deterministic
recent history accompanies goal admission/goal context under the already-adopted
module-pipeline contract. It supplements the accepted message; it does not replace
its content, canonical request reference or sender evidence. Admission does not run a
model to summarize, classify or choose a target. Context later retrieved by an
invocation remains subject to existing authorized context/domain read boundaries.
The history block is not a domain prompt or direct cross-domain database privilege.

### D8: Fencing, stamps and exposure survive together

Retain an explicit untrusted-data banner instructing the reader not to follow
instructions, links or calls-to-action inside history, and code-fence each message.
The source content remains data even if it resembles owner instructions. Fencing
cannot replace per-item sender/provenance stamps or the D5/D6 exposure rules.

Origin comes from trusted owner-channel authorization, never chat membership, prior
affinity, display text, model output or a history banner. Non-owner messages retain
that sender's identity; third-party goals start exposed with their restricted ceiling.
An owner message containing connector-marked forwarded/quoted spans starts exposed.
Reading non-owner or tainted history exposes the invocation and goal before a
consequential effect; missing/unreadable stamps fail closed as exposed. Exposure only
increases, stored exposed writes remain tainted, and retrieval cannot wash authority.
Private-custody content stays unreachable. All effects retain broker/grant/approval
checks; this packet adds no capability, subscriber or authority grant.

### D9: No competing thread store or owner allocation

The retired routing cache is not migrated into a new chat-to-butler or chat-to-goal
identity table. The existing conversation key and exact owner-reply binding contract
supply their separate guarantees. `bu-0ynlk.15` is a historical coordination reference,
not an implementation prerequisite or authorization to design its storage here.
No sticky ownership is inferred from bounded context. Existing runtime email behavior
is untouched by this amendment; target-state email retirement remains governed by
generalist. No new command grammar, callback format or wire field is introduced.

## Verification and delivery boundaries

The original tasks' ten checked drafting entries are historical evidence only. Their
state does not assert that this conversion is reviewed, adopted, implemented or
archived. Conversion acceptance is PENDING exact-head review and required gates.
All sixteen unchecked approval/implementation/verification/archive entries remain
unchecked, with bodies conformed where their old mechanisms have retired.

For this prose-only amendment: run `openspec validate --all --strict`,
`make check-guards`, the dirty-tree planner and source hygiene. Tests: `+0 ~0 -0`.
Preservation evidence maps all ten old requirements and all 27 full old scenarios to
retirement or surviving guarantees, retaining full-body hashes and task states.
Retirement is an explicit adopted disposition, not a way to hide an unaffected loss
from the MODIFIED-body matcher; no baseline, ratchet or RFC text changes here.

Future implementation needs its own authority, tests and exact-head review. Its named
cases retain bounds, same-conversation/topic isolation, separate reply targets,
third-party/forwarded input and malformed stamp exposure, replay and canonical request
lineage. Any live API call, migration, activation, deployment, hard cutover and archive
remain separately authorized future acts. No owner exact-text sign-off is invented
for this canonical-conforming prose. No remaining normative choice is delegated to a
future implementation under the guise of a new TTL or owner-selection question.
