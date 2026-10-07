## 1. Adoption gates (owner)

- [ ] 1.1 Owner adopts this exact artifact, including the supersession of the baseline "Input disabled during streaming" scenario and the three-bouncing-dots Typing Indicator scenario, and the proposed suggestion and empty-state copy in `design.md` Decisions 5 and 6 (edits allowed at adoption).
- [ ] 1.2 Owner answers `design.md` § Owner decision (queued-text retention). On Option B, replace REQ-dashboard-chat-ui-010's scenarios with the single "Queued text is not retained across reload" scenario before implementation starts, and re-run section 5.

## 2. Composer identity and queue (REQ-dashboard-chat-ui-006, -008, -009, -010)

- [ ] 2.1 Add a focused composer helper under `frontend/src/components/chat/composer/` that holds unsent text and the single queued item per `{butler, conversation | new}` identity, in memory, for one posture instance; migrate `new` to the created id on `conversation_created`. Shape it as the seam `bu-2jtfw.15` later backs with the adopted draft store; do not add browser storage here.
- [ ] 2.2 Integrate the helper with `useConversationTurn` so release happens only on a committed `message_complete` and every other terminal or interrupted outcome, Stop activation, conversation switch, unmount, and unload demotes per REQ-dashboard-chat-ui-009; dispatch reuses the stored `message_id` and captured context.
- [ ] 2.3 Update `MessageInput` so no prop disables the textarea; Send and Stop render side by side during a turn; add the queued row with `Edit`, the one-item helper line, and the polite announcements.
- [ ] 2.4 Replace the per-posture `inputValue` state in `ChatDock`, `ChatPanel`, `FloatingChatWidget`, and `ChatPage` with the shared helper; keep `bu-p5umi2.1`'s ownership gate as a Send gate only.
- [ ] 2.5 If Option A is adopted and `bu-2jtfw.15`'s chat-draft seam has landed, store queued text in the same draft record per REQ-dashboard-chat-ui-010; otherwise record the follow-up on `bu-2jtfw.15` so its chat slice includes the queued-text field.

## 3. Starting prompts and empty thread (REQ-dashboard-chat-ui-011, -012)

- [ ] 3.1 Add the optional suggestion factory to `ShellCapability` with a closed projection type `{ routePattern, resourceKind, windowClass }`, built in one place; define it only for `/spend`, `/qa`, and `/entities/:entityId` with the adopted copy.
- [ ] 3.2 Add `SuggestedPrompts.tsx` (pill buttons in a named list) and the new empty-state copy in the `MessageThread` empty slot; respect `suppressEmptyState`.

## 4. Chrome (REQ-dashboard-chat-ui-007, -013)

- [ ] 4.1 Replace `TypingIndicator`'s bouncing dots with the non-interactive status pill reusing `pendingActivityStatus()` text; add the `animate-bounce` selector to `frontend/eslint.config.js` as the Motion Vocabulary spec already requires.
- [ ] 4.2 Restyle the below-`xl` trigger as the hairline mono pill with the unread label and one `StateDot`, keeping the 44×44px hit area, accessible name, and `.13` announcement; remove card shadow and fill from the popover.

## 5. Verification

- [ ] 5.1 Focused Vitest first: `MessageInput.test.tsx`, a new composer-helper test file, `FloatingChatWidget.test.tsx`, `ChatDock.test.tsx`, `ChatPanel.test.tsx`, `ChatPage.test.tsx`, `MessageThread.test.tsx`, and `shell-capability.context.test.ts`, each citing the REQ IDs it pins. Cover every scenario in this delta at the rendered seam, including exactly-once release, each demotion trigger, the second-Enter refusal, Edit placement, identity isolation on switch, prompt input closure, and the absence of bounce elements. Replace the existing `span.animate-bounce` assertion in `MessageThread.test.tsx`.
- [ ] 5.2 Existing `.13` shortcut, focus, live-region, reduced-motion, target-size, and unread-announcement tests stay green unchanged.
- [ ] 5.3 From `frontend/`: `npm run lint`, `npm run knip`, `npm run build`, `npm test`. Expected test delta: +1 file (composer helper), about 8 existing files extended, -0 files; no backend or migration tests.
- [ ] 5.4 From the repo root: `openspec validate amend-dashboard-chat-composer-queue-and-suggestions --strict`, `make check-spec-overwrites`, and `make check-guards`.
- [ ] 5.5 At archive, reconcile `SSE Client Integration` "re-enable the input" wording (baseline and the active `durable-dashboard-terminal-action-recovery` block) with Decision 8, and confirm no clause of `persist-safe-dashboard-unsent-drafts` was restated or weakened.
