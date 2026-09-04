## MODIFIED Requirements

### Requirement: Message Data Model

The `public.dashboard_messages` table SHALL store individual messages within a conversation, including both user inputs and assistant responses with full attribution.

#### Scenario: Message table schema

- **WHEN** the migration creates the `public.dashboard_messages` table
- **THEN** the table SHALL contain the following columns:
  - `id` (UUID, primary key) — immutable dashboard user-turn identity; dashboard UI generates it before submission and reuses it for retry and Stop, while server generation remains legacy API compatibility only
  - `conversation_id` (UUID, NOT NULL, FK to `public.dashboard_conversations.id` ON DELETE CASCADE) — parent conversation
  - `role` (TEXT, NOT NULL) — one of `user`, `assistant`
  - `content` (TEXT, NOT NULL) — message text (markdown for assistant responses)
  - `created_at` (TIMESTAMPTZ, NOT NULL, default `now()`) — when the message was created
  - `session_id` (UUID, nullable) — FK to the butler's `sessions.id` for assistant responses; NULL for user messages
  - `model_name` (TEXT, nullable) — the LLM model used for this response; NULL for user messages
  - `input_tokens` (INTEGER, nullable) — tokens consumed reading input; NULL for user messages
  - `output_tokens` (INTEGER, nullable) — tokens produced in response; NULL for user messages
  - `duration_ms` (INTEGER, nullable) — response generation time in milliseconds; NULL for user messages
  - `tool_calls` (JSONB, nullable) — array of tool calls made during response; NULL for user messages
  - `error` (TEXT, nullable) — error message if the response failed; NULL on success and for user messages
  - `request_id` (UUID, nullable) — the Switchboard request_id for lineage; NULL for user messages
  - `sources` (JSONB, nullable) — array of source strings named by an answer-lane `conversation_reply` call (see the Conversation Reply Channel requirement); NULL for user messages and for any assistant reply that did not pass `sources`

#### Scenario: Message table indexes

- **WHEN** the migration creates indexes
- **THEN** an index on `(conversation_id, created_at ASC)` SHALL exist for chronological message listing within a conversation

### Requirement: Conversation Reply Channel

A routed butler session SHALL confirm its interpretation of a dashboard
statement (or acknowledge a filed bug report, or answer a question) by
calling the `conversation_reply` MCP tool, which persists an assistant-role
message directly into the conversation it was routed from. The SSE poller
MUST watch for this message rather than the routed session's raw completion
(see the SSE Response Streaming requirement).

#### Scenario: conversation_reply persists the confirm-loop message

- **WHEN** a routed butler session calls `conversation_reply(conversation_id, message)` for a `conversation_id` that references an existing conversation
- **THEN** an assistant-role row is inserted into `public.dashboard_messages` with `content = message`
- **AND** the conversation's `message_count` is incremented and `updated_at` is refreshed
- **AND** the tool returns `{"status": "ok", "message_id": "...", "conversation_id": "..."}`

#### Scenario: conversation_reply rejects an unknown conversation_id

- **WHEN** `conversation_reply` is called with a `conversation_id` that does not reference an existing conversation
- **THEN** no message row is inserted
- **AND** the tool returns `{"status": "error", "error": "..."}` (never raises) so the calling model can see and correct its own mistake

#### Scenario: conversation_reply is available to every butler

- **WHEN** any butler's MCP server registers its core tools
- **THEN** `conversation_reply` SHALL be registered regardless of `core_groups` configuration — any butler can be the classification or pinned-target destination of a dashboard conversation, so the tool cannot be scoped to a subset of butlers

#### Scenario: conversation_reply accepts an optional sources list for an answer-lane reply

- **WHEN** a routed butler session calls `conversation_reply(conversation_id, message, sources=[...])` with a non-empty list of strings
- **THEN** the inserted message row's `sources` column SHALL persist the given list
- **AND** the tool's success response SHALL be unaffected in shape otherwise

#### Scenario: conversation_reply is unaffected when sources is omitted

