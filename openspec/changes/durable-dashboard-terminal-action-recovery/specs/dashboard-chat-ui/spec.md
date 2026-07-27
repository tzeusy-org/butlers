## MODIFIED Requirements

### Requirement: SSE Client Integration

The frontend SHALL connect to the SSE streaming endpoints for real-time
response delivery and SHALL reconcile any dashboard terminal action with the
durable conversation read model rather than treating its original stream as the
authority.

ID: REQ-dashboard-chat-ui-001
Source: dashboard-chat-ui § SSE Client Integration; dashboard-conversations REQ-dashboard-conversations-004; dashboard-terminal-action-recovery REQ-dashboard-terminal-action-recovery-004; design.md Decision 6
Scope: v1-mandatory

#### Scenario: SSE connection for new conversation

- **WHEN** `POST /api/butlers/{name}/conversations` is called
- **THEN** the frontend SHALL read the SSE stream using Fetch `ReadableStream`
- **AND** `conversation_created` creates local conversation state, `token`
  appends content, `message_complete` finalizes metadata, `error` displays in
  the thread, and `done` closes the stream and re-enables input

#### Scenario: SSE connection for follow-up

- **WHEN** `POST /api/butlers/{name}/conversations/{id}/messages` is called
- **THEN** the frontend SHALL apply the same SSE handling without a
  `conversation_created` event

#### Scenario: Stream cancellation is a real server-side stop

- **WHEN** the owner clicks Stop during streaming
- **THEN** the frontend SHALL call `POST
  /api/butlers/{name}/conversation-turns/{message_id}/cancel` with a pending
  (`Stopping…`) state on the Stop button, disabling it against a second click
- **AND** the backend SHALL resolve that exact immutable turn to its in-flight
  runtime or terminal action and either kill the routed butler's runtime
  subprocess through `cancel_session` or persist terminal-action Stop intent;
  it SHALL not merely detach a watcher
- **AND** only after the API returns `outcome: "cancelled"` may the frontend
  abort its own SSE watch (`AbortController`) and render the partial assistant
  message with `Cancelled by owner`, distinct from the generic `Interrupted`
  indicator used for unrelated client-side aborts
- **AND** it SHALL re-enable the input

#### Scenario: Stop requires terminal-action reconciliation

- **WHEN** the owner clicks Stop and the server returns
  `outcome: "pending_reconciliation"` or `outcome: "ambiguous"`
- **THEN** the frontend SHALL render that exact durable state and preserve the
  Stop/recovery context in the thread
- **AND** it SHALL NOT render `Cancelled by owner`, `Interrupted`, or a filed
  claim
- **AND** it SHALL invalidate and refetch the current conversation message query
  using the response `message_id`/`conversation_id`, then follow its bounded
  pending-action refresh policy instead of relying on the original SSE stream
- **AND** when the refetched targetless turn has an unconfirmed durable Stop
  intent, it SHALL render `dashboard_turn.state: "pending_cancellation"` rather
  than ordinary pending ingress

#### Scenario: Stop clicks an already-finished turn

- **WHEN** the owner clicks Stop and the server returns
  `outcome: "already_finished"`
- **THEN** the frontend SHALL stop watching without claiming that it stopped
  the already-arrived or arriving reply
- **AND** it SHALL refetch the current durable message record before rendering a
  terminal outcome

#### Scenario: Cancel request fails before a durable outcome

- **WHEN** the cancel request itself fails without a durable action outcome
- **THEN** the frontend SHALL surface the failure inline and re-enable the
  Stop control
- **AND** it SHALL not render a terminal cancellation indicator

### Requirement: Conversation React Query Hooks

TanStack Query hooks SHALL manage conversation data fetching and caching,
including bounded refresh of a terminal action or dashboard turn while its
durable state is still pending ingress or reconciliation.

ID: REQ-dashboard-chat-ui-002
Source: dashboard-chat-ui § Conversation React Query Hooks; dashboard-terminal-action-recovery REQ-dashboard-terminal-action-recovery-003; design.md Decision 6
Scope: v1-mandatory

#### Scenario: useConversations hook

- **WHEN** `useConversations(butlerName, status)` is called
- **THEN** it SHALL return a paginated list using key
  `["conversations", butlerName, "list", params]` and a 10-second stale time

#### Scenario: useConversationMessages hook

- **WHEN** `useConversationMessages(butlerName, conversationId)` is called
- **THEN** it SHALL return the message list using key
  `["conversation-messages", butlerName, conversationId]` and refetch on
  conversation switch

#### Scenario: Pending dashboard outcome is refreshed

- **WHEN** the current conversation contains a
  `terminal_action.state = "pending_reconciliation"` or
  `dashboard_turn.state = "pending_ingress"` or
  `dashboard_turn.state = "pending_reconciliation"` or
  `dashboard_turn.state = "pending_cancellation"`
- **THEN** the messages query SHALL refetch no slower than every 10 seconds
- **AND** for `pending_ingress`, it SHALL stop passive refresh at the durable
  `ingress_recovery_at` boundary and render the owner-initiated exact-message
  recovery affordance instead of dispatching automatically
- **AND** for `pending_cancellation`, it SHALL continue bounded refresh only
  until durable reconciliation projects a concrete outcome or `ambiguous`; the
  server SHALL resolve the state no later than one reconciliation cadence after
  `stop_reconcile_deadline_at`
- **AND** it SHALL stop that pending-outcome refresh after the action or turn
  reaches completed, failed, cancelled, retryable_error, rejected, or ambiguous
  state

