# Telegram Continuity and Routed Context

## Purpose

Defines the contract for two coupled gaps in Telegram bot routing: (1) stable, chat-keyed affinity —
when a follow-up message in the same Telegram chat may skip LLM classification and go directly to
the previously-routed butler, and how that affinity expires, resets, and behaves under an
unavailable target or concurrent turns — and (2) the bounded, trust-fenced conversation context a
target butler deterministically receives, whether reached through classification or an affinity
bypass. Preserves the Switchboard as the sole ingest/route authority and reconciles with the future
`bu-0ynlk.15` owner-thread spine rather than introducing a competing thread-identity store (see
design.md D9). This spec is a prerequisite contract; no code, configuration, migration, or runtime
described here exists yet.

## ADDED Requirements

### Requirement: Chat-Keyed Affinity Lookup

Telegram bot affinity SHALL be keyed on the `chat_id` extracted from `event.external_thread_id`
(format `<chat_id>:<message_id>`, falling back to a bare `chat_id`), matched against
`switchboard.routing_log.thread_id` using the same `thread_id = $chat_id OR thread_id LIKE
($chat_id || ':%')` pattern already used by the pipeline's realtime-history loader, not the raw
per-message thread identity.

#### Scenario: New chat has no affinity

- **WHEN** a `telegram_bot` message arrives for a `chat_id` with no prior routing history within the
  configured TTL
- **THEN** the affinity lookup reports a miss and the message proceeds to LLM classification, exactly
  as an email thread with no history does today

#### Scenario: Follow-up message hits the pinned target

- **WHEN** a `telegram_bot` message arrives for a `chat_id` that has exactly one distinct
  `target_butler` in its routing history within the configured TTL, and that butler is currently
  registered and available
- **THEN** the affinity lookup reports a hit and the message is dispatched directly to that butler,
  skipping LLM classification

#### Scenario: Extraction matches both thread-identity shapes

- **WHEN** `routing_log.thread_id` for a chat's prior routes is stored as either a bare `chat_id` or
  a `<chat_id>:<message_id>` composite
- **THEN** the affinity lookup's `chat_id` match includes both shapes, exactly as the pipeline's
  existing realtime-history query already does for the same identity format

### Requirement: Telegram-Specific Affinity TTL

Telegram affinity SHALL use a TTL setting distinct from and independent of email thread-affinity's
TTL, defaulting to 24 hours, without altering email's existing 30-day default or its settings row.

#### Scenario: Default TTL applies without configuration

- **WHEN** no Telegram-specific affinity TTL is configured
- **THEN** the effective TTL is 24 hours

#### Scenario: Affinity expires after the TTL window

- **WHEN** a `chat_id`'s most recent routing-history entry is older than the configured Telegram TTL
- **THEN** the affinity lookup reports a miss (stale) and the message proceeds to fresh LLM
  classification

#### Scenario: Email affinity settings are unaffected

- **WHEN** the Telegram-specific TTL is configured or changed
- **THEN** `switchboard.thread_affinity_settings`'s existing `thread_affinity_ttl_days` and
  `thread_overrides` for `source_channel == "email"` are unchanged

### Requirement: Unavailable Pinned Target Falls Through

Affinity SHALL NOT dispatch to a pinned `target_butler` that is not currently registered and
available; such a hit SHALL be treated as a miss and the message SHALL proceed to LLM classification.

#### Scenario: Pinned butler removed from roster

- **WHEN** an affinity lookup would otherwise hit on a `target_butler` that is no longer present in
  the current butler registry
- **THEN** the lookup reports a miss and the message is classified fresh, exactly as an invalid
  `control.pinned_target` is rejected today rather than silently misrouted

### Requirement: Concurrent Turn Conflict Resolution

The affinity lookup SHALL report a miss and fall through to fresh LLM classification when more than
one distinct `target_butler` appears in a `chat_id`'s routing history within the TTL window (e.g.,
from two messages racing past the lookup before the first turn's route is recorded), self-healing to
a single new pin once that classification's route is recorded.

#### Scenario: Two distinct butlers in the window

- **WHEN** a `chat_id`'s routing history within the TTL window contains routes to two or more
  distinct butlers
- **THEN** the affinity lookup reports a miss (conflict) rather than guessing between them

#### Scenario: Conflict self-heals

- **WHEN** a conflicted `chat_id` is subsequently classified fresh and routed to a single butler
- **THEN** the next affinity lookup for that `chat_id` sees a single distinct butler in the window
  (once the conflicting older entry ages past the TTL, or immediately if only one route remains
  within it) and can report a hit again

### Requirement: Replay and Dedup Inherit Existing Ingest Idempotency

A redelivered or duplicate Telegram update SHALL NOT be treated as a new turn for affinity purposes;
this requirement adds no new dedup mechanism and relies entirely on the existing
`connector-telegram-bot` Idempotency and Safety guarantee.

#### Scenario: Duplicate update does not double-write affinity

- **WHEN** a Telegram update with an already-seen `control.idempotency_key`
  (`tg:<chat_id>:<message_id>`) is redelivered
- **THEN** it is deduplicated by Switchboard before triage or routing runs, and no second
  `routing_log` row or affinity-relevant routing decision is produced for it

### Requirement: Owner Correction Updates Affinity

