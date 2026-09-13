## MODIFIED Requirements

### Requirement: Message Data Model

The `public.dashboard_messages` table SHALL store individual messages within a conversation, including both user inputs and assistant responses with full attribution. Structured citations and per-message butler attribution SHALL be nullable so legacy and non-session-authored rows remain truthful without backfilled guesses.

ID: REQ-dashboard-conversations-009
Source: dashboard-conversations § Message Data Model and Conversation Reply Channel; heart-and-soul/security.md § Session Sandboxing; design.md Decisions 2-4
Scope: v1-mandatory

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
  - `sources` (JSONB, nullable) — deprecated compatibility array of source strings named by an answer-lane `conversation_reply` call; NULL when omitted and retained only through the bounded migration window
  - `citations` (JSONB, nullable) — server-normalized array of structured citation objects; NULL for user messages and assistant replies that did not pass `sources`
  - `routed_butler` (TEXT, nullable) — server-derived butler that authored this assistant message through `conversation_reply`; NULL for user, legacy, and non-session-authored messages

#### Scenario: Message table indexes

- **WHEN** the migration creates indexes
- **THEN** an index on `(conversation_id, created_at ASC)` SHALL exist for chronological message listing within a conversation

### Requirement: Conversation Messages List

The dashboard API SHALL retrieve the full message history for a conversation, including the same canonical answer provenance that completes over SSE.

ID: REQ-dashboard-conversations-012
Source: dashboard-conversations § Conversation Messages List and Conversation Pydantic Response Models; RFC 0007 § API Surface; design.md Decisions 3-4
Scope: v1-mandatory

#### Scenario: List messages

- **WHEN** `GET /api/butlers/{name}/conversations/{conversation_id}/messages?limit=50&offset=0` is called
- **THEN** messages are returned ordered by `created_at ASC` with pagination metadata
- **AND** each message includes `id`, `role`, `content`, `created_at`, `session_id`, `model_name`, `input_tokens`, `output_tokens`, `duration_ms`, `tool_calls`, `error`, `request_id`, `citations`, and `routed_butler`
- **AND** `citations` is always an array on the wire (`[]` when persistence is NULL), while `routed_butler` remains nullable
- **AND** during the bounded compatibility window, assistant rows also include the deprecated `sources` string array (`[]` when persistence is NULL) defined by the compatibility requirement

#### Scenario: Messages for non-existent conversation

- **WHEN** messages are requested for a conversation that does not exist or belongs to a different butler
- **THEN** a 404 response with `code: "CONVERSATION_NOT_FOUND"` is returned

#### Scenario: Stored and streamed provenance are equivalent

- **WHEN** a client receives an assistant reply's `message_complete` event and later retrieves that message through the messages list after reload or reconnect
- **THEN** the message ID, content, canonical `citations`, nullable `routed_butler`, and nullable `session_id` are equivalent to the persisted row
- **AND** the history response does not reconstruct citation trust or authorship from conversation-level routing, live phase events, or model text

### Requirement: SSE Response Streaming

Assistant responses SHALL be streamed to the dashboard via Server-Sent Events on the conversation creation and message continuation endpoints. The reply text and attribution MUST come from the routed butler's `conversation_reply` call (see the Conversation Reply Channel requirement), not from the raw completion of its spawned session. `message_complete` SHALL project the persisted message's canonical citations and per-message butler attribution rather than a client-derived interpretation.

ID: REQ-dashboard-conversations-013
Source: dashboard-conversations § SSE Response Streaming and Conversation Reply Channel; RFC 0007 § API Surface; design.md Decisions 3-4
Scope: v1-mandatory

#### Scenario: SSE stream for new conversation

