# Dashboard Conversations

## Purpose

Provides the persistence layer, data model, and API endpoints for per-butler conversational threads originating from the dashboard. Dashboard conversations create real butler sessions via the existing Switchboard ingestion pipeline, enabling full lineage tracking, audit, and cost attribution. This capability covers conversation lifecycle (create, continue, archive, rename), message storage with model attribution and token counts, and SSE-streamed responses.

## Requirements

### Requirement: Conversation Data Model

The `public.dashboard_conversations` table SHALL store conversation thread metadata. Each conversation belongs to exactly one butler and progresses through a defined lifecycle.

#### Scenario: Conversation table schema

- **WHEN** the migration creates the `public.dashboard_conversations` table
- **THEN** the table SHALL contain the following columns:
  - `id` (UUID7, primary key) — time-ordered unique identifier
  - `butler_name` (TEXT, NOT NULL) — the butler this conversation belongs to
  - `title` (TEXT, nullable): auto-generated or user-edited title; the API always populates it from the first user message (no DB-level default)
  - `status` (TEXT, NOT NULL, default `'active'`) — one of `active`, `archived`
  - `created_at` (TIMESTAMPTZ, NOT NULL, default `now()`) — when the conversation was started
  - `updated_at` (TIMESTAMPTZ, NOT NULL, default `now()`) — when the last message was added
  - `message_count` (INTEGER, NOT NULL, default `0`) — denormalized count of messages
  - `routed_butler` (TEXT, nullable): the butler this conversation's first message was routed to by Switchboard classification; NULL for pinned per-butler conversations (already deterministic) and for classification-routed conversations that haven't routed yet (e.g. a bug-lane report, which never targets a domain butler)

#### Scenario: Conversation table indexes

- **WHEN** the migration creates indexes
- **THEN** a composite index on `(butler_name, status, updated_at DESC)` SHALL exist for listing active conversations per butler
- **AND** a composite index on `(butler_name, updated_at DESC)` SHALL exist for chronological listing

#### Scenario: Sticky routed_butler stamping

- **WHEN** a classification-routed (Switchboard-addressed) conversation's message is submitted and Switchboard's triage produces a `route_to` decision with a target butler, and the conversation has no `routed_butler` yet
- **THEN** `routed_butler` is set to that target butler
- **AND** a later `route_to` decision for the same conversation (e.g. from a follow-up that still goes through classification) does NOT overwrite an already-set `routed_butler` — the first successful route wins

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

#### Scenario: Message table indexes

- **WHEN** the migration creates indexes
- **THEN** an index on `(conversation_id, created_at ASC)` SHALL exist for chronological message listing within a conversation

### Requirement: Conversation List API

The dashboard API SHALL provide an endpoint to list conversations for a butler with pagination and filtering.

#### Scenario: List active conversations

- **WHEN** `GET /api/butlers/{name}/conversations?status=active&limit=20&offset=0` is called
- **THEN** conversations are returned ordered by `updated_at DESC` with pagination metadata
- **AND** each conversation includes `id`, `title`, `status`, `created_at`, `updated_at`, `message_count`, `latest_assistant_reply_at`

#### Scenario: latest_assistant_reply_at reflects the most recent assistant message

- **WHEN** a conversation list entry is built
- **THEN** `latest_assistant_reply_at` is the `MAX(created_at)` of that conversation's `public.dashboard_messages` rows where `role = 'assistant'`, or `null` if it has none
- **AND** this field is the freshness signal for detecting a new reply because `conversation_reply_create` persists a new assistant message before the routed session's accounting is known

#### Scenario: List all conversations

- **WHEN** `GET /api/butlers/{name}/conversations?status=all` is called
- **THEN** both active and archived conversations are returned

#### Scenario: Default status filter

- **WHEN** `GET /api/butlers/{name}/conversations` is called without a `status` parameter
- **THEN** only `active` conversations are returned

### Requirement: Conversation Creation

Starting a new conversation SHALL create a conversation record and send the first user message through the Switchboard ingestion pipeline.

#### Scenario: Create conversation with first message

- **WHEN** `POST /api/butlers/{name}/conversations` is called with `{ "message": "Hello butler" }` and an optional `page_context`
- **THEN** a new conversation row is inserted in `public.dashboard_conversations` with `butler_name = {name}`, `status = 'active'`, and a default title
- **AND** a user message row is inserted in `public.dashboard_messages` **before** Switchboard submission is attempted; the dashboard UI MUST provide one immutable UUID as `message_id` and reuse it for every retry and Stop of that user turn (API callers that omit it are compatibility-only and cannot offer pre-SSE Stop)
- **AND** the message is submitted to the Switchboard's `ingest` MCP tool as an `ingest.v1` envelope with `source.channel = "dashboard"`, `source.provider = "internal"`, `source.endpoint_identity = "dashboard:web:{conversation_id}"`
- **AND** the response is streamed back via SSE on the same request (see SSE Streaming requirement)
- **AND** the response includes the `conversation_id` in the initial SSE event

