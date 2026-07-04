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
  - `total_input_tokens` (BIGINT, NOT NULL, default `0`): aggregate input tokens across all assistant responses
  - `total_output_tokens` (BIGINT, NOT NULL, default `0`): aggregate output tokens across all assistant responses
  - `total_duration_ms` (BIGINT, NOT NULL, default `0`): aggregate response duration across all assistant responses
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

The `public.dashboard_messages` table stores individual messages within a conversation, including both user inputs and assistant responses with full attribution.

#### Scenario: Message table schema

- **WHEN** the migration creates the `public.dashboard_messages` table
- **THEN** the table SHALL contain the following columns:
  - `id` (UUID7, primary key) — time-ordered unique identifier
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
- **AND** each conversation includes `id`, `title`, `status`, `created_at`, `updated_at`, `message_count`, `total_input_tokens`, `total_output_tokens`, `total_duration_ms`

#### Scenario: List all conversations

- **WHEN** `GET /api/butlers/{name}/conversations?status=all` is called
- **THEN** both active and archived conversations are returned

#### Scenario: Default status filter

- **WHEN** `GET /api/butlers/{name}/conversations` is called without a `status` parameter
- **THEN** only `active` conversations are returned

### Requirement: Conversation Creation

Starting a new conversation creates a conversation record and sends the first user message through the Switchboard ingestion pipeline.

#### Scenario: Create conversation with first message

- **WHEN** `POST /api/butlers/{name}/conversations` is called with `{ "message": "Hello butler" }` and an optional `page_context`
- **THEN** a new conversation row is inserted in `public.dashboard_conversations` with `butler_name = {name}`, `status = 'active'`, and a default title
- **AND** a user message row is inserted in `public.dashboard_messages` **before** Switchboard submission is attempted
- **AND** the message is submitted to the Switchboard's `ingest` MCP tool as an `ingest.v1` envelope with `source.channel = "dashboard"`, `source.provider = "internal"`, `source.endpoint_identity = "dashboard:web:{conversation_id}"`
- **AND** the response is streamed back via SSE on the same request (see SSE Streaming requirement)
- **AND** the response includes the `conversation_id` in the initial SSE event

#### Scenario: Auto-generated title

- **WHEN** a conversation is created
- **THEN** the title is set to the first 80 characters of the first user message, truncated at word boundary with ellipsis if needed

### Requirement: Continue Conversation

Sending a follow-up message in an existing conversation preserves the thread context.

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

### Requirement: Conversation Lifecycle Management

Operators can archive, unarchive, and rename conversations.

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

Retrieve the full message history for a conversation.

#### Scenario: List messages

- **WHEN** `GET /api/butlers/{name}/conversations/{conversation_id}/messages?limit=50&offset=0` is called
- **THEN** messages are returned ordered by `created_at ASC` with pagination metadata
- **AND** each message includes `id`, `role`, `content`, `created_at`, `session_id`, `model_name`, `input_tokens`, `output_tokens`, `duration_ms`, `tool_calls`, `error`, `request_id`

#### Scenario: Messages for non-existent conversation

- **WHEN** messages are requested for a conversation that does not exist or belongs to a different butler
- **THEN** a 404 response with `code: "CONVERSATION_NOT_FOUND"` is returned

### Requirement: Conversation Search

Search across conversation history for a butler.

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
- **AND** a client retry that resubmits the same message content is deduplicated idempotently at the Switchboard ingest boundary (no duplicate route or session is created)

#### Scenario: Switchboard rejects the envelope

- **WHEN** the Switchboard's `ingest` MCP tool rejects the envelope (e.g. an invalid `pinned_target`)
- **THEN** an `event: error` with `data: {"code": "INGEST_REJECTED", "message": "..."}` is sent, followed by `event: done`
- **AND** this is a deterministic rejection distinct from `SWITCHBOARD_UNAVAILABLE`: retrying the identical envelope will fail the same way

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
  - `event.external_event_id`: `"{message_id}"`
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

### Requirement: Conversation Aggregate Queries

Provide aggregate statistics for conversation usage.

#### Scenario: Conversation summary per butler

- **WHEN** `GET /api/butlers/{name}/conversations/summary` is called
- **THEN** the response includes: `total_conversations`, `active_conversations`, `total_messages`, `total_input_tokens`, `total_output_tokens`, `total_duration_ms`

### Requirement: Conversation Pydantic Response Models

API response models for conversation endpoints.

#### Scenario: ConversationSummary model

- **WHEN** a conversation list response is serialized
- **THEN** each entry includes: `id`, `butler_name`, `title`, `status`, `created_at`, `updated_at`, `message_count`, `total_input_tokens`, `total_output_tokens`, `total_duration_ms`

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

