# Design: generic Telegram confirm-interaction contract

## Context

`bu-ow5a4`'s original design already flagged the risk this draft exists to resolve:
"COLLISION NOTE: bu-24lu6.4/.5 are active work on inline-keyboard primitives. This task
depends on or supersedes those — coordinate before starting." What shipped
(`bu-24lu6.4`/PR #3381, `bu-24lu6.5`/PR #3394, RFC 0021, `decision-loop-one-tap-approvals`)
is a complete, sealed wire contract for exactly one thing: turning a `pending_actions` row
into an owner-tappable approve/reject decision. Its callback token
(`apr1:<action_id>:<verb_char>:<hmac>`) encodes a verb from a two-value alphabet
(`a`/`r`), its identity check is "does the tapper resolve to a verified **owner** channel"
(approvals are always owner-decided), and its resolution path calls directly into the
approvals decision surface, which performs an **audited state transition and dispatches
execution** via the standard approved-action executor.

None of that is reusable for a generic confirm. A generic `notify(intent="confirm", ...)`
caller is not proposing a tool invocation for approval — it is asking an arbitrary
yes/no/multiple-choice question ("Which restaurant?", "Should I mark this task done?",
"Reply within 10 minutes or I'll assume no") and wants the chosen answer delivered back to
itself as ordinary information, to reason about on its own. Reusing `apr1:`'s callback
format, HMAC key, or resolution path would either (a) require every confirm answer to flow
through the approvals executor — which would silently grant confirm answers the power to
approve/reject unrelated pending actions, the exact hazard `bu-5iieo`'s design field warns
against ("never turn arbitrary confirm text into effect approval") — or (b) require
`pending_actions` to model non-approval semantics it was never designed for
(`module-approvals`'s spec ties every row to `tool_name`/`tool_args`/`execution_result`).
Both are worse than a second, narrower, purpose-built mechanism.

The `cgi:` gap-interview callback prefix (`connector-telegram-bot`'s "Update Type Handling"
requirement) is the closer precedent: it is *also* a second, additive `callback_query`
prefix living alongside `apr1:`, routed to its own resolve endpoint
(`POST /api/chronicler/gap-interview/resolve`), with its own acknowledgement/toast
behavior. This draft follows that precedent's *shape* — a third, additively-recognized
prefix — while diverging from its *routing target*: gap-interview resolution is genuinely
control-plane (it never becomes conversation), whereas a confirm answer is conversational
content the origin butler should reason about like any other owner turn (see D6).

## Goals and non-goals

Goals:

- Define one wire contract for `notify(intent="confirm")`: envelope shape, a bounded
  option list, timeout bounds, and channel fallback for non-interactive channels.
- Define authenticated callback identity that does not assume the confirm's recipient is
  always the system owner (generalizing RFC 0021's owner-only check).
- Define replay fencing (a captured/resent callback_data can resolve a confirm at most
  once) and expiry semantics (an unanswered confirm reaches a terminal, origin-visible
  outcome rather than hanging forever).
- Define reply-to-origin delivery that reuses existing routing primitives
  (`IngestControlV1.pinned_target`) instead of inventing a new session-spawn mechanism.
- Define concurrent-confirm independence: two or more open confirms in the same chat
  resolve independently, keyed only by their own `confirm_id`.
- Make the separation from approval authority a normative, testable boundary, not a prose
  aspiration: name the exact code paths the confirm-resolution handler MUST NOT call.
- Preserve plain-`notify()` compatibility: zero behavior change for any call that does not
  request `intent="confirm"`.

Non-goals:

- No implementation, migration, runtime process, or live Telegram API call. Nothing here
  executes.
- No change to RFC 0021, `pending_actions`, the approvals executor, or the `apr1:`/`cgi:`
  callback formats.
- No email (or other non-interactive-channel) automatic answer capture. A confirm sent over
  email renders as prompt text with reply instructions; parsing a reply email back into a
  chosen option is explicitly out of scope for this contract (no channel today gives email
  the structured tap affordance Telegram's inline keyboard gives).
- No ownership of Telegram thread-affinity/target-reuse (`bu-n3dn7`'s separate contract) or
  voice-note transcription (`bu-w6acf`'s separate contract). This draft reuses
  `pinned_target` for exactly one reentry event per confirm; it does not define, extend, or
  duplicate general sticky-routing behavior.
- No new generic "ask the owner a question and block until answered" synchronous API for
  butler code — the origin butler's own session ends normally after calling `notify()`;
  the answer arrives later as a new, ordinary triggered turn (see D6). A future
  synchronous-wait primitive, if ever wanted, is separate, later-authorized work.
- No multi-recipient / broadcast confirm (single resolved recipient per confirm, matching
  `notify()`'s existing single-recipient resolution model).

## Decisions

### D1: A new domain-separated callback prefix and a new RFC, not an RFC 0021 amendment

`cfm1:` is a wholly new `callback_data` prefix, checked as an independent, mutually
exclusive branch alongside the existing `apr1:` and `cgi:` prefixes (a given `callback_data`
matches at most one). It uses its own token shape (`cfm1:<confirm_id>:<opt_idx>:<hmac>`,
where `opt_idx` is a decimal option index rather than `apr1:`'s single-character verb code,
since confirm's option alphabet is caller-defined and unbounded-by-two) and its own durable
record (`public.pending_confirms`, D3) rather than a new column shape bolted onto
`pending_actions`.

If accepted, a future implementation PR should record this as a **new RFC** (the next free
number after RFC 0023 — i.e. RFC 0024) rather than an RFC 0021 amendment, because RFC 0021's
own doctrine section is specifically about approval-gate strengthening ("One-tap inline
approval buttons... strengthens the per-event approval path"); this contract's doctrine
position is different and needs its own statement (D7): a confirm answer carries zero
execution authority, so it is not a per-event-review strengthening mechanism at all, and
conflating the two RFCs would make a future reader assume confirm answers inherit RFC
0021's approval semantics. This draft does not create that RFC file (no RFC is amended or
added by this change); it only records the numbering/authorship decision here for whoever
implements the accepted contract.

### D2: Envelope shape — bounded options, explicit or default timeout, no required thread context

`notify(intent="confirm", options=[...], message=..., timeout_seconds=?, request_context=?)`.

- `options`: a list of 2-8 `{value: str, label: str}` entries. Lower bound 2 (a
  single-option "confirm" is not a choice); upper bound 8 is a UX/message-legibility cap,
  not the wire format's own ceiling (the `cfm1:` token's 2-digit `opt_idx` supports up to
  100 — see D3 byte budget). `value` is the caller-defined machine-readable answer (what
  the origin butler receives back); `label` is the button text.
- `timeout_seconds`: optional, default 600 (10 minutes), bounded `[30, 86400]` (30 seconds
  to 24 hours). Unlike `approval_request`'s decision-latency assumption (a busy owner might
  not open the dashboard for hours, so pending actions default to a 72-hour expiry per the
  approvals module), a generic confirm is typically part of a live, turn-based exchange
  the origin butler is actively waiting on; a much shorter default keeps that exchange from
  stalling indefinitely while still tolerating "let me check" gaps. This is an
  engineering-allocation choice (default/bounds only), not a product/privacy decision, so
  it is decided here rather than deferred.
- `on_timeout_value`: optional caller-supplied `value` (must match one of `options`) that
  the reentry synthesizes if no tap occurs before expiry. When omitted, expiry synthesizes
  a distinct `{"outcome": "expired", "value": null}` reentry instead of guessing a "declined"
  option that was never actually offered (see D5) — this generalizes, rather than silently
  drops, `bu-ow5a4`'s "expired confirms are treated as declined" criterion: a caller that
  wants exactly that legacy behavior sets `on_timeout_value` to its own decline-equivalent
  option's `value`; a caller with no natural decline option (e.g. "pick a restaurant: A/B/C")
  gets an honest "no answer" outcome instead of a fabricated one.
- `request_context`: optional (unlike `reply`/`react`, which require it). Reply-to-origin
  routing (D6) is keyed on `origin_butler` alone — a field every `notify()` envelope already
  carries automatically — not on `request_context`. When present, it is carried through and
  MAY be used by a future implementation to thread the reentry into the same Telegram chat
  history the confirm was asked in; its absence never blocks resolution or reentry.
- Channel scope: `telegram` renders as an inline keyboard (D4). `email` (or any other
  channel the base `notify()` contract may add later) renders the prompt as plain text
  listing each option's label, with no automatic reply-capture — the notify response for a
  non-interactive channel reports `status="ok"` with no `confirm_id` usable for resolution,
  exactly like `approval_request`'s non-interactive fallback.

### D3: Durable record lives in `public.pending_confirms`, not a per-butler schema

Unlike `pending_actions` (owned per-butler, inside each butler's own schema, because an
approval decision is that butler's own audited authority to exercise), a `pending_confirms`
row carries no privileged authority — it is routing/state data connecting a Telegram
message to an eventual reentry event. It is modeled as a **new cross-butler table in
`public`**, alongside `public.attention_ledger` and `public.entities`, exactly matching
this repository's stated convention ("cross-butler tables in public, each role sees only
its schema plus public" — `CLAUDE.md`). This avoids the indirection `pending_actions`
needs (a connector outside any butler's schema calling a dashboard API that itself knows
how to reach the right butler's own table) — the connector (or the dashboard API route it
calls) reads/writes `public.pending_confirms` directly, keyed by `confirm_id`.

Columns (illustrative; exact migration is future implementation work, not specified to the
byte here): `confirm_id UUID PK`, `origin_butler TEXT`, `channel TEXT`, `recipient TEXT`
(the resolved chat/address the confirm was sent to), `options JSONB` (the full
`{value, label}` list, so resolution never has to re-derive it), `message TEXT`,
`request_context JSONB NULL`, `on_timeout_value TEXT NULL`, `status TEXT` (`pending` /
`answered` / `expired`), `chosen_value TEXT NULL`, `decided_at TIMESTAMPTZ NULL`,
`created_at TIMESTAMPTZ`, `expires_at TIMESTAMPTZ`, `telegram_chat_id`/`telegram_message_id`
(for the edit-on-resolution call).

Resolution is a single atomic statement:
`UPDATE public.pending_confirms SET status='answered', chosen_value=$1, decided_at=now() WHERE confirm_id=$2 AND status='pending' RETURNING *`.
Zero rows returned means the confirm was already answered or expired — the handler answers
the tap with a non-destructive "already handled" toast and performs no further action,
exactly mirroring RFC 0021's "Expired or already-decided action" scenario. This single
`WHERE status='pending'` guard is the entire replay-fencing mechanism at the state layer —
no separate lock table or advisory lock is needed.

### D4: `cfm1:` token format and byte budget

`cfm1:<confirm_id>:<opt_idx>:<hmac_16hex>` — `cfm1:` (5 bytes) + UUID `confirm_id` (36
bytes) + `:` (1) + `opt_idx` (1-2 decimal digits) + `:` (1) + 16-hex-character HMAC
truncation (16 bytes) = 60-61 bytes, within Telegram's 64-byte `callback_data` limit with
the same margin `apr1:` uses (60/64) and room for a 2-digit index (options 0-99, well above
the 8-option UX cap in D2).

The HMAC is computed over a **domain-tagged** message,
`"cfm1|<confirm_id>|<opt_idx>|<requested_at>"`, reusing the same daemon-internal
callback-signing secret RFC 0021 already provisions (no new secret-provisioning surface),
but the literal `"cfm1|"` domain tag makes the signed bytes for a confirm token
structurally different from `apr1:`'s `(action_id, verb_char, requested_at)` message even
under a shared key — a valid `apr1:` token can never verify as a `cfm1:` token or vice
versa, and there is no cross-domain replay even if the shared secret were ever
compromised in one domain's request path before the other's (defense-in-depth, not the
primary control — see D5 for the primary one).

### D5: Authenticated callback identity — recipient match, not owner-only

RFC 0021's approval callbacks check "does the tapper resolve to a verified **owner**
channel" because every `pending_actions` decision is, by definition, the owner's to make.
A generic confirm has no such universal constraint: `notify(intent="confirm", entity_id=X,
...)` can address any resolved recipient `notify()` already supports, not only the owner.
The correct generalization is **not** "verify the tapper is the owner" — it is "verify the
tapper is looking at the exact chat the confirm was sent to."

Primary defense: the resolution handler compares the tapping update's `chat.id` against
`pending_confirms.telegram_chat_id`, recorded at send time from the same recipient
resolution `notify()` already performs for `send`/`reply`. An exact match is the whole
identity check — Telegram itself guarantees that only a participant with access to a given
chat can generate a `callback_query` update for a message inside it, so a chat-id match is
already a real identity assertion, not merely a convenience lookup. This subsumes the
owner case for free (an owner-targeted confirm's `telegram_chat_id` is the owner's own
verified chat) without hardcoding an owner-only check that would incorrectly reject every
non-owner-targeted confirm.

Defense-in-depth: the HMAC (D4) still MUST validate before any database read — a
syntactically well-formed but forged/tampered `cfm1:` token is rejected before it can
probe for a `confirm_id`'s existence, exactly mirroring RFC 0021's "Invalid or tampered
token" scenario.

A mismatched `chat.id` (a tap arriving on a copy/relay of the message content, or a
group-chat member who is not the resolved recipient) is treated identically to RFC 0021's
"Non-owner tap is ignored": the callback is answered generically, the event is logged, and
no state changes occur.

### D6: Reply-to-origin is conversational reentry via `ingest.v1` + `pinned_target`, not a control-plane bypass

This is the decision that most needs to be explicit, because it is the opposite choice
from `apr1:`/`cgi:`, and the difference is exactly why a generic confirm cannot borrow
either mechanism's routing.

`apr1:` and `cgi:` are deterministic **control-plane** signals: a decision token carries no
routable content and needs no reasoning, so both bypass `ingest.v1`/Switchboard triage
entirely and call a decision surface directly (RFC 0021 §2's "Deterministic routing
rationale"). A confirm answer is different in kind: "the owner picked *Thai food*" or "the
owner said *not this week*" is conversational content the origin butler's own session
needs to reason about, in context, exactly as if the owner had typed it. Routing it through
a bespoke control-plane callback would either force the origin butler to poll a new,
confirm-specific state table (a second reentry mechanism next to its normal
trigger/session pipeline) or require inventing a way to "wake" a specific already-ended
session — this repository's runtime spawns an ephemeral LLM CLI per trigger and does not
keep sessions alive awaiting async replies (`docs/architecture/butler-daemon.md`).

Instead, resolution constructs one ordinary `ingest.v1` envelope:
`source.channel`/`source.provider` = the resolved delivery channel (`telegram`/`telegram_bot`
for a telegram confirm), `sender.identity` = the resolved recipient's channel identity (the
same verified chat identity D5 just matched) for an answered confirm, or a fixed
`"system:confirm-timeout"` sentinel for an expired one (so the origin butler can tell "the
owner said X" apart from "nobody answered in time" without inferring it from text),
`payload.normalized_text` = a deterministic template ("Confirm answer: <label>" / "Confirm
timed out with no answer: <message>"), and `control.pinned_target = origin_butler` (the
existing `IngestControlV1.pinned_target` field connector-base-spec already defines,
shipped by `2026-07-04-switchboard-pinned-target`) — bypassing LLM classification for
*routing* (the target butler is already known and asserted by the system, not inferred),
while the origin butler's own subsequently spawned session still applies its full normal
reasoning, permission, and approval-gate stack to whatever it decides to do about the
answer (D7). This is deliberately the *opposite* precedence rationale from `pinned_target`'s
existing thread-affinity use case (asserting a caller's continuity choice) — here it
asserts routing identity, not conversational stickiness — but it is the same field and the
same validated-target/no-behavior-change-when-absent contract, so no new envelope field or
Switchboard code path is needed.

### D7: Separation from approval authority is a normative MUST NOT, named at the code-path level

The confirm-resolution handler (whether a tap or a timeout reaper tick) MUST NOT call, and
this contract's future implementation is instrumented to prove it never calls:

- any approvals decision route (`approve_action`, `reject_action`, or any dashboard
  `/approve`/`/reject` HTTP route),
- the approved-action executor,
- any write to `pending_actions` or `approval_events`.

Its only two side effects are the `pending_confirms` state transition + Telegram message
edit (D3/D4), and the single `ingest.v1` submission (D6). The resulting reentry is plain
conversational text delivered to the origin butler's normal trigger pipeline — it carries
no elevated trust beyond "an authenticated chat participant said this," identical to any
other inbound message from that same chat. If the origin butler's session decides, having
seen the answer, to call a gated tool, that call still goes through the ordinary
approval-gate check with the ordinary consequences (owner-directed auto-approve, or park
for a non-owner target) — a confirm answer grants no bypass, no pre-authorization, and no
implicit `_why` justification for any subsequent gated call. This is the concrete
operationalization of the bead's own instruction: "never turn arbitrary confirm text into
effect approval."

### D8: Concurrent, independent confirms need no new mechanism beyond `confirm_id` keying

Two or more `pending_confirms` rows can be `status='pending'` simultaneously, including
multiple confirms addressed to the same chat. Resolution is keyed exclusively by the
`confirm_id` embedded in the tapped button's own `callback_data` (D4) — there is no
"most recent confirm for this chat" fallback, no shared cursor, and no global
single-outstanding-confirm assumption anywhere in this contract. A tap on confirm A's
button can therefore never resolve, expire, or otherwise affect confirm B's row, regardless
of tap order or timing. This is stated as an explicit invariant (and a required test
scenario, D9) rather than left implicit, because it is exactly `bu-ow5a4` acceptance
criterion 5 ("Multiple concurrent confirm payloads render independently").

### D9: Strict additivity — zero behavior change for non-confirm traffic

Every touch point this contract adds is a new, independently-guarded branch:

- `notify()`: the `confirm` intent is a new `Literal` value; every existing intent's
  validation, gating, and delivery path is untouched.
- `module-telegram`: confirm rendering is a new send-time branch keyed on the envelope's
  `intent`; the existing plain-text send/reply/react paths, and the separately-owned
  approval inline-keyboard path, run exactly as they do today for any envelope that is not
  a confirm.
- `connector-telegram-bot`: `cfm1:` is checked as an additional, mutually exclusive
  `callback_data` prefix alongside `apr1:` and `cgi:`; a `callback_data` matching none of
  the three retains the connector's existing silent-drop behavior verbatim.

No shared code path, table, or token format is modified to accommodate confirm — every
addition is new surface area alongside the existing one, matching this contract's own
strict-additivity requirement in the accompanying capability spec.

## Rejected alternatives

- **Extend `apr1:`'s verb alphabet / reuse `pending_actions` for generic confirms** —
  rejected (D1, D7): would either grant confirm answers approval-executor authority they
  must never have, or force `pending_actions`'s approval-specific schema/lifecycle to model
  an unrelated concept.
- **Route confirm resolution through the same control-plane bypass as `apr1:`/`cgi:`,
  delivering the answer via a bespoke session-wake API** — rejected (D6): this repository's
  runtime has no mechanism to resume an already-ended ephemeral session, and a confirm
  answer is conversational content the origin butler should reason about through its
  normal pipeline, not a zero-reasoning control signal.
- **Owner-only callback identity, mirroring RFC 0021 verbatim** — rejected (D5): would
  incorrectly reject every confirm addressed to a non-owner resolved recipient, which
  `notify()`'s existing `entity_id`/`recipient` targeting already supports for every other
  intent.
- **Timeout always synthesizes a "declined" answer** — rejected (D2): fabricates a choice
  the caller never actually offered when no option is marked as the decline-equivalent;
  an honest `{"outcome": "expired", "value": null}` is offered instead, with an opt-in
  `on_timeout_value` for callers that want the legacy binary-decline shape.
- **A new per-domain HMAC secret for confirm tokens** — rejected (D4): a literal domain tag
  in the signed message already gives cryptographic domain separation from `apr1:` under
  the existing shared daemon-internal secret, at zero new secret-provisioning cost.

## Test Strategy (future implementation only)

Named seams a future implementation PR's tests must cover (none exist yet; this draft adds
no tests, `+0 ~0 -0`):

- Unit: `cfm1:` token mint/verify (valid, tampered, wrong-domain `apr1:` token rejected as
  a `cfm1:` token and vice versa), `on_timeout_value` validation against the caller's own
  `options` list, options-list bound validation (2-8 entries).
- Contract/API: `notify(intent="confirm")` envelope validation (channel fallback to plain
  text for email, `request_context` optional), the confirm-resolve route's atomic
  `UPDATE ... WHERE status='pending'` returning zero rows on a repeat/racing tap.
- Real-Postgres: `public.pending_confirms` migration; concurrent resolution race (two
  simultaneous tap requests for the same `confirm_id`, exactly one wins); two independent
  `pending_confirms` rows in the same chat resolve independently (D8).
- Connector: `cfm1:` callback additivity (an `apr1:`/`cgi:`/unrecognized `callback_data`
  is unaffected by the new branch); chat-id mismatch is ignored and logged (D5).
- Privacy/authority: an integration test asserting the confirm-resolution code path never
  imports or calls the approvals decision surface, executor, or `pending_actions`/
  `approval_events` write paths (D7) — a static/dependency assertion, not just a behavioral
  one, so a future refactor cannot silently reintroduce the coupling this draft forbids.
- Expiry reaper: a confirm past `expires_at` transitions to `expired` exactly once even
  under a scheduler restart/re-tick (idempotent per `confirm_id`), and its reentry carries
  the `system:confirm-timeout` sender sentinel (D6), never the recipient's own identity.

## Delivery gates

1. Land this draft only after independent exact-head semantic review returns GO or
   corrections are applied and re-reviewed.
2. Obtain separate owner approval naming the exact reviewed commit before any
   implementation claims this contract as authority.
3. Implementation happens under `bu-ow5a4` (unmodified by this draft) or an explicitly
   coordinator-approved successor, drafting the RFC 0024 text named in D1 as part of that
   implementation, plus the tests in `tasks.md`/Test Strategy above.
4. Treat any live Telegram API call, database migration, or deployment as a separate,
   later-authorized act this draft does not perform or authorize.

## Open questions

None are silently decided here. Whether a future implementation reuses the approvals
module's existing callback-token helper file (as a sibling function, not shared state) or
adds a new small module is left to that implementation PR — both satisfy this contract's
domain-separation requirement (D4) equally, and choosing between them is ordinary
engineering allocation, not a decision this draft needs to pin.
