## 1. Approval and Readiness Gates

- [x] 1.1 Record exact owner approval of the final proposal, design, and delta-spec commit/digest, including all eight choices in `design.md` § Owner Review Gate; do not begin implementation from draft publication or pursuit release alone.
- [ ] 1.2 Require approved backend prerequisite `bu-z4eqza` and its implemented content-blind read-only exact-message capability when `(butler, message_id)` is absent from a bounded loaded response, including after a missed new-conversation event, plus the active `durable-dashboard-terminal-action-recovery` message/dashboard-turn projection; do not implement the full chat outcome while either prerequisite is absent.
- [x] 1.3 Revalidate the cited doctrine, Dispatch design language, `dashboard-chat-ui`, `dashboard-shell`, and active conversation-recovery clauses at the implementation base; reconcile any new overlapping active delta before editing code.
- [x] 1.4 Run a fresh ownership and open-PR overlap scan for the shared draft seam and every Decision 2 consumer; assign one implementation owner or explicitly split non-overlapping domain slices without dropping an eligible surface.

## 2. Browser Draft Store and Safety Contract

- [x] 2.1 Implement the versioned IndexedDB draft store and hook under `frontend/src/lib/` and `frontend/src/hooks/` with Decision 1's `drafts`, `chatAttempts`, and `meta` stores, stable hashed key dimensions, immutable random content revisions distinct from mutable stale-writer fences, one coalescing transaction queue per hook, sliding 24-hour expiry, 64 KiB limit, and content-free tombstones (REQ-dashboard-shell-002).
- [x] 2.2 Implement edit-baseline comparison, automatic safe restore, stale-baseline choice, one-time accessible status, one-click discard, normal three-way dismissal, and repeat-safe lifecycle behavior (REQ-dashboard-shell-003).
- [x] 2.3 Implement transactional store-epoch/write-revision compare-and-swap, new content identity for every distinct in-memory edit/reset/recreation persisted unchanged on acceptance, content-free ordered deletion, content-identity-preserving 256-or-30-day epoch compaction, BroadcastChannel plus focus/visibility reconciliation, untouched-tab refresh, suspended writes during dirty-tab conflict, and explicit override/load actions without field-level merge (REQ-dashboard-shell-004).
- [x] 2.4 Implement exception-contained denied/blocked/quota/oversize/failed/hung storage handling, 1-second commit-plus-read-back dismissal bounds, truthful form/chat close-with-uncertainty choices, per-instance/key callback guards, and content-blind diagnostics while keeping current-mount input intact (REQ-dashboard-shell-006).
- [ ] 2.5 Add focused hook/store behavior tests for remount and reload reads, empty/reset/expiry/auth-reset/invalid tombstones, byte-identical recreation, quota/storage denial, blocked-open and hung transactions, oversize refusal, confirmed and unconfirmed save/discard close, read-back recovery, late callback after context replacement, inverted write completion, stale write after every deletion reason, compaction during in-flight submit, and dirty-conflict write suspension.

## 3. Eligibility and Forbidden-Surface Gate

- [x] 3.1 Define a typed field-level registry containing exactly Decision 2's approved surface IDs, field bundles, and stable context builders; shared input primitives and unlisted callers must remain ineligible (REQ-dashboard-shell-001).
- [x] 3.2 Add a fail-closed structural gate and deliberately rejected fixtures proving `/secrets`, provider configuration, password controls, credential-purpose fields, unknown surface IDs, and unregistered field names cannot register for drafts (REQ-dashboard-shell-005).
- [ ] 3.3 Prove the persistence seam has no API client, URL, telemetry, event-bus, error-report payload, provider, or global draft-list path; verify same-origin records are read only from the matching accessible surface and document that ordinary eligible content is not classified for secrets.

## 4. Chat Composer Integration

- [x] 4.1 Integrate `ChatPanel` and `FloatingChatWidget` with the shared `chat.message` key based on butler plus conversation/new identity; persist message text only and keep page context/context-chip choice current and in-memory (REQ-dashboard-chat-ui-014).
- [ ] 4.2 Before send, flush and capture the immutable submitted content revision, allocate the existing immutable client `message_id`, and attempt to persist its snapshot for at most 1 second; send once with that same in-memory ID even when persistence is unavailable, disclose loss of reload recovery, and never create a draft-specific attempt ID or reconcile by message text (REQ-dashboard-chat-ui-015).
- [ ] 4.3 Reconcile an attempt only when the bounded loaded message/dashboard-turn read contains its exact ID; on observed `conversation_created`, atomically bind the real conversation ID and tombstone only the exact submitted attempt/content revision without copying it into a new live conversation draft; keep any attempt absent from that bounded read visibly unknown with no content search, unbounded scan, mutating status probe, or automatic replay until `bu-z4eqza` is approved and implemented.
- [ ] 4.4 Clear draft content only when its immutable content revision equals the submitted identity, using the current mutable fence after any unrelated compaction; retire a completed attempt without deleting same-tab, cross-tab, reset, auth-reset, or recreated content.
- [ ] 4.5 Extend `ChatPanel.test.tsx` and `FloatingChatWidget.test.tsx` to prove close/unmount/remount restoration, conversation and butler isolation, matching-surface sharing, one-time restored announcement, confirmed and unconfirmed save/discard close, send continuation with disclosed unavailable attempt persistence, immutable message-ID/content-revision binding, compaction during in-flight send, new-to-created identity movement, reload reconciliation for accepted/pending/retryable/rejected/cancelled/ambiguous/unknown outcomes, successful-send deletion uncertainty, out-of-order success and successor-context protection, current context at send, and active-history-read recovery.