- **WHEN** `conversation_reply` is called without a `sources` argument (the existing confirm-loop, action-proposal, and bug-report call sites)
- **THEN** the inserted message row's `sources` column SHALL be NULL
- **AND** behavior SHALL be identical to before `sources` existed

#### Scenario: conversation_reply rejects empty or blank source names

- **WHEN** `conversation_reply` is called with `sources=[]` or with any blank source name
- **THEN** no message row is inserted
- **AND** the tool returns `{"status": "error", "error": "..."}` guiding the caller to either name what it consulted or omit `sources` entirely and give an honest decline instead of fabricating a citation

### Requirement: Dashboard Message Intent Lanes

A dashboard chat-widget turn SHALL be classified into exactly one of STATEMENT, ACTION REQUEST, QUESTION, or ambiguous before it produces any effect. Consent MUST precede effect for an ACTION REQUEST (`about/heart-and-soul/security.md`, "Approval gates must never be bypassable by the LLM session"): a dashboard turn that asks the routed butler to DO something with a real-world or hard-to-reverse effect SHALL never apply a write before the owner has approved it, and SHALL never be reported to the owner as already done. A QUESTION turn SHALL never apply a write and SHALL never be reported as an action taken.

#### Scenario: Classifier offers a distinct ACTION lane alongside statement and bug lanes

- **WHEN** the Switchboard's dashboard classification prompt is built for a chat-widget message
- **THEN** it SHALL present four lanes: LANE A (data statement/correction, routed via `route_to_butler`), LANE B (bug/system report, filed via `file_bug_report`), LANE C (action request, also routed via `route_to_butler` — the classifier's job is only to pick the target butler; the propose-don't-apply contract is enforced by the routed envelope's injected instructions, not by the classifier itself), and LANE D (question, answered via `answer_question` or dead-lettered via `cannot_answer`)

#### Scenario: The routed envelope carries distinct STATEMENT and ACTION-REQUEST instructions

- **WHEN** `route_to_butler` injects the deterministic dashboard confirm-loop block into a routed envelope's `input.context`
- **THEN** the block SHALL contain a STATEMENT instruction set (interpret, apply the write, then call `conversation_reply` to confirm) and a distinct ACTION-REQUEST instruction set
- **AND** the ACTION-REQUEST set SHALL instruct the routed session to route the write through its normal approval-gated tool (never an ungated path) so the gate parks it before anything happens, and to call `conversation_reply` describing the action as proposed and awaiting approval — never as already completed
- **AND** the block SHALL state the failure mode explicitly: applying an action's write before the gate parks it, or claiming completion for a pending action, is never acceptable
- **WHEN** `answer_question(scope="domain")` injects the deterministic dashboard answer block into a routed envelope's `input.context` instead of the confirm-loop block
- **THEN** the block SHALL instruct the routed session to answer strictly read-only, from its own tools only, and to call `conversation_reply` citing what it consulted via `sources` when grounded, or to give an honest decline (never fabricate a citation) when it cannot ground the answer

#### Scenario: A parked action request produces zero domain writes and no completion claim

- **WHEN** a routed butler session follows the ACTION-REQUEST instructions for a gated write tool whose target contact is unresolvable or requires review
- **THEN** exactly one `pending_actions` row is created with `status = 'pending'`
- **AND** the underlying domain tool function is not invoked
- **AND** the `conversation_reply` text describes the action as proposed/queued, not completed

#### Scenario: An ambiguous dashboard turn yields a clarifying reply, never a best-guess route

- **WHEN** the dashboard classification session cannot confidently place a message into LANE A, B, C, or D
- **THEN** it SHALL call neither `route_to_butler` nor `file_bug_report` rather than guessing a target butler, and SHALL likewise not call `answer_question` or `cannot_answer` while the turn remains ambiguous
- **AND** the pipeline's existing dashboard dead-letter path (see the Durable Dashboard Turn Control requirement's failure handling) SHALL capture the turn and reply in-thread asking the owner to clarify, with no route to any domain butler

### Requirement: SSE Response Streaming

Assistant responses SHALL be streamed to the dashboard via Server-Sent Events on the conversation creation and message continuation endpoints. The reply text and attribution MUST come from the routed butler's `conversation_reply` call (see the Conversation Reply Channel requirement), not from the raw completion of its spawned session.

