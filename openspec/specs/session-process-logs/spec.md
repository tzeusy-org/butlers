# Session Process Logs

## Purpose
Captures subprocess metadata (PID, exit code, command, stderr) for each runtime invocation, enabling post-mortem debugging of adapter failures with automatic TTL-based cleanup.

## Requirements

### Requirement: Process log table schema
The system SHALL maintain a `session_process_logs` table per butler schema with columns: `session_id` (UUID PK, FK → sessions with CASCADE delete), `pid` (INTEGER, nullable), `exit_code` (INTEGER, nullable), `command` (TEXT, nullable), `stderr` (TEXT, nullable), `runtime_type` (TEXT, nullable), `created_at` (TIMESTAMPTZ, default now()), `expires_at` (TIMESTAMPTZ, default now() + 14 days). An index on `expires_at` SHALL exist for efficient cleanup queries.

#### Scenario: Table created by migration
- **WHEN** migration core_022 runs on a butler schema
- **THEN** the `session_process_logs` table is created with the specified schema and index

#### Scenario: CASCADE delete on session removal
- **WHEN** a session row is deleted from the `sessions` table
- **THEN** the corresponding `session_process_logs` row is automatically deleted

### Requirement: Process log write
The system SHALL provide a `write()` function that inserts or upserts a process log row for a given session_id. The function SHALL accept pid, exit_code, command, stderr, runtime_type, and ttl_days parameters. Stderr SHALL be capped at 32 KiB; content exceeding this limit is trimmed with a marker. If a row for the session already exists, it SHALL be replaced (upsert).

#### Scenario: Write process log for a completed session
- **WHEN** `write(pool, session_id, pid=12345, exit_code=0, stderr="...", runtime_type="codex")` is called
- **THEN** a row is inserted into `session_process_logs` with `expires_at` set to now() + 14 days

#### Scenario: Stderr exceeding 32 KiB is trimmed
- **WHEN** `write()` is called with stderr content larger than 32 KiB
- **THEN** the stored stderr is capped at 32 KiB with a trimmed marker appended

#### Scenario: Upsert on duplicate session_id
- **WHEN** `write()` is called for a session_id that already has a process log row
- **THEN** the existing row is replaced with the new values

#### Scenario: Custom TTL
- **WHEN** `write()` is called with `ttl_days=7`
- **THEN** the `expires_at` is set to now() + 7 days instead of the default 14

### Requirement: Process log read
The system SHALL provide a `get()` function that returns the process log for a given session_id, or None if no non-expired row exists. Expired rows (where `expires_at < now()`) SHALL NOT be returned.

#### Scenario: Read existing non-expired process log
- **WHEN** `get(pool, session_id)` is called for a session with a non-expired process log
- **THEN** a dict with pid, exit_code, command, stderr, runtime_type, created_at, expires_at is returned

#### Scenario: Read expired process log
- **WHEN** `get(pool, session_id)` is called for a session whose process log has expired
- **THEN** None is returned

#### Scenario: Read non-existent process log
- **WHEN** `get(pool, session_id)` is called for a session with no process log row
- **THEN** None is returned

### Requirement: TTL cleanup
The system SHALL provide a `cleanup()` function that deletes all rows where `expires_at < now()`. The function SHALL return the count of deleted rows.

#### Scenario: Cleanup deletes expired rows
- **WHEN** `cleanup(pool)` is called and 5 rows have `expires_at` in the past
- **THEN** those 5 rows are deleted and the function returns 5

#### Scenario: Cleanup with no expired rows
- **WHEN** `cleanup(pool)` is called and no rows have expired
- **THEN** no rows are deleted and the function returns 0

### Requirement: Adapter process info capture
Each subprocess-based runtime adapter (Codex, Gemini, OpenCode) SHALL expose a `last_process_info` property returning a dict with keys: `pid`, `exit_code`, `command`, `stderr`, `runtime_type`. The property SHALL be populated after every `invoke()` call (success, failure, or timeout). The base `RuntimeAdapter` ABC SHALL return None by default.

#### Scenario: Process info after successful invocation
- **WHEN** a Codex adapter `invoke()` completes successfully with exit code 0
- **THEN** `adapter.last_process_info` returns a dict with the process PID, exit_code=0, the command string, stderr content, and runtime_type="codex"

#### Scenario: Process info after timeout
- **WHEN** a Codex adapter `invoke()` times out
- **THEN** `adapter.last_process_info` returns a dict with exit_code=-1, stderr indicating timeout, and the PID (if available)

#### Scenario: Process info on SDK-based adapter
- **WHEN** `last_process_info` is accessed on a ClaudeCodeAdapter
- **THEN** None is returned (inherited default from ABC)

### Requirement: Spawner writes process log
The spawner SHALL write process log data to the database after every runtime invocation that has a session_id and a database pool, on both success and error paths. The write SHALL be best-effort: failures are logged at DEBUG level and never propagate to the caller or affect the session result.

#### Scenario: Process log written on successful session
- **WHEN** a runtime invocation succeeds and `runtime.last_process_info` is not None
- **THEN** the spawner calls `session_process_log_write()` with the process info after `session_complete()`

#### Scenario: Process log written on failed session
- **WHEN** a runtime invocation fails and `runtime.last_process_info` is not None
- **THEN** the spawner calls `session_process_log_write()` with the process info after `session_complete()`

#### Scenario: Process log write failure does not propagate
- **WHEN** `session_process_log_write()` raises an exception
- **THEN** the exception is caught, logged at DEBUG, and the spawner returns normally

#### Scenario: No process log for SDK-based sessions
- **WHEN** a ClaudeCodeAdapter invocation completes and `runtime.last_process_info` is None
- **THEN** no process log write is attempted
