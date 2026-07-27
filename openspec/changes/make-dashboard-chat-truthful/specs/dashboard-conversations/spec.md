## MODIFIED Requirements

### Requirement: SSE Response Streaming

Assistant responses SHALL be streamed to the dashboard via Server-Sent Events on the conversation creation and message continuation endpoints. The reply text and attribution MUST come from the routed butler's `conversation_reply` call (see the Conversation Reply Channel requirement), not from the raw completion of its spawned session.

#### Scenario: SSE stream for new conversation

- **WHEN** `POST /api/butlers/{name}/conversations` is called
- **THEN** the response is a `StreamingResponse` with `media_type: "text/event-stream"`
- **AND** the first event is `event: conversation_created` with `data: {"conversation_id": "...", "title": "..."}`
- **AND** after Switchboard accepts the envelope, an `event: dispatch_accepted` receipt is sent before reply polling begins (see the accepted-routing-receipt scenario)
- **AND** an `event: token` with `data: {"content": "..."}` carries the full `conversation_reply` message text once it arrives (not incremental generation — token-level streaming is out of scope)
- **AND** a final `event: message_complete` with `data: {"message_id": "...", "model_name": null, "input_tokens": null, "output_tokens": null, "duration_ms": null, "tool_calls": []}` is sent — attribution fields are `null` because the reply is persisted mid-session, before the routed session's own accounting (tokens/duration/model) is known
- **AND** an `event: done` is sent to signal the stream is finished

#### Scenario: SSE stream for follow-up message

- **WHEN** `POST /api/butlers/{name}/conversations/{conversation_id}/messages` is called
- **THEN** the same SSE streaming pattern as conversation creation is used, without the `conversation_created` event
- **AND** a successful Switchboard submission still emits `dispatch_accepted` before reply polling begins

#### Scenario: Accepted routing receipt is truthful

- **WHEN** Switchboard accepts a dashboard conversation envelope
- **THEN** the server SHALL emit `event: dispatch_accepted` with `data: {"routed_butler": "<name>"}` only when `triage_decision` is `route_to` and `triage_target` is non-empty
- **AND** the server SHALL emit `data: {"routed_butler": null}` when the envelope was accepted without a domain route
- **AND** the receipt SHALL mean only that Switchboard accepted the submission; it SHALL NOT claim that a downstream session completed or that a reply exists
- **AND** the receipt SHALL NOT be emitted when Switchboard is unreachable or rejects the envelope

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

#### Scenario: SSE keepalive during processing

- **WHEN** the butler session is processing but no tokens have been emitted for 15 seconds
- **THEN** a `: keepalive` SSE comment is sent to prevent connection timeout
