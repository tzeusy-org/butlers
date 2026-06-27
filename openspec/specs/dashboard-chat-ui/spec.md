# Dashboard Chat UI

## Purpose

Provides the frontend conversational interface for the Butlers dashboard, enabling operators to interact with any butler directly from the butler detail page. The chat UI surfaces per-butler conversation threads with real-time response streaming, markdown rendering, tool call visibility, cost indicators, and conversation management — all within the existing dashboard shell design system.

## ADDED Requirements

### Requirement: Chat Panel on Butler Detail Page

The chat interface renders as a slide-out panel on the butler detail page (`/butlers/:name`), toggled by a dedicated button in the butler detail header area.

#### Scenario: Chat panel toggle

- **WHEN** the user clicks the "Chat" button on the butler detail page
- **THEN** a slide-out panel opens from the right side of the viewport at 480px width
- **AND** the panel uses the existing `Sheet` component with `side="right"`
- **AND** the panel header shows the butler name and a close button
- **AND** the main content area (butler detail) remains visible and scrollable behind the panel

#### Scenario: Chat panel persistence across tabs

- **WHEN** the chat panel is open and the user switches between butler detail tabs (Overview, Sessions, State, etc.)
- **THEN** the chat panel remains open and retains its state

#### Scenario: Chat panel responsive behavior

- **WHEN** the viewport width is below the `sm` breakpoint (640px)
- **THEN** the chat panel opens as a full-width overlay instead of a side panel (`w-full sm:max-w-[480px]`)
- **AND** the standard Sheet close button is retained for navigation

### Requirement: Conversation List Sidebar

Within the chat panel, a conversation list allows switching between threads or starting new ones.

#### Scenario: Conversation list renders

- **WHEN** the chat panel opens
- **THEN** the left portion (200px, collapsible) shows the conversation list for the current butler
- **AND** conversations are sorted by `updated_at DESC` (most recent first)
- **AND** each conversation shows the title (truncated to 2 lines) and a relative timestamp (e.g., "2h ago")
- **AND** the active conversation is highlighted with `bg-accent`
- **AND** a "New conversation" button appears at the top of the list with a `+` icon

#### Scenario: Conversation list empty state

- **WHEN** the butler has no conversations
- **THEN** an `EmptyState` component renders with message "No conversations yet" and a "Start a conversation" action button

#### Scenario: Conversation list collapsed mode

- **WHEN** the user clicks the collapse toggle on the conversation list
- **THEN** the list collapses to an icon-only column (48px) showing only the first letter of each conversation title
- **AND** the chat area expands to fill the available width
- **AND** collapse state is stored in `localStorage` under `butlers:chat-sidebar-collapsed`

### Requirement: Message Thread Display

The active conversation renders as a scrollable message thread with user and assistant messages differentiated by alignment and styling.

#### Scenario: User message rendering

- **WHEN** a user message is displayed in the thread
- **THEN** it renders right-aligned with `bg-primary text-primary-foreground` styling
- **AND** it shows the message content and a relative timestamp below

#### Scenario: Assistant message rendering

- **WHEN** an assistant message is displayed in the thread
- **THEN** it renders left-aligned with `bg-muted` styling
- **AND** the content is rendered as markdown (via the `SimpleMarkdown` renderer supporting fenced code blocks and newline-preserving paragraphs; links, lists, and tables are not yet parsed)
- **AND** below the content: model name badge, token count (`{input}+{output} tokens`), and duration (`{N}ms`) render in `text-xs text-muted-foreground`

#### Scenario: Auto-scroll to latest message

- **WHEN** a new message is added to the thread (user or assistant)
- **THEN** the thread scrolls to the bottom to show the latest message
- **AND** if the user has manually scrolled up (more than 100px from bottom), auto-scroll is suppressed until they scroll back to the bottom

#### Scenario: Tool call visibility

- **WHEN** an assistant message includes `tool_calls`
- **THEN** a collapsible "Tool calls" section renders below the message content
- **AND** each tool call shows the tool name and a truncated argument summary
- **AND** clicking a tool call expands to show full arguments and result as formatted JSON

#### Scenario: Error message rendering

- **WHEN** an assistant message has a non-null `error` field
- **THEN** the message renders with a `destructive` border-left accent
- **AND** the error text is shown below any partial content in `text-destructive text-sm`

### Requirement: Message Input Area

The message input area occupies the bottom of the chat panel with a text input and send controls.

#### Scenario: Text input

- **WHEN** the chat panel is active with a conversation (or ready to start a new one)
- **THEN** a `Textarea` component renders at the bottom of the panel with placeholder "Type a message..."
- **AND** the textarea auto-grows with content (up to 200px max height) and scrolls internally beyond that
- **AND** pressing `Enter` sends the message (without Shift)
- **AND** pressing `Shift+Enter` inserts a newline

#### Scenario: Send button

- **WHEN** the textarea has non-empty content
- **THEN** a send button (arrow-up icon) renders at the right edge of the input area
- **AND** the button uses `default` variant at `icon` size (size-9)
- **AND** clicking the button sends the message and clears the input

#### Scenario: Input disabled during streaming

- **WHEN** an assistant response is currently streaming
- **THEN** the textarea and send button are disabled
- **AND** a "Stop" button replaces the send button, which cancels the active stream

