# Tasks - spawner-undelivered-reply-failed-session

This change documents and validates the SPEC side of the
completed-but-undelivered outcome. The implementation already landed/landing in
PR #2693; the boxes below record that work plus the spec amendment and its
close-out.

## 1. Delivery-accounting detector (spec: core-spawner) - PR #2693

- [x] 1.1 `_check_undelivered_interactive_reply` in
  `src/butlers/core/spawner_guardrails.py`: route-trigger + interactive-channel
  gated; returns a reason string when notify was attempted but nothing delivered,
  else `None`.
- [x] 1.2 Delivered-status set `_DELIVERED_NOTIFY_STATUSES = {"ok", "deferred"}`;
  `_notify_call_delivered` treats `outcome="error"`, a non-dict result, or any
  other `status` (including `suppressed_quiet_hours`) as undelivered.
- [x] 1.3 `_is_notify_call` matches the merged bare `notify` name and prefixed
  forms (`*_notify`, `*.notify`, `*__notify`).
- [x] 1.4 `_extract_source_channel` reads `request_context.source_channel` then
  `source_metadata.channel` from the captured routing context.

## 2. Spawner wiring (spec: core-spawner) - PR #2693

- [x] 2.1 Call `_check_undelivered_interactive_reply` on the normal-completion
  path in `src/butlers/core/spawner.py`, BEFORE building `SpawnerResult`, using
  `_INTERACTIVE_ROUTE_CHANNELS` from `butlers.routing_guidance`.
- [x] 2.2 Persist `session_complete(success=_undelivered_reason is None,
  error=_undelivered_reason)`; keep `SpawnerResult.success=True`; do NOT raise (so
  no failover / self-healing).
- [x] 2.3 Tests: `tests/core/test_spawner_guardrails.py` (delivered statuses,
  undelivered statuses incl. `suppressed_quiet_hours`, null-result shape,
  zero-attempt, non-route, non-interactive).

## 3. Spec amendment (this change)

- [ ] 3.1 `specs/core-spawner/spec.md` delta: MODIFY `Spawner Session Lifecycle`
  to amend the "Successful session" scenario; ADD the `Interactive Reply Delivery
  Accounting` requirement with its scenarios.
- [ ] 3.2 `openspec validate spawner-undelivered-reply-failed-session --strict`
  passes.

## 4. Close-out

- [ ] 4.1 On archive, fold the amended scenario + new requirement into the
  normative `openspec/specs/core-spawner/spec.md`.
