## MODIFIED Requirements

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