#### Scenario: Auto-generated title

- **WHEN** a conversation is created
- **THEN** the title is set to the first 80 characters of the first user message, truncated at word boundary with ellipsis if needed

#### Scenario: Retry initial conversation before its SSE response is received

- **WHEN** a client retries `POST /api/butlers/{name}/conversations` with the same `message_id` after losing the initial SSE response
- **THEN** the API SHALL reuse that message's existing conversation rather than insert a second conversation or user-message row
- **AND** the retried envelope SHALL retain the original `source.endpoint_identity` and `event.external_event_id`
- **AND** a request that reuses a `message_id` for another butler, role, or message content SHALL return `409` with `code: "MESSAGE_ID_CONFLICT"`

### Requirement: Continue Conversation

Sending a follow-up message in an existing conversation SHALL preserve the thread context.

#### Scenario: Send follow-up message

- **WHEN** `POST /api/butlers/{name}/conversations/{conversation_id}/messages` is called with `{ "message": "Follow up question" }` and an optional `page_context`
- **THEN** a user message row is inserted in `public.dashboard_messages`
- **AND** the message is submitted to the Switchboard's `ingest` MCP tool as an `ingest.v1` envelope with the same `endpoint_identity` as the original conversation and `event.external_thread_id = {conversation_id}`
- **AND** the envelope's `payload.normalized_text` includes prior conversation context (last N messages as summarized context, configurable, default last 5 exchange pairs)
- **AND** the response is streamed back via SSE
- **AND** `updated_at` and `message_count` on the conversation are updated

#### Scenario: Continue archived conversation

- **WHEN** a message is sent to a conversation with `status = 'archived'`
- **THEN** the conversation status is changed to `active` before processing
- **AND** the message is processed normally

#### Scenario: Continue conversation for wrong butler

- **WHEN** `POST /api/butlers/{name}/conversations/{conversation_id}/messages` is called but the conversation belongs to a different butler
- **THEN** a 404 response with `code: "CONVERSATION_NOT_FOUND"` is returned

### Requirement: Durable Dashboard Turn Control

Each dashboard UI user message SHALL have one durable control record keyed by
its immutable `message_id`. It makes Stop and retry behavior truthful across
the API, Switchboard, target butler, and route-inbox recovery boundaries.

#### Scenario: Open the durable turn before external ingress

- **WHEN** the dashboard API persists a dashboard UI user message
- **THEN** it opens or loads that message's durable turn before it returns its
  SSE response or calls external `ingest.v1`
- **AND** the same `message_id` is used as `event.external_event_id`
- **AND** Stop can address that turn even before a newly-created conversation
  has delivered `conversation_id` over SSE

#### Scenario: Only one caller may cross the ingress boundary

- **WHEN** one or more callers submit the same `message_id`
- **THEN** exactly one caller whose durable ingress claim is `dispatch` MAY
  invoke `ingest.v1`
- **AND** a caller that observes `accepted` SHALL observe the original request
  and SHALL NOT invoke `ingest.v1` again
- **AND** a caller that observes `pending` or `cancelling` SHALL receive SSE
  `error {code: "INGEST_IN_PROGRESS"}` followed by `done`, retain the same
  logical message, and SHALL NOT offer automatic replay
- **AND** a caller that observes `cancelled` SHALL receive `SESSION_CANCELLED`
  followed by `done`
- **AND** a caller that observes `ambiguous` SHALL receive
  `TURN_OUTCOME_UNKNOWN` followed by `done` and SHALL NOT automatically replay
  the turn
- **AND** a durable `retryable_error` permits a later retry with the same
  immutable `message_id`; a deterministic rejected submission remains a
  rejection rather than a new logical message

#### Scenario: Message-scoped Stop is canonical

- **WHEN** the dashboard calls `POST
  /api/butlers/{name}/conversation-turns/{message_id}/cancel`
- **THEN** the API returns the raw typed `ConversationCancelResponse` with
  HTTP 200 and exactly one truthful domain outcome: `cancelled`,
  `already_finished`, or unconfirmed cancellation described by `message`
- **AND** `{name}` retains the established butler-route namespace, while
  `message_id` is the exact durable control key and not a second target selector
