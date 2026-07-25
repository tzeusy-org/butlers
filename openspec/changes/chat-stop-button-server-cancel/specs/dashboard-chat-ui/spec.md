## MODIFIED Requirements

### Requirement: Message Input Area

The input area SHALL provide text entry and message sending controls,
including cancellation of an active response.

#### Scenario: Text input

- **WHEN** the user types in the message input
- **THEN** the textarea auto-grows up to a maximum height
- **AND** Enter sends the message (Shift+Enter inserts a newline)

#### Scenario: Send button

- **WHEN** the input has content and no message is currently streaming
- **THEN** the send button is enabled
- **AND** clicking it (or pressing Enter) submits the message

#### Scenario: Input disabled during streaming

- **WHEN** a message is streaming
- **THEN** the text input is disabled
- **AND** a "Stop" button replaces the send button, which calls the
  server-side cancel endpoint (see Stream cancellation below) rather than
  only detaching the client's own stream watch

#### Scenario: Starting a new conversation from empty state

- **WHEN** no conversation is active
- **THEN** the input area still functions, and sending a message creates a
  new conversation

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
  /api/butlers/{name}/conversations/{conversation_id}/cancel` with a pending
  ("Stopping…") state on the Stop button, disabling it against a second click
- **AND** the backend resolves the conversation's active turn to its
  in-flight session and kills the routed butler's runtime subprocess (not
  merely detaching a watcher) via the `cancel_session` MCP tool
- **AND** only once the server confirms `cancelled: true` does the frontend
  abort its own SSE watch (`AbortController`) and render the partial
  assistant message with a "Cancelled by owner" indicator, distinct from the
  generic "Interrupted" indicator used for unrelated client-side aborts
  (e.g. component unmount, switching conversations)
- **AND** the input is re-enabled

#### Scenario: Stop click on an already-finished turn is a benign no-op

- **WHEN** the user clicks "Stop" but the turn already completed on the
  routed butler (`already_finished: true` in the cancel response)
- **THEN** the frontend stops watching the stream without rendering
  "Cancelled by owner" or any other claim that it stopped something —
  the (already-arrived or arriving) reply is unaffected

#### Scenario: A failed cancel attempt is never rendered as calm

- **WHEN** the cancel request itself fails (e.g. the routed butler is
  unreachable) so the server cannot confirm the session was killed
- **THEN** the frontend surfaces the failure message inline in the thread
  and re-enables the Stop button for another attempt
- **AND** it SHALL NOT render "Cancelled by owner", "Interrupted", or any
  other terminal-state indicator implying the session actually stopped

### Requirement: Session Linkage Navigation

Assistant messages SHALL link to their corresponding butler sessions for drill-down.

#### Scenario: Session link on assistant message

- **WHEN** an assistant message has a non-null `session_id`
- **THEN** a small link icon renders next to the message metadata
- **AND** clicking it navigates to `/sessions/{session_id}` in a new tab (or the same tab with a back-navigation path)

#### Scenario: Request lineage link

- **WHEN** an assistant message has a non-null `request_id`
- **THEN** a "View lineage" link navigates to the ingestion event detail view at `/ingestion?event={request_id}`

#### Scenario: A cancelled session's detail renders a first-class "Cancelled" state

- **WHEN** a session's `sessions.error` is the owner-cancellation marker
  (written by `Spawner.cancel_session()`)
- **THEN** the session detail status badge (`/sessions/{id}` and the session
  detail drawer) renders "Cancelled", not the generic destructive "Failed"
  badge used for other error outcomes
