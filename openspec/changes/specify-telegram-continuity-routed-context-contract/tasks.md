## 1. Draft the contract

- [x] 1.1 Specify chat-keyed affinity: extraction of `chat_id` from `event.external_thread_id`
  (`<chat_id>:<message_id>`, fallback bare `chat_id`), matched against `switchboard.routing_log.
  thread_id` using the same pattern the pipeline's realtime-history loader already uses.
- [x] 1.2 Specify a Telegram-specific affinity TTL (default 24h) distinct from and non-interfering
  with email thread-affinity's existing 30-day default and settings row.
- [x] 1.3 Specify unavailable-pinned-target fallback: a hit on a no-longer-registered butler is
  treated as a miss, mirroring `pinned_target`'s existing registry validation.
- [x] 1.4 Specify concurrent-turn conflict resolution reusing the existing conflict-then-reclassify
  outcome already proven for email, with no new locking.
- [x] 1.5 Specify that replay/dedup is inherited from the existing Telegram ingest idempotency
  guarantee, adding no new dedup mechanism.
- [x] 1.6 Specify the owner-correction reset path as an extension of the existing `correct_route`
  tool rather than a new command surface, and draft the `## MODIFIED Requirements` delta for
  `butler-switchboard`'s Misroute Correction Re-dispatch requirement that preserves every existing
  scenario while adding the new Telegram-affinity-update scenario.
- [x] 1.7 Specify the deterministic, bounded target-side routed-context block: additive to
  classifier-authored context, sole context on an affinity-bypass turn, reusing module-pipeline's
  existing 15-minute/30-message bound rather than a new number.
- [x] 1.8 Specify trust fencing for the routed-context block reusing the exact banner/code-fence
  pattern already shipped in `_format_history_context`.
- [x] 1.9 Specify the scope boundary: no new cross-channel thread-identity table, no change to email
  affinity's channel gate or settings, and name the future consolidation path with `bu-0ynlk.15`
  without blocking on or implementing it (design.md D9).
- [x] 1.10 Confirm `bu-p6v8k` and `bu-d2ft5` dispositions: both remain open, blocked on this
  contract per `bu-27dxl.9`'s explicit sequencing; this draft performs no Beads mutation.

## 2. Approval gates

- [ ] 2.1 Obtain independent exact-head semantic review of this draft.
- [ ] 2.2 After review passes, obtain separate owner approval naming the exact reviewed artifact.
  Any semantic edit invalidates that review and requires a fresh pass.
- [ ] 2.3 Keep any implementation, migration, credential, or live Telegram API call blocked until
  this contract is accepted and its own separate authorities are satisfied.

## 3. Future implementation after approval (`bu-p6v8k` / `bu-d2ft5` or an approved successor)

- [ ] 3.1 Generalize `roster/switchboard/tools/triage/thread_affinity.py`'s `source_channel ==
  "email"` gate to a per-channel dispatch, adding the `chat_id`-based match for `telegram_bot` and a
  per-channel TTL/override settings surface (schema shape left to the implementer per design.md's
  Open Questions).
- [ ] 3.2 Insert the Telegram affinity check into `roster/switchboard/tools/ingestion/ingest.py`'s
  existing deterministic pipeline, alongside (not replacing) the email thread-affinity step, before
  LLM classification.
- [ ] 3.3 Extend `roster/switchboard/tools/routing/correct_route.py` so a `telegram_bot`-channel
  correction also writes a fresh affinity pin for the corrected butler.
- [ ] 3.4 Add the deterministic bounded/fenced routed-context block to
  `src/butlers/core_tools/_switchboard.py::_dispatch_dashboard_target`'s `_effective_context`
  construction, following the same pattern already used for the dashboard confirm-block, gated to
  `telegram_bot`-channel routes.
- [ ] 3.5 Wire the affinity-bypass dispatch path so a hit produces `prompt` = raw incoming message
  and `input.context` = the deterministic routed-context block, without invoking LLM classification.

## 4. Future verification after approval (`bu-p6v8k` / `bu-d2ft5` or an approved successor)

- [ ] 4.1 Unit tests: `chat_id` extraction from both thread-identity shapes; per-channel TTL
  resolution defaulting to 24h for `telegram_bot` without changing email's default.
- [ ] 4.2 Contract tests: new-thread miss, follow-up hit (classification skipped), TTL-expired miss,
  unavailable-pinned-target miss, and conflict miss — each against the existing `AffinityOutcome`
  values, no new outcome added.
- [ ] 4.3 Integration test: `correct_route` on a `telegram_bot`-channel request writes a fresh
  affinity hit for the corrected butler, verified by a subsequent lookup.
- [ ] 4.4 Idempotency test: a redelivered Telegram update never reaches the affinity lookup or
  writes a second `routing_log` row.
- [ ] 4.5 Routed-context tests: deterministic block present and correctly bounded/fenced on both a
  classify-then-route turn (additive) and an affinity-bypass turn (sole context); a malicious
  embedded instruction in a fenced prior message is not followed.
- [ ] 4.6 Regression test: email's existing thread-affinity suite is unaffected by the per-channel
  generalization.
- [ ] 4.7 Run targeted switchboard/pipeline/connector unit and integration tests, repo guards (`make
  check-guards`), strict OpenSpec and overwrite checks, lint/format, a fresh independent exact-head
  review, and terminal hosted CI. Report the implementation PR's actual test delta separately.

## 5. Archive only after implementation

- [ ] 5.1 After the separately approved implementation is merged, sync the
  `telegram-continuity-routed-context` capability plus the `butler-switchboard` delta to their
  `openspec/specs/` baselines and archive this change. Archival does not authorize deployment or any
  live Telegram API call.
