## MODIFIED Requirements

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
