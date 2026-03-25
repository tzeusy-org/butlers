## 1. Source Channel and Trigger Source Extensions

- [ ] 1.1 Add `"dashboard"` to `SourceChannel` Literal type in `roster/switchboard/tools/routing/contracts.py`
- [ ] 1.2 Add `"dashboard": frozenset({"internal"})` to `_ALLOWED_PROVIDERS_BY_CHANNEL` in `roster/switchboard/tools/routing/contracts.py`
- [ ] 1.3 Add `"dashboard"` to `TRIGGER_SOURCES` frozenset in `src/butlers/core/sessions.py`
- [ ] 1.4 Add tests for dashboard channel-provider validation and trigger source validation

## 2. Database Migration

- [ ] 2.1 Create Alembic migration for `shared.dashboard_conversations` table with columns: id (UUID7 PK), butler_name, title, status, created_at, updated_at, message_count, total_input_tokens, total_output_tokens, total_duration_ms
- [ ] 2.2 Create `shared.dashboard_messages` table in the same migration with columns: id (UUID7 PK), conversation_id (FK CASCADE), role, content, created_at, session_id, model_name, input_tokens, output_tokens, duration_ms, tool_calls (JSONB), error, request_id
- [ ] 2.3 Add composite indexes: `(butler_name, status, updated_at DESC)` and `(butler_name, updated_at DESC)` on conversations; `(conversation_id, created_at ASC)` on messages

## 3. Conversation Persistence Layer

- [ ] 3.1 Create `src/butlers/api/conversations.py` with data access functions: `conversation_create`, `conversation_get`, `conversation_list`, `conversation_update`, `conversation_search`, `conversation_summary`
- [ ] 3.2 Implement `message_create`, `message_list`, `message_get` data access functions for dashboard messages
- [ ] 3.3 Implement conversation aggregate update logic (increment message_count, total_input_tokens, total_output_tokens, total_duration_ms on each assistant message)
- [ ] 3.4 Add unit tests for all data access functions

## 4. Dashboard Ingestion Envelope Construction

- [ ] 4.1 Create `src/butlers/api/conversation_envelope.py` with `build_dashboard_envelope(conversation_id, message_id, message_text, conversation_context)` function that constructs a valid `ingest.v1` envelope
- [ ] 4.2 Implement conversation context builder that serializes last N message pairs as text preamble for follow-up messages
- [ ] 4.3 Add tests for envelope construction and context serialization

## 5. Conversation API Endpoints

- [ ] 5.1 Create `src/butlers/api/routers/conversations.py` with APIRouter for conversation endpoints
- [ ] 5.2 Implement `GET /api/butlers/{name}/conversations` — list conversations with status filter and pagination
- [ ] 5.3 Implement `POST /api/butlers/{name}/conversations` — create conversation with first message, submit ingest envelope, return SSE streaming response
- [ ] 5.4 Implement `POST /api/butlers/{name}/conversations/{conversation_id}/messages` — send follow-up message with SSE streaming response
- [ ] 5.5 Implement `PATCH /api/butlers/{name}/conversations/{conversation_id}` — update title or status (archive/unarchive)
- [ ] 5.6 Implement `GET /api/butlers/{name}/conversations/{conversation_id}/messages` — list messages with pagination
- [ ] 5.7 Implement `GET /api/butlers/{name}/conversations/search` — full-text search across conversation messages
- [ ] 5.8 Implement `GET /api/butlers/{name}/conversations/summary` — aggregate statistics
- [ ] 5.9 Create Pydantic response models: `ConversationSummary`, `ConversationMessage`, `ConversationSearchResult`, `ConversationStats`
- [ ] 5.10 Register the conversations router in `create_app()` in `src/butlers/api/app.py`

## 6. SSE Response Streaming

- [ ] 6.1 Implement SSE streaming logic in the POST conversation/message endpoints: submit ingest envelope, poll for session completion, stream result as SSE events (`conversation_created`, `token`, `message_complete`, `error`, `done`)
- [ ] 6.2 Implement keepalive comment emission (every 15 seconds during processing)
- [ ] 6.3 Implement assistant message creation on `message_complete` (persist model_name, tokens, duration, tool_calls, session_id, request_id)
- [ ] 6.4 Implement conversation aggregate update after assistant message persistence
- [ ] 6.5 Add tests for SSE event format and streaming lifecycle

