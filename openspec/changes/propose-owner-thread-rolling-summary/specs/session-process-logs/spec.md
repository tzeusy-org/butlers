# Draft candidate: private rolling-summary custody

Status: NOT ADOPTED. Future additions require exact review/adoption and owning integrations. Full current requirements and prior scenarios are retained.

## MODIFIED Requirements

### Requirement: Process log write
The system SHALL provide a `write()` function that inserts or upserts a process log row for a given session_id. The function SHALL accept pid, exit_code, command, stderr, runtime_type, and ttl_days parameters. Stderr SHALL be capped at 32 KiB; content exceeding this limit is trimmed with a marker. If a row for the session already exists, it SHALL be replaced (upsert).

After independent exact adoption and all owned custody integrations, for a private-summary turn only, protected material SHALL be omitted from command/argv, stderr/stdout/errors and last_process_info before write/upsert/trimming/telemetry/read. Safe command shape/status/exit/PID and fixed omission preserve evidence without payload. TTL/32-KiB trim/DEBUG/best-effort is not disposal. Eligible adapters prove no private file/transcript/resume/cache survives outside leased invocation; unknown capture/cleanup makes them ineligible. Normal safe diagnostics/TTL/upsert/errors retain their owner.

ID: REQ-session-process-logs-003
Source: session-process-logs § Process log write; private summary custody R1; RFC 0003/0007 unchanged wire/source identity
Scope: proposed-owner-gated


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

#### Scenario: Process diagnostics omit private command and echo copies

- **WHEN** eligible adapter captures command/stderr/error for private context
- **THEN** safe metadata is projected before persistence/upsert/trimming with no derivative/private provenance
- **AND** success/failure/timeout evidence remains useful without prompt-bearing command/echo

#### Scenario: Private copy cannot survive a log upsert or cleanup race

- **WHEN** old private attempt reports diagnostics after invalidation or replacement log write
- **THEN** epoch/copy admission prevents retained/exposed private text in either row
- **AND** ordinary safe TTL/upsert remains; unresolved cleanup is not completed custody
