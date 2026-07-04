# Decision Loop: One-Tap Approvals and Decision Memory

## Why

The approvals subsystem already implements graduated autonomy's hard parts —
gate interception, durable `pending_actions`, standing rules with fail-closed
safety-arg pinning, and a human-confirmed promotion ladder (`autonomy_tracker`
+ `autonomy_suggestions`). But the loop is broken at both ends:

- **Decisions are invisible until sought.** When the gate parks an action, the
  owner learns of it only by visiting the dashboard. The Telegram module is
  output-only (no inline keyboards) and the bot connector silently drops
  `callback_query` updates, so the `[TARGET-STATE] Inline Approval Buttons`
  requirement in `module-telegram` is unreachable. In practice, expiry-as-denial
  is the default outcome not because the owner denied, but because they never
  saw the request.
- **Decisions evaporate.** Approve/reject outcomes land in `execution_result`
  and the immutable `approval_events` audit spine, but nothing is written to
  butler memory. The owner's revealed preferences — the one data stream no
  connector can ingest — never reach spawn-time recall.
- **The ratchet never fires.** `compute_fingerprint` hashes exact args, so
  "reply to mom" with varying text never accumulates toward a promotion
  suggestion. The ladder exists but is practically unreachable.
- **Proposals are unexplained.** `why`/`evidence` are optional; there is no
  structured blast-radius or reversibility metadata, so neither a push message
  nor the dashboard can render an honest decision dossier.

RFC 0021 records the owner dispositions (2026-07-02): human-confirmed ratchet
preserved, fingerprint generalized to safety-critical args, push-all with
budget gating, per-action risk classification deferred. RFC 0019's parked
automation engine stays parked; inline approval buttons were already catalogued
there as a non-doctrine-gated gap that strengthens per-event review.

## What Changes

- **Push on park.** The approvals gate's park path emits a deterministic
  `notify.v1` envelope with `intent = "approval_request"` through the standard
  Switchboard `deliver` → Messenger plane: dossier summary + decision
  affordances. One push per action, quiet-hours deferral, burst collapse into a
  digest.
- **One-tap Telegram decisions.** `module-telegram` gains inline-keyboard
  (`reply_markup`) support; the bot connector gains a narrow `callback_query`
  handler that verifies the tapper against verified owner channels, validates a
  signed single-purpose callback token, routes the decision deterministically to
  the approvals decision surface (no LLM, no triage), and edits the originating
  message to its resolved state.
- **Structured decision dossier.** `pending_actions` gains `blast_radius` and
  `reversibility` enums; `evidence` upgrades to typed references
  (fact/entity/url/text); `why` becomes required at the gate for
  non-owner-target calls (structured retryable error when missing).
- **Decision memory writeback.** On terminal decisions and rule
  creation/revocation, the approvals module writes deterministic templated
  facts (`decision:approval_tally`, `decision:standing_rule`) into the owning
  butler's own memory store, entity-linked when the target contact resolves,
  with action-id provenance.
- **Generalized promotion fingerprint (v2).** Fingerprint over
  `(tool_name, safety-critical args)` per module-declared arg sensitivities,
  falling back to all-args when none are declared; `fingerprint_version` column
  keeps v1 history from polluting v2 counts; suggested rules pin exactly the
  fingerprinted args.

## Capabilities

### Modified Capabilities

- `module-approvals` — dossier fields, required `why`, push-on-park, decision
  memory writeback.
- `module-telegram` — inline keyboard support; `[TARGET-STATE]` inline-approval
  requirement promoted to concrete.
- `connector-telegram-bot` — `callback_query` ingestion for approval decisions.
- `core-notify` — `approval_request` delivery intent with per-channel action
  rendering and fallback.
- `autonomy-tracker` — fingerprint v2 semantics and versioning.

## Impact

- **Schema:** new nullable columns on `pending_actions` (`blast_radius`,
  `reversibility`), `fingerprint_version` on `autonomy_approval_history` and
  `autonomy_suggestions` (approvals-chain migration; additive, legacy rows
  readable).
- **Code:** `src/butlers/modules/approvals/` (gate, executor, tracker, models),
  `src/butlers/modules/telegram.py`, `src/butlers/connectors/telegram_bot.py`,
  `src/butlers/core_tools/_notifications.py` + Messenger delivery tools,
  approvals dashboard API models (dossier surfacing).
- **Security surface:** new inbound callback path — bounded by owner-channel
  verification, HMAC tokens, single-purpose binding; reviewed under RFC 0017.
- **No new components, no cross-schema access, no LLM in the daemon.**

## Out of Scope

- Fully-automatic rule minting (auto-confirming promotion suggestions) —
  rejected by owner; would cross RFC 0019's doctrine line.
- Per-action dynamic risk classification (computing risk tier from the dossier)
  — deferred to a follow-up change; risk tier remains static per-tool config.
- Edit-in-Telegram flows — the Open button deep-links the dashboard, where the
  existing `edits`-aware approve endpoint lives.
- WhatsApp/email interactive buttons — those channels receive the summary plus
  a dashboard deep link.
- Un-parking RFC 0019's event-driven automation rule engine or revisiting the
  rejected calendar auto-responses.
- Insight-broker integration — approval requests are control-plane and do not
  share the insight daily budget.
