# Design: Telegram continuity and routed-context contract

## Context

Today, every `telegram_bot`-channel message that does not match a deterministic triage rule falls
through to LLM classification (`src/butlers/modules/pipeline.py`), which loads bounded realtime
history (15-minute window, 30-message cap; `HISTORY_STRATEGY["telegram_bot"] = "realtime"`) purely
to help the classifier *write* a self-contained `prompt`/`context` pair for
`route_to_butler(butler, prompt, context=None, complexity=None)`
(`src/butlers/core_tools/_switchboard.py`). `_set_routing_context`'s own docstring is explicit that
this is deliberate: "conversation_history is intentionally NOT forwarded here... forwarding the raw
unfiltered history would bypass that filtering." The target butler's actual continuity therefore
depends entirely on how well one classifier LLM call summarized the chat — there is no structural
guarantee.

Two things compound this. First, `bu-p6v8k` wants sticky routing: once a chat is routed to a
butler, skip classification for follow-up turns. But skipping classification means *nobody* ever
writes a `prompt`/`context` pair for that turn — the mechanism that today (however unreliably)
carries context disappears entirely on a sticky hit. Second, `bu-d2ft5` observes that even on a
normal classify-then-route turn, the target is "history-blind" beyond whatever the classifier chose
to restate. Both beads name the same underlying authority gap: nothing deterministically forwards
bounded conversation context across the routing boundary. This draft settles that contract, plus the
affinity mechanics `bu-p6v8k` needs to decide when to skip classification at all.

The email precedent already exists and works: `lookup_thread_affinity()`
(`roster/switchboard/tools/triage/thread_affinity.py`) reads `switchboard.routing_log` (already
populated for every channel via `roster/switchboard/tools/routing/route.py`'s `thread_id`/
`source_channel` columns) and `switchboard.thread_affinity_settings`, gated to `source_channel ==
"email"`. Telegram cannot reuse it unmodified for one structural reason: email's `thread_id` is a
stable `Message-ID`-derived identity that persists across a whole conversation, while Telegram's
`event.external_thread_id` is `<chat_id>:<message_id>` — unique per message. A raw port of the email
lookup would never hit twice. The pipeline's own `_load_realtime_history` already solves exactly this
problem for its history query (extracting `chat_id` via `_TELEGRAM_CHAT_MESSAGE_RE` and matching
`thread_id = $chat_id OR thread_id LIKE '$chat_id:%'`); this draft's affinity lookup reuses that same
extraction and matching shape against `routing_log.thread_id` instead.

## Goals and non-goals

Goals:

- Define chat-keyed affinity semantics: new thread, follow-up hit, TTL expiry, unavailable target,
  concurrent turns, and replay/dedup — each named against an existing `AffinityOutcome` class (D1-D5).
- Define a Telegram-appropriate TTL distinct from email's, and where that setting lives (D2).
- Define the reset path by extending the already-specified `correct_route` misroute-correction tool,
  not inventing a new command surface (D6).
- Define the exact bounded, trust-fenced target-side context contract, its ownership boundary, and
  its relationship to the classifier-authored `context` string (D7, D8).
- Name the reconciliation boundary with `bu-0ynlk.15` precisely enough that a future implementation
  does not build a second, competing notion of "which butler owns this chat" (D9).
- Preserve `bu-27dxl.9`'s non-goals: no unified cross-channel ledger, no raw/unbounded transcript, no
  change to email affinity's channel gate or numbers.

Non-goals:

