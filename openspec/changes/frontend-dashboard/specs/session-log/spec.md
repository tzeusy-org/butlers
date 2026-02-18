# Session Log (Delta)

Delta spec for the `session-log` capability. Adds token tracking, model identification, trace correlation, and parent session linkage columns to the sessions table to support cost estimation, distributed tracing, and session hierarchy in the dashboard.

---

## MODIFIED Requirements

### Requirement: Sessions table provisioning

The `sessions` table SHALL be created during butler database provisioning as part of the core Alembic migration chain, before any butler-specific or module Alembic migrations run.

#### Scenario: Butler starts with a fresh database

WHEN a butler starts up against a newly provisioned database
THEN the `sessions` table MUST exist with columns `id` (UUID PRIMARY KEY DEFAULT gen_random_uuid()), `trigger_source` (TEXT NOT NULL), `prompt` (TEXT NOT NULL), `result` (TEXT), `tool_calls` (JSONB NOT NULL DEFAULT '[]'), `success` (BOOLEAN), `error` (TEXT), `duration_ms` (INT), `started_at` (TIMESTAMPTZ NOT NULL DEFAULT now()), `completed_at` (TIMESTAMPTZ), `input_tokens` (INT, nullable), `output_tokens` (INT, nullable), `model` (TEXT, nullable), `trace_id` (TEXT, nullable), and `parent_session_id` (UUID, nullable)

---

## ADDED Requirements

### Requirement: Token columns migration

An Alembic migration in the core revision chain SHALL add the `input_tokens`, `output_tokens`, `model`, `trace_id`, and `parent_session_id` columns to the existing `sessions` table as nullable columns.

#### Scenario: Migration applies to an existing sessions table

- **WHEN** a butler with an existing `sessions` table (from the initial core migration) runs the new Alembic revision
- **THEN** five new nullable columns MUST be added: `input_tokens` (INT), `output_tokens` (INT), `model` (TEXT), `trace_id` (TEXT), and `parent_session_id` (UUID)
- **AND** existing rows MUST remain intact with NULL values for all new columns
- **AND** no data loss or table recreation SHALL occur

#### Scenario: Migration is idempotent on fresh databases

- **WHEN** a butler starts against a newly provisioned database and both the initial and new core migrations run in sequence
- **THEN** the `sessions` table MUST contain all original columns plus the five new columns
- **AND** the migration chain MUST complete without errors

---

### Requirement: Session update includes token data

When a runtime instance completes, the LLM CLI Spawner SHALL capture token usage data from the Claude Code SDK response and store it in the session record alongside the existing completion fields.

#### Scenario: Successful CC completion captures token usage

- **WHEN** a runtime instance completes successfully and the SDK response includes token usage data
- **THEN** the corresponding session row MUST be updated with `input_tokens` set to the input token count from the SDK response, `output_tokens` set to the output token count, and `model` set to the model ID used for the invocation

#### Scenario: SDK response lacks token data

- **WHEN** a runtime instance completes and the SDK response does not include token usage data
- **THEN** the corresponding session row MUST have `input_tokens`, `output_tokens`, and `model` set to NULL
- **AND** the session MUST still be recorded with all other fields populated normally

#### Scenario: Failed CC completion captures partial token data

- **WHEN** a runtime instance fails with an error but the SDK response includes partial token usage data
- **THEN** the corresponding session row MUST store whatever token data is available (e.g., `input_tokens` may be set while `output_tokens` is NULL)
- **AND** the `success`, `error`, and `duration_ms` fields MUST still be recorded as specified by the existing session update requirement

---

### Requirement: Active session detection

Sessions with `completed_at` IS NULL SHALL indicate a currently running runtime instance. This enables the dashboard to display live session status.

#### Scenario: runtime instance is currently running

- **WHEN** a runtime instance has been spawned and has not yet completed
- **THEN** the corresponding session row MUST have `completed_at` set to NULL
- **AND** a query for `SELECT * FROM sessions WHERE completed_at IS NULL` MUST return this session

#### Scenario: runtime instance completes

- **WHEN** a previously running runtime instance finishes (successfully or with error)
- **THEN** the corresponding session row MUST have `completed_at` set to a non-NULL timestamp
- **AND** the session MUST no longer appear in queries filtering for `completed_at IS NULL`

#### Scenario: Multiple butlers have active sessions

- **WHEN** two different butlers each have a running runtime instance
- **THEN** each butler's own `sessions` table MUST contain exactly one row with `completed_at IS NULL`
- **AND** the dashboard API can detect active sessions by querying each butler's database independently
