# Session Management

## Purpose
Provides append-only session records for every ephemeral LLM CLI invocation, capturing trigger source, prompt, outcome, tool calls, token usage, duration, and trace correlation. Supports pagination, aggregation, and cost analysis queries.

## ADDED Requirements

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

### Requirement: Session Completion
Session completion is the only mutation allowed after creation (append-only contract). It updates result, tool calls, duration, success flag, error, cost, and token counts, and sets `completed_at=now()`.

#### Scenario: Successful session completion
- **WHEN** `session_complete(pool, session_id, output, tool_calls, duration_ms, success=True, input_tokens, output_tokens)` is called
- **THEN** the session row is updated with all provided fields and `completed_at=now()`

#### Scenario: Failed session completion
- **WHEN** `session_complete(pool, session_id, output=None, tool_calls=[], duration_ms, success=False, error=error_msg)` is called
- **THEN** the session row records the error and sets `success=False`

#### Scenario: Non-existent session ID
- **WHEN** `session_complete()` is called with a session ID that does not exist
- **THEN** a `ValueError` is raised

### Requirement: Minimum Persisted Session Fields
The sessions table stores all fields required by the base butler contract: `id`, `prompt`, `trigger_source`, `started_at`, `completed_at`, `result`, `tool_calls` (JSONB), `success`, `error`, `duration_ms`, `trace_id`, `model`, `input_tokens`, `output_tokens`, `cost` (JSONB), `request_id` (UUID7, NOT NULL), `ingestion_event_id` (UUID, nullable FK → `shared.ingestion_events.id`), `complexity`, `resolution_source`, and `healing_fingerprint` (TEXT, nullable).

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

### Requirement: Session Detail Query
Return a full session record by UUID, with JSONB fields deserialized to native Python objects.

#### Scenario: Successful session retrieval
- **WHEN** `sessions_get(pool, session_id)` is called with a valid session UUID
- **THEN** a complete session record is returned with all persisted fields
- **AND** `tool_calls` and `cost` JSONB fields are deserialized to Python lists/dicts

#### Scenario: Non-existent session
- **WHEN** `sessions_get(pool, session_id)` is called with a UUID that does not exist
- **THEN** `None` is returned (no exception raised)

### Requirement: Session List (Paginated)
Return sessions ordered by `started_at DESC` with `limit` and `offset` parameters for pagination.

#### Scenario: Paginated session list
- **WHEN** `sessions_list(pool, limit=20, offset=0)` is called
- **THEN** up to 20 session records are returned, most recent first

### Requirement: Active Sessions Query
Return all sessions where `completed_at IS NULL` (in-progress), ordered by `started_at DESC`. This is the primary mechanism for the dashboard to detect running sessions.

#### Scenario: Active session detection
- **WHEN** `sessions_active(pool)` is called while a session is in-flight
- **THEN** the in-flight session appears in the results with `completed_at=NULL`

### Requirement: Session Summary
Return aggregate session and token statistics grouped by model for a given period (`today`, `7d`, `30d`).

#### Scenario: Summary by period
- **WHEN** `sessions_summary(pool, period="7d")` is called
- **THEN** it returns `total_sessions`, `total_input_tokens`, `total_output_tokens`, and a `by_model` breakdown for the last 7 days

### Requirement: Daily Session Aggregates
Return daily session counts and token usage within a date range, with per-model breakdowns.

#### Scenario: Daily breakdown
- **WHEN** `sessions_daily(pool, from_date, to_date)` is called
- **THEN** it returns a list of daily entries with `date`, `sessions`, `input_tokens`, `output_tokens`, and `by_model`

### Requirement: Top Sessions by Token Usage
Return the highest-token completed sessions ordered by total tokens descending.

#### Scenario: Top sessions query
- **WHEN** `top_sessions(pool, limit=10)` is called
- **THEN** it returns up to 10 sessions sorted by `(input_tokens + output_tokens)` descending

### Requirement: Schedule Cost Analysis
Return per-schedule token usage aggregates by joining `scheduled_tasks` with `sessions` on `trigger_source`, including estimated `runs_per_day` from cron evaluation.

#### Scenario: Schedule costs query
- **WHEN** `schedule_costs(pool)` is called
- **THEN** it returns per-schedule records with `name`, `cron`, `model`, `total_runs`, `total_input_tokens`, `total_output_tokens`, and `runs_per_day`

### Requirement: Session Detail API Response
The session detail endpoint (`GET /api/butlers/{name}/sessions/{session_id}`) SHALL return all existing session fields plus an optional `process_log` object. When a non-expired process log row exists for the session, the `process_log` field SHALL contain: `pid` (int|null), `exit_code` (int|null), `command` (str|null), `stderr` (str|null), `runtime_type` (str|null), `created_at` (datetime|null), `expires_at` (datetime|null). When no process log exists or it has expired, `process_log` SHALL be null. The query SHALL be best-effort: if the `session_process_logs` table does not exist (pre-migration), the field SHALL be null without raising an error.

#### Scenario: Session detail with process log
- **WHEN** `GET /api/butlers/education/sessions/{id}` is called for a session with a non-expired process log
- **THEN** the response includes a `process_log` object with pid, exit_code, command, stderr, runtime_type, created_at, expires_at

#### Scenario: Session detail without process log
- **WHEN** `GET /api/butlers/education/sessions/{id}` is called for a session with no process log (e.g. SDK-based session)
- **THEN** the response includes `process_log: null`

#### Scenario: Session detail with expired process log
- **WHEN** `GET /api/butlers/education/sessions/{id}` is called for a session whose process log has expired
- **THEN** the response includes `process_log: null`

#### Scenario: Session detail before migration
- **WHEN** the session detail endpoint is called on a database that has not yet run migration core_022
- **THEN** the response includes `process_log: null` and no error is raised

### Requirement: Healing Fingerprint Update
The system SHALL expose a `session_set_healing_fingerprint(pool, session_id, fingerprint)` function that sets the `healing_fingerprint` column on an existing session row.

#### Scenario: Fingerprint set after session failure
- **WHEN** `session_set_healing_fingerprint(pool, session_id, "abc123...")` is called
- **THEN** the session row's `healing_fingerprint` is updated to `"abc123..."`

#### Scenario: Setting fingerprint on non-existent session
- **WHEN** `session_set_healing_fingerprint(pool, invalid_id, "abc123...")` is called
- **THEN** no error is raised (best-effort update, 0 rows affected)
