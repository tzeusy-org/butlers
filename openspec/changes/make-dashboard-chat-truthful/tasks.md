## 1. Switchboard acceptance receipt

- [x] 1.1 Add failing unit coverage proving a successful routed dashboard submission emits `dispatch_accepted` with the actual target before the reply event.
- [x] 1.2 Add failing unit coverage proving an accepted targetless dashboard lane emits `dispatch_accepted` with `routed_butler: null` and never invents a target.
- [x] 1.3 Emit the additive receipt from `_stream_conversation_response` after successful Switchboard acceptance and before reply polling.

## 2. Shared chat activity and accountability

- [x] 2.1 Add failing widget and detail-panel interaction tests for the receipt's routed and targetless owner-visible status.
- [x] 2.2 Extend shared stream state and both SSE handlers to consume `dispatch_accepted` without changing cancellation or final-reply behavior.
- [x] 2.3 Render one polite activity status while waiting, hide decorative typing dots from assistive technology, and make a known Switchboard routed target a navigable header identity.

## 3. Read-side recovery

- [x] 3.1 Add failing frontend tests for conversation-list and message-history read failures, including retry and preservation of local thread/draft state.
- [x] 3.2 Render shared non-destructive `role="alert"` recovery controls from the existing React Query refetch functions.
- [x] 3.3 Align timeout and other recovery notices with the same alert semantics without changing retry policy for deterministic rejection or timeout.

## 4. Verification and handoff

- [x] 4.1 Run focused backend and frontend test suites, then lint/format the touched files.
- [x] 4.2 Validate `make-dashboard-chat-truthful` with strict OpenSpec validation and walk the keyboard/screen-reader state contract from tests.
- [ ] 4.3 Commit the scoped change, push the branch, open a clean PR, and record its verification evidence.
