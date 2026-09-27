# Dashboard Chat UI

## Purpose

Provides the frontend conversational interface for the Butlers dashboard, enabling operators to interact with any butler directly from the butler detail page. The chat UI surfaces per-butler conversation threads with real-time response streaming, markdown rendering, tool call visibility, cost indicators, and conversation management — all within the existing dashboard shell design system.

## Requirements

### Requirement: Chat Panel on Butler Detail Page

The chat interface SHALL render as a slide-out panel on the butler detail page (`/butlers/:name`), toggled by a dedicated button in the butler detail header area. This is a per-butler thread view, distinct from the global "Talk to Butlers" surface's three postures (docked rail, full page, popover) described under Requirement: Global Chat Postures — `MessageThread`, `MessageInput`, and the send/stream/stop turn logic (`useConversationTurn`) are shared, but this panel is scoped to one butler's own conversations rather than the Switchboard-routed set.

#### Scenario: Chat panel toggle

- **WHEN** the user clicks the "Chat" button on the butler detail page
- **THEN** the panel opens as a right-side drawer built on the shared sheet primitive
- **AND** the panel header shows the butler name and a close button
- **AND** the main content area (butler detail) remains visible and scrollable behind the panel

#### Scenario: Chat panel persistence across tabs

- **WHEN** the chat panel is open and the user switches between butler detail tabs (Overview, Sessions, State, etc.)
- **THEN** the chat panel remains open and retains its state

#### Scenario: Chat panel responsive behavior

- **WHEN** the viewport is narrow
- **THEN** the drawer opens full-width instead of as a side panel
- **AND** the standard close button is retained for navigation

### Requirement: Global Chat Postures

The global "Talk to Butlers" surface (Switchboard-routed conversations, distinct from the per-butler panel above) SHALL present exactly one of three postures at a time, chosen by viewport width and an explicit collapse/expand toggle, never stacked:

- **Docked rail** — `ChatDock`, rendered in the shell's `chatDock` slot at or above the `xl` breakpoint while open; the default posture there. The shell owns only the slot (see `dashboard-shell`, Requirement: Chat Dock Rail (>= xl breakpoint)). The dock is resizable within bounds, and both its width and its open/collapsed state are persisted per viewer.
- **Full page** — `/chat` and `/chat/:conversationId`, reached via the dock/popover's "Open in full page" action, a copy-link, or cmdk recall.
- **Popover** — the floating bottom-right widget (`FloatingChatWidget`), the only posture below `xl` or while the dock is collapsed.

The dock and the popover share the same Switchboard-routed conversation set. All three postures render the same `MessageThread`/`MessageInput` components and drive their send/stream/stop turn state through the one shared `useConversationTurn` hook (`frontend/src/hooks/use-conversation-turn.ts`) — there is exactly one implementation of that turn logic, not a per-posture copy.

#### Scenario: Postures are mutually exclusive