- **AND** `POST /api/butlers/{name}/conversations/{conversation_id}/cancel`
  is compatibility-only; dashboard UI clients SHALL NOT use it as their normal
  Stop path

#### Scenario: Dashboard route recovery never infers a safe replay

- **WHEN** a recovery worker claims a stale `processing` `route_inbox` row
  sourced from a durable dashboard turn
- **THEN** it SHALL reconcile the predecessor as unprovable and mark the turn
  ambiguous rather than automatically replaying it
- **AND** it MAY still address any exact, durably registered predecessor
  session with Stop
- **AND** ordinary non-dashboard route recovery retains its normal replay
  behavior subject to its route-inbox ownership lease

### Requirement: Conversation Lifecycle Management

The dashboard API SHALL allow operators to archive, unarchive, and rename conversations.

#### Scenario: Archive conversation

- **WHEN** `PATCH /api/butlers/{name}/conversations/{conversation_id}` is called with `{ "status": "archived" }`
- **THEN** the conversation status is set to `archived`

#### Scenario: Unarchive conversation

- **WHEN** `PATCH /api/butlers/{name}/conversations/{conversation_id}` is called with `{ "status": "active" }`
- **THEN** the conversation status is set to `active`

#### Scenario: Rename conversation

- **WHEN** `PATCH /api/butlers/{name}/conversations/{conversation_id}` is called with `{ "title": "New title" }`
- **THEN** the conversation title is updated

#### Scenario: Update non-existent conversation

- **WHEN** `PATCH /api/butlers/{name}/conversations/{conversation_id}` is called for a conversation that does not exist or belongs to a different butler
- **THEN** a 404 response with `code: "CONVERSATION_NOT_FOUND"` is returned

### Requirement: Conversation Messages List

The dashboard API SHALL retrieve the full message history for a conversation.

#### Scenario: List messages

- **WHEN** `GET /api/butlers/{name}/conversations/{conversation_id}/messages?limit=50&offset=0` is called
- **THEN** messages are returned ordered by `created_at ASC` with pagination metadata
- **AND** each message includes `id`, `role`, `content`, `created_at`, `session_id`, `model_name`, `input_tokens`, `output_tokens`, `duration_ms`, `tool_calls`, `error`, `request_id`

#### Scenario: Messages for non-existent conversation

- **WHEN** messages are requested for a conversation that does not exist or belongs to a different butler
- **THEN** a 404 response with `code: "CONVERSATION_NOT_FOUND"` is returned

### Requirement: Conversation Search

The dashboard API SHALL search across conversation history for a butler.

#### Scenario: Search conversations by content

- **WHEN** `GET /api/butlers/{name}/conversations/search?q=keyword&limit=20` is called
- **THEN** conversations whose messages contain the search term are returned, ordered by relevance (most recent match first)
- **AND** each result includes the conversation metadata plus a `snippet` field with the matching message content (the first 200 characters of the matching message)

#### Scenario: Empty search query

- **WHEN** the `q` parameter is empty or missing
- **THEN** a 400 response with `code: "VALIDATION_ERROR"` is returned

### Requirement: SSE Response Streaming

Assistant responses SHALL be streamed to the dashboard via Server-Sent Events on the conversation creation and message continuation endpoints. The reply text and attribution MUST come from the routed butler's `conversation_reply` call (see the Conversation Reply Channel requirement), not from the raw completion of its spawned session.

#### Scenario: SSE stream for new conversation

- **WHEN** `POST /api/butlers/{name}/conversations` is called
- **THEN** the response is a `StreamingResponse` with `media_type: "text/event-stream"`
- **AND** the first event is `event: conversation_created` with `data: {"conversation_id": "...", "title": "..."}`
- **AND** an `event: token` with `data: {"content": "..."}` carries the full `conversation_reply` message text once it arrives (not incremental generation — token-level streaming is out of scope)
- **AND** a final `event: message_complete` with `data: {"message_id": "...", "model_name": null, "input_tokens": null, "output_tokens": null, "duration_ms": null, "tool_calls": []}` is sent — attribution fields are `null` because the reply is persisted mid-session, before the routed session's own accounting (tokens/duration/model) is known
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

### Requirement: Dashboard Ingestion Envelope Construction

Dashboard conversations SHALL construct `ingest.v1` envelopes that flow through the standard Switchboard ingestion pipeline, submitted to the Switchboard's `ingest` MCP tool.

#### Scenario: Envelope structure for dashboard messages

