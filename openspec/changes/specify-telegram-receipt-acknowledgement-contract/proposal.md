# Specify Telegram receipt acknowledgement controls

## Why

`bu-2an2p` ("Telegram instant receipt acknowledgment") asks for one thing owners actually feel:
close the 10-60s dead-air gap between sending the bot a message and seeing any sign it is being
worked on. Its acceptance criteria name six concrete bounds: a typing indicator within 1 second of
receipt, a 4-second keepalive resend, no orphaned indicators after completion, reaction removal on
arrival, a configurable ack style (typing/reaction/both/none), and owner-only filtering.

A 2026-09-06 `bu-27dxl.9` shaping pass proposed closing `bu-2an2p` as "covered" by the
lifecycle-reaction feature already shipped in `module-telegram` (`react_for_ingest`, called from
Switchboard's ingest/buffer wiring before and after `pipeline.process()` to fire an eyes reaction
that is replaced by a checkmark or alien reaction on terminal success/failure). The binding
coordinator review (`coordinator-review.md`,
`/home/tze/.local/share/butlers/coordinator-evidence/run6-shaping-20260906/`) rejected that closure:
the shipped feature proves *a* receipt-acknowledgement mechanism exists, but proves nothing about
the exact 1-second typing SLA, the 4-second keepalive, or a configurable typing mode — `bu-2an2p`
remains open with all six criteria intact, "no SLA or configuration clause is dropped."

This change is the OpenSpec-first prerequisite `bu-27dxl.9` requires before any implementation of
`bu-2an2p` (or a successor) proceeds. It defines the exact receipt-acknowledgement contract —
timing bound, keepalive cadence, terminal cleanup, configurable ack style, and owner-only
filtering — and draws the ownership line the existing reaction feature never had to draw because it
was never configurable or timing-bound: **which layer (transport connector, Telegram module, or
Switchboard's core ingest wiring) owns dispatch, orchestration, and cleanup**, and **what this
contract can honestly promise** — a bounded local dispatch attempt, never a guarantee that Telegram
renders or delivers the indicator within any fixed window. It performs no implementation, no
runtime, provider, or credential access, and no Beads, repository merge, or deployment action
beyond opening this draft PR.

## What Changes

- Add a new `telegram-receipt-acknowledgement` capability defining: the ack-style configuration
  surface (`typing` / `reaction` / `both` / `none`, additive to `[modules.telegram]`), the
  1-second local-dispatch timing bound for the typing indicator (a bounded attempt, not a delivery
  guarantee), the 4-second typing keepalive cadence (grounded in the Telegram Bot API's documented
  ≤5-second typing-status duration), per-chat keepalive reference counting for concurrent
  in-flight owner messages in the same chat, terminal cleanup semantics for both ack styles, and
  owner-only filtering via a fast, pipeline-independent identity lookup so the ack is never gated
  on (or delayed by) LLM classification.
- Add a **Typing Indicator Dispatch for Ingest Lifecycle** requirement to `module-telegram`:
  a new pair of primitives (start/stop a per-chat `sendChatAction` keepalive loop) parallel to the
  existing `react_for_ingest`, and a new **Receipt Acknowledgement Style Configuration**
  requirement adding the `[modules.telegram.ack]` config surface. Both are purely additive — the
  existing Telegram Send/Reply Tools, TelegramConfig, and Lifecycle Reaction Emoji Support
  requirements are untouched.
- Modify `connector-telegram-bot`'s existing **Lifecycle Reactions** requirement to state, without
  dropping any existing clause or scenario, that reaction dispatch is now gated by the configured
  ack style and the owner-only filter this capability defines — reconciling the normative conflict
  between "reactions apply to an ingested message" (baseline, unconditional) and the new
  configurable/gated behavior this change requires.
- Reconcile `bu-2an2p` criterion 4 ("reaction emoji is removed when response arrives") against the
  already-shipped behavior (the in-progress reaction is *replaced* by a terminal thumbsup/alien
  reaction, never cleared to no-reaction): this draft keeps the shipped, strictly more informative
  terminal-replacement behavior as the specified contract rather than reverting to literal removal,
  and records that generalization explicitly (design.md D5) so it does not read as a silently
  dropped criterion.
- No new RFC: this generalizes `module-telegram`/`connector-telegram-bot` the same way the existing
  reaction lifecycle was introduced — as a capability-spec addition, with no RFC of its own.

## Capabilities

### New Capabilities

- `telegram-receipt-acknowledgement`: the ack-style contract (config, timing bound, keepalive,
  terminal cleanup, owner-only filtering, ownership boundary between transport/module/core wiring).

### Modified Capabilities

- `connector-telegram-bot`: the Lifecycle Reactions requirement gains ack-style/owner-only gating
  (additive to its existing unconditional emoji-mapping and failure-handling scenarios).
- `module-telegram`: adds typing-indicator dispatch primitives and the ack-style config surface
  (both `## ADDED Requirements`, no existing requirement modified).

## Impact

- Affected future code (not touched by this draft): `src/butlers/modules/telegram.py` (new
  `begin_typing_for_ingest`/`end_typing_for_ingest` methods and `ack` field on `TelegramConfig`),
  `src/butlers/switchboard_wiring.py` and `src/butlers/core_tools/_switchboard.py` (the two
  existing pre/post-`pipeline.process()` reaction call sites gain the ack-style branch and the
  owner-only gate), and a new or reused fast owner-identity lookup call
  (`butlers.identity.resolve_owner_channel_via_definer` is the existing precedent for this exact
  kind of pipeline-independent owner check).
- Affected RFCs: none. This is a capability-spec-level contract, matching how the existing
  lifecycle-reaction feature was introduced without a dedicated RFC.
- `bu-2an2p` disposition: remains open, per the binding coordinator review. This draft is the
  prerequisite its eventual implementation must satisfy; all six original acceptance criteria are
  preserved (criterion 4 generalized per D5, not dropped).
- No implementation, runtime, provider/account/credential/data access, message delivery,
  activation, deployment, or merge is performed or authorized by this change. Expected tests:
  `+0 ~0 -0`.
