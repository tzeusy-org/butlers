## 1. Approval and Readiness Gates

- [ ] 1.1 Record exact owner approval of the final proposal, design, and delta-spec commit/digest, including all eight choices in `design.md` § Owner Review Gate; do not begin implementation from draft publication or pursuit release alone.
- [ ] 1.2 Require the separately approved and implemented content-blind read-only exact-message capability needed to resolve `(butler, message_id)` when the message is absent from a bounded loaded response, including after a missed new-conversation event, plus the active `durable-dashboard-terminal-action-recovery` message/dashboard-turn projection; do not implement the full chat outcome while either prerequisite is absent.
- [ ] 1.3 Revalidate the cited doctrine, Dispatch design language, `dashboard-chat-ui`, `dashboard-shell`, and active conversation-recovery clauses at the implementation base; reconcile any new overlapping active delta before editing code.
- [ ] 1.4 Run a fresh ownership and open-PR overlap scan for the shared draft seam and every Decision 2 consumer; assign one implementation owner or explicitly split non-overlapping domain slices without dropping an eligible surface.

## 2. Browser Draft Store and Safety Contract

- [ ] 2.1 Implement the versioned IndexedDB draft store and hook under `frontend/src/lib/` and `frontend/src/hooks/` with Decision 1's `drafts`, `chatAttempts`, and `meta` stores, stable hashed key dimensions, one coalescing transaction queue per hook, controlled-unmount flush, sliding 24-hour expiry, 64 KiB limit, and content-free tombstones (REQ-dashboard-shell-002).
- [ ] 2.2 Implement edit-baseline comparison, automatic safe restore, stale-baseline choice, one-time accessible status, one-click discard, and repeat-safe lifecycle behavior (REQ-dashboard-shell-003).
- [ ] 2.3 Implement transactional epoch/revision compare-and-swap, content-free ordered deletion, 256-or-30-day epoch compaction, BroadcastChannel plus focus/visibility reconciliation, untouched-tab refresh, suspended writes during dirty-tab conflict, and explicit override/load actions without field-level merge (REQ-dashboard-shell-004).
- [ ] 2.4 Implement exception-contained read/write/parse handling and exact unavailable-copy behavior while keeping current-mount input intact; emit only fixed failure category plus surface ID in any diagnostics (REQ-dashboard-shell-006).
- [ ] 2.5 Add focused hook/store behavior tests for remount and reload reads, empty/expiry/invalid tombstones, unsupported records, quota/storage throws, oversize refusal, read-back recovery, repeated actions, inverted write completion, stale write after every deletion reason, epoch compaction, and dirty-conflict write suspension.

## 3. Eligibility and Forbidden-Surface Gate

- [ ] 3.1 Define a typed field-level registry containing exactly Decision 2's approved surface IDs, field bundles, and stable context builders; shared input primitives and unlisted callers must remain ineligible (REQ-dashboard-shell-001).
- [ ] 3.2 Add a fail-closed structural gate and deliberately rejected fixtures proving `/secrets`, provider configuration, password controls, credential-purpose fields, unknown surface IDs, and unregistered field names cannot register for drafts (REQ-dashboard-shell-005).
- [ ] 3.3 Prove the persistence seam has no API client, URL, telemetry, event-bus, error-report payload, provider, or global draft-list path; verify same-origin records are read only from the matching accessible surface and document that ordinary eligible content is not classified for secrets.

## 4. Chat Composer Integration

- [ ] 4.1 Integrate `ChatPanel` and `FloatingChatWidget` with the shared `chat.message` key based on butler plus conversation/new identity; persist message text only and keep page context/context-chip choice current and in-memory (REQ-dashboard-chat-ui-004).
- [ ] 4.2 Before send, flush and capture the submitted revision, allocate the existing immutable client `message_id`, persist its attempt snapshot, and send that same ID; never create a draft-specific attempt ID or reconcile by message text (REQ-dashboard-chat-ui-005).
- [ ] 4.3 Reconcile an attempt only when the bounded loaded message/dashboard-turn read contains its exact ID; on observed `conversation_created`, atomically bind the real conversation ID and tombstone only the exact submitted attempt/draft revision without copying it into a new live conversation draft; keep any attempt absent from that bounded read visibly unknown with no content search, unbounded scan, mutating status probe, or automatic replay until the explicit read-only prerequisite is available.
- [ ] 4.4 Clear draft content only through compare-and-delete of the submitted revision; retire a completed attempt without deleting same-tab or cross-tab edits accepted after submission.
- [ ] 4.5 Extend `ChatPanel.test.tsx` and `FloatingChatWidget.test.tsx` to prove close/unmount/remount restoration, conversation and butler isolation, matching-surface sharing, one-time restored announcement, discard, immutable message-ID binding, new-to-created identity movement, reload reconciliation for accepted/pending/retryable/rejected/cancelled/ambiguous/unknown outcomes, out-of-order success retention, current context at send, and active-history-read recovery.

## 5. Form and Dialog Integration

- [ ] 5.1 Integrate the six health create/edit form families and their complete eligible bundles; extend the nearest `ConditionTracker`, `MealTracker`, `MeasurementTracker`, `MedicationTracker`, `ResearchTracker`, and `SymptomTracker` tests for restoration, record isolation, submission outcomes, and the three-way dirty-dismiss flow.
- [ ] 5.2 Integrate schedule create/edit and create-standing-rule forms; extend `ScheduleForm.test.tsx` and add focused standing-rule coverage for butler/mode/record identity, prompt/job-mode values, argument validation failure retention, success clearing, and dirty dismissal.
- [ ] 5.3 Integrate all four `EntityVerbRail` forms and episode correction; extend `EntityVerbRail.test.tsx` and `EpisodeDrawer.correction-form.test.tsx` for entity/action/episode isolation, failure retention, success clearing, and suppression of corrected-title restoration for currently sensitive episodes.
- [ ] 5.4 Integrate new and existing butler-state value dialogs with separate butler/create/edit/target identities; add focused `ButlerStateTab` and `StateBrowser` tests for JSON parse failure, source-baseline mismatch, success clearing, and dirty dismissal.
- [ ] 5.5 Integrate user-calendar and butler-calendar create/edit dialogs; extend `CalendarWorkspacePage.test.tsx` for lane/operation/event isolation, complete eligible bundles, domain-conflict and transport-failure retention, source-baseline mismatch, success clearing, and dirty dismissal while leaving inline description autosave unchanged.

## 6. Documentation and Verification

- [ ] 6.1 Update the owning frontend documentation with the exact allowlist, stable context dimensions, IndexedDB V1 revision/tombstone/epoch protocol, expiry/limit policy, submitted-revision clear rule, durable chat attempt binding and prerequisite, rollback behavior, browser-profile risk, forbidden surfaces, and explicit no-content-classification statement.
- [ ] 6.2 Run the exact new/affected frontend test nodes first, then their owning files; record the actual implementation test delta as `Tests: +a ~b -c` without treating the pre-implementation estimate as an owner decision.
- [ ] 6.3 Run `npm run lint`, frontend typecheck, `npm run knip`, the repository's spec-trace/strict OpenSpec checks, `make check-spec-overwrites`, and `make check-guards`; fix every failure attributable to the implementation.
- [ ] 6.4 Push the exact clean implementation head and require terminal hosted CI plus fresh independent semantic/code review before merge; any rebase or edit invalidates exact-head evidence.
