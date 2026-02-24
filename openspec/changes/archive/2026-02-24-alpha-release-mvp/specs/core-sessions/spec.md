# Session Management

## Purpose
Provides append-only session records for every ephemeral LLM CLI invocation, capturing trigger source, prompt, outcome, tool calls, token usage, duration, and trace correlation. Supports pagination, aggregation, and cost analysis queries.

## ADDED Requirements

### Requirement: Session Creation
A session row is inserted before the runtime invocation begins, capturing the prompt, trigger source, trace ID, model identifier, and optional request ID from routed ingestion context.

#### Scenario: Session created with valid trigger source
- **WHEN** `session_create(pool, prompt, trigger_source, trace_id, model, request_id)` is called with a valid trigger source
- **THEN** a new row is inserted in the `sessions` table with `started_at=now()` and `completed_at=NULL`
- **AND** the session's UUID is returned

#### Scenario: Invalid trigger source rejected
- **WHEN** `session_create()` is called with an unrecognized trigger source (not `tick`, `external`, `trigger`, `route`, or `schedule:<name>`)
- **THEN** a `ValueError` is raised

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
The sessions table stores all fields required by the base butler contract: `id`, `prompt`, `trigger_source`, `started_at`, `completed_at`, `result`, `tool_calls` (JSONB), `success`, `error`, `duration_ms`, `trace_id`, `model`, `input_tokens`, `output_tokens`, `cost` (JSONB), and `request_id`.

#### Scenario: Token counts captured
- **WHEN** the runtime adapter returns usage data with `input_tokens` and `output_tokens`
- **THEN** these values are persisted in the session record

#### Scenario: Tool calls captured as JSONB
- **WHEN** the runtime invocation produces tool call records
- **THEN** the tool calls are serialized as a JSONB array in the session record

### Requirement: Trigger Source Tracking
Valid trigger sources are: `tick`, `external`, `trigger`, `route`, and `schedule:<task-name>` (where task-name is any non-empty string). The trigger source is validated at session creation.

#### Scenario: Schedule trigger source
- **WHEN** `trigger_source="schedule:daily_digest"` is provided
- **THEN** validation passes (matches the `schedule:<name>` pattern)

#### Scenario: Route trigger source
- **WHEN** `trigger_source="route"` is provided
- **THEN** validation passes (exact match in the `TRIGGER_SOURCES` frozenset)

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
