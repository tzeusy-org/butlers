# Healing Session Tracking

## Purpose

Database schema and query layer for tracking healing attempts. Links error fingerprints to investigation branches, PRs, session IDs, and outcome status. Provides the data backbone for dispatch gate checks, dashboard visibility, and operational observability. Includes atomicity guarantees for concurrent dispatch and recovery mechanisms for daemon restarts.

## ADDED Requirements

### Requirement: Healing Attempts Table
The system SHALL maintain a `shared.healing_attempts` table tracking every healing investigation lifecycle.

#### Scenario: Table schema
- **WHEN** the migration creates `shared.healing_attempts`
- **THEN** the table contains: `id` (UUID PK), `fingerprint` (TEXT NOT NULL), `butler_name` (TEXT NOT NULL), `status` (TEXT NOT NULL DEFAULT 'investigating'), `severity` (INTEGER NOT NULL), `exception_type` (TEXT NOT NULL), `call_site` (TEXT NOT NULL), `sanitized_msg` (TEXT), `branch_name` (TEXT), `worktree_path` (TEXT), `pr_url` (TEXT), `pr_number` (INTEGER), `session_ids` (UUID[] NOT NULL DEFAULT '{}'), `healing_session_id` (UUID), `created_at` (TIMESTAMPTZ NOT NULL DEFAULT now()), `updated_at` (TIMESTAMPTZ NOT NULL DEFAULT now()), `closed_at` (TIMESTAMPTZ), `error_detail` (TEXT)

#### Scenario: Fingerprint index
- **WHEN** the table is created
- **THEN** an index exists on `fingerprint` for novelty and cooldown gate lookups

#### Scenario: Status index
- **WHEN** the table is created
- **THEN** an index exists on `status` for concurrency cap and circuit breaker queries

#### Scenario: Active investigation uniqueness
- **WHEN** the table is created
- **THEN** a partial unique index exists on `fingerprint` WHERE `status IN ('investigating', 'pr_open')`
- **AND** this prevents two concurrent dispatchers from creating duplicate active investigations for the same fingerprint

### Requirement: Atomic Attempt Creation (Novelty + Insert)
The dispatcher's novelty check and attempt insertion MUST be atomic to prevent race conditions where two concurrent session failures with the same fingerprint both pass the novelty gate.

#### Scenario: Concurrent dispatch with same fingerprint
- **WHEN** two sessions fail simultaneously with the same fingerprint and both dispatchers reach the novelty gate
- **THEN** at most one `healing_attempts` row is created with status `investigating`
- **AND** the second dispatcher's insert fails on the partial unique index
- **AND** the second dispatcher appends its session ID to the winning attempt's `session_ids` array instead

#### Scenario: Insert-or-append pattern
- **WHEN** `create_or_join_attempt(pool, fingerprint, ...)` is called
- **THEN** it attempts `INSERT INTO shared.healing_attempts ... ON CONFLICT (fingerprint) WHERE status IN ('investigating', 'pr_open') DO UPDATE SET session_ids = array_append(session_ids, $session_id)`
- **AND** it returns `(attempt_id, is_new)` where `is_new = True` if a new row was created, `False` if an existing row was joined

### Requirement: Healing Attempt State Machine
The `status` field SHALL follow a defined state machine with valid transitions.

#### Scenario: Valid states
- **WHEN** a healing attempt status is set
- **THEN** the value MUST be one of: `investigating`, `pr_open`, `pr_merged`, `failed`, `unfixable`, `anonymization_failed`, `timeout`

#### Scenario: Initial state
- **WHEN** a healing attempt row is inserted
- **THEN** the status is `investigating`

#### Scenario: Successful completion
- **WHEN** a healing agent produces a valid fix and passes anonymization
- **THEN** the status transitions from `investigating` to `pr_open`
- **AND** `pr_url` and `pr_number` are populated

#### Scenario: Agent failure
- **WHEN** a healing agent session fails or produces no viable fix
- **THEN** the status transitions from `investigating` to `failed`
- **AND** `closed_at` is set and `error_detail` describes the failure

#### Scenario: Terminal states are final
- **WHEN** a healing attempt reaches `pr_merged`, `failed`, `unfixable`, `anonymization_failed`, or `timeout`
- **THEN** no further status transitions are allowed
- **AND** `update_attempt_status()` SHALL reject transitions from terminal states with a logged warning (no exception raised)

#### Scenario: Status transition sets updated_at
- **WHEN** any status transition occurs
- **THEN** `updated_at` is set to `now()`

#### Scenario: Terminal transition sets closed_at
- **WHEN** a status transitions to any terminal state (`pr_merged`, `failed`, `unfixable`, `anonymization_failed`, `timeout`)
- **THEN** `closed_at` is set to `now()`

### Requirement: Session ID Accumulation
When a new session fails with a fingerprint that already has an active (non-terminal) healing attempt, the failing session's ID SHALL be appended to the existing attempt's `session_ids` array.

#### Scenario: Additional session linked to existing attempt
- **WHEN** session `s2` fails with fingerprint `fp1` and an `investigating` attempt exists for `fp1` with `session_ids = [s1]`
- **THEN** the attempt's `session_ids` is updated to `[s1, s2]`

#### Scenario: Duplicate session ID is idempotent
- **WHEN** the same session ID is appended twice (e.g. due to retry logic)
- **THEN** the `session_ids` array does not contain duplicates (use `array_append` only if not already present)

