## MODIFIED Requirements

### Requirement: Session Creation
A session row is inserted before the runtime invocation begins, capturing the prompt, trigger source, trace ID, model identifier, and a required `request_id`. For connector-sourced sessions, `request_id` is the UUID7 returned by the Switchboard (equal to the `shared.ingestion_events.id`); for internally-triggered sessions (tick, schedule, trigger), the spawner mints a fresh UUID7 before calling `session_create`. `request_id` is never NULL.

#### Scenario: Session created with valid trigger source
- **WHEN** `session_create(pool, prompt, trigger_source, trace_id, model, request_id, ingestion_event_id=None)` is called with a valid trigger source and a non-null `request_id`
- **THEN** a new row is inserted in the `sessions` table with `started_at=now()` and `completed_at=NULL`
- **AND** the session's UUID is returned

#### Scenario: Invalid trigger source rejected
- **WHEN** `session_create()` is called with an unrecognized trigger source (not `tick`, `external`, `trigger`, `route`, or `schedule:<name>`)
- **THEN** a `ValueError` is raised

#### Scenario: Missing request_id rejected
- **WHEN** `session_create()` is called with `request_id=None`
- **THEN** a `ValueError` is raised

#### Scenario: Connector-sourced session with ingestion event FK
- **WHEN** `session_create()` is called with `request_id=<uuid7>` and `ingestion_event_id=<same-uuid7>`
- **THEN** the session row is inserted with both `request_id` and `ingestion_event_id` set to the provided UUID7

#### Scenario: Internally-triggered session without ingestion event
- **WHEN** `session_create()` is called with a spawner-minted `request_id` and no `ingestion_event_id`
- **THEN** the session row is inserted with `request_id` set and `ingestion_event_id` NULL

### Requirement: Minimum Persisted Session Fields
The sessions table stores all fields required by the base butler contract: `id`, `prompt`, `trigger_source`, `started_at`, `completed_at`, `result`, `tool_calls` (JSONB), `success`, `error`, `duration_ms`, `trace_id`, `model`, `input_tokens`, `output_tokens`, `cost` (JSONB), `request_id` (UUID7, NOT NULL), and `ingestion_event_id` (UUID, nullable FK → `shared.ingestion_events.id`).

#### Scenario: Token counts captured
- **WHEN** the runtime adapter returns usage data with `input_tokens` and `output_tokens`
- **THEN** these values are persisted in the session record

#### Scenario: Tool calls captured as JSONB
- **WHEN** the runtime invocation produces tool call records
- **THEN** the tool calls are serialized as a JSONB array in the session record

#### Scenario: request_id always present
- **WHEN** any session row is read from the database
- **THEN** `request_id` is always a non-null UUID7

#### Scenario: ingestion_event_id present only for connector-sourced sessions
- **WHEN** a session was spawned from a connector ingestion event
- **THEN** `ingestion_event_id` equals the `request_id` and references a row in `shared.ingestion_events`
- **WHEN** a session was triggered internally (tick, schedule, trigger)
- **THEN** `ingestion_event_id` is NULL
