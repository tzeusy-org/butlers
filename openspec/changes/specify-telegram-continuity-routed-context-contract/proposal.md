# Specify Telegram continuity and routed-context contract

## Why

A 2026-09-06 `bu-27dxl.9` shaping pass reconciled the outcomes of an earlier Telegram-parity move
and left two of them "REMAINING" with concrete implementation beads already filed but blocked on
authority: `bu-p6v8k` ("Telegram sticky routing via pinned_target thread affinity") and `bu-d2ft5`
("Forward filtered conversation context to responding butler"). Both name a real, observed gap —
today, every Telegram message is re-classified by the Switchboard's LLM router from scratch
(`src/butlers/modules/pipeline.py`), which both re-runs classification the owner already implicitly
settled in the same chat and never gives the target butler anything beyond what that classifier
chose to write into `route_to_butler`'s free-text `context` argument
(`src/butlers/core_tools/_switchboard.py::route_to_butler`). Neither bead has an accepted design for
*how* affinity should behave (expiry, reset, fallback, concurrent turns) or *what* bounded context a
target butler may see, and `bu-27dxl.9`'s own design section flags exactly this: "Define stable
Telegram bot affinity... and bounded target-side context with trust fencing" as the OpenSpec-first
prerequisite both implementation beads need before either can be dispatched.

This is also the exact junction where a competing thread-identity store could accidentally get
built: `bu-0ynlk.15` ("Owner thread spine: cross-channel `conversation_threads` +
`conversation_channel_bindings`") is a separate, larger, currently-undispatched move that will
eventually own a durable cross-channel binding of `(channel, thread_identity, butler_name)` for
dashboard/Telegram handoff. If this contract invented its own independent notion of "which butler
currently owns this Telegram chat," the two would diverge the moment `.15` lands. This draft
therefore defines the affinity behavior `bu-p6v8k` needs as a **routing-decision fact** — reusing
the Switchboard's existing `switchboard.routing_log` / `thread_affinity_settings` machinery, already
proven for email — rather than a new schema, and names the exact point where a future implementation
should fold into `.15`'s binding once it exists (see design.md D9).

This change performs no implementation, no runtime, provider, or credential access, and no Beads,
repository merge, or deployment action beyond opening this draft PR.

## What Changes

- Add a new `telegram-continuity-routed-context` capability defining:
  - **Chat-keyed affinity**, generalizing the existing email-only `lookup_thread_affinity()` (
    `roster/switchboard/tools/triage/thread_affinity.py`) to the `telegram_bot` channel, keyed on
    the Telegram `chat_id` extracted from `event.external_thread_id` (which is `<chat_id>:
    <message_id>`, unique per message, not per conversation) — the same extraction the pipeline's
    own realtime-history loader already performs
    (`src/butlers/modules/pipeline.py::_load_realtime_history`).
  - Affinity outcomes for a new thread, a follow-up hit, TTL expiry, an unavailable pinned target,
    a conflicting concurrent turn, and a replayed/duplicate update — each reusing an existing
    outcome class from the email precedent (`AffinityOutcome`) rather than inventing new semantics,
    with a Telegram-appropriate TTL default distinct from email's 30-day default.
  - **Owner correction as reset**: rather than a new conversational command surface, this draft
    reuses the already-specified `correct_route` misroute-correction tool
    (`openspec/specs/butler-switchboard/spec.md` "Misroute Correction Re-dispatch") as the
    deterministic reset path — a target butler that recognizes a sticky-routed message does not
    belong to it already has a specified way to say so, and this draft extends that path's effect to
    also update the chat's affinity pin.
  - **Bounded, trust-fenced target-side context**: a deterministic (non-LLM-authored) block appended
    to the routed `route.v1` envelope's `input.context` for `telegram_bot`-channel routes, reusing
    the exact bound (15-minute window, 30-message cap) already normative for classifier-side history
    in `openspec/specs/module-pipeline/spec.md` "Conversation History for Routing Context," and the
    exact untrusted-data fencing pattern already shipped in
    `src/butlers/modules/pipeline.py::_format_history_context`. This is additive to, never a
    replacement for, the classifier-authored `prompt`/`context` when classification ran; when
    affinity bypasses classification entirely, it is the target butler's only source of continuity.
- Modify `butler-switchboard`'s existing **Misroute Correction Re-dispatch** requirement to state,
  without dropping any existing clause or scenario, that a correction on a `telegram_bot`-channel
  request also updates that chat's affinity pin to the corrected butler.
- No new RFC: this is a capability-spec-level contract, matching how the existing email
  thread-affinity and dashboard sticky-routing behaviors were both introduced without a dedicated RFC.
- Explicit non-goals: no unified cross-channel ledger or handoff (owned by `bu-0ynlk.15`, named as a
  future reconciliation point, not duplicated here); no raw/unbounded transcript forwarding; no
  change to email thread-affinity's channel gate, TTL, or override semantics; no new Telegram bot
  command grammar; no code, migration, runtime, or provider effect.

## Capabilities

### New Capabilities

- `telegram-continuity-routed-context`: chat-keyed affinity (lookup, TTL, fallback, conflict,
  replay), the correction-driven reset path, and the bounded/fenced target-side context contract.

### Modified Capabilities

- `butler-switchboard`: the Misroute Correction Re-dispatch requirement gains a Telegram-affinity
  update scenario (additive to its existing re-dispatch, retention, and audit-trail scenarios).

## Impact

- Affected future code (not touched by this draft): `roster/switchboard/tools/triage/
  thread_affinity.py` (generalize the `source_channel == "email"` gate to a per-channel dispatch
  with per-channel TTL/override settings), `roster/switchboard/tools/ingestion/ingest.py` (insert the
  Telegram affinity check into the existing deterministic pipeline alongside the email lookup, before
  LLM classification), `src/butlers/core_tools/_switchboard.py::_dispatch_dashboard_target` (the
  seam that already deterministically appends the dashboard confirm-block to `input.context`
  regardless of classifier prose — the new routed-context block follows the identical pattern),
  `roster/switchboard/tools/routing/correct_route.py` (extend its post-re-dispatch effect for
  `telegram_bot`-channel corrections), and `switchboard.thread_affinity_settings` /
  `switchboard.routing_log` (both already channel-generic; no new table).
- Affected RFCs: none.
- `bu-p6v8k` and `bu-d2ft5` disposition: both remain open and blocked on this contract, per
  `bu-27dxl.9`'s explicit sequencing. This draft is the prerequisite their eventual implementation(s)
  must satisfy; per `bu-27dxl.9`'s cohesion-scan guidance, the two share enough surface (thread
  identity, ingest/pipeline interface, route envelope) that a future implementer should default to
  one cohesive carrier unless this contract's affinity and routed-context halves prove independently
  rollback-safe.
- `bu-0ynlk.15` disposition: unaffected and not duplicated. Its `conversation_channel_bindings` is a
  durable cross-channel identity object for dashboard/Telegram handoff; this draft's affinity pin is
  a Switchboard-internal routing-decision cache scoped to `telegram_bot` only. Design.md D9 names the
  future consolidation path once `.15` lands; this draft does not block on or implement it.
- No implementation, runtime, provider/account/credential/data access, message delivery, activation,
  deployment, or merge is performed or authorized by this change. Expected tests: `+0 ~0 -0`.