- **WHEN** any dashboard route is rendered
- **THEN** at most one of {docked rail, full page, popover} is mounted at a time
- **AND** the docked rail and the popover in particular are never both mounted (the dock's presence is what decides whether the popover renders at all)

#### Scenario: Each mounted instance owns its own turn state

- **WHEN** a conversation is actively streaming in one posture (e.g. the dock) and the operator navigates to `/chat/{id}` for that same conversation in another tab or after the dock collapses
- **THEN** the full-page instance calls `useConversationTurn` independently and does not receive a live mirror of the other posture's in-flight stream — it resumes from the persisted message history once the turn completes
- **AND** this is a deliberate scope boundary (cross-posture live-turn mirroring is out of scope for this capability)

#### Scenario: Popover trigger reopens a collapsed dock

- **WHEN** the dock has been collapsed while the viewport is at or above the `xl` breakpoint and the operator activates the popover trigger
- **THEN** the dock reopens instead of the popover opening

### Requirement: Full-Page Chat Route

`/chat` and `/chat/:conversationId` SHALL render the full-page chat posture (`ChatPage`), resolving a specific conversation cross-butler by id.

#### Scenario: Bare /chat starts or resumes composing for the Switchboard butler

- **WHEN** `/chat` is visited with no `:conversationId`
- **THEN** the page renders the composer for a not-yet-created conversation against the Switchboard butler
- **AND** sending the first message creates the conversation and the URL updates (via a replace navigation) to `/chat/{newId}` once the id is known

#### Scenario: /chat/:conversationId resolves cross-butler

- **WHEN** `/chat/:conversationId` is visited
- **THEN** the frontend calls `GET /api/conversations/{id}` to resolve the owning `butler_name` before fetching messages — the conversation may belong to any butler, not only the Switchboard (any assistant message's copy-link, wherever it renders, points here)
- **AND** once resolved, messages are fetched via the existing per-butler `GET /api/butlers/{name}/conversations/{id}/messages` route using that resolved `butler_name`

#### Scenario: Unknown conversation id renders an explicit not-found state

- **WHEN** `GET /api/conversations/{id}` returns 404 for the `:conversationId` in the URL
- **THEN** the page renders an explicit "Conversation not found" empty state with an action to start a new conversation
- **AND** it SHALL NOT render a blank or infinitely-loading thread

#### Scenario: A `#m-{messageId}` fragment scrolls to and focuses that message

- **WHEN** `/chat/:conversationId#m-{messageId}` is visited and the thread finishes loading
- **THEN** the named message bubble is scrolled into view and receives keyboard focus (the bubble carries `tabIndex={-1}` for this purpose)
- **AND** if no message with that id is in the loaded thread, the thread still renders normally with no scroll and no error — a stale or incorrect fragment is a silent no-op

#### Scenario: Copy-link produces a full-page deep link

- **WHEN** the operator clicks the copy-link action on an assistant message (any posture, any butler's conversation)
- **THEN** the clipboard receives `{origin}/chat/{conversationId}#m-{messageId}`, matching the anchor `scrollToMessageAnchor` looks for

### Requirement: Recent-Thread Recall (Command Palette)

Recently-updated Switchboard conversations SHALL be reachable through the command palette (cmdk), alongside the existing "Talk to Butlers" command, navigating directly to `/chat/{id}`.

#### Scenario: Recent threads appear as palette commands

- **WHEN** the command palette is opened
- **THEN** recent Switchboard conversations each appear as a command labeled with the conversation's title (or "Untitled conversation")
- **AND** selecting one navigates to `/chat/{conversationId}`
- **AND** this recall list is registered once, mounted regardless of which chat posture (dock or popover) is currently showing

### Requirement: Conversation List Sidebar

Within the chat panel, a conversation list SHALL allow switching between threads or starting new ones.

#### Scenario: Conversation list renders

- **WHEN** the chat panel opens
- **THEN** a collapsible column beside the thread shows the conversation list for the current butler
- **AND** conversations are sorted by `updated_at DESC` (most recent first)
- **AND** each conversation shows the title (truncated to 2 lines) and a relative timestamp (e.g., "2h ago")
- **AND** the active conversation is visibly highlighted
- **AND** a "New conversation" action appears at the top of the list

#### Scenario: Conversation list empty state

- **WHEN** the butler has no conversations
- **THEN** an `EmptyState` component renders with message "No conversations yet" and a "Start a conversation" action button

#### Scenario: Conversation list collapsed mode

- **WHEN** the user clicks the collapse toggle on the conversation list
- **THEN** the list collapses to a narrow column showing only the first letter of each conversation title
- **AND** the chat area expands to fill the available width
- **AND** the collapse state is persisted per viewer

### Requirement: Message Thread Display

The active conversation SHALL render as a scrollable message thread with user and assistant messages differentiated by alignment and styling. Assistant answers SHALL use one safe rendering path for stored and streaming content, and SHALL present citations and authorship without implying trust the server cannot establish.

ID: REQ-dashboard-chat-ui-004
Source: heart-and-soul/vision.md § What Butlers Is Not (Not an experiment); dashboard-design-language § Type System, Voice Surface, Butler Letter-Mark, and Interaction Affordances; design.md Decisions 1-4
Scope: v1-mandatory

#### Scenario: User message rendering

- **WHEN** a user message is displayed in the thread
- **THEN** it renders right-aligned with `bg-primary text-primary-foreground` styling
- **AND** it shows the message content and a relative timestamp below

#### Scenario: Assistant message rendering

- **WHEN** an assistant message is displayed in the thread
- **THEN** it renders left-aligned with `bg-muted` styling
- **AND** sanitized markdown renders headings, lists, inline code, emphasis, blockquotes, fenced code, and tables while raw HTML and executable markup render no active element
- **AND** answer prose uses the Voice type role, structural labels use Body, and code plus tabular numeric cells use Mono with tabular numerals
- **AND** butler, time, session, model, token, duration, and cost metadata follow the attribution and disclosure scenarios below

#### Scenario: Streaming markdown remains safe and stable

- **WHEN** an assistant answer is rendered before the stream has completed, including an unterminated fenced code block or other half-open markdown construct
- **THEN** the partial content uses the same sanitized renderer as the stored answer and cannot create raw HTML, an executable element, or an unsafe link
- **AND** an unterminated fence renders as code without breaking the surrounding layout
- **AND** when later tokens close the construct, the answer re-renders into its completed structure without duplicated or lost text
- **AND** `message_complete` renders the persisted content byte-for-byte as renderer input, with no duplicated, lost, or synthetic fence text; rendered semantics remain governed by the allowed-markdown clauses above

#### Scenario: Internal citation navigation

- **WHEN** an assistant message carries a citation whose `kind` is `internal`
- **THEN** the citation renders as a labeled, keyboard-focusable link and activates through the dashboard router
- **AND** activation performs in-SPA navigation to the server-validated target without assigning `window.location` or causing a full page reload
- **AND** focus is visible according to the Interaction Affordances contract

#### Scenario: External citation navigation

- **WHEN** an assistant message carries a citation whose `kind` is `external`
- **THEN** the citation renders as a labeled HTTPS link with `rel="noopener noreferrer"`
- **AND** the external destination is visually and accessibly distinguishable from an internal dashboard destination
- **AND** the citation does not claim that the destination's content or availability was verified

#### Scenario: Unlinked legacy citation remains honest

- **WHEN** an assistant message carries a citation whose `kind` is `unlinked` or whose once-valid destination is unavailable when followed
- **THEN** an unlinked citation renders as text rather than as a fabricated destination
- **AND** a destination that later returns a not-found or unavailable state uses that destination surface's existing recovery behavior
- **AND** neither state is labeled as verified evidence

#### Scenario: Per-message butler attribution

- **WHEN** an assistant message carries a non-null server-derived `routed_butler`
- **THEN** its attribution line renders the existing `ButlerMark`, the butler name, and the relative message time
- **AND** a non-null `session_id` adds a keyboard-focusable `Session ->` navigation link beside that attribution
- **AND** the line describes only that message and does not infer authorship from the conversation's sticky route or a live phase event

#### Scenario: Unknown message author is not guessed

- **WHEN** an assistant message has `routed_butler = null`, including a legacy or non-session-authored row
- **THEN** the thread renders no butler mark or butler name for that message
- **AND** it does not substitute the conversation owner, sticky `routed_butler`, current route phase, model name, or session lookup as the author
- **AND** any available timestamp, error, and session navigation remain usable without an attribution claim

#### Scenario: Secondary details are disclosed accessibly

- **WHEN** an assistant message has model, token, duration, or cost details
- **THEN** one disclosure control contains those secondary details and is closed by default
- **AND** the control exposes its expanded state, has an accessible name, and toggles by keyboard
- **AND** the primary butler, time, and available session navigation remain visible without opening it

#### Scenario: Narrow viewport contains wide answers

- **WHEN** an answer containing a table or long code line is displayed at a 360 CSS-pixel viewport
- **THEN** the message and page do not acquire horizontal overflow
- **AND** each wide table or code block scrolls within its own named region
- **AND** the overflow region is keyboard focusable and keyboard scrollable
- **AND** citation and disclosure controls retain at least the active Viewport and Modality Contract's coarse-pointer target floor

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

The message input area SHALL occupy the bottom of the chat panel with a text input and send controls, including cancellation of an active response.

#### Scenario: Text input

- **WHEN** the chat panel is active with a conversation (or ready to start a new one)
- **THEN** a textarea renders at the bottom of the panel with placeholder "Type a message..."
- **AND** the textarea auto-grows with content up to a bounded height and scrolls internally beyond that
- **AND** pressing `Enter` sends the message (without Shift)
- **AND** pressing `Shift+Enter` inserts a newline

#### Scenario: Send button

- **WHEN** the textarea has non-empty content
- **THEN** a send button renders at the right edge of the input area
- **AND** clicking the button sends the message and clears the input

#### Scenario: Input disabled during streaming

- **WHEN** an assistant response is currently streaming
- **THEN** the textarea and send button are disabled
- **AND** a "Stop" button replaces the send button, which calls the
  server-side cancel endpoint (see Stream cancellation below) rather than
  only detaching the client's own stream watch

#### Scenario: Starting a new conversation from empty state

- **WHEN** no conversation is selected and the user types a message and sends it
- **THEN** a new conversation is created via `POST /api/butlers/{name}/conversations` with the message
- **AND** the new conversation appears in the conversation list and is selected

### Requirement: Typing Indicator

A typing indicator SHALL provide visual feedback while the butler is processing a response.

#### Scenario: Typing indicator during processing

- **WHEN** a user message has been sent and the assistant response has not started streaming
- **THEN** a typing indicator renders at the bottom of the message thread, left-aligned (assistant position)
- **AND** the indicator shows three animated dots with a bounce animation (staggered `animation-delay`)

#### Scenario: Typing indicator during streaming

- **WHEN** the assistant response is actively streaming tokens
- **THEN** the typing indicator is replaced by the growing assistant message content

### Requirement: Cost Indicator

Each conversation and message SHALL display cost-related metrics for operator awareness.

#### Scenario: Per-message cost display

- **WHEN** an assistant message has `input_tokens` and `output_tokens`
- **THEN** a cost estimate is displayed alongside the token counts using the dashboard's existing `PricingConfig` model-to-price mapping
- **AND** the estimate renders as muted mono metadata in the form `~$0.0400`

#### Scenario: Conversation total cost

- **WHEN** a conversation is selected and has messages with token counts
- **THEN** the conversation header shows the total estimated cost for the conversation
- **AND** the total is the sum of per-message cost estimates

### Requirement: Conversation Quick-Switch

The chat UI SHALL allow operators to quickly switch between conversations using keyboard shortcuts.

#### Scenario: Keyboard navigation in conversation list

- **WHEN** the chat panel is open and the user presses `Ctrl+Shift+Up` or `Ctrl+Shift+Down`
- **THEN** the active conversation changes to the previous or next conversation in the list
- **AND** the message thread updates to show the newly selected conversation

#### Scenario: Quick-switch does not conflict with text input

- **WHEN** the cursor is focused in the message textarea
- **THEN** `Ctrl+Shift+Up/Down` still triggers conversation switching (not text editing)

### Requirement: Conversation Search UI

A search input in the conversation list SHALL enable full-text search across conversation history.

#### Scenario: Search input

- **WHEN** the user types in the search input at the top of the conversation list
- **THEN** a debounced search request is sent to `GET /api/butlers/{name}/conversations/search?q={query}` after 300ms of inactivity
- **AND** the conversation list is replaced with search results showing conversation title and a highlighted snippet

#### Scenario: Clear search

- **WHEN** the user clears the search input (empty text or clicks X)
- **THEN** the conversation list reverts to the standard chronological listing

### Requirement: SSE Client Integration

The frontend SHALL connect to the SSE streaming endpoints for real-time response delivery.

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

#### Scenario: Stream cancellation is a real server-side stop

- **WHEN** the user clicks the "Stop" button during streaming
- **THEN** the frontend calls `POST
  /api/butlers/{name}/conversation-turns/{message_id}/cancel`, where
  `message_id` is the client-created immutable user-turn identifier; this
  SHALL work before a new conversation has delivered its `conversation_id`
  over SSE
- **AND** the Stop button enters a pending ("Stopping…") state that prevents
  a second request and exposes its state to assistive technology
- **AND** the backend records cancellation intent against that durable turn,
  preventing any later Switchboard ingress claim, classifier, target-route,
  recovery, or runtime-invoke transition from starting work for it
- **AND** if a runtime already crossed the invocation boundary, the backend
  resolves every exact registered runtime session and kills the subprocesses
  through the `cancel_session` MCP tool (not merely detaching a watcher)
- **AND** only once the server confirms `cancelled: true` **or** emits the
  terminal `SESSION_CANCELLED` SSE outcome from the durable turn record does
  the frontend abort its own SSE watch (`AbortController`) and render the
  partial assistant message with a "Cancelled by owner" indicator, distinct
  from the generic "Interrupted" indicator used for unrelated client-side
  aborts (e.g. component unmount, switching conversations)
- **AND** the input is re-enabled

#### Scenario: Stop click on an already-finished turn is a benign no-op

- **WHEN** the user clicks "Stop" but the turn already completed on the
  routed butler (`already_finished: true` in the cancel response)
- **THEN** the frontend stops watching the stream without rendering
  "Cancelled by owner" or any other claim that it stopped something —
  the (already-arrived or arriving) reply is unaffected

#### Scenario: A failed cancel attempt is never rendered as calm

- **WHEN** the cancel request itself fails (e.g. the routed butler is
  unreachable), an already-ended runtime is still settling, or an irreversible
  action was already committed, so the server cannot confirm cancellation
- **THEN** the frontend surfaces the returned explanation inline in the thread
  and gives the owner an actionable, truthful Stop state
- **AND** it SHALL NOT render "Cancelled by owner", "Interrupted", or any
  other terminal-state indicator implying the session actually stopped

#### Scenario: A same-message ingress is still in progress

- **WHEN** the SSE stream reports `INGEST_IN_PROGRESS` because another caller
  owns or is settling the same immutable dashboard turn
- **THEN** the frontend retains that logical message and presents a
  "Check again" or history-refresh affordance
- **AND** it SHALL NOT offer Retry or issue a new ingestion request for that
  `message_id`
- **AND** `SESSION_CANCELLED` remains a confirmed terminal Stop outcome, while
  `TURN_OUTCOME_UNKNOWN` suppresses automatic replay and surfaces the
  uncertainty honestly

### Requirement: Conversation React Query Hooks

TanStack Query hooks SHALL manage conversation data fetching and caching.

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

### Requirement: Cross-Butler Conversation Lookup

`GET /api/conversations/{conversation_id}` SHALL resolve a conversation's identity by id alone, independent of which butler owns it, for the `/chat/:conversationId` deep link and cmdk recall.

#### Scenario: Lookup succeeds for any owning butler

- **WHEN** `GET /api/conversations/{id}` is called for a conversation owned by any butler's schema
- **THEN** the response returns that conversation's `id`, `butler_name`, `title`, `status`, `created_at`, `updated_at`, `message_count`, and `routed_butler` (when set) as a raw JSON object, not paginated or wrapped in a `data` envelope — this is a single-resource lookup, not a list
- **AND** no butler-scoped filter is required — `public.dashboard_conversations` is a shared, mount-boundary-safe table keyed by its UUID7 primary key

#### Scenario: Unknown id 404s

- **WHEN** `GET /api/conversations/{id}` is called for an id with no matching row
- **THEN** the response is HTTP 404
- **AND** this endpoint being a single-resource, non-aggregating lookup, the fleet-wide degraded-mode/cursor-pagination envelope conventions do not apply — an unreachable shared pool SHALL instead fail with a hard 503, consistent with other single-resource endpoints that have no partial/degraded rendering to offer

#### Scenario: useConversationById hook

- **WHEN** `useConversationById(conversationId)` is called
- **THEN** it returns the lookup using `useQuery` with key `["conversations", "by-id", conversationId]`
- **AND** `retry` is disabled — a 404 here is a legitimate terminal state, not a transient failure worth retrying

### Requirement: Session Linkage Navigation

Assistant messages SHALL link to their corresponding butler sessions for drill-down, and a
session detail with a linked dashboard message SHALL link back to that exact chat message.

#### Scenario: Session link on assistant message

- **WHEN** an assistant message has a non-null `session_id`
- **THEN** a small link icon renders next to the message metadata
- **AND** clicking it navigates to `/sessions/{session_id}` in a new tab (or the same tab with a back-navigation path)

#### Scenario: Request lineage link

- **WHEN** an assistant message has a non-null `request_id`
- **THEN** a "View lineage" link navigates to the ingestion event detail view at `/ingestion?event={request_id}`

#### Scenario: Session detail links back to the originating chat message

- **WHEN** a session detail has a non-null `linked_message` containing a
  `conversation_id` and `message_id`
- **THEN** an "Asked in chat" link navigates to the canonical full-page chat
  route at `/chat/{conversation_id}#m-{message_id}` using both persisted IDs
- **AND** the link does not require the session's owning butler because the
  destination resolves the conversation cross-butler
- **WHEN** `linked_message` is absent or null, or either persisted identifier
  is absent
- **THEN** no "Asked in chat" link is rendered

#### Scenario: A cancelled session's detail renders a first-class "Cancelled" state

- **WHEN** a session's `sessions.error` is the owner-cancellation marker
  (written by `Spawner.cancel_session()`)
- **THEN** the session detail status badge (`/sessions/{id}` and the session
  detail drawer) renders "Cancelled", not the generic destructive "Failed"
  badge used for other error outcomes