## 5. Form and Dialog Integration

- [x] 5.1 Integrate the six health create/edit form families and their complete eligible bundles; extend the nearest `ConditionTracker`, `MealTracker`, `MeasurementTracker`, `MedicationTracker`, `ResearchTracker`, and `SymptomTracker` tests for restoration, record isolation, submission outcomes, and the three-way dirty-dismiss flow.
- [x] 5.2 Integrate schedule create/edit and create-standing-rule forms; extend `ScheduleForm.test.tsx` and add focused standing-rule coverage for butler/mode/record identity, prompt/job-mode values, argument validation failure retention, success clearing, and dirty dismissal.
- [x] 5.3 Integrate all four `EntityVerbRail` forms and episode correction; extend `EntityVerbRail.test.tsx` and `EpisodeDrawer.correction-form.test.tsx` for entity/action/episode isolation, failure retention, success clearing, and suppression of corrected-title restoration for currently sensitive episodes.
- [x] 5.4 Integrate new and existing butler-state value dialogs with separate butler/create/edit/target identities; add focused `ButlerStateTab` and `StateBrowser` tests for JSON parse failure, source-baseline mismatch, success clearing, and dirty dismissal.
- [x] 5.5 Integrate user-calendar and butler-calendar create/edit dialogs; extend `CalendarWorkspacePage.test.tsx` for lane/operation/event isolation, complete eligible bundles, domain-conflict and transport-failure retention, source-baseline mismatch, success clearing, and dirty dismissal while leaving inline description autosave unchanged.

## 6. Documentation and Verification

- [x] 6.1 Update the owning frontend documentation with the exact allowlist, stable context dimensions, IndexedDB V1 content-identity/fence/tombstone/epoch protocol, expiry/limit policy, 1-second degraded-dismiss matrix, submitted-content clear rule, durable chat attempt binding and `bu-z4eqza` prerequisite, rollback behavior, browser-profile risk, forbidden surfaces, and explicit no-content-classification statement.
- [x] 6.2 Run the exact new/affected frontend test nodes first, then their owning files; record the actual implementation test delta as `Tests: +a ~b -c` without treating the pre-implementation estimate as an owner decision.
- [ ] 6.3 Run `npm run lint`, frontend typecheck, `npm run knip`, the repository's spec-trace/strict OpenSpec checks, `make check-spec-overwrites`, and `make check-guards`; fix every failure attributable to the implementation.
- [ ] 6.4 Push the exact clean implementation head and require terminal hosted CI plus fresh independent semantic/code review before merge; any rebase or edit invalidates exact-head evidence.

## Local implementation qualification

Owner adoption binds artifact `d2dc32ffb001398af169363064b7610595512f9d`
(PR #4056) and the recorded September 13 implementation release. The current
implementation follows the released forms-plus-chat-restore outcome. The exact
backend message resolver and trusted turn projection remain absent, so tasks
1.2 and 4.2–4.5 are not claimed complete. `chatAttempts` is reserved and empty;
there is no reload outcome inference or automatic resend.

The later new-test-files-only instruction supersedes the nearest-retained-file
advice above. Existing test/fixture bytes are preserved. New component and
eligibility tests plus real-browser IndexedDB tests cover restoration, two-tab
conflicts, expiry/invalid records, compaction and late success, oversize/quota,
blocked/queued transactions, dismissal, auth reset, and chat identity/no replay.
The retired relationship reach-out identity remains inactive rather than being
replaced or reintroduced. Navigation interception is confined to enrolled dirty
tasks and does not add a navigation-intent consumer.

Local checks do not complete task 6.4. The initial implementation's admission
against newer public main was refused because it lacked the newly landed retained
tests; that dated refusal remains preserved. The separately authorized ordinary
integration now includes public main `3900aadd76fc7403c025fb85187d40476570e4f5`
and preserves its retained tests. Independent exact-head review, publication and
hosted delivery remain pending; local evidence does not replace those gates.
