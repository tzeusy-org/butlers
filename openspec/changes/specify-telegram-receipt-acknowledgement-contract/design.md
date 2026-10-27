# Design: Telegram receipt acknowledgement controls

## Context

`bu-2an2p`'s original design already sketched the shape: send a typing indicator or reaction
immediately on receipt, keep the typing indicator alive by resending it (Telegram's typing status
"expires after ~5s"), clear whatever ack was shown once the response arrives, and make the style
configurable. What actually shipped is `module-telegram`'s `react_for_ingest`, called from two
call sites — `_buffer_process` in `src/butlers/switchboard_wiring.py` (the durable-buffer ingest
path) and the background task in `src/butlers/core_tools/_switchboard.py` (the direct ingest path)
— both firing an in-progress (eyes) reaction immediately before `pipeline.process()` and a terminal
(thumbsup/alien) reaction immediately after, unconditionally for every `telegram_bot`-channel
message, with no config surface and no typing indicator at all.

That is real, tested coverage of *a* receipt-acknowledgement mechanism, but it settles none of
`bu-2an2p`'s six criteria as stated: there is no 1-second SLA claim anywhere in the shipped code
(it happens to be fast because it runs before classification, but nothing bounds or tests that), no
typing indicator exists, there is no keepalive, and there is no config or owner filter — every
`telegram_bot` message gets a reaction, including ones from non-owner senders in a shared or group
chat. The binding coordinator review held `bu-2an2p` open for exactly this reason. This draft
settles the contract those two call sites and `module-telegram` must implement.

## Goals and non-goals

Goals:

- Define the ack-style configuration surface and its default (D4).
- Define what the "1 second" and "4 second" bounds in `bu-2an2p`'s criteria actually promise: a
  local dispatch bound, never a delivery or rendering guarantee (D2).
- Define the typing-indicator keepalive cadence and its grounding in the Telegram Bot API's
  documented behavior (D3).
- Define terminal cleanup for both ack styles, including the honest limits of what "no orphaned
  indicator" can mean given the Bot API exposes no explicit un-type call (D5, D8).
- Define owner-only filtering using a fast, classification-independent lookup so the ack is not
  itself delayed by the thing it exists to paper over (D7).
- Assign transport/module/core ownership so a future implementation does not re-litigate where
  each piece of dispatch logic lives (D1).
- Preserve every one of `bu-2an2p`'s six criteria, generalizing (not dropping) criterion 4 against
  already-shipped behavior (D5).

Non-goals:

- No implementation, migration, runtime process, or live Telegram API call. Nothing here executes.
- No change to `connector-telegram-user-client` or any non-`telegram_bot` channel's ack behavior.
- No change to the approval or confirm inline-keyboard machinery, callback token formats, or
  `apr1:`/`cgi:`/`cfm1:` handling — orthogonal surfaces, untouched.
- No new Telegram Bot API method beyond `sendChatAction` (already documented, not yet called by
  this codebase) — no custom "clear typing" affordance, because the Bot API defines none (D5).
- No change to what triggers ingestion or how messages are classified/routed — this contract only
  adds acknowledgement dispatch around the existing pre/post-`pipeline.process()` call sites.

## Decisions

### D1: Ownership split — transport unchanged, module owns API primitives, core wiring owns orchestration

