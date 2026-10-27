## 1. Draft the contract

- [x] 1.1 Specify the `notify(intent="confirm")` envelope: bounded 2-8 option list,
  timeout bounds `[30, 86400]` with a 600s default, optional `on_timeout_value`, and
  optional (not required) `request_context`.
- [x] 1.2 Specify the non-interactive-channel (email) fallback: plain-text option listing,
  no automatic answer-capture, no resolvable `pending_confirms` row.
- [x] 1.3 Specify the `public.pending_confirms` cross-butler durable record and why it lives
  in `public` rather than a per-butler schema (no privileged authority to isolate).
- [x] 1.4 Specify the `cfm1:<confirm_id>:<opt_idx>:<hmac>` callback token format, its
  64-byte budget, and its domain-tagged HMAC separation from `apr1:`/`cgi:`.
- [x] 1.5 Specify authenticated callback identity by exact recipient-chat match (not
  owner-only), with HMAC verification as defense-in-depth ahead of any database read.
- [x] 1.6 Specify atomic replay-fenced resolution (`status='pending' -> 'answered'`/
  `'expired'`, single-writer-wins) and idempotent expiry-sweep behavior.
- [x] 1.7 Specify reply-to-origin reentry via `ingest.v1` + `control.pinned_target`, the
  `sender.identity` distinction between an answered confirm and the `system:confirm-timeout`
  sentinel for an expired one, and independent resolution of concurrent confirms.
- [x] 1.8 Specify the normative separation-from-approval-authority boundary: the exact
  approvals code paths (`approve_action`, `reject_action`, the executor, `pending_actions`/
  `approval_events` writes) the confirm-resolution path MUST NOT call.
- [x] 1.9 Draft additive-only deltas for `core-notify`, `module-telegram`, and
  `connector-telegram-bot` that add new requirement blocks without modifying any existing
  requirement those files (or the unarchived `decision-loop-one-tap-approvals` change) own.
- [x] 1.10 Confirm `bu-ow5a4`'s disposition: remains open per the binding 2026-09-06
  coordinator review; this draft is its prerequisite and performs no Beads mutation.

## 2. Approval gates

- [ ] 2.1 Obtain independent exact-head semantic review of this draft.
- [ ] 2.2 After review passes, obtain separate owner approval naming the exact reviewed
  artifact. Any semantic edit invalidates that review and requires a fresh pass.
- [ ] 2.3 Keep any implementation, migration, credential, or live Telegram API call blocked
  until this contract is accepted and its own separate authorities are satisfied.

## 3. Future implementation after approval (`bu-ow5a4` or an approved successor)

- [ ] 3.1 Draft and, once code exists, apply the new RFC (proposed number 0024 per
  design.md D1) recording this contract — do not amend RFC 0021.
- [ ] 3.2 Add the `public.pending_confirms` migration and the `confirm` intent to
  `notify()`'s `Literal` and validation in `src/butlers/core_tools/_notifications.py`.
- [ ] 3.3 Add a confirm-token mint/verify helper (domain-tagged HMAC per D4), independent of
  the existing approvals callback-token helper.
- [ ] 3.4 Implement `module-telegram` confirm-envelope rendering and the resolved-state
  message edit.
- [ ] 3.5 Implement the `telegram_bot` connector's `cfm1:` callback handling: chat-identity
  match, HMAC verification, atomic `pending_confirms` transition, and `ingest.v1` reentry
  with `control.pinned_target`.
- [ ] 3.6 Implement the expiry-sweep tick (mirroring the existing deferred-notification
  flush-tick pattern) for confirms past `expires_at`.

## 4. Future verification after approval (`bu-ow5a4` or an approved successor)

- [ ] 4.1 Unit tests: token mint/verify (valid, tampered, cross-domain `apr1:`/`cgi:`
  rejection), options-list and timeout-bound validation, `on_timeout_value` membership
  check.
- [ ] 4.2 Contract/API tests: confirm envelope validation per channel, the resolve route's
  atomic conditional update returning zero rows on a repeat/racing tap.
- [ ] 4.3 Real-Postgres tests: `pending_confirms` migration; concurrent-tap race (exactly
  one winner); two independent concurrent confirms in the same chat resolve independently.
- [ ] 4.4 Connector tests: `cfm1:` additivity (existing `apr1:`/`cgi:`/unrecognized
  `callback_data` behavior unchanged); chat-identity mismatch is ignored and logged.
- [ ] 4.5 Privacy/authority test: assert the confirm-resolution code path has no import or
  call edge into the approvals decision surface, executor, or `pending_actions`/
  `approval_events` writes.
- [ ] 4.6 Expiry-sweep tests: idempotent transition under a repeated/restarted tick; the
  `system:confirm-timeout` sender sentinel on the timeout reentry.
- [ ] 4.7 Run targeted unit/contract/real-Postgres/connector tests, repo guards
  (`make check-guards`), strict OpenSpec and overwrite checks, lint/format, a fresh
  independent exact-head review, and terminal hosted CI. Report the implementation PR's
  actual test delta separately.

## 5. Archive only after implementation

- [ ] 5.1 After the separately approved implementation is merged, sync the
  `notify-confirm-interaction` capability plus the `core-notify`/`module-telegram`/
  `connector-telegram-bot` deltas to their `openspec/specs/` baselines and archive this
  change. Archival does not authorize deployment or any live Telegram API call.
