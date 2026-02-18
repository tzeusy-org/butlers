# Session Log

The session log records every CC (Claude Code) invocation for audit and debugging. Each butler maintains its own session log in its dedicated PostgreSQL database via the `sessions` table. Sessions are append-only: once written, they are never deleted. Two MCP tools (`sessions_list`, `sessions_get`) provide read access to the log.

## Database Schema

```sql
CREATE TABLE sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    trigger_source TEXT NOT NULL,         -- 'schedule:<task-name>', 'tick', 'external', 'trigger'
    prompt TEXT NOT NULL,
    result TEXT,
    tool_calls JSONB NOT NULL DEFAULT '[]',
    success BOOLEAN,
    error TEXT,
    duration_ms INT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ
);
```

## ADDED Requirements

### Requirement: Sessions table provisioning

The `sessions` table SHALL be created during butler database provisioning as part of the core Alembic migration chain, before any butler-specific or module Alembic migrations run.

#### Scenario: Butler starts with a fresh database

WHEN a butler starts up against a newly provisioned database
THEN the `sessions` table MUST exist with columns `id` (UUID PRIMARY KEY DEFAULT gen_random_uuid()), `trigger_source` (TEXT NOT NULL), `prompt` (TEXT NOT NULL), `result` (TEXT), `tool_calls` (JSONB NOT NULL DEFAULT '[]'), `success` (BOOLEAN), `error` (TEXT), `duration_ms` (INT), `started_at` (TIMESTAMPTZ NOT NULL DEFAULT now()), and `completed_at` (TIMESTAMPTZ)

---

### Requirement: Session creation on runtime spawn

A new session row SHALL be inserted into the `sessions` table when a runtime instance is spawned, capturing the trigger source, prompt, and start time.

#### Scenario: runtime instance is spawned by the scheduler

WHEN the LLM CLI spawner creates a new runtime instance triggered by a scheduled task named "daily-review"
THEN a new row MUST be inserted into the `sessions` table with `trigger_source` set to `'schedule:daily-review'`, `prompt` set to the prompt given to CC, and `started_at` set to the current timestamp
AND `result`, `success`, `error`, `duration_ms`, and `completed_at` MUST be NULL
AND `tool_calls` MUST be `'[]'`

#### Scenario: runtime instance is spawned by the tick handler

WHEN the LLM CLI spawner creates a new runtime instance triggered by the tick handler
THEN a new row MUST be inserted into the `sessions` table with `trigger_source` set to `'tick'`

#### Scenario: runtime instance is spawned by an external MCP call

WHEN the LLM CLI spawner creates a new runtime instance triggered by an external MCP call
THEN a new row MUST be inserted into the `sessions` table with `trigger_source` set to `'external'`

#### Scenario: runtime instance is spawned by a direct trigger invocation

WHEN the LLM CLI spawner creates a new runtime instance triggered by a direct `trigger()` tool call
THEN a new row MUST be inserted into the `sessions` table with `trigger_source` set to `'trigger'`

---

### Requirement: Session update on CC completion

The session row SHALL be updated when the runtime instance completes, recording the result, tool calls, success status, duration, and completion time.

#### Scenario: CC completes successfully

WHEN a runtime instance finishes execution without error
THEN the corresponding session row MUST be updated with `result` set to CC's output, `tool_calls` set to the JSONB array of tool call records, `success` set to `true`, `completed_at` set to the current timestamp, and `duration_ms` set to the difference between `completed_at` and `started_at` in milliseconds

#### Scenario: CC fails with an error

WHEN a runtime instance fails or raises an error during execution
THEN the corresponding session row MUST be updated with `success` set to `false`, `error` set to the error message, `completed_at` set to the current timestamp, and `duration_ms` set to the difference between `completed_at` and `started_at` in milliseconds

---

### Requirement: duration_ms computation

The `duration_ms` column SHALL be computed as the difference between `completed_at` and `started_at` in milliseconds.

#### Scenario: CC completes after 3.5 seconds

WHEN a runtime instance starts at time T and completes at time T + 3500ms
THEN the session row MUST have `duration_ms` set to `3500`

#### Scenario: CC fails after 1.2 seconds

WHEN a runtime instance starts at time T and fails at time T + 1200ms
THEN the session row MUST have `duration_ms` set to `1200`

---

### Requirement: tool_calls records MCP tool invocations

The `tool_calls` column SHALL contain a JSONB array of objects recording each MCP tool call the runtime instance made during the session.

#### Scenario: CC makes multiple tool calls

WHEN a runtime instance calls `state_get("foo")` and then `state_set("bar", 42)` during a session
THEN the session's `tool_calls` MUST be a JSONB array containing an object for each tool call, preserving the order in which calls were made

#### Scenario: CC makes no tool calls

WHEN a runtime instance completes without making any MCP tool calls
THEN the session's `tool_calls` MUST be `'[]'`

---

### Requirement: sessions_list returns recent sessions with pagination

The `sessions_list` MCP tool SHALL accept optional `limit` and `offset` parameters and return a list of sessions ordered by `started_at` descending.

#### Scenario: Listing sessions with default parameters

WHEN `sessions_list()` is called without arguments
THEN it MUST return up to 20 sessions ordered by `started_at` descending
AND each entry MUST include `id`, `trigger_source`, `prompt`, `success`, `duration_ms`, `started_at`, and `completed_at`

#### Scenario: Listing sessions with a custom limit

WHEN `sessions_list(limit=5)` is called
THEN it MUST return at most 5 sessions ordered by `started_at` descending

#### Scenario: Listing sessions with offset for pagination

WHEN `sessions_list(limit=10, offset=10)` is called
THEN it MUST skip the first 10 sessions and return up to 10 sessions ordered by `started_at` descending

#### Scenario: Listing sessions when the sessions table is empty

WHEN `sessions_list()` is called on an empty `sessions` table
THEN it MUST return an empty list
AND it MUST NOT raise an error

---

### Requirement: sessions_get returns full session detail

The `sessions_get` MCP tool SHALL accept an `id` parameter and return the complete session record including the `tool_calls` array.

#### Scenario: Session exists

WHEN `sessions_get(id)` is called with an `id` that exists in the `sessions` table
THEN it MUST return the full session record including `id`, `trigger_source`, `prompt`, `result`, `tool_calls`, `success`, `error`, `duration_ms`, `started_at`, and `completed_at`

#### Scenario: Session does not exist

WHEN `sessions_get(id)` is called with an `id` that does not exist in the `sessions` table
THEN it MUST return null
AND it MUST NOT raise an error

---

### Requirement: Sessions are append-only

The session log SHALL be an append-only audit log. Sessions MUST NOT be deleted or have their historical fields overwritten after completion.

#### Scenario: No deletion mechanism exists

WHEN any MCP tool is called on the session log
THEN there MUST be no tool or operation that deletes rows from the `sessions` table

#### Scenario: Completed session is immutable

WHEN a session has `completed_at` set to a non-null value
THEN the session's `trigger_source`, `prompt`, `result`, `tool_calls`, `success`, `error`, `duration_ms`, `started_at`, and `completed_at` MUST NOT be modified by any subsequent operation
