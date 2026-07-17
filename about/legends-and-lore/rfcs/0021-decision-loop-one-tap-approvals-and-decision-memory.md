# RFC 0021: Decision Loop — One-Tap Approvals and Decision Memory

**Status:** Accepted (owner sign-off 2026-07-17, gate bu-24lu6.1)
**Date:** 2026-07-02
**Related:** RFC 0011 (insight anti-spam patterns), RFC 0017 (owner-routing safety),
RFC 0019 (proactive egress dispositions), `about/heart-and-soul/security.md`
(Approval Gates), OpenSpec change `decision-loop-one-tap-approvals`

---

## Summary

The approvals subsystem already implements the hard parts of graduated autonomy:
gate interception, a durable pending-actions queue, standing rules with
fail-closed safety-arg pinning, and a human-confirmed promotion ladder
(`autonomy_tracker` + `autonomy_suggestions`). What it lacks is a usable decision
surface and a knowledge dividend. Today the owner learns of parked actions only
by visiting the dashboard, and every decision they make evaporates into audit
tables that no butler session can recall.

This RFC defines the **decision loop**: parked actions push a one-tap
approve/reject/edit message to the owner over Telegram; taps route back
deterministically to the approvals decision surface; every terminal decision is
written into butler memory as an entity-linked decision fact; and the promotion
fingerprint is generalized so the existing ratchet actually accumulates evidence
for real-world action patterns.

Owner dispositions (2026-07-02) recorded here:

- **Human-confirmed ratchet** — standing rules continue to require explicit
  owner confirmation of each promotion suggestion. Fully-automatic rule minting
  remains out of scope (consistent with RFC 0019's parked automation engine,
  which this RFC does **not** un-park).
- **Fingerprint generalized to safety-critical args** — so repeated approvals of
  the same action pattern (same recipient, varying text) accumulate toward a
  suggestion.
- **Push all parked actions, budget-gated** — every park pushes a one-tap
  message, subject to quiet hours, per-action dedup, and burst collapse.
- **In scope:** one-tap Telegram, decision→memory writeback, structured dossier.
  **Deferred:** per-action dynamic risk classification (risk tier stays static
  per-tool config for now).

## Doctrine position

RFC 0019 drew the line at "acting or messaging on the owner's behalf without
per-event review." Everything in this RFC is on the sanctioned side of that
line:

- **One-tap inline approval buttons** were explicitly catalogued by RFC 0019 as
  a non-doctrine-gated gap that *strengthens* the per-event approval path
  (plan §7.1, folded into `module-telegram` as a `[TARGET-STATE]` requirement).
  This RFC concretizes that requirement; it does not create new egress
  authority.
- **Decision memory writeback** is knowledge capture with zero egress. It gives
  future LLM sessions recall of the owner's revealed preferences; it grants no
  new permission to act on them.
- **The promotion ladder is unchanged in authority**: suggestions are created
  deterministically, and only an authenticated human decision mints a standing
  rule. Approval gates remain non-bypassable by LLM sessions; timeouts remain
  denials (security.md, Approval Gates).
- The **event-driven automation rule engine stays parked** and calendar-based
  auto-responses stay rejected, exactly as RFC 0019 records.

## Design

### 1. Push on park (approvals → owner)

When the gate parks a pending action (status `pending`, no matching rule, no
owner-role bypass), the daemon constructs a `notify.v1` envelope with
`intent = "approval_request"` and submits it through the standard Switchboard
`deliver` → Messenger plane. This is deterministic daemon infrastructure — a
templated summary of the dossier (tool, why, blast radius, reversibility,
expiry), never an LLM session.