## 7. Discretion Bypass for Dashboard Channel

- [ ] 7.1 Add dashboard channel exemption in Switchboard ingestion path — skip discretion evaluation when `source.channel == "dashboard"`
- [ ] 7.2 Add test verifying dashboard messages bypass discretion

## 8. Frontend: Conversation API Client and Types

- [ ] 8.1 Add TypeScript types: `Conversation`, `ConversationMessage`, `ConversationSearchResult`, `ConversationStats` in `frontend/src/api/types.ts`
- [ ] 8.2 Add API client functions: `getConversations`, `createConversation`, `sendMessage`, `updateConversation`, `getMessages`, `searchConversations`, `getConversationSummary` in `frontend/src/api/client.ts`
- [ ] 8.3 Implement SSE stream reader utility for conversation POST responses

## 9. Frontend: TanStack Query Hooks

- [ ] 9.1 Create `frontend/src/hooks/useConversations.ts` with `useConversations(butlerName, status)` query hook (staleTime: 10s)
- [ ] 9.2 Create `useConversationMessages(butlerName, conversationId)` query hook (staleTime: 0)
- [ ] 9.3 Create `useConversationSummary(butlerName)` query hook
- [ ] 9.4 Create mutation hooks for `useCreateConversation`, `useSendMessage`, `useUpdateConversation` with proper query invalidation

## 10. Frontend: Chat Panel Component

- [ ] 10.1 Create `frontend/src/components/chat/ChatPanel.tsx` — slide-out Sheet component (480px right panel, full-width on mobile)
- [ ] 10.2 Create `frontend/src/components/chat/ConversationList.tsx` — collapsible conversation sidebar (200px) with search, new conversation button, active highlighting
- [ ] 10.3 Create `frontend/src/components/chat/MessageThread.tsx` — scrollable message list with auto-scroll, user/assistant alignment, markdown rendering
- [ ] 10.4 Create `frontend/src/components/chat/MessageInput.tsx` — auto-growing textarea with Enter-to-send, Shift+Enter for newline, send/stop buttons
- [ ] 10.5 Create `frontend/src/components/chat/TypingIndicator.tsx` — three-dot pulsing animation
- [ ] 10.6 Create `frontend/src/components/chat/ToolCallDetails.tsx` — collapsible tool call display with formatted JSON
- [ ] 10.7 Create `frontend/src/components/chat/MessageMetadata.tsx` — model badge, token counts, cost estimate, duration, session link
- [ ] 10.8 Create `frontend/src/components/chat/ConversationHeader.tsx` — conversation title (editable), total cost, close button

## 11. Frontend: Chat Panel Integration

- [ ] 11.1 Add "Chat" button to butler detail page header (`/butlers/:name`)
- [ ] 11.2 Wire ChatPanel open/close state in butler detail page
- [ ] 11.3 Implement SSE stream consumption in ChatPanel — handle `conversation_created`, `token`, `message_complete`, `error`, `done` events
- [ ] 11.4 Implement stream cancellation via AbortController on "Stop" button
- [ ] 11.5 Implement `Ctrl+Shift+Up/Down` keyboard shortcuts for conversation quick-switch
- [ ] 11.6 Persist conversation list collapse state in localStorage

## 12. Frontend: Empty and Error States

- [ ] 12.1 Add empty state for conversation list ("No conversations yet" with start button)
- [ ] 12.2 Add error rendering for failed assistant messages (destructive border, error text)
- [ ] 12.3 Add "Interrupted" indicator for cancelled streams
- [ ] 12.4 Add loading skeleton for conversation list and message thread

## 13. Tests and Validation

- [ ] 13.1 Add integration tests for conversation API endpoints (create, list, update, search, messages)
- [ ] 13.2 Add tests for SSE streaming response format
- [ ] 13.3 Add tests for dashboard ingest envelope construction and Switchboard acceptance
- [ ] 13.4 Verify end-to-end: dashboard message -> Switchboard ingestion -> session creation -> session completion -> message persistence with lineage