#### Scenario: SSE stream for new conversation

- **WHEN** `POST /api/butlers/{name}/conversations` is called
- **THEN** the response is a `StreamingResponse` with `media_type: "text/event-stream"`
- **AND** the first event is `event: conversation_created` with `data: {"conversation_id": "...", "title": "..."}`
- **AND** an `event: token` with `data: {"content": "..."}` carries the full `conversation_reply` message text once it arrives (not incremental generation — token-level streaming is out of scope)
- **AND** a final `event: message_complete` with `data: {"message_id": "...", "model_name": null, "input_tokens": null, "output_tokens": null, "duration_ms": null, "tool_calls": [], "sources": []}` is sent — attribution fields are `null` because the reply is persisted mid-session, before the routed session's own accounting (tokens/duration/model) is known; `sources` is the list passed to `conversation_reply` (or `[]` if omitted)
- **AND** an `event: done` is sent to signal the stream is finished

#### Scenario: SSE stream for follow-up message

- **WHEN** `POST /api/butlers/{name}/conversations/{conversation_id}/messages` is called
- **THEN** the same SSE streaming pattern as conversation creation is used, without the `conversation_created` event

#### Scenario: No conversation_reply arrives within the poll window

- **WHEN** the routed butler session's spawned process does not call `conversation_reply` before the poll window (300s) elapses
- **THEN** an `event: error` with `data: {"code": "SESSION_TIMEOUT", "message": "...", "session_id": "..."}` is sent, followed by `event: done`
- **AND** `session_id` is the routed butler's session row for this request when it could be resolved (best-effort by `request_id`), or omitted when it could not
- **AND** the conversation is NOT marked failed and the thread stays open — a `conversation_reply` that lands after the SSE stream has closed is a normal message row, visible on the next history fetch or unread-badge poll

#### Scenario: Switchboard unreachable during submission

- **WHEN** the Switchboard MCP server cannot be reached while submitting the ingest envelope
- **THEN** an `event: error` with `data: {"code": "SWITCHBOARD_UNAVAILABLE", "message": "Switchboard offline — retry"}` is sent, followed by `event: done`
- **AND** the user message row inserted before submission is preserved (not rolled back)
- **AND** a client retry resubmits the original `message_id`, so Switchboard deduplicates by the stable `event.external_event_id` even when the retry crosses an hourly content-hash bucket or its rebuilt conversation-context preamble differs (no duplicate user row, route, or session is created)

#### Scenario: Switchboard rejects the envelope

- **WHEN** the Switchboard's `ingest` MCP tool rejects the envelope (e.g. an invalid `pinned_target`)
- **THEN** an `event: error` with `data: {"code": "INGEST_REJECTED", "message": "..."}` is sent, followed by `event: done`
- **AND** this is a deterministic rejection distinct from `SWITCHBOARD_UNAVAILABLE`: retrying the identical envelope will fail the same way

#### Scenario: A durable turn is still being observed or settled

- **WHEN** a same-message submission sees a durable `pending` or `cancelling`
  ingress state
- **THEN** the API emits `event: error` with
  `data: {"code": "INGEST_IN_PROGRESS", "message": "..."}`, followed by
  `event: done`
- **AND** the client treats it as an observer/check-again state, not a
  retryable send failure

#### Scenario: A durable Stop or uncertain recovery becomes terminal on SSE

- **WHEN** the durable turn records confirmed cancellation
- **THEN** the API emits `event: error` with
  `data: {"code": "SESSION_CANCELLED", "message": "..."}`, followed by
  `event: done`
- **WHEN** route recovery cannot prove the prior dashboard runtime stopped
- **THEN** the API emits `event: error` with
  `data: {"code": "TURN_OUTCOME_UNKNOWN", "message": "..."}`, followed by
  `event: done` and no automatic replay

#### Scenario: SSE keepalive during processing

- **WHEN** the butler session is processing but no tokens have been emitted for 15 seconds
- **THEN** a `: keepalive` SSE comment is sent to prevent connection timeout
