## MODIFIED Requirements

### Requirement: Typing Indicator

A typing indicator SHALL provide visual and assistive-technology feedback while the butler is processing a response.

#### Scenario: Typing indicator during processing

- **WHEN** a user message has been sent and the assistant response has not started streaming
- **THEN** a typing indicator renders at the bottom of the message thread, left-aligned (assistant position)
- **AND** the indicator shows three animated dots with a bounce animation (staggered `animation-delay`)
- **AND** a single polite, atomic status region announces the current dispatch activity without exposing the decorative dots to assistive technology

#### Scenario: Typing indicator during streaming

- **WHEN** the assistant response is actively streaming tokens
- **THEN** the typing indicator is replaced by the growing assistant message content

### Requirement: SSE Client Integration

The frontend SHALL connect to the SSE streaming endpoints for real-time response delivery.

#### Scenario: SSE connection for new conversation

- **WHEN** `POST /api/butlers/{name}/conversations` is called
- **THEN** the frontend reads the SSE stream using the Fetch API with `ReadableStream`
- **AND** `conversation_created` events create the conversation in local state
- **AND** `dispatch_accepted` with a non-null `routed_butler` renders and announces `Routed to <butler>; waiting for a reply`
- **AND** `dispatch_accepted` with a null `routed_butler` renders and announces `Received by Switchboard; waiting for a reply` without claiming a domain route
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

## ADDED Requirements

### Requirement: Conversation Routing Accountability

The chat interface SHALL make the accountable butler visible whenever a Switchboard conversation has a known routed target.

#### Scenario: Active Switchboard conversation has a routed target

- **WHEN** the active Switchboard conversation has a non-null persisted `routed_butler` or an in-flight `dispatch_accepted` receipt with a non-null target
- **THEN** the conversation header SHALL name that butler as the accountable responder
- **AND** the name SHALL link to `/butlers/{routed_butler}`
- **AND** the header SHALL NOT present Switchboard as the domain owner of the response

#### Scenario: Accepted conversation has no domain target

- **WHEN** an in-flight `dispatch_accepted` receipt has `routed_butler: null`
- **THEN** the interface SHALL identify Switchboard only as the accepting service
- **AND** it SHALL NOT render or announce a domain butler destination

### Requirement: Conversation Read Recovery

The chat interface SHALL distinguish failed conversation reads from an empty conversation and offer non-destructive recovery.

#### Scenario: Conversation list read fails

- **WHEN** the conversation-list query fails
- **THEN** the list area SHALL render a visible `role="alert"` failure message with a retry control
- **AND** activating retry SHALL refetch the existing list query without creating, archiving, or changing a conversation

#### Scenario: Message-history read fails

- **WHEN** the active conversation's message-history query fails
- **THEN** the active chat area SHALL render a visible `role="alert"` failure message with a retry control
- **AND** any already-visible local messages, selected conversation, and draft text SHALL remain available while the error is shown and during retry
