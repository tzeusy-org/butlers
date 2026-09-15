## 1. Draft the contract

- [x] 1.1 Specify the `[modules.telegram.ack]` style configuration (`typing`/`reaction`/`both`/
  `none`) and its `reaction` default preserving today's shipped unconditional behavior.
- [x] 1.2 Specify the typing-indicator 1-second local-dispatch timing bound and explicitly
  distinguish it from a Telegram-side delivery/rendering guarantee.
- [x] 1.3 Specify the 4-second typing keepalive cadence, grounded in the Telegram Bot API's
  documented ≤5-second chat-action duration (live-fetched, not recalled from memory).
- [x] 1.4 Specify per-chat typing keepalive reference counting for concurrent in-flight owner
  messages in the same chat.
- [x] 1.5 Specify terminal cleanup for both ack styles, including the honest limit that the Bot API
  exposes no explicit "clear typing" call.
- [x] 1.6 Specify owner-only acknowledgement filtering via a fast, classification-pipeline-
  independent lookup, naming the existing `identity.resolve_owner_channel_via_definer` precedent.
- [x] 1.7 Specify the ownership boundary: connector stays transport-only, `module-telegram` owns
  API primitives, Switchboard's core ingest wiring owns orchestration/gating.
- [x] 1.8 Reconcile `bu-2an2p` criterion 4 ("reaction removed on arrival") against the already-
  shipped replace-with-terminal-reaction behavior as an explicit generalization, not a silent drop.
- [x] 1.9 Draft additive-only deltas for `module-telegram` (new typing-dispatch primitives and ack
  config) and a `## MODIFIED Requirements` delta for `connector-telegram-bot`'s Lifecycle Reactions
  requirement that preserves every existing clause and scenario while adding the new gating clause.
- [x] 1.10 Confirm `bu-2an2p`'s disposition: remains open per the binding 2026-09-06 coordinator
  review; this draft is its prerequisite and performs no Beads mutation.

## 2. Approval gates

- [ ] 2.1 Obtain independent exact-head semantic review of this draft.
- [ ] 2.2 After review passes, obtain separate owner approval naming the exact reviewed artifact.
  Any semantic edit invalidates that review and requires a fresh pass.
- [ ] 2.3 Keep any implementation, migration, credential, or live Telegram API call blocked until
  this contract is accepted and its own separate authorities are satisfied.

## 3. Future implementation after approval (`bu-2an2p` or an approved successor)

- [ ] 3.1 Add `begin_typing_for_ingest`/`end_typing_for_ingest` to `src/butlers/modules/telegram.py`
  with per-chat reference counting and a 4-second keepalive loop.
- [ ] 3.2 Add the `ack` field to `TelegramConfig` (`src/butlers/modules/telegram.py`) with
  `style` validation and the `reaction` default.
- [ ] 3.3 Add the owner-only gate and ack-style branch to both existing call sites
  (`_buffer_process` in `src/butlers/switchboard_wiring.py` and the background task in
  `src/butlers/core_tools/_switchboard.py`), reusing `identity.resolve_owner_channel_via_definer`
  or an equivalent fast lookup.
- [ ] 3.4 Wire the pre/post-`pipeline.process()` calls to invoke the configured combination of
  `begin_typing_for_ingest`/`end_typing_for_ingest` and `react_for_ingest` per the ack style.

## 4. Future verification after approval (`bu-2an2p` or an approved successor)

- [ ] 4.1 Unit tests: ack-style config validation and default; per-chat keepalive refcount
  transitions (0→1 starts, N→0 stops, no stacking on 1→2).
- [ ] 4.2 Timing tests: pre-processing hook dispatches the typing call within the 1-second local
  bound; keepalive cadence holds at 4 seconds while a chat's counter is nonzero.
- [ ] 4.3 Owner-only gate tests: non-owner sender and no-owner-participant batch suppress
  acknowledgement dispatch under every ack style.
- [ ] 4.4 Terminal-state tests: `reaction`/`both` replace the in-progress reaction on both success
  and failure; `typing`/`both` stop the keepalive loop on both success and failure and on the
  existing caught-exception path.
- [ ] 4.5 Style-suppression tests: `none` results in zero acknowledgement API calls for an
  otherwise-qualifying owner message.
- [ ] 4.6 Regression test naming the inherited `asyncio.CancelledError` cleanup gap (D9) so this
  contract's implementation does not silently widen or claim to close it.
- [ ] 4.7 Run targeted unit/connector tests, repo guards (`make check-guards`), strict OpenSpec and
  overwrite checks, lint/format, a fresh independent exact-head review, and terminal hosted CI.
  Report the implementation PR's actual test delta separately.

## 5. Archive only after implementation

- [ ] 5.1 After the separately approved implementation is merged, sync the
  `telegram-receipt-acknowledgement` capability plus the `module-telegram`/`connector-telegram-bot`
  deltas to their `openspec/specs/` baselines and archive this change. Archival does not authorize
  deployment or any live Telegram API call.