Anti-spam is control-plane-appropriate (approval requests are time-sensitive, so
they do not share the insight broker's daily budget; they borrow its shape):

- **One push per action** (dedup key = `action_id`), edits/retries never re-push.
- **Quiet hours** follow the existing approvals quiet-hours policy
  (`core/approvals_policy.py`): pushes are deferred, not dropped; the pending
  queue and expiry clock are unaffected.
- **Burst collapse**: when more than 3 actions park within a 10-minute window,
  subsequent pushes collapse into a single digest message ("N actions awaiting
  review") deep-linking the dashboard, until the burst window closes.

Channel fallback: Telegram renders inline buttons; channels without interactive
affordances (email, WhatsApp today) receive the same summary plus a dashboard
deep link.

### 2. One-tap decision (owner → approvals)

`module-telegram` gains `reply_markup` (inline keyboard) support on send/reply.
An approval-request message carries buttons: **Approve**, **Reject**, and
**Open** (dashboard deep link, which is where edit-then-approve lives — the
`edits`-aware approve endpoint already exists).

`callback_data` is an opaque signed token bound to the action:
`apr1:<action_id>:<verb_char>:<hmac_16hex>` (fits Telegram's 64-byte limit:
5+36+1+1+1+16 = 60 bytes), where `verb_char` is a single-character decision
code (`a` = approve, `r` = reject). The HMAC is keyed by a daemon-internal
secret over `(action_id, verb_char, requested_at)`. Tokens are single-purpose
and die with the action.

The Telegram bot connector, which today drops `callback_query` updates, gains a
narrow handler:

1. Verify the tapping user's chat resolves to a **verified owner channel** via
   the existing identity resolution (primary defense; the HMAC is
   defense-in-depth against forged/stale payloads).
2. `answerCallbackQuery` immediately (UX ack).
3. Deliver the decision to the approvals decision surface (the dashboard API's
   approve/reject routes) with actor identity `human:owner@telegram`, which
   audit-logs and dispatches via the standard executor.
4. Edit the originating message to reflect the resolved state (✅ Approved /
   ❌ Rejected / ⏰ Expired), removing the keyboard.

**Deterministic routing rationale (trade-off).** Callback taps bypass Switchboard
triage deliberately: a decision token is control-plane, carries zero routable
content, and needs no classification. Routing it through an LLM session would be
exactly the zero-reasoning LLM cost RFC 0010 exists to avoid; routing it through
deterministic Switchboard code would add a hop with no isolation benefit, since
the connector already terminates transport and the decision endpoint already
enforces actor authentication and audit. Doctrine rule 7 is preserved: butlers
still never see transport — the connector normalizes the tap into a channel-
agnostic decision call; the approvals module sees only "an authenticated human
decided."

Failure modes: expired/already-decided action → callback answered with a
non-destructive toast ("already handled"), message updated; unknown or
HMAC-invalid token → answered generically, logged, no state change; non-owner
tapper → ignored and logged (RFC 0017 owner-routing safety applies).

### 3. Structured decision dossier

`pending_actions` gains structured risk metadata alongside the existing
`why`/`evidence`:

- `blast_radius TEXT` — enum `none | self | contact | external` (who is affected
  if this executes).
- `reversibility TEXT` — enum `reversible | compensable | irreversible`.
- `evidence` upgrades from free strings to typed references:
  `[{"type": "fact|entity|url|text", "ref": "<id-or-url>", "note": "<string>"}]`
  (legacy string entries are migrated once into typed `text` entries; new
  runtime inputs are strictly validated and are not silently coerced).
- `why` becomes **required at the gate** for non-owner-target gated calls: a
  gated invocation missing `_why` receives a structured retryable error naming
  the missing field, instead of parking an unexplained action. (Owner-role
  auto-approved calls are exempt — no dossier is displayed for them.)

The dossier is what the push message renders and what the dashboard detail
shows. Risk *tier* remains static per-tool config; the dossier feeds a future
per-action classifier (deferred, see dispositions).

### 4. Decision memory writeback

On every terminal decision — `rejected`, or `approved` + execution outcome —
the approvals module writes **deterministic, templated facts** into the owning
butler's memory store (no LLM; templates are daemon infrastructure, reasoning
stays in sessions):

- **Decision tally fact** (upserted per pattern): subject = human-readable
  pattern descriptor (tool + pinned safety-critical args), predicate
  `decision:approval_tally`, `entity_id` linked to the resolved target contact
  entity when channel identity resolves, metadata
  `{approve_count, reject_count, last_decision, last_action_id,
  fingerprint, fingerprint_version}`. Repeated decisions update the tally —
  facts are stable triples, decisions are events; the tally is the recall-worthy
  summary with action-id provenance for drill-down.
- **Autonomy grant fact** (on standing-rule creation/revocation): predicate
  `decision:standing_rule`, describing the granted/revoked autonomy scope with
  the rule id.

These facts flow into spawn-time memory context via the existing recall
machinery, closing the loop: a butler drafting its next proposal can see "owner
has approved this pattern 7×, rejected similar-but-external 2×."

This writes only to the **owning butler's own schema** via its own memory
module — no cross-schema access, no Rule 3 exception needed.

### 5. Generalized promotion fingerprint

`compute_fingerprint` v2 hashes `(tool_name, safety-critical args only)`, where
safety-critical args are the module-declared `ToolMeta.arg_sensitivities` the
rule engine already pins fail-closed. Tools declaring no safety-critical args
fall back to all-args hashing (current behavior). `autonomy_approval_history`
and `autonomy_suggestions` gain `fingerprint_version`; counts aggregate only
within a version, so v1 history neither pollutes nor inflates v2 patterns.
Promotion suggestions minted from v2 fingerprints pin exactly the
safety-critical args — the suggested rule can never be broader than what the
fingerprint held constant.

## Rejected alternatives

- **Auto-minting rules after N approvals** — rejected (owner, 2026-07-02);
  crosses RFC 0019's line and inverts security.md's per-event-review default.
- **Routing callback taps through Switchboard ingestion/triage** — rejected;
  zero-reasoning LLM cost (RFC 0010 rationale) and no isolation benefit.
- **Modeling auto-approve policy as memory rules** — rejected; memory `rules`
  are free-text behavioral heuristics for LLM guidance and cannot gate
  execution. The deterministic `approval_rules` engine is the enforcement home;
  memory receives descriptive facts *about* grants, not the grants themselves.
- **Per-decision episodes instead of tally facts** — rejected as noise; the
  audit spine (`approval_events`, immutable) already holds the event log.

## Consequences

- The owner's decision latency drops from "next dashboard visit" to one tap;
  expiry-as-denial becomes a deliberate choice rather than the default outcome.
- The knowledge graph gains its first ground-truth revealed-preference stream,
  with provenance to the immutable audit spine.
- The existing ratchet becomes practically reachable (generalized fingerprint),
  while remaining human-confirmed.
- New attack surface (callback ingestion) is bounded by owner-channel
  verification + HMAC + single-purpose tokens, and reviewed under RFC 0017.