### Requirement: Fingerprint Collision Detection
When appending a session to an existing healing attempt, the system SHALL compare the new error's `(exception_type, call_site)` against the existing attempt's stored values. If they differ, a CRITICAL log entry is emitted indicating a potential fingerprint collision.

#### Scenario: Matching error metadata
- **WHEN** session `s2` joins attempt for fingerprint `fp1` and both have `exception_type = "KeyError"` and `call_site = "src/foo.py:bar"`
- **THEN** the session is appended normally with no collision warning

#### Scenario: Mismatched error metadata (collision)
- **WHEN** session `s2` joins attempt for fingerprint `fp1` but has a different `exception_type` or `call_site` than the stored attempt
- **THEN** a CRITICAL log is emitted: "Fingerprint collision detected for {fingerprint}: existing={exc_type}@{call_site}, new={exc_type2}@{call_site2}"
- **AND** the session is still appended (collision doesn't block deduplication — it's an observability signal)

### Requirement: Daemon Restart Recovery
On dispatcher startup (daemon boot), the system SHALL recover from incomplete healing attempts left behind by a prior crash or restart.

#### Scenario: Stale investigating attempts recovered
- **WHEN** the daemon starts and `healing_attempts` rows exist with status `investigating` and `updated_at` older than `[healing] timeout_minutes` (default: 30)
- **THEN** those rows are transitioned to `timeout` with `error_detail = "Recovered on daemon restart — investigation was interrupted"`
- **AND** their worktrees are cleaned up by the stale worktree reaper

#### Scenario: Recently created investigating attempt preserved
- **WHEN** the daemon starts and a `healing_attempts` row exists with status `investigating` and `updated_at` is 5 minutes ago
- **THEN** the row is NOT transitioned (it may have been created by a still-running agent from before the restart)
- **AND** the watchdog timeout will handle it if the agent is truly dead

#### Scenario: Investigating attempt with no healing_session_id
- **WHEN** the daemon starts and a `healing_attempts` row has status `investigating` but `healing_session_id = NULL` and `created_at` is older than 5 minutes
- **THEN** the row is transitioned to `failed` with `error_detail = "Recovered on daemon restart — agent was never spawned"`
- **AND** its worktree (if any) is cleaned up

#### Scenario: Recovery runs before new dispatches
- **WHEN** the daemon starts the healing dispatcher
- **THEN** recovery runs FIRST, before the dispatcher begins accepting new errors
- **AND** the stale worktree reaper runs after recovery completes

### Requirement: Query Functions
The system SHALL expose query functions for dispatch gate checks and dashboard display.

#### Scenario: Active attempt lookup by fingerprint
- **WHEN** `get_active_attempt(pool, fingerprint)` is called
- **THEN** it returns the `healing_attempts` row with status `investigating` or `pr_open` for that fingerprint, or `None`

#### Scenario: Recent attempt lookup for cooldown
- **WHEN** `get_recent_attempt(pool, fingerprint, window_minutes)` is called
- **THEN** it returns the most recent terminal attempt for that fingerprint closed within the window, or `None`

#### Scenario: Active count for concurrency cap
- **WHEN** `count_active_attempts(pool)` is called
- **THEN** it returns the count of rows with status `investigating`

#### Scenario: Recent failures for circuit breaker
- **WHEN** `get_recent_terminal_statuses(pool, limit)` is called
- **THEN** it returns the `status` values of the N most recent terminal attempts, ordered by `closed_at DESC`
- **AND** `unfixable` statuses are included in the result set (the caller decides whether to count them as failures)

#### Scenario: List attempts for dashboard
- **WHEN** `list_attempts(pool, limit, offset, status_filter)` is called
- **THEN** it returns paginated healing attempt rows, optionally filtered by status, ordered by `created_at DESC`

### Requirement: Dashboard API Routes
The system SHALL expose API endpoints for healing attempt visibility.

#### Scenario: List healing attempts
- **WHEN** `GET /api/healing/attempts?limit=20&offset=0` is called
- **THEN** it returns a paginated list of healing attempts with all fields

#### Scenario: Filter by status
- **WHEN** `GET /api/healing/attempts?status=investigating` is called
- **THEN** only attempts with status `investigating` are returned

#### Scenario: Get healing attempt detail
- **WHEN** `GET /api/healing/attempts/{attempt_id}` is called
- **THEN** it returns the full healing attempt record including linked session IDs

#### Scenario: Retry a failed attempt
- **WHEN** `POST /api/healing/attempts/{attempt_id}/retry` is called for an attempt with terminal status
- **THEN** a new healing attempt is created for the same fingerprint, bypassing cooldown
- **AND** the original attempt's status is unchanged

#### Scenario: Retry a non-terminal attempt is rejected
- **WHEN** `POST /api/healing/attempts/{attempt_id}/retry` is called for an attempt with status `investigating`
- **THEN** the request is rejected with HTTP 409 Conflict

#### Scenario: Reset circuit breaker
- **WHEN** `POST /api/healing/circuit-breaker/reset` is called
- **THEN** the circuit breaker state is cleared and dispatch resumes

#### Scenario: Circuit breaker status
- **WHEN** `GET /api/healing/circuit-breaker` is called
- **THEN** it returns the current circuit breaker state: `tripped` (boolean), `consecutive_failures` (int), `last_success_at` (timestamp or null)