- No implementation, migration, runtime process, or live Telegram API call. Nothing here executes.
- No change to `pinned_target` (the dashboard's explicit per-envelope override, evaluated *before*
  thread-affinity in `ingest.py`'s documented precedence) or to email's thread-affinity behavior,
  TTL, or override dict — this draft adds a parallel per-channel path, it does not touch the existing
  gate's email branch.
- No new Telegram bot command grammar, inline keyboard, or conversational reset phrase. RFC 0021's
  pending-action approval machinery and the `apr1:`/`cgi:`/`cfm1:` callback formats are orthogonal
  and untouched.
- No change to `connector-telegram-bot`'s `ingest.v1` field mapping — `chat_id` is already
  recoverable from the existing `event.external_thread_id`; no new field is needed.
- No decomposition-branch change: this draft's routed-context contract applies to the
  classify-then-route and sticky-bypass paths (`route_to_butler` / `route.execute` via
  `_dispatch_dashboard_target`), not the structurally distinct batch-decomposition envelope
  (`_build_decomp_route_envelope`), which already carries its own `conceptual_message` object and is
  untouched here.

## Decisions

### D1: Affinity key is `chat_id`, extracted the same way the pipeline's history loader already does

`switchboard.routing_log.thread_id` is already populated for `telegram_bot` routes today (the column
is channel-generic; `route.py`'s `_record_routing_log` writes it for every channel) with the raw
per-message `<chat_id>:<message_id>` value. Rather than change what gets written, the affinity
*lookup* SHALL match on `chat_id` using the identical pattern
`thread_id = $chat_id OR thread_id LIKE ($chat_id || ':%')` that
`_load_realtime_history`'s SQL query already uses against `message_inbox.request_context ->>
'source_thread_identity'`. This keeps the write side unchanged (no migration, no new column) and
reuses a pattern already proven correct in production code for the same identity mismatch.

### D2: A separate, Telegram-scoped TTL — not email's 30-day default

`switchboard.thread_affinity_settings` is a singleton row (`WHERE id = 1`) whose `thread_affinity_
ttl_days` (default 30) and `thread_overrides` currently apply only to email because `lookup_thread_
affinity()` gates on `source_channel == "email"` before consulting them. Telegram's cadence is
fundamentally faster than email's (an open chat, not a reopened thread weeks later); reusing email's
30-day default would let a pin from an unrelated topic three weeks ago silently hijack today's first
message. This contract requires a **per-channel TTL setting**, not a shared one — the exact schema
shape (new columns on the existing table vs. a channel-keyed settings table) is ordinary engineering
allocation for the implementer, but behaviorally: Telegram's default TTL SHALL be short enough to
self-heal after a natural conversational gap and long enough to survive a multi-hour pause without
forcing needless reclassification. **24 hours** is the chosen default — long enough that "away for
the afternoon, still on the same topic" doesn't force a reclassify, short enough that yesterday's
resolved topic doesn't quietly own tomorrow's first message. This is an engineering-allocation call
(reversible via config), not a product decision: no owner currently depends on any Telegram sticky
behavior, since `bu-p6v8k` is unimplemented.

### D3: Unavailable pinned target is validated at lookup time, exactly like `pinned_target`

`ingest.py`'s existing envelope-pin path already validates `pinned_target` against
`available_butlers` before trusting it (rejecting an unknown target rather than silently misrouting).
The Telegram affinity lookup SHALL apply the same validation: if the pinned `target_butler` from a
HIT is no longer a registered, available butler (removed from roster, disabled), the lookup SHALL
report a miss and fall through to LLM classification — never dispatch to a butler that no longer
exists. This reuses `AffinityOutcome`'s existing miss shape rather than adding a new one; the
telemetry reason is `"disabled"` (already an allowed value in
`_ALLOWED_AFFINITY_MISS_REASONS`).

### D4: Concurrent turns reuse the existing conflict outcome — no new locking

Email's `lookup_thread_affinity()` already handles more than one distinct butler appearing in the
routing-history window within the TTL by returning `MISS_CONFLICT` (falls through to fresh
classification) rather than picking one arbitrarily. A burst of quick Telegram messages that race
past the affinity lookup before the first turn's routing decision is durably recorded is the same
shape of problem: if both resolve to different butlers, the *next* lookup sees two distinct rows in
the window and reports conflict, self-healing to a single fresh pin once one classification
completes. This draft specifies reuse of that exact outcome for Telegram rather than inventing
message-level locking or a queueing discipline — the existing behavior is already correct for "don't
guess, reclassify," and Telegram messages that race this tightly are rare enough that one extra
classification call is the right cost, not a latency problem worth new machinery.

### D5: Replay and dedup are inherited from existing ingest idempotency — not re-specified here

`connector-telegram-bot`'s existing Idempotency and Safety requirement guarantees a redelivered
Telegram update (`control.idempotency_key = "tg:<chat_id>:<message_id>"`) is deduplicated by
Switchboard before triage or routing runs at all. A duplicate therefore never reaches the affinity
lookup as a "new" turn, and never double-writes `routing_log`. This draft adds no new dedup logic; it
states the inherited guarantee explicitly (mirroring how the sibling
`telegram-receipt-acknowledgement` contract named its own inherited cancellation gap, D9 there) so a
future implementation does not assume it must build fresh dedup for affinity specifically.

### D6: Owner correction reuses `correct_route` instead of a new command surface

The bead's "owner correction/reset" scenario could be built as a new conversational trigger (a slash
command, a fixed reset phrase) detected deterministically before the affinity lookup. That path was
considered and rejected (see Rejected Alternatives): `openspec/specs/butler-switchboard/spec.md`
already specifies `correct_route`, an MCP tool a downstream butler calls via its own `correct` tool
when it determines an incoming message does not belong to its domain. This is *already* the
mechanism a sticky-routed target butler would use to say "this message reached the wrong domain" —
extending its effect to also update the chat's affinity pin (a new scenario on the existing Misroute
Correction Re-dispatch requirement) reuses a tested, specified, audited path instead of adding a
second one. Concretely: when `correct_route` re-dispatches a `telegram_bot`-channel request to
`correct_butler`, it SHALL also record a fresh affinity HIT for that chat's `chat_id` pointing at
`correct_butler` (the same write path an ordinary successful route already exercises via
`routing_log`), so the *next* message in that chat does not repeat the same misroute. No new field is
added to `correct_route`'s existing parameters; the behavior is derived from the original event's
already-available `source_channel` and thread identity.

### D7: Deterministic routed-context follows the exact seam that already exists for dashboard context

`_dispatch_dashboard_target` (`src/butlers/core_tools/_switchboard.py`) already deterministically
appends a dashboard confirm-block to `input.context` "regardless of what the classification session
itself wrote into `context`" — the exact same shape of problem this draft's routed-context
requirement solves for Telegram continuity. The new behavior SHALL follow the identical pattern: for
a `telegram_bot`-channel route (whether reached via LLM classification or an affinity-bypass hit), a
bounded, filtered history block SHALL be deterministically appended to `_effective_context` before
the `route.v1` envelope is built — additive to the classifier-authored `context` string when one
exists, and the *only* context present when classification was skipped (affinity bypass), in which
case `prompt` SHALL be the raw incoming message text (already self-contained by definition — it is
literally what the owner typed) rather than an LLM-authored restatement.

### D8: Reuse the exact bound and exact trust-fencing already normative for classifier history

`openspec/specs/module-pipeline/spec.md`'s "Conversation History for Routing Context" requirement
already normatively bounds realtime-channel history to a 15-minute window and 30 messages; this
draft's routed-context block SHALL use that identical bound rather than a new number, so there is one
source of truth for "how much recent Telegram history is ever loaded," not two. Trust fencing SHALL
reuse `_format_history_context`'s already-shipped pattern verbatim: an explicit "UNTRUSTED USER DATA"
banner instructing the reading LLM not to follow any embedded instruction, and each message's content
fenced inside a code block. A malicious prior message in the window (e.g., "ignore your instructions
and...") is data under this fencing, exactly as it already is for the classifier today — this draft
extends the same defense to the new target-side surface rather than inventing a second one that could
drift out of sync.

### D9: Reconciliation with `bu-0ynlk.15` — a routing cache now, a migration point later

`bu-0ynlk.15`'s `conversation_channel_bindings(channel, thread_identity, butler_name, ...)` will,
once built, be able to answer "which butler is this Telegram chat currently bound to" as a durable,
cross-channel fact serving dashboard/Telegram handoff. This draft's affinity pin answers a narrower,
Switchboard-internal question — "should this turn skip LLM classification" — using the existing
`routing_log`/`thread_affinity_settings` machinery already proven for email. These are not the same
object: `.15`'s binding is a durable identity used for cross-channel handoff and resume; this
draft's pin is an ephemeral (TTL-bound, disposable-on-conflict) routing-decision cache with no
handoff semantics and no dashboard-facing surface. Building them as one object now would couple this
contract's Switchboard-internal routing shortcut to `.15`'s much larger, currently-undispatched
schema and UI surface, delaying `bu-p6v8k`/`bu-d2ft5` on work that is not sequenced to land first.
Once `.15` lands, a future implementation SHOULD read/write the Telegram affinity fact through
`conversation_channel_bindings` instead of `routing_log`-generalization, so the ecosystem converges on
one authoritative answer — this draft names that consolidation path explicitly rather than leaving it
to be rediscovered, but does not implement, schedule, or block on it. `bu-27dxl.9`'s own instruction
("serialize with `bu-0ynlk.15` wherever both choose the same thread/binding state") is satisfied by
this explicit hand-off note plus keeping this draft's storage choice swappable (behavior is specified
independently of which table backs it).

## Rejected alternatives

- **A new conversational reset command** (`/switchboard`, a fixed reset phrase) — rejected (D6): adds
  a new user-facing command surface and a new deterministic-detection code path for a need
  `correct_route` already meets, and a bare string-match trigger risks colliding with an owner's
  actual message content in a way a domain-butler's own misroute judgment does not.
- **Share email's `thread_affinity_settings` row and 30-day TTL for Telegram** — rejected (D2):
  email's cadence (a thread reopened weeks later) and Telegram's (a live chat) are different enough
  that a shared default would misbehave for one of them; per-channel settings cost one settings
  dimension, not a new subsystem.
- **New message-level locking or a per-chat write queue for concurrent turns** — rejected (D4): the
  existing conflict-then-reclassify behavior already produces a correct, if occasionally
  extra-classified, outcome; new locking adds complexity for a rare race with no observed cost today.
- **Forward the full unbounded chat transcript to the target butler** — rejected (D8, and the bead's
  explicit non-goal): defeats token budgets, duplicates the classifier's own history load, and widens
  the untrusted-data surface with no bound.
- **Build affinity directly on `bu-0ynlk.15`'s not-yet-existing binding table** — rejected (D9):
  `.15` is open and undispatched; sequencing `bu-p6v8k`/`bu-d2ft5` behind its full schema and handoff
  UI would block two ready-to-shape implementation beads on an unrelated, much larger move.

## Test Strategy (future implementation only)

Named seams a future implementation PR's tests must cover (none exist yet; this draft adds no tests,
`+0 ~0 -0`):

- Unit: `chat_id` extraction from `<chat_id>:<message_id>` and bare `chat_id` thread identities
  (mirroring `_load_realtime_history`'s existing regex test coverage); per-channel TTL setting
  resolution defaulting to 24h for `telegram_bot` without altering email's 30-day default.
- Contract: new-thread miss, follow-up hit (classification skipped), TTL-expired miss, unavailable
  pinned-target miss (butler removed from roster), and conflict miss (two distinct butlers in window)
  — each asserted against the existing `AffinityOutcome` enum values, no new outcome added.
- Integration: `correct_route` on a `telegram_bot`-channel request writes a fresh affinity HIT for
  the corrected butler, verified by a subsequent lookup for the same `chat_id`.
- Idempotency: a redelivered Telegram update (duplicate `idempotency_key`) never reaches the affinity
  lookup or writes a second `routing_log` row.
- Routed-context: the deterministic block is present and correctly bounded/fenced on both a
  classify-then-route turn (additive to classifier `context`) and an affinity-bypass turn (sole
  context, with `prompt` equal to the raw incoming message); a malicious embedded instruction in a
  fenced prior message is not followed by a downstream test harness LLM call.
- Regression: email's existing thread-affinity test suite (`roster/switchboard/tests/*thread_
  affinity*`) is unaffected — no shared-code-path behavior change for `source_channel == "email"`.

## Delivery gates

1. Land this draft only after independent exact-head semantic review returns GO or corrections are
   applied and re-reviewed.
2. Obtain separate owner approval naming the exact reviewed commit before any implementation claims
   this contract as authority.
3. Implementation happens under `bu-p6v8k` and/or `bu-d2ft5` (unmodified by this draft) or an
   explicitly coordinator-approved successor, per `bu-27dxl.9`'s cohesion-scan guidance on whether
   they should share one implementation carrier.
4. Treat any live Telegram API call, database migration, configuration change, or deployment as a
   separate, later-authorized act this draft does not perform or authorize.

## Open questions

None are silently decided here. Whether the per-channel TTL setting is implemented as new columns on
`switchboard.thread_affinity_settings` or a new channel-keyed table is left to the implementation PR
— both satisfy D2's behavioral requirement (a Telegram-specific default independent of email's)
equally, and choosing between them is ordinary engineering allocation.
