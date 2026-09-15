# Specify the generic Telegram confirm-interaction contract

## Why

`bu-ow5a4` ("Wire notify() confirm payload to Telegram inline keyboards") asks for a
generic mechanism: any butler tool can ask the owner (or a resolved recipient) a
tap-to-answer question over Telegram and get the choice delivered back to the butler that
asked, independent of the approvals subsystem. Its acceptance criteria name a generic
`notify()` confirm payload, reply-to-origin delivery, concurrent independent confirms,
timeout-as-decline, and zero regression to plain-text `notify()` traffic.

A 2026-09-06 `bu-27dxl.9` shaping pass proposed closing `bu-ow5a4` as "covered" by the
inline-keyboard/callback primitives `bu-24lu6.4`/`bu-24lu6.5` shipped (PRs #3381, #3394)
under RFC 0021 and the `decision-loop-one-tap-approvals` OpenSpec change. The binding
coordinator review (`coordinator-review.md`,
`/home/tze/.local/share/butlers/coordinator-evidence/run6-shaping-20260906/`) rejected that
closure: those primitives implement one specific, sealed wire contract — the
`apr1:<action_id>:<verb_char>:<hmac>` pending-action approve/reject callback bound to
`public.pending_actions` and the approvals executor. They prove that *a* Telegram inline
keyboard/callback mechanism exists; they prove nothing about a *generic*, caller-defined
confirm contract that any notify() caller can use for an arbitrary yes/no/multiple-choice
question that is not a tool-approval decision. The review holds `bu-ow5a4`'s generic
outcome open as a genuine authority gap.

This change is the OpenSpec-first prerequisite `bu-27dxl.9` requires before any
implementation of `bu-ow5a4` (or a successor) can proceed. It defines the exact wire
contract — envelope shape, callback identity, replay fencing, expiry, and reply-to-origin
delivery — and draws a hard line the existing approval primitives do not need to draw
because they only ever produce one of two audited, executor-triggering verbs: **a generic
confirm answer MUST NEVER be treated as approval authority for any gated tool call.** It
performs no implementation, no runtime, provider, or credential access, and no Beads,
repository merge, or deployment action beyond opening this draft PR.

## What Changes

- Add a new `notify-confirm-interaction` capability defining the generic wire contract:
  the `notify(intent="confirm")` envelope (bounded option list, timeout bounds), a new
  cross-butler `public.pending_confirms` durable record, a domain-separated
  `cfm1:<confirm_id>:<opt_idx>:<hmac>` callback token distinct from `apr1:`/`cgi:`,
  recipient-chat-identity authentication (not owner-only — a confirm can target any
  resolved recipient, unlike owner-only `approval_request`), atomic replay-fenced
  resolution, expiry-as-timeout reentry, independent concurrent-confirm resolution, and the
  explicit separation-from-approval-authority boundary.
- Add a `[TARGET-STATE]` **Confirm Delivery Intent** requirement to `core-notify`: a sixth
  `notify()` intent (`confirm`, alongside `send`/`reply`/`react`/`insight`) with its own
  envelope/options/timeout validation. Purely additive — the existing `Delivery Intent
  Validation` requirement and its four documented intents are untouched.
- Add a **Confirm Inline Keyboard Rendering** requirement to `module-telegram`: sending a
  confirm envelope as an inline keyboard bound to `cfm1:` tokens, and editing the message to
  its resolved/expired state. Additive alongside the existing (separately owned) approval
  inline-keyboard requirements — no shared button/token/state machinery.
- Add a **Generic Confirm Callback Ingestion** requirement to `connector-telegram-bot`:
  routing `cfm1:`-prefixed `callback_query` updates to the new confirm-resolution path,
  strictly additive to its existing `apr1:`/`cgi:` prefix handling and its default-drop
  behavior for every other `callback_query`.
- Reply-to-origin uses the existing `IngestControlV1.pinned_target` primitive
  (`connector-base-spec`, shipped in `2026-07-04-switchboard-pinned-target`) to route a
  resolved answer back through the normal `ingest.v1` → Switchboard path as an ordinary
  conversational turn pinned to the calling butler — **not** a new session-spawn mechanism,
  and **not** the sticky multi-turn thread-affinity contract `bu-n3dn7` separately owns.
  This draft pins exactly one reentry event per confirm; it does not touch, extend, or
  duplicate ongoing thread-affinity/target-reuse behavior.
- No change to `about/legends-and-lore/rfcs/0021-decision-loop-one-tap-approvals-and-decision-memory.md`,
  `pending_actions`, the approvals executor, or the `apr1:`/`cgi:` callback formats.

## Capabilities

### New Capabilities

- `notify-confirm-interaction`: the generic notify() confirm envelope, durable record,
  callback identity/replay/expiry contract, and reply-to-origin reentry mechanism.

### Modified Capabilities

- `core-notify`: adds the `confirm` delivery intent (`[TARGET-STATE]`, additive only).
- `module-telegram`: adds confirm-specific inline-keyboard rendering (additive only).
- `connector-telegram-bot`: adds `cfm1:` callback routing (additive only).

## Impact

- Affected future code (not touched by this draft): `src/butlers/core_tools/_notifications.py`
  (`confirm` intent branch), a new `public.pending_confirms` migration, a new confirm-token
  mint/verify helper (`src/butlers/core/telegram_confirm_tokens.py` or similar, alongside the
  existing approvals callback-token helper — a sibling, not a shared module), the Telegram
  module's send/edit tools, and the `telegram_bot` connector's `callback_query` handler.
- Affected RFCs: none amended by this draft. If accepted, a future implementation PR should
  record this contract as a new RFC (proposed number 0024, since 0021-0023 are taken) rather
  than amending RFC 0021, because the trust model (recipient-identity match, not
  owner-verification; conversational reentry, not control-plane bypass) is materially
  different from RFC 0021's approval-decision design — see `design.md` D1.
- `bu-ow5a4` disposition: remains open, per the binding coordinator review. This draft is
  the prerequisite its eventual implementation must satisfy. `bu-24lu6.4`/`bu-24lu6.5` and
  RFC 0021 are retained unmodified — this draft neither depends on editing them nor
  supersedes them.
- Coexistence note: `core-notify`, `module-telegram`, and `connector-telegram-bot` each
  already carry an unarchived `## ADDED`/`## MODIFIED` block from
  `decision-loop-one-tap-approvals` (approval-request intent, approval inline keyboard,
  `apr1:` callback ingestion respectively). This draft adds only new, uniquely named `##
  ADDED` requirement blocks to each file — it does not touch any requirement name that
  change owns, and does not add a `## MODIFIED` block to any of the three files, so the two
  changes can archive in either order without conflict.
- No implementation, runtime, provider/account/credential/data access, message delivery,
  activation, deployment, or merge is performed or authorized by this change. Expected
  tests: `+0 ~0 -0`.