- **WHEN** `POST /api/butlers/{name}/conversations` is called
- **THEN** the response is a `StreamingResponse` with `media_type: "text/event-stream"`
- **AND** the first event is `event: conversation_created` with `data: {"conversation_id": "...", "title": "..."}`
- **AND** one or more `event: token` events with `data: {"content": "..."}` carry the `conversation_reply` message text — a single event carrying the full text when the routed runtime cannot stream incrementally (every runtime adapter today), or several events whose concatenated `content` fields are byte-for-byte identical to the persisted reply row's content when a streaming-capable producer publishes incremental deltas on the turn's chat-stream channel (see the Real-Time Processing Phase Events requirement's Trust boundary)
- **AND** a final `event: message_complete` carries the persisted `message_id`, nullable session/model/token/duration fields, `tool_calls`, canonical `citations` (`[]` when persistence is NULL), and nullable `routed_butler`; during the bounded compatibility window it also carries the deprecated `sources` string array (`[]` when persistence is NULL)
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

#### Scenario: Message completion is persistence-authoritative

- **WHEN** display-only token events disagree with the eventual persisted reply or a client reconnects after missing `message_complete`
- **THEN** the persisted row remains authoritative for content, `citations`, `routed_butler`, and `session_id`
- **AND** the client reconciles to the stored message without inventing citations or message authorship from the token stream

## ADDED Requirements

### Requirement: Assistant Citation Normalization and Trust

The server SHALL normalize the existing `conversation_reply(..., sources=...)` input into one canonical `citations` representation before persistence. A canonical citation SHALL contain a non-empty `label`, a nullable `target`, and exactly one `kind` of `internal`, `external`, or `unlinked`. A structurally valid citation establishes only a safe navigation target, never the truth, completeness, availability, or evidentiary quality of the model's claim.

ID: REQ-dashboard-conversations-010
Source: heart-and-soul/security.md § Session Sandboxing; dashboard-conversations § Conversation Reply Channel; design.md Decisions 2-3
Scope: v1-mandatory

#### Scenario: Structured internal source is accepted by server authority

- **WHEN** `conversation_reply` receives a `sources` entry with a non-empty label and an internal target matching a server-held citation-eligible shell route pattern
- **THEN** the server normalizes it to `{label, target, kind: "internal"}` and persists it in `citations`
- **AND** the client cannot expand the accepted route set by changing its own route registry or request payload
- **AND** target validation does not assert that the referenced resource exists or proves the answer text

#### Scenario: Structured external source is accepted safely

- **WHEN** `conversation_reply` receives a `sources` entry with a non-empty label and an absolute HTTPS target with no embedded credentials
- **THEN** the server normalizes it to `{label, target, kind: "external"}` and persists it in `citations`
- **AND** HTTP, script, data, file, protocol-relative, credential-bearing, or malformed targets are invalid
- **AND** acceptance does not assert that the remote destination is reachable or trustworthy

#### Scenario: Legacy source string remains compatible but unlinked

- **WHEN** `conversation_reply` receives an existing non-empty string entry in `sources`
- **THEN** the server normalizes it to `{label: <trimmed string>, target: null, kind: "unlinked"}`
- **AND** the string cannot become a link by resembling a path or URL
- **AND** the reply success shape remains compatible with the existing tool contract

#### Scenario: Invalid entries are removed content-blindly

- **WHEN** a non-empty `sources` input contains at least one valid entry and one blank, malformed, unsafe, over-budget, or non-allowlisted entry
- **THEN** only the valid normalized entries are persisted in `citations`
- **AND** the server emits a content-blind warning with reason code and rejected count, without labels, targets, answer text, request arguments, session IDs, or sensitive payloads
- **AND** the tool success response does not echo the rejected entry

#### Scenario: Explicit evidence with no usable entry is rejected

- **WHEN** `sources` is explicitly empty or every supplied entry is invalid after server normalization
- **THEN** no assistant message is inserted
- **AND** the tool returns a structured error directing the caller to supply usable sources or omit `sources` and give an honest decline
- **AND** no rejected label or target is copied into logs, telemetry, or the error response

#### Scenario: Omitted sources preserve refusal and non-answer semantics

- **WHEN** `conversation_reply` is called without `sources` for a confirm loop, action proposal, bug report, or honest answer decline
- **THEN** the message persists with `citations = null` and `sources = null`
- **AND** omission is not relabeled as grounded, invalid, or verified

### Requirement: Assistant Message Author Attribution

Every assistant message created by `conversation_reply` SHALL persist the authoring butler name from server-held tool registration context. The system MUST NOT accept caller-asserted message authorship or infer a missing author from conversation-level routing, model text, phase events, or session lookup.

ID: REQ-dashboard-conversations-011
Source: heart-and-soul/security.md § Session Sandboxing; heart-and-soul/vision.md § Domain specialization; dashboard-conversations § Conversation Reply Channel; design.md Decision 4
Scope: v1-mandatory

#### Scenario: conversation_reply records its registered butler

- **WHEN** a butler's registered `conversation_reply` tool persists an assistant message
- **THEN** that message's `routed_butler` equals the server-held butler identity bound when the tool was registered
- **AND** a `sources` object, message text, conversation field, request field, or browser value cannot override it
- **AND** the tool's existing success and unknown-conversation error shapes remain otherwise unchanged

#### Scenario: Classification and sticky routing are not message authorship

- **WHEN** a Switchboard-owned conversation has a sticky conversation `routed_butler` or the current SSE stream names a routed target
- **THEN** that value does not populate or repair an assistant message's `routed_butler`
- **AND** only the server-held identity at the assistant-message write boundary can establish message authorship

#### Scenario: Legacy and non-session-authored rows remain unknown

- **WHEN** an existing assistant row predates per-message attribution or a deterministic API failure row is created outside a registered butler's `conversation_reply`
- **THEN** its `routed_butler` remains NULL
- **AND** migration, read, SSE, and frontend paths do not guess or backfill an author

### Requirement: Assistant Message Compatibility Lifecycle

The citation and attribution rollout SHALL be additive and reversible until all repository consumers read the canonical fields. The `citations` field is the sole forward representation; `sources` is a bounded read-compatibility projection, not a second evidence contract.

ID: REQ-dashboard-conversations-014
Source: craft-and-care/interfaces-and-dependencies.md § Compatibility Rules; design.md Decision 6 and Migration Plan
Scope: v1-mandatory

#### Scenario: Mixed-version reads remain safe during rollout

- **WHEN** the additive migration is deployed while a repository consumer still reads `sources`
- **THEN** new writes persist canonical `citations` and a string-only `sources` projection sufficient for that verified consumer, and both read APIs serialize absent arrays as `[]`
- **AND** canonical readers use `citations` and do not merge, rank, or infer additional trust from the deprecated projection
- **AND** nullable new fields allow old rows to remain readable without a guessed author or fabricated target

#### Scenario: Same-repo compatibility projection is retired

- **WHEN** every verified repository consumer reads `citations` and `routed_butler`
- **THEN** the same implementation change removes the `sources` response projection and its compatibility branches
- **AND** the retained database column is removed only after downgrade safety and deployment ordering no longer depend on it

#### Scenario: Downgrade preserves legacy readers

- **WHEN** runtime code is rolled back during the compatibility window
- **THEN** the previous code can still read the retained `sources` strings and ignore nullable `citations` and message `routed_butler`
- **AND** downgrade never converts a structured target into a string that appears server-validated