`connector-telegram-bot` remains transport-only, exactly as its existing Lifecycle Reactions
requirement already states ("applied downstream by the pipeline... not by this transport-only
connector. The connector SHALL neither send nor track reactions."). This draft does not change that
boundary for typing indicators either — the connector never calls `sendChatAction`.

`module-telegram` owns the low-level Telegram API primitives: the existing `react_for_ingest`
(unconditional emoji mapping, unchanged) and a new typing-indicator primitive pair,
`begin_typing_for_ingest`/`end_typing_for_ingest` (D6), each a thin wrapper around one Bot API call
plus the per-chat keepalive loop. Neither primitive reads ack-style config or performs the
owner-only check — they do exactly what they are told, mirroring `react_for_ingest`'s own
unconditional design.

Switchboard's core ingest wiring (`_buffer_process` in `switchboard_wiring.py` and the background
task in `core_tools/_switchboard.py` — the two existing pre/post-`pipeline.process()` call sites)
owns orchestration: reading the configured ack style, running the owner-only gate (D7), deciding
which of `begin_typing_for_ingest`/the in-progress reaction to fire before processing, and
guaranteeing the matching `end_typing_for_ingest`/terminal reaction fires after processing — reusing
the same structural guarantee those call sites already provide for reactions today (a broad
`except Exception` around `pipeline.process()` that sets a failure flag and falls through to the
terminal-reaction call, rather than re-raising). This is not a new architectural exception: core
Switchboard wiring already calls directly into a specific module's method (`telegram_mod.react_for_ingest`)
by name, bypassing the MCP tool-call boundary because it runs in the same daemon process — the same
pattern extends to the new typing primitives. No RFC is needed for this; the existing reaction
lifecycle set this precedent without one.

### D2: "1 second" and "4 seconds" are local-dispatch bounds, not delivery guarantees

`bu-2an2p` criterion 1 ("Typing indicator appears within 1s of message receipt") and criterion 2
("re-sent every 4s") describe what a reader experiences, but this contract can only bind what the
butler controls: the elapsed time between the pre-processing lifecycle hook firing (immediately
after Switchboard accepts the ingest, before classification or routing) and the process initiating
the `sendChatAction` HTTP call. That initiation SHALL happen within 1 second of the hook firing;
whether Telegram renders it to the user within that same second is an external guarantee this
contract explicitly does not make — network latency, Telegram-side load, and the connector's own
existing rate-limit/backoff handling (`connector-telegram-bot`'s Error Handling and Backoff
requirement) all sit outside this boundary. The existing "Reaction API failure" scenario already
establishes this class of call as best-effort and non-fatal; the typing dispatch inherits the same
posture. A future implementation's test for criterion 1 therefore asserts the local call is issued
within the bound, not that Telegram displays anything within it.

### D3: 4-second keepalive, grounded in the Bot API's documented ≤5-second typing duration