#### Scenario: Mutation or reconciliation changes a conversation

- **WHEN** a message is sent, a conversation is created, or a terminal-action
  reconciliation changes its durable state
- **THEN** the frontend SHALL invalidate the relevant conversation-list and
  message queries

#### Scenario: Exact-message recovery hands control back to the durable query

- **WHEN** the owner invokes `POST
  /api/butlers/{name}/conversation-turns/{message_id}/retry-ingress`
- **THEN** the UI SHALL disable a duplicate recovery click until the JSON
  response arrives, then invalidate and refetch that response's exact
  `conversation_id` and `message_id`
- **AND** it SHALL resume the appropriate bounded durable-status refresh only
  when the returned/refetched state is pending; it SHALL render a terminal,
  rejected, retryable, cancellation, or ambiguous result from the read model
  without opening a second SSE stream or inserting another local user message

## ADDED Requirements

### Requirement: Durable Dashboard Outcome Presentation

The dashboard chat UI SHALL render terminal-action and dashboard-turn state from
the durable conversation read model and SHALL never present a filed, routed, or
cancelled claim that the journal cannot prove. An ambiguous terminal action SHALL
expose its sanitized reason and owner-only resolution link, but SHALL not offer
an automatic duplicate retry.

ID: REQ-dashboard-chat-ui-003
Source: heart-and-soul/vision.md § Non-Negotiable Rule 1; dashboard-terminal-action-recovery REQ-dashboard-terminal-action-recovery-005; design.md Decisions 5-6
Scope: v1-mandatory

#### Scenario: Action has a durable outcome

- **WHEN** a user message has a terminal action in its conversation read model
- **THEN** the UI SHALL render its exact durable state and safe reference
- **AND** it SHALL expose manual resolution only for an ambiguous action

#### Scenario: A primary effect completed but acknowledgement failed

- **WHEN** `qa_report` is completed and `conversation_reply` is failed for a
  bug-report action
- **THEN** the UI SHALL render `Report filed; acknowledgement failed` and the
  safe report reference
- **AND** it SHALL not reduce that effect-level truth to a generic `Report
  failed` parent label

#### Scenario: A dead-letter capture completed but acknowledgement failed

- **WHEN** `dead_letter_capture` is completed and `conversation_reply` is failed
  for a dead-letter action
- **THEN** the UI SHALL render `Saved for manual review; acknowledgement failed`
  and the safe capture reference
- **AND** it SHALL not imply that the capture was lost

#### Scenario: Action is ambiguous

- **WHEN** a terminal action is ambiguous
- **THEN** the UI SHALL show the sanitized ambiguity reason and resolution link
- **AND** it SHALL not offer an automatic effect retry

#### Scenario: Route-only outcome is ambiguous

- **WHEN** a message has `terminal_action: null` and
  `dashboard_turn = {target_kind: "route", state: "ambiguous", ...}`
- **THEN** the UI SHALL render `Route outcome could not be confirmed; no second
  delivery was attempted` with the sanitized reason code
- **AND** it SHALL not claim that the routed butler received the statement or
  offer an automatic route, bug-report, or dead-letter retry

#### Scenario: Fresh ingress is visibly pending without a false target

- **WHEN** a message has `terminal_action: null` and
  `dashboard_turn = {ingress_state: "pending", target_kind: null,
  state: "pending_ingress", ...}`
- **THEN** the UI SHALL render an in-progress submission state and continue its
  bounded durable-status refresh
- **AND** it SHALL not show a routed, filed, saved, or cancelled result

#### Scenario: Retryable ingress failure is visibly distinct

- **WHEN** a message has `terminal_action: null` and a targetless
  `dashboard_turn.state = "retryable_error"`
- **THEN** the UI SHALL render a sanitized retryable submission failure distinct
  from a rejected message
- **AND** it SHALL not claim the message was routed, filed, saved, or cancelled;
  its recovery affordance SHALL call the exact-message ingress-recovery API and
  rely on its ingress fence

#### Scenario: Rejected ingress is not automatically replayed

- **WHEN** a message has `terminal_action: null` and a targetless
  `dashboard_turn.state = "rejected"`
- **THEN** the UI SHALL render the sanitized rejection state rather than an
  in-progress, routed, filed, saved, or cancelled result
- **AND** it SHALL not automatically replay the message

#### Scenario: Stop during ingress survives reload as pending cancellation

- **WHEN** a reloaded message has `terminal_action: null` and
  `dashboard_turn = {ingress_state: "submitting", target_kind: null,
  state: "pending_cancellation", cancel_requested_at: ..., ...}`
- **THEN** the UI SHALL render the unconfirmed Stop context and continue bounded
  durable-status refresh
- **AND** it SHALL not show ordinary submission, a recovery affordance, or
  `Cancelled by owner`

#### Scenario: Unresolved ingress Stop becomes ambiguity

- **WHEN** a reloaded message's targetless pending Stop reaches its bounded
  reconciliation deadline without a provable outcome
- **THEN** the UI SHALL render the durable ambiguous state and sanitized
  `ingress_stop_outcome_unknown` reason
- **AND** it SHALL not offer ingress recovery or claim that the Stop succeeded

#### Scenario: Stale ingress requires an explicit exact-message recovery

- **WHEN** a pending-ingress message reaches its durable
  `ingress_recovery_at` boundary after a crash or stalled submission
- **THEN** the UI SHALL stop passive polling and offer recovery for that exact
  immutable message only
- **AND** it SHALL not automatically re-dispatch Switchboard or create a second
  user message
