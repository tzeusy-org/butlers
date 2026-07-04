## MODIFIED Requirements

### Requirement: Dashboard Ingestion Envelope Construction

Dashboard conversations SHALL construct `ingest.v1` envelopes that flow through the standard Switchboard ingestion pipeline.

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

#### Scenario: Per-butler conversation envelope carries a routing pin

- **WHEN** a message is submitted via `POST /api/butlers/{name}/conversations` (or a follow-up on an existing per-butler conversation)
- **THEN** the constructed envelope SHALL set `control.pinned_target` to `{name}`
- **AND** the Switchboard SHALL route the message to `{name}` deterministically, without LLM classification choosing a different target