The Telegram Bot API's `sendChatAction` reference states: "The status is set for 5 seconds or less
(when a message arrives from your bot, Telegram clients clear its typing status)"
(https://core.telegram.org/bots/api#sendchataction, mirrored verbatim at
https://docs.aiogram.dev/en/latest/api/methods/send_chat_action.html, fetched 2026-09-09). The API
reference does not itself mandate a resend interval, only that a long-running operation should call
`sendChatAction` "in a loop" to keep the indicator alive. `bu-2an2p`'s own design already chose 4
seconds; this draft keeps that exact number rather than substituting a different one, and grounds it
against the same official 5-second bound: 4 seconds leaves a 1-second safety margin absorbing the
keepalive task's own scheduling jitter and the HTTP round-trip, so a slow tick cannot let the
client-visible indicator lapse before the next call lands.

### D4: Default ack style is `reaction`, preserving today's shipped behavior

`[modules.telegram.ack]` (new, optional) accepts `style: "typing" | "reaction" | "both" | "none"`.
Default is `reaction` — this is a non-regression choice, not a product preference: every currently
deployed butler with the telegram module enabled already gets unconditional reaction acks today with
no config present, and defaulting to anything else would silently change that butler's behavior the
moment this contract's implementation lands, with no configuration change on the owner's part. This
is an engineering-allocation call (continuity of existing behavior), not a hard-gated product
decision.

### D5: Reaction terminal state generalizes "removed" to "replaced with a terminal reaction"

`bu-2an2p` criterion 4 says the reaction "is removed when response arrives." The shipped
`react_for_ingest` behavior never removes a reaction — it replaces the in-progress eyes reaction
with a terminal thumbsup (success) or alien (failure) reaction, which is strictly more informative
than a bare removal (the owner learns the outcome, not just that processing ended) and is the
already-tested, already-deployed behavior. This draft specifies the shipped replace-on-terminal
behavior as the contract for `reaction`/`both` styles rather than reverting to literal removal,
generalizing rather than dropping the criterion — the same shape of move the sibling
`notify-confirm-interaction` contract made when it generalized "expired confirms are treated as
declined" into an explicit expired outcome (see that change's design.md D2). The `typing` style has
no reaction to remove or replace; its terminal behavior is defined separately (D6, D8).

### D6: Typing keepalive is per-chat and reference-counted, not per-message

Telegram's typing status is a property of the chat, not of an individual message — sending it twice
for the same chat is not additive. A chat can have more than one owner message in flight at once
(a burst of quick follow-ups before the first reply lands), so `begin_typing_for_ingest(chat_id)`
SHALL increment a per-chat in-flight counter and start the keepalive loop only on the transition
from 0 to 1; `end_typing_for_ingest(chat_id)` SHALL decrement the counter and stop the loop only on
the transition to 0. This prevents two failure modes a naive per-message implementation would hit:
stacking duplicate keepalive loops (wasted API calls, no user-visible benefit) and stopping the
indicator early because one of several concurrent messages finished while others are still
processing.

### D7: Owner-only filtering uses a fast, classification-independent lookup

The full identity-resolution pipeline (`enable_identity_resolution=True` on `pipeline.process()`)
runs as part of classification, which can take seconds — using it to gate the pre-processing ack
would defeat the 1-second bound (D2) it exists to satisfy. `butlers/identity.py` already provides
exactly the kind of fast, pipeline-independent owner check this needs:
`resolve_owner_channel_via_definer(pool, channel_type, channel_value)`, a `SECURITY DEFINER`-backed
lookup against `public.resolve_owner_triple` built for a structurally identical problem (an
approval-callback-time owner check that cannot wait on or depend on the classification pipeline).
The owner-only gate SHALL use this helper (or an equivalent single indexed lookup) keyed on the raw
Telegram sender identity already available at the pre-processing hook (`sender.identity` for a
single message; for a batched envelope, `sender.owner_sender_id`/`participants`, gating on whether
the owner participant is actually present in that batch) — never the full classification-time
identity resolution. A request with no owner-matching sender (including internally synthesized
buffer refs with no real Telegram sender) receives no ack under any configured style.

### D8: Terminal cleanup for typing is "stop dispatching," not "actively clear"

The Bot API defines no call to explicitly clear a chat action — a typing status only ever clears by
Telegram's own ≤5-second expiry or by the bot sending an actual message into that chat (D3). "No
orphaned typing indicators after response delivery or error" (criterion 3) therefore means: the
keepalive loop stops issuing further `sendChatAction` calls as soon as processing reaches a terminal
state (success, failure, or exception — using the same call-site structure that already guarantees
`react_for_ingest`'s terminal call runs today, D1), and the butler's own reply, once sent to that
chat, clears any residual client-side status immediately per Telegram's documented behavior. The
only bounded exception is the gap between the last dispatched keepalive tick and the terminal stop:
Telegram may still show typing for up to its own ≤5-second window during that gap even though this
contract's own dispatch has already stopped. That is a documented, bounded tail — never indefinite
orphaning — and is named here explicitly rather than left as an implicit assumption.

### D9: Cancellation is an inherited limitation, not a new one

Both existing call sites wrap `pipeline.process()` in `except Exception`, which does not catch
`asyncio.CancelledError` (e.g. a worker cancelled by `DurableBuffer.stop()`'s shutdown-drain
timeout). Under that specific cancellation path, today's terminal reaction call — and this
contract's `end_typing_for_ingest` call — would not run structurally, exactly as today's terminal
reaction does not run under the same cancellation. This draft does not widen or narrow that
existing gap; it is named here so a future implementation does not need to rediscover it, and so
this contract is not read as promising cleanup guarantees stronger than the call sites it reuses
actually provide.

## Rejected alternatives

- **Have `module-telegram` read the ack-style config and perform the owner-only check itself** —
  rejected (D1): the module has no access to the raw sender identity or config resolution the two
  call sites already have in scope, and duplicating that plumbing into the module would create a
  second place to keep the gating logic consistent for no architectural benefit.
- **Default ack style to `both` (typing + reaction) for maximal feedback** — rejected (D4): changes
  today's deployed behavior for every existing butler with no config change on the owner's part,
  which is a product-visible regression risk this draft is not authorized to introduce.
- **Restore literal reaction removal on terminal state, matching `bu-2an2p`'s literal wording** —
  rejected (D5): would regress the shipped, tested, more informative replace-on-terminal behavior
  to satisfy the letter of a criterion whose spirit (the in-progress signal must end) is already
  better served by the current behavior.
- **Actively "clear" the typing status via a synthetic follow-up call** — rejected (D8): the Bot API
  defines no such call; the only ways a typing status ends are documented Telegram-side behaviors
  (≤5s expiry, or a message arriving from the bot), and inventing a fictitious API call would
  misrepresent what this contract can promise.
- **Gate the pre-processing ack on the full classification-time identity resolution** — rejected
  (D7): directly defeats the 1-second local-dispatch bound this contract exists to define.

## Test Strategy (future implementation only)

Named seams a future implementation PR's tests must cover (none exist yet; this draft adds no
tests, `+0 ~0 -0`):

- Unit: ack-style config validation (`typing`/`reaction`/`both`/`none`, default `reaction`); per-chat
  keepalive refcount transitions (0→1 starts the loop, N→0 stops it, no stacking on 1→2).
- Contract/API: the pre-processing hook issues its dispatch call within the 1-second local bound
  (measured against the hook's own firing time, not against Telegram's response); the keepalive
  loop issues `sendChatAction` at a 4-second cadence while a chat's in-flight counter is nonzero.
- Connector/integration: owner-only gate correctly suppresses ack dispatch for a non-owner sender
  and for a batched envelope with no owner participant, using the fast lookup (D7) rather than the
  classification pipeline's own resolution.
- Terminal-state: `reaction`/`both` styles replace the in-progress reaction with the correct
  terminal reaction on both success and failure; `typing`/`both` styles stop the keepalive loop on
  both success and failure (and on the existing call sites' caught-exception path).
- Cancellation: an `asyncio.CancelledError` at either call site leaves the same structural gap for
  `end_typing_for_ingest` that it already leaves for the terminal reaction today (D9) — a regression
  test that this draft does not silently widen that gap.
- Style suppression: `none` results in zero `sendChatAction`/`setMessageReaction` calls for an
  otherwise-qualifying owner message.

## Delivery gates

1. Land this draft only after independent exact-head semantic review returns GO or corrections are
   applied and re-reviewed.
2. Obtain separate owner approval naming the exact reviewed commit before any implementation claims
   this contract as authority.
3. Implementation happens under `bu-2an2p` (unmodified by this draft) or an explicitly
   coordinator-approved successor, plus the tests in `tasks.md`/Test Strategy above.
4. Treat any live Telegram API call, database migration, configuration change, or deployment as a
   separate, later-authorized act this draft does not perform or authorize.

## Open questions

None are silently decided here. Whether a future implementation places
`begin_typing_for_ingest`/`end_typing_for_ingest`'s per-chat state in an in-memory dict on the
module instance or a small dedicated helper class is left to that implementation PR — both satisfy
this contract's per-chat refcounting requirement (D6) equally, and choosing between them is ordinary
engineering allocation, not a decision this draft needs to pin.
