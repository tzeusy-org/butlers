## Why

The chat "Stop" button (JARVIS pursuit run 07, ranked move #2, bu-ep4ks.2) is a
placebo: clicking it only aborts the client's own SSE watch
(`FloatingChatWidget.tsx` / `ChatPanel.tsx` `handleStop`), while the backend
(`conversations.py` `_stream_conversation_response`) simply detaches — the
routed butler's spawned session keeps running, keeps spending, and the owner
has no real emergency brake on the highest-trust dashboard surface. The
current `dashboard-chat-ui` spec (Stream cancellation scenario) documents this
client-only behavior as normative, so it must change alongside the fix.

## What Changes

- Add a server-side cancel endpoint (`POST
  /api/butlers/{name}/conversations/{id}/cancel`) that resolves the
  conversation's active turn to a live session and kills the actual runtime
  subprocess via a new `cancel_session` MCP tool backed by
  `Spawner.cancel_session()`, rather than only detaching the client.
- Wire both Stop buttons (`FloatingChatWidget`, `ChatPanel`) to the new
  endpoint with a pending state; a failed cancel attempt surfaces honestly in
  the thread instead of being silently dropped or rendered as a false
  "stopped" confirmation.
- Render "Cancelled by owner" as a distinct terminal state, separate from the
  generic client-side "Interrupted" indicator, in both the chat message
  thread and the session detail status badge.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `dashboard-chat-ui`: Stop is a server-confirmed cancellation, not a
  client-side stream detach; adds a distinct "Cancelled by owner" terminal
  state alongside the existing "Interrupted" client-abort indicator.

## Impact

- Backend: `src/butlers/core/spawner.py` (session_id-keyed invoke-task
  registry, `cancel_session()`, CancelledError handling distinguishing
  owner-cancel from shutdown drain), the four runtime adapters (kill the CLI
  subprocess on cancellation instead of orphaning it),
  `src/butlers/core_tools/_sessions.py` (new unconditionally-registered
  `cancel_session` MCP tool), `src/butlers/api/routers/conversations.py` (new
  cancel endpoint, process-local active-turn registry).
- Frontend: `FloatingChatWidget.tsx`, `ChatPanel.tsx`, `MessageThread.tsx`,
  `MessageInput.tsx`, `api/client.ts`, `api/types.ts`,
  `components/sessions/StatusBadge.tsx`.
- No database migration — session outcome remains the existing
  success/error-text convention (no status enum), extended with one new
  `error` marker string (`"Cancelled by owner"`).
