# Design — Decision Loop: One-Tap Approvals and Decision Memory

## Context

Full design rationale lives in RFC 0021
(`about/legends-and-lore/rfcs/0021-decision-loop-one-tap-approvals-and-decision-memory.md`).
This file records the implementation-shaping decisions and their trade-offs.

Existing substrate this change composes (all [Observed]):

- Gate wrapper + park path: `src/butlers/modules/approvals/gate.py` (park at
  ~705-746, dossier kwarg extraction at ~448-507).
- Shared executor: `executor.py` `execute_approved_action` (single execution
  path for auto- and manual approval; demotion hook at ~252-274).
- Ratchet: `autonomy_tracker.py` (`compute_fingerprint` ~34-90, threshold check
  ~117), `autonomy_suggestions.py` (confirm mints rule pinning representative
  args).
- Edit-aware approve endpoint: `api/routers/approvals.py` `approve_approval`
  (~2063, merges `edits` into `tool_args`).
- Outbound plane: `core_tools/_notifications.py` `notify()` → Switchboard
  `deliver` → Messenger; owner-channel resolution + quiet hours already exist
  (`core/approvals_policy.py`).
- Correlation primitives: `request_id` (UUID7), `external_thread_id`
  `<chat_id>:<message_id>`, thread affinity.

## Decisions

### D1 — Callback taps route deterministically to the decision surface, not through triage

A callback token is control-plane: zero routable content, nothing to classify.
The connector verifies owner identity (existing identity resolution against
verified owner channels) + HMAC, answers the callback, and calls the approvals
decision routes with actor `human:owner@telegram`.

- Rejected: `ingest.v1` → Switchboard triage → LLM session. Zero-reasoning LLM
  cost (RFC 0010 rationale) and adds latency to a UX whose whole point is
  immediacy.
- Rejected: connector calling butler MCP decision tools directly. The decision
  tools require an authenticated human actor via FastMCP token
  (`_require_authenticated_human_actor`); the dashboard decision routes are the
  existing human-decision surface with audit + dispatch built in. One decision
  surface, not two.
- Doctrine rule 7 holds: the approvals module sees a channel-agnostic
  authenticated decision; only the connector knows it was a Telegram tap.

### D2 — Callback token format

`apr1:<action_id>:<verb_char>:<hmac_16hex>`, HMAC-SHA256 (truncated 16 hex)
keyed by a daemon-internal secret over `(action_id, verb_char, requested_at)`.
`verb_char` is a single-character decision code (`a` = approve, `r` = reject),
so the token fits Telegram's 64-byte `callback_data` limit
(5+36+1+1+1+16 = 60). Owner-channel verification is the primary control; the
HMAC is defense-in-depth (forged/stale payloads, cross-instance replay). Tokens
die with the action (expired/decided → toast "already handled", keyboard
removed).

### D3 — Push is deterministic daemon infrastructure

The park path renders a fixed template of the dossier (tool, why, blast radius,
reversibility, expiry) into an `approval_request` notify envelope. No LLM
composes the message (vision.md rule 4). Burst collapse (>3 parks / 10 min →
digest) and quiet-hours deferral are daemon-side counters, not broker state;
approval requests deliberately do NOT share the insight broker's daily budget —
suppressing a time-boxed approval request because insights spent the budget
would silently convert "pending" into "expired = denied".

### D4 — Decision facts are upserted tallies, not per-decision episodes

Memory `facts` are stable triples with uniqueness on (entity, scope, predicate)
variants; decisions are events. Writing one fact per decision would collide or
spam. The writeback upserts a tally fact per `(fingerprint, entity)` —
predicate `decision:approval_tally`, metadata carries counts, last outcome,
last `action_id`, `fingerprint_version` — plus a `decision:standing_rule` fact
on rule mint/revoke. The immutable `approval_events` spine remains the event
log; facts are the recall-worthy summary with provenance pointers into it.
Writes go through the owning butler's own memory storage layer (same schema, no
Rule 3 exception). Predicates use the existing free-admission predicate
vocabulary (registry is advisory); templates normalize via
`normalize_predicate`.

### D5 — Fingerprint v2 scopes to safety-critical args, versioned

v2 = SHA-256 over `(tool_name, {arg: value for module-declared safety-critical
args})`; tools with no declared sensitivities fall back to all-args (v1
behavior, still recorded as version 2 with full-args basis). `fingerprint_version`
lands on `autonomy_approval_history` and `autonomy_suggestions`; counts and
threshold checks aggregate within a version only. Suggested rules pin exactly
the fingerprinted args — a v2 suggestion can never be broader than what its
fingerprint held constant, preserving the existing fail-closed
`_unpinned_safety_critical_args` invariant.

### D6 — `why` required at the gate, enforced with a retryable error

A gated non-owner-target call missing `_why` returns a structured error naming
the missing field instead of parking. The calling LLM session retries with the
dossier filled in. Rejected: park-anyway-and-flag — parks unexplainable actions
the owner then can't judge from a push message, defeating the loop's purpose.

## Risks / Trade-offs

- **New inbound surface (callback handler).** Bounded: owner-only, HMAC,
  single-purpose tokens, answerCallbackQuery-only side effects on failure.
  RFC 0017 review required in implementation PRs.
- **Gate hard-requiring `why` changes agent-visible behavior.** Sessions using
  gated tools without dossier kwargs will see errors until prompts/skills are
  updated; mitigation: precise error message + roster skill updates in the same
  epic.
- **Fingerprint v2 resets accumulation.** Existing v1 history stops counting
  toward suggestions (by design); the ladder restarts on v2 evidence. Acceptable
  because v1 effectively never accumulated.
- **Push fatigue.** Mitigated by burst collapse + quiet hours; if still noisy,
  the follow-up lever is the deferred risk classifier (push HIGH only), not
  disabling the loop.

## Test Strategy

- **Unit:** token mint/verify (tamper, expiry, wrong verb); fingerprint v2
  (declared vs undeclared sensitivities, version segregation); burst-collapse
  counter; dossier validation errors; tally-fact upsert idempotency.
- **Integration (testcontainers Postgres):** park → push envelope emitted;
  callback decision → status transition + audit + executor dispatch + message
  edit; terminal decision → memory fact present and entity-linked; promotion
  suggestion fires at threshold on varying-text same-recipient approvals.
  (Mocked-pool-only coverage is insufficient for the DB-query changes — run the
  real-Postgres integration suite.)
- **E2E (dev stack):** owner taps Approve in Telegram; action executes; message
  edits to ✅; dashboard shows decided_by `human:owner@telegram`.