- **WHEN** a dashboard message is submitted for ingestion
- **THEN** the envelope SHALL have:
  - `schema_version`: `"ingest.v1"`
  - `source.channel`: `"dashboard"`
  - `source.provider`: `"internal"`
  - `source.endpoint_identity`: `"dashboard:web:{conversation_id}"`
  - `event.external_event_id`: `"{message_id}"`, where dashboard UI provides one immutable client-generated ID for a new user message and reuses it for retry and Stop
  - `event.external_thread_id`: `"{conversation_id}"`
  - `event.observed_at`: current timestamp
  - `sender.identity`: `"dashboard:operator"`
  - `payload.normalized_text`: the user's message content (with conversation context for follow-ups)
  - `payload.raw`: `{"source": "dashboard", "conversation_id": "...", "message_id": "...", "message": "...", "page_context": {...}}` — `page_context` is present only when the client supplied one
  - `control.policy_tier`: `"interactive"`
  - `control.ingestion_tier`: `"full"`
  - `control.pinned_target`: present per the routing-pin scenarios below

#### Scenario: Dashboard messages bypass discretion

- **WHEN** a dashboard message is ingested by the Switchboard
- **THEN** the `"dashboard"` channel SHALL NOT be subject to discretion evaluation (operator messages are always intentional)

#### Scenario: Per-butler conversation envelope carries a routing pin

- **WHEN** a message is submitted via `POST /api/butlers/{name}/conversations` (or a follow-up on an existing per-butler conversation) and `{name}` is a routable domain butler (not the Switchboard staffer)
- **THEN** the constructed envelope SHALL set `control.pinned_target` to `{name}`
- **AND** the Switchboard SHALL route the message to `{name}` deterministically, without LLM classification choosing a different target

#### Scenario: Switchboard-addressed conversations are unpinned until routed

- **WHEN** a message is submitted via `POST /api/butlers/switchboard/conversations` (the dashboard chat widget's classification-routed conversation) and the conversation has no `routed_butler` yet
- **THEN** the constructed envelope SHALL NOT set `control.pinned_target` — the Switchboard staffer is never a registered, routable target
- **AND** the message proceeds through Switchboard's ordinary classify -> route pipeline

#### Scenario: Sticky follow-up pinning for classification-routed conversations

- **WHEN** a follow-up message is submitted via `POST /api/butlers/switchboard/conversations/{conversation_id}/messages` and the conversation already has a `routed_butler` set (from an earlier successful `route_to` decision)
- **THEN** the constructed envelope SHALL set `control.pinned_target` to `routed_butler`, bypassing classification entirely
- **AND** a conversation whose `routed_butler` is still NULL (not yet routed, or a bug-lane report with no domain-butler target) continues through classification as in the "unpinned until routed" scenario above

#### Scenario: Optional page context on dashboard messages

- **WHEN** a dashboard message is submitted with a `page_context` object (`route`, `query_params`, optional `entity_ref`) on the request body
- **THEN** the envelope's `payload.raw.page_context` SHALL carry that object unchanged, grounding the statement for the routed butler
- **AND** when no `page_context` is provided, `payload.raw` SHALL NOT contain a `page_context` key

### Requirement: Conversation Summary Queries

The dashboard API SHALL provide conversation-count summary statistics.

#### Scenario: Conversation summary per butler

- **WHEN** `GET /api/butlers/{name}/conversations/summary` is called
- **THEN** the response includes: `total_conversations`, `active_conversations`, `total_messages`

### Requirement: Conversation Pydantic Response Models

Conversation endpoint API response models SHALL provide typed response shapes.

#### Scenario: ConversationSummary model

- **WHEN** a conversation list response is serialized
- **THEN** each entry includes: `id`, `butler_name`, `title`, `status`, `created_at`, `updated_at`, `message_count`, `routed_butler`, `latest_assistant_reply_at`

#### Scenario: ConversationMessage model

- **WHEN** a message response is serialized
- **THEN** each entry includes: `id`, `conversation_id`, `role`, `content`, `created_at`, `session_id`, `model_name`, `input_tokens`, `output_tokens`, `duration_ms`, `tool_calls`, `error`, `request_id`

#### Scenario: ConversationSearchResult model

- **WHEN** a search result is serialized
- **THEN** each entry includes the `ConversationSummary` fields plus `snippet` (the matching message content excerpt)

### Requirement: Conversation Reply Channel

A routed butler session SHALL confirm its interpretation of a dashboard
statement (or acknowledge a filed bug report) by calling the
`conversation_reply` MCP tool, which persists an assistant-role message
directly into the conversation it was routed from. The SSE poller MUST watch
for this message rather than the routed session's raw completion (see the
SSE Response Streaming requirement).

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
