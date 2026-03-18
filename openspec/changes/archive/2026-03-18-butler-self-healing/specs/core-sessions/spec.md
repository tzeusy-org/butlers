# Session Management — Healing Fingerprint

## MODIFIED Requirements

### Requirement: Minimum Persisted Session Fields
The sessions table stores all fields required by the base butler contract: `id`, `prompt`, `trigger_source`, `started_at`, `completed_at`, `result`, `tool_calls` (JSONB), `success`, `error`, `duration_ms`, `trace_id`, `model`, `input_tokens`, `output_tokens`, `cost` (JSONB), `request_id` (UUID7, NOT NULL), `ingestion_event_id` (UUID, nullable FK), `complexity`, `resolution_source`, and `healing_fingerprint` (TEXT, nullable).

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

#### Scenario: Healing fingerprint populated on failed sessions
- **WHEN** a session fails and the self-healing dispatcher computes a fingerprint
- **THEN** `healing_fingerprint` is set to the 64-character hex fingerprint string

#### Scenario: Healing fingerprint null on success
- **WHEN** a session completes successfully
- **THEN** `healing_fingerprint` is NULL

#### Scenario: Healing fingerprint null on healing sessions
- **WHEN** a session with `trigger_source = "healing"` completes (success or failure)
- **THEN** `healing_fingerprint` is NULL (healing sessions are not fingerprinted)

### Requirement: Trigger Source Tracking
Valid trigger sources are: `tick`, `external`, `trigger`, `route`, `healing`, and `schedule:<task-name>` (where task-name is any non-empty string). The trigger source is validated at session creation.

#### Scenario: Schedule trigger source
- **WHEN** `trigger_source="schedule:daily_digest"` is provided
- **THEN** validation passes (matches the `schedule:<name>` pattern)

#### Scenario: Route trigger source
- **WHEN** `trigger_source="route"` is provided
- **THEN** validation passes (exact match in the `TRIGGER_SOURCES` frozenset)

#### Scenario: Healing trigger source
- **WHEN** `trigger_source="healing"` is provided
- **THEN** validation passes (exact match in the `TRIGGER_SOURCES` frozenset)

## ADDED Requirements

### Requirement: Healing Fingerprint Update
The system SHALL expose a `session_set_healing_fingerprint(pool, session_id, fingerprint)` function that sets the `healing_fingerprint` column on an existing session row.

#### Scenario: Fingerprint set after session failure
- **WHEN** `session_set_healing_fingerprint(pool, session_id, "abc123...")` is called
- **THEN** the session row's `healing_fingerprint` is updated to `"abc123..."`

#### Scenario: Setting fingerprint on non-existent session
- **WHEN** `session_set_healing_fingerprint(pool, invalid_id, "abc123...")` is called
- **THEN** no error is raised (best-effort update, 0 rows affected)