The existing `correct_route` tool SHALL also update a chat's affinity pin to the corrected butler
when dispatching a misroute correction for a `telegram_bot`-channel request, so a subsequent message
in the same chat does not repeat the same misroute.

#### Scenario: Correction re-pins the chat

- **WHEN** `correct_route` successfully re-dispatches a `telegram_bot`-channel request's original
  message to `correct_butler`
- **THEN** the affinity state for that request's `chat_id` reflects `correct_butler` as the current
  pin, using the same write path an ordinary successful route already exercises

#### Scenario: Next message in the chat uses the corrected target

- **WHEN** a subsequent `telegram_bot` message arrives in a chat whose affinity was just updated by a
  correction, within the Telegram affinity TTL
- **THEN** the affinity lookup reports a hit on `correct_butler`, skipping LLM classification

### Requirement: Bounded Target-Side Routed Context

A deterministic, bounded conversation-history block SHALL be appended to the routed `route.v1`
envelope's `input.context` for every `telegram_bot`-channel route dispatched via
`route_to_butler`/`route.execute` — whether reached through LLM classification or an affinity bypass
— using the same 15-minute window and 30-message cap already normative for classifier-side realtime
history (`module-pipeline`'s Conversation History for Routing Context requirement). This block is
additive to any classifier-authored `context` string when classification ran, and is the target
butler's sole source of conversation continuity when classification was skipped.

#### Scenario: Additive to classifier-authored context

- **WHEN** a `telegram_bot` message is routed after LLM classification wrote its own `context` string
- **THEN** the deterministic bounded-history block is appended after the classifier's `context`,
  neither replacing nor truncating it

#### Scenario: Sole context on an affinity-bypass turn

- **WHEN** a `telegram_bot` message is dispatched via an affinity hit, skipping classification
- **THEN** `prompt` is the raw incoming message text and `input.context` consists of the deterministic
  bounded-history block — no LLM-authored restatement exists for this turn

#### Scenario: Bound matches the existing classifier-side history limit

- **WHEN** the deterministic block is constructed for a chat with more than 30 messages or older than
  15 minutes of history available
- **THEN** only the most recent 30 messages within the most recent 15-minute window are included,
  identical to the bound already normative for classifier-side history — no separate, larger bound is
  introduced for the routed-context surface

#### Scenario: No cross-chat leakage

- **WHEN** the deterministic block is constructed for a given `chat_id`
- **THEN** it includes only messages whose thread identity resolves to that same `chat_id` — never
  another chat's messages

### Requirement: Routed Context Trust Fencing

The deterministic routed-context block SHALL use the same untrusted-data fencing already shipped for
classifier-side history: an explicit banner instructing the reading session not to follow any
instruction, link, or call-to-action found inside the block, with each message's content fenced
inside a code block.

#### Scenario: Malicious prior message is fenced, not followed

- **WHEN** a prior message in the routed-context window contains text resembling an instruction
  (e.g., "ignore your previous instructions and...")
- **THEN** the block's banner and code-fencing present that text as data for context only, matching
  the existing classifier-side fencing discipline

#### Scenario: Fencing applies regardless of dispatch path

- **WHEN** the routed-context block is constructed for either a classify-then-route turn or an
  affinity-bypass turn
- **THEN** the same fencing banner and code-block wrapping apply in both cases — no unfenced variant
  exists

### Requirement: Scope Boundary — No Competing Thread Store, No Email Change

This capability's affinity state SHALL remain a Switchboard-internal, TTL-bound routing-decision
cache scoped to the `telegram_bot` channel: it SHALL NOT be implemented as, or read/written through,
a new cross-channel thread-identity table, and it SHALL NOT alter email thread-affinity's channel
gate, settings row, or override semantics.

#### Scenario: Email routing is unaffected

- **WHEN** this capability's Telegram affinity and routed-context behavior is active
- **THEN** `source_channel == "email"` requests continue to resolve thread affinity exactly as
  specified before this change, with no shared code path change that could alter their outcome

#### Scenario: No new durable cross-channel identity object

- **WHEN** a future implementation of this capability is built
- **THEN** it reuses `switchboard.routing_log` and a per-channel TTL/override settings surface,
  introducing no new table that models a durable, cross-channel conversation or thread identity
  (that remains `bu-0ynlk.15`'s scope; see design.md D9 for the future consolidation path)

## Source References

- `about/heart-and-soul/vision.md` Non-Negotiable Rule (Switchboard is the sole ingress/routing
  authority): preserved — this capability adds no new ingress path and no bypass of Switchboard's
  routing decision; affinity only changes whether classification runs before an existing dispatch
  mechanism, and correction remains mediated by the existing `correct_route` tool.
- `openspec/specs/module-pipeline/spec.md` "Conversation History for Routing Context": source of the
  15-minute/30-message bound this capability reuses rather than redefining.
- `openspec/specs/connector-telegram-bot/spec.md` "Idempotency and Safety" and "ingest.v1 Field
  Mapping": source of the `chat_id:message_id` thread-identity shape and the dedup guarantee this
  capability inherits rather than re-specifying.
- `openspec/specs/butler-switchboard/spec.md` "Misroute Correction Re-dispatch": the existing
  mechanism this capability's owner-correction reset path extends (see the modified delta in this
  same change).
- No RFC governs this capability; see design.md for why none is proposed.