#### Scenario: Starting a new conversation from empty state

- **WHEN** no conversation is selected and the user types a message and sends it
- **THEN** a new conversation is created via `POST /api/butlers/{name}/conversations` with the message
- **AND** the new conversation appears in the conversation list and is selected

### Requirement: Typing Indicator

A typing indicator provides visual feedback while the butler is processing a response.

#### Scenario: Typing indicator during processing

- **WHEN** a user message has been sent and the assistant response has not started streaming
- **THEN** a typing indicator renders at the bottom of the message thread, left-aligned (assistant position)
- **AND** the indicator shows three animated dots with a bounce animation (staggered `animation-delay`)

#### Scenario: Typing indicator during streaming

- **WHEN** the assistant response is actively streaming tokens
- **THEN** the typing indicator is replaced by the growing assistant message content

### Requirement: Cost Indicator

Each conversation and message displays cost-related metrics for operator awareness.

#### Scenario: Per-message cost display

- **WHEN** an assistant message has `input_tokens` and `output_tokens`
- **THEN** a cost estimate is displayed alongside the token counts using the dashboard's existing `PricingConfig` model-to-price mapping
- **AND** the format is e.g., `~$0.0400` in `text-xs text-muted-foreground`

#### Scenario: Conversation total cost

- **WHEN** a conversation is selected and has messages with token counts
- **THEN** the conversation header shows the total estimated cost for the conversation
- **AND** the total is the sum of per-message cost estimates

### Requirement: Conversation Quick-Switch

Operators can quickly switch between conversations using keyboard shortcuts.

#### Scenario: Keyboard navigation in conversation list

- **WHEN** the chat panel is open and the user presses `Ctrl+Shift+Up` or `Ctrl+Shift+Down`
- **THEN** the active conversation changes to the previous or next conversation in the list
- **AND** the message thread updates to show the newly selected conversation

#### Scenario: Quick-switch does not conflict with text input

- **WHEN** the cursor is focused in the message textarea
- **THEN** `Ctrl+Shift+Up/Down` still triggers conversation switching (not text editing)

### Requirement: Conversation Search UI

A search input in the conversation list enables full-text search across conversation history.

#### Scenario: Search input

- **WHEN** the user types in the search input at the top of the conversation list
- **THEN** a debounced search request is sent to `GET /api/butlers/{name}/conversations/search?q={query}` after 300ms of inactivity
- **AND** the conversation list is replaced with search results showing conversation title and a highlighted snippet

#### Scenario: Clear search

- **WHEN** the user clears the search input (empty text or clicks X)
- **THEN** the conversation list reverts to the standard chronological listing

### Requirement: SSE Client Integration

The frontend connects to the SSE streaming endpoints for real-time response delivery.

#### Scenario: SSE connection for new conversation

- **WHEN** `POST /api/butlers/{name}/conversations` is called
- **THEN** the frontend reads the SSE stream using the Fetch API with `ReadableStream`
- **AND** `conversation_created` events create the conversation in local state
- **AND** `token` events append content to the active assistant message
- **AND** `message_complete` events finalize the message with metadata (model, tokens, duration, tool calls)
- **AND** `error` events display the error in the message thread
- **AND** `done` events close the stream and re-enable the input

#### Scenario: SSE connection for follow-up

- **WHEN** `POST /api/butlers/{name}/conversations/{id}/messages` is called
- **THEN** the same SSE event handling applies as for new conversations (without `conversation_created`)

#### Scenario: Stream cancellation

- **WHEN** the user clicks the "Stop" button during streaming
- **THEN** the fetch request is aborted via `AbortController`
- **AND** the partial assistant message is retained in the thread with an "Interrupted" indicator
- **AND** the input is re-enabled

### Requirement: Conversation React Query Hooks

TanStack Query hooks manage conversation data fetching and caching.

#### Scenario: useConversations hook

- **WHEN** `useConversations(butlerName, status)` is called
- **THEN** it returns a paginated list of conversations using `useQuery` with key `["conversations", butlerName, "list", params]`
- **AND** `staleTime` is 10 seconds (conversations update frequently during active chat)

#### Scenario: useConversationMessages hook

- **WHEN** `useConversationMessages(butlerName, conversationId)` is called
- **THEN** it returns the message list using `useQuery` with key `["conversation-messages", butlerName, conversationId]`
- **AND** `staleTime` is 0 (always refetch when switching conversations)

#### Scenario: Mutation invalidation

- **WHEN** a new message is sent or a conversation is created
- **THEN** the `["conversations", butlerName]` query is invalidated to refresh the list
- **AND** the `["conversation-messages", butlerName, conversationId]` query is invalidated after `message_complete`

### Requirement: Session Linkage Navigation

Assistant messages link to their corresponding butler sessions for drill-down.

#### Scenario: Session link on assistant message

- **WHEN** an assistant message has a non-null `session_id`
- **THEN** a small link icon renders next to the message metadata
- **AND** clicking it navigates to `/sessions/{session_id}` in a new tab (or the same tab with a back-navigation path)

#### Scenario: Request lineage link

- **WHEN** an assistant message has a non-null `request_id`
- **THEN** a "View lineage" link navigates to the ingestion event detail view at `/ingestion?event={request_id}`
