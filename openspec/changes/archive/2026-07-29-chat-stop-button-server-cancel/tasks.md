## 1. Backend: real cancellation

- [x] 1.1 Add a session_id-keyed invoke-task registry and `Spawner.cancel_session()` to `src/butlers/core/spawner.py`, distinguishing owner-initiated cancellation from shutdown `drain()` so only the former is absorbed into an honest `SpawnerResult(success=False)` rather than propagating as task cancellation.
- [x] 1.2 Add `CancelledError` handling to all four runtime adapters (`claude_code.py`, `codex.py`, `gemini.py`, `opencode.py`) that kills the CLI subprocess on cancellation — without this the process would be orphaned even though the Python task ends.
- [x] 1.3 Register a `cancel_session` MCP tool unconditionally (like `route.execute`) in `src/butlers/core_tools/_sessions.py`.
- [x] 1.4 Add `POST /api/butlers/{name}/conversations/{conversation_id}/cancel` to `src/butlers/api/routers/conversations.py`, resolving the active turn via a process-local registry populated by `_stream_conversation_response`.

## 2. Frontend: wire both Stop buttons

- [x] 2.1 Add `cancelConversationTurn` to `api/client.ts`/`api/index.ts` and `ConversationCancelResponse` to `api/types.ts`.
- [x] 2.2 Extend `StreamingState` (`MessageThread.tsx`) with `cancelling`/`cancelled`/`cancelError`; render "Cancelled by owner" distinct from "Interrupted", and surface `cancelError` honestly.
- [x] 2.3 Wire `handleStop` in `FloatingChatWidget.tsx` and `ChatPanel.tsx` to call the cancel endpoint with a pending state (`MessageInput.tsx` `stopPending`).
- [x] 2.4 Extend `StatusBadge` to render "Cancelled" (not "Failed") for a session whose `error` is the cancellation marker; wire it into `SessionDetailPage.tsx` / `SessionDetailDrawer.tsx`.

## 3. Verification

- [x] 3.1 Backend: targeted spawner/runtime-adapter/core_tools/conversations tests plus full `tests/daemon/` + `tests/core/` regression run.
- [x] 3.2 Frontend: `tsc -b`, `eslint .`, full `npm run build`, full `vitest run`.
- [x] 3.3 Sync the `dashboard-chat-ui` spec delta into the main spec.
