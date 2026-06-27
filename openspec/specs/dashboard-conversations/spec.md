# Dashboard Conversations

## Purpose

Provides the persistence layer, data model, and API endpoints for per-butler conversational threads originating from the dashboard. Dashboard conversations create real butler sessions via the existing Switchboard ingestion pipeline, enabling full lineage tracking, audit, and cost attribution. This capability covers conversation lifecycle (create, continue, archive, rename), message storage with model attribution and token counts, and SSE-streamed responses.

## ADDED Requirements

### Requirement: Conversation Data Model

The `public.dashboard_conversations` table stores conversation thread metadata. Each conversation belongs to exactly one butler and progresses through a defined lifecycle.

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

#### Scenario: Conversation table indexes

- **WHEN** the migration creates indexes
- **THEN** a composite index on `(butler_name, status, updated_at DESC)` SHALL exist for listing active conversations per butler
- **AND** a composite index on `(butler_name, updated_at DESC)` SHALL exist for chronological listing

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

- **WHEN** `POST /api/butlers/{name}/conversations` is called with `{ "message": "Hello butler" }`
- **THEN** a new conversation row is inserted in `public.dashboard_conversations` with `butler_name = {name}`, `status = 'active'`, and a default title
- **AND** a user message row is inserted in `public.dashboard_messages`
- **AND** the message is submitted to the Switchboard as an `ingest.v1` envelope with `source.channel = "dashboard"`, `source.provider = "internal"`, `source.endpoint_identity = "dashboard:web:{conversation_id}"`
- **AND** the response is streamed back via SSE on the same request (see SSE Streaming requirement)
- **AND** the response includes the `conversation_id` in the initial SSE event

#### Scenario: Auto-generated title

- **WHEN** a conversation is created
- **THEN** the title is set to the first 80 characters of the first user message, truncated at word boundary with ellipsis if needed

### Requirement: Continue Conversation

Sending a follow-up message in an existing conversation preserves the thread context.

#### Scenario: Send follow-up message

- **WHEN** `POST /api/butlers/{name}/conversations/{conversation_id}/messages` is called with `{ "message": "Follow up question" }`
- **THEN** a user message row is inserted in `public.dashboard_messages`
- **AND** the message is submitted to the Switchboard as an `ingest.v1` envelope with the same `endpoint_identity` as the original conversation and `event.external_thread_id = {conversation_id}`
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

Assistant responses are streamed to the dashboard via Server-Sent Events on the conversation creation and message continuation endpoints.

#### Scenario: SSE stream for new conversation

- **WHEN** `POST /api/butlers/{name}/conversations` is called
- **THEN** the response is a `StreamingResponse` with `media_type: "text/event-stream"`
- **AND** the first event is `event: conversation_created` with `data: {"conversation_id": "...", "title": "..."}`
- **AND** subsequent events are `event: token` with `data: {"content": "..."}` as the assistant generates tokens
- **AND** a final event is `event: message_complete` with `data: {"message_id": "...", "model_name": "...", "input_tokens": N, "output_tokens": N, "duration_ms": N, "tool_calls": [...]}` is sent when generation completes
- **AND** an `event: done` is sent to signal the stream is finished

#### Scenario: SSE stream for follow-up message

- **WHEN** `POST /api/butlers/{name}/conversations/{conversation_id}/messages` is called
- **THEN** the same SSE streaming pattern as conversation creation is used, without the `conversation_created` event

#### Scenario: SSE error during streaming

- **WHEN** the butler session fails during response generation
- **THEN** an `event: error` with `data: {"code": "SESSION_FAILED", "message": "..."}` is sent
- **AND** the error is recorded in the assistant message row with `error` set
- **AND** the stream is closed with `event: done`

#### Scenario: SSE keepalive during processing

- **WHEN** the butler session is processing but no tokens have been emitted for 15 seconds
- **THEN** a `: keepalive` SSE comment is sent to prevent connection timeout

### Requirement: Dashboard Ingestion Envelope Construction

Dashboard conversations construct `ingest.v1` envelopes that flow through the standard Switchboard ingestion pipeline.

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
  - `payload.raw`: `{"source": "dashboard", "conversation_id": "...", "message_id": "...", "message": "..."}`
  - `control.policy_tier`: `"interactive"`
  - `control.ingestion_tier`: `"full"`

#### Scenario: Dashboard messages bypass discretion

- **WHEN** a dashboard message is ingested by the Switchboard
- **THEN** the `"dashboard"` channel SHALL NOT be subject to discretion evaluation (operator messages are always intentional)

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
