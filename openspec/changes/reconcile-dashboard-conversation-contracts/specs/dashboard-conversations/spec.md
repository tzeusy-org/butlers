## MODIFIED Requirements

### Requirement: Dashboard Ingestion Envelope Construction

Dashboard conversations SHALL construct `ingest.v1` envelopes that flow through
the standard Switchboard ingestion pipeline, submitted to the Switchboard's
`ingest` MCP tool. RFC 0003 §"ingest.v1 Envelope Format" SHALL recognize
`dashboard` / `internal` as the canonical pair for this direct owner-dashboard
ingress; it is not connector provenance. The dashboard API, not a connector
startup probe, SHALL assign `dashboard:web:{conversation_id}` as the endpoint
identity.

ID: REQ-dashboard-conversations-001
Source: RFC 0003 § ingest.v1 Envelope Format; dashboard-conversations § Dashboard Ingestion Envelope Construction; design.md Decision 1
Scope: v1-mandatory

#### Scenario: Envelope structure for dashboard messages

- **WHEN** a dashboard message is submitted for ingestion
- **THEN** the envelope SHALL have:
  - `schema_version`: `"ingest.v1"`
  - `source.channel`: `"dashboard"`
  - `source.provider`: `"internal"`
  - `source.endpoint_identity`: `"dashboard:web:{conversation_id}"`
  - `event.external_event_id`: `"{message_id}"`, where `message_id` is
    client-generated for a new user message and reused for a retry of that
    message
  - `event.external_thread_id`: `"{conversation_id}"`
  - `event.observed_at`: current timestamp
  - `sender.identity`: `"dashboard:operator"`
  - `payload.normalized_text`: the user's message content (with conversation
    context for follow-ups)
  - `payload.raw`: `{"source": "dashboard", "conversation_id": "...",
    "message_id": "...", "message": "...", "page_context": {...}}` —
    `page_context` is present only when the client supplied one
  - `control.policy_tier`: `"interactive"`
  - `control.ingestion_tier`: `"full"`
  - `control.pinned_target`: present per the routing-pin scenarios below

#### Scenario: Dashboard messages bypass discretion

- **WHEN** a dashboard message is ingested by the Switchboard
- **THEN** the `"dashboard"` channel SHALL NOT be subject to discretion
  evaluation (operator messages are always intentional)

#### Scenario: Per-butler conversation envelope carries a routing pin

- **WHEN** a message is submitted via `POST /api/butlers/{name}/conversations`
  (or a follow-up on an existing per-butler conversation) and `{name}` is a
  routable domain butler (not the Switchboard staffer)
- **THEN** the constructed envelope SHALL set `control.pinned_target` to
  `{name}`
- **AND** the Switchboard SHALL route the message to `{name}` deterministically,
  without LLM classification choosing a different target

#### Scenario: Switchboard-addressed conversations are unpinned until routed

- **WHEN** a message is submitted via `POST
  /api/butlers/switchboard/conversations` (the dashboard chat widget's
  classification-routed conversation) and the conversation has no
  `routed_butler` yet
- **THEN** the constructed envelope SHALL NOT set `control.pinned_target` — the
  Switchboard staffer is never a registered, routable target
- **AND** the message proceeds through Switchboard's ordinary classify -> route
  pipeline

#### Scenario: Classification-routed follow-up is sticky

- **WHEN** a follow-up message is submitted via `POST
  /api/butlers/switchboard/conversations/{conversation_id}/messages` and the
  conversation already has a `routed_butler` set (from an earlier successful
  `route_to` decision)
- **THEN** the constructed envelope SHALL set `control.pinned_target` to
  `routed_butler`, bypassing classification entirely
- **AND** a conversation whose `routed_butler` is still NULL (not yet routed, or
  a bug-lane report with no domain-butler target) continues through
  classification as in the "unpinned until routed" scenario above

#### Scenario: Optional page context is preserved

- **WHEN** a dashboard message is submitted with a `page_context` object
  (`route`, `query_params`, optional `entity_ref`) on the request body
- **THEN** the envelope's `payload.raw.page_context` SHALL carry that object
  unchanged, grounding the statement for the routed butler
- **AND** when no `page_context` is provided, `payload.raw` SHALL NOT contain a
  `page_context` key
