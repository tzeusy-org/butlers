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
  appends content, `message_complete` finalizes model, token, duration, and
  tool-call metadata, `error` displays in the thread, and `done` closes the
  stream and re-enables input

#### Scenario: SSE connection for follow-up

- **WHEN** `POST /api/butlers/{name}/conversations/{id}/messages` is called
- **THEN** the frontend SHALL apply the same SSE handling without a
  `conversation_created` event

#### Scenario: Stream cancellation is a real server-side stop

- **WHEN** the owner clicks Stop during streaming
- **THEN** the frontend SHALL call `POST
  /api/butlers/{name}/conversation-turns/{message_id}/cancel`, where
  `message_id` is the client-created immutable user-turn identifier and SHALL
  work before a new conversation has delivered its `conversation_id` over SSE
- **AND** the Stop button SHALL enter a pending (`Stopping…`) state that prevents
  a second request and exposes its state to assistive technology
- **AND** the backend SHALL record cancellation intent against that durable turn,
  preventing a later Switchboard ingress claim, classifier, target route,
  recovery, or runtime invocation from starting work for it
- **AND** when the exact immutable turn owns one or more in-flight routed
  runtimes, the backend SHALL resolve every exact registered runtime session and
  kill the corresponding subprocesses through `cancel_session`; it SHALL not
  merely detach a watcher or persist intent alone
- **AND** when the turn instead owns a terminal action, the backend SHALL persist
  and linearize terminal-action Stop intent against that action
- **AND** only after the API returns `outcome: "cancelled"` or emits the terminal
  `SESSION_CANCELLED` SSE outcome from the durable turn record may the frontend
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

- **WHEN** the cancel request itself fails without a durable action outcome, an
  already-ended runtime is still settling, or an irreversible action was already
  committed without a cancellation result
- **THEN** the frontend SHALL surface the returned safe explanation or durable
  outcome inline; it SHALL re-enable the Stop control only when the server has
  not persisted a pending, terminal, or ambiguous outcome
- **AND** it SHALL not render `Cancelled by owner`, `Interrupted`, or any other
  terminal-state indicator implying that the runtime or action actually stopped

#### Scenario: Dispatch receipt is an accessible current-turn-only status

- **WHEN** the stream emits `dispatch_accepted` for the immutable message owned
  by the active chat turn
- **THEN** the UI SHALL render exactly one polite, atomic textual pending status:
  `Received by Switchboard; waiting for a reply.` for `routed_butler: null`, or
  `Routed to <name>; waiting for a reply.` for a named durable route
- **AND** the first receipt SHALL be rendered as the targetless status even
  when its safe durable observation already has a route; only the later named
  upgrade may change that status and add the current-turn link
- **AND** before any receipt it SHALL render `Sending to Switchboard.`, preserve
  its animated typing dots as decorative `aria-hidden` elements, and expose the
  pending text through one `role="status"` live region
- **AND** it SHALL link `/butlers/{encodeURIComponent(name)}` only for the
  named receipt on that current stream; targetless receipts and absent receipts
  SHALL keep the ordinary Butler label with no current-route link
- **AND** it SHALL never use a historical `conversation.routed_butler` or a
  pre-routing triage value as a fallback link or pending status target

#### Scenario: Receipt preserves durable Stop semantics

- **WHEN** a turn is cancelling, cancelled, ambiguous, or owns a terminal action
- **THEN** the UI SHALL retain the exact existing Stop/error/result presentation
  and SHALL not synthesize receipt progress or route language
- **AND** during cancelling or confirmed Stop it SHALL suppress the pending
  receipt live region, decorative dots, and `Routed to` link so the authoritative
  Stop status is the only live status

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
  `["conversation-messages", butlerName, conversationId]` with `staleTime: 0`
  so it always refetches on conversation switch

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
- **THEN** a sent message or created conversation SHALL invalidate
  `["conversations", butlerName]` to refresh the list
- **AND** `message_complete` SHALL invalidate
  `["conversation-messages", butlerName, conversationId]`
- **AND** a terminal-action reconciliation SHALL invalidate the relevant
  conversation-list and exact message queries

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

#### Scenario: List and search read errors preserve cached rows

- **WHEN** a conversation-list or search query errors while it still has cached
  rows
- **THEN** the UI SHALL show those cached rows together with a retryable
  `role="alert"` that calls that query's own `refetch`
- **AND** it SHALL not replace the rows with an empty-state claim

#### Scenario: History read recovery preserves the active thread only

- **WHEN** the active conversation history query fails or refreshes while it
  retains same-thread messages
- **THEN** the UI SHALL preserve the draft, selection, and same-thread cached or
  optimistic messages beside a retryable query-refetch alert
- **AND** after the owner selects a different loading or failed conversation it
  SHALL not render optimistic messages owned by the previous conversation or a
  false empty-history state

## ADDED Requirements

### Requirement: Durable Dashboard Outcome Presentation

The dashboard chat UI SHALL render terminal-action and dashboard-turn state from
the durable conversation read model and SHALL never present a filed, routed, or
cancelled claim that the journal cannot prove. An ambiguous terminal action SHALL
expose its sanitized reason and owner-only resolution link, but SHALL not offer
an automatic duplicate retry.

ID: REQ-dashboard-chat-ui-003
Source: heart-and-soul/vision.md § What Butlers Is Not (Not an experiment); dashboard-terminal-action-recovery REQ-dashboard-terminal-action-recovery-005; design.md Decisions 5-6
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

#### Scenario: Stop suppresses an unstarted acknowledgement after a primary effect

- **WHEN** a primary terminal effect is completed and its required
  `conversation_reply` effect is `cancelled` with
  `reason_code: "suppressed_by_stop"`
- **THEN** the UI SHALL render the completed primary safe reference and a
  truthful `Stopped after partial effect; acknowledgement was suppressed` state
- **AND** it SHALL not render `Cancelled`, `Report failed`, or an unqualified
  filed/saved success while the parent is pending reconciliation or after it
  becomes failed with `reason_code: "stopped_after_partial_effect"`

#### Scenario: Action is ambiguous

- **WHEN** a terminal action is ambiguous
- **THEN** the UI SHALL show the sanitized ambiguity reason and resolution link
- **AND** it SHALL not offer an automatic effect retry

#### Scenario: Owner assessment remains an ambiguity overlay

- **WHEN** an ambiguous terminal action has an `owner_resolution` overlay
- **THEN** the UI SHALL show the owner assessment, bounded sanitized note, and
  recorded time beside the still-ambiguous action
- **AND** it SHALL not relabel the action as a receipt-backed completion/failure
  or offer another resolution submission after the immutable overlay exists

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
