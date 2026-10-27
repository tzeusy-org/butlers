## MODIFIED Requirements

### Requirement: Severity Filtering
The scanner SHALL filter log entries by severity level, extracting entries at ERROR level or above, plus WARNING entries that match crash sentinel patterns, except for known duplicate operational logs that are better sourced from structured discovery sources.

#### Scenario: ERROR entries included
- **WHEN** a log entry has `level = "error"` or `level = "critical"`
- **AND** the entry is not a known duplicate operational log covered by another discovery source
- **THEN** it is included in the finding set
- **EXCEPT** duplicate spawner runtime timeout logs MAY be excluded when the scanner is registered alongside the `session_records` source

#### Scenario: Spawner timeout duplicate suppression
- **WHEN** the scanner is registered with `session_records` available in the same patrol configuration
- **AND** a log entry has logger `butlers.core.spawner`
- **AND** its event starts with `Runtime invocation failed: TimeoutError:`
- **AND** the event contains timeout wording
- **THEN** the scanner excludes that entry from the log-scanner finding set
- **AND** timeout coverage is provided by the `session_records` source with session identifiers and normalized timeout status

#### Scenario: Log-scanner-only timeout coverage
- **WHEN** the scanner is registered without an available `session_records` source
- **AND** a spawner runtime timeout log qualifies by severity
- **THEN** the scanner includes the entry in the finding set

#### Scenario: Codex timeout diagnostics delegated to session records
- **WHEN** the scanner is registered with `session_records` available in the same patrol configuration
- **AND** the log scanner sees `butlers.core.runtimes.codex` emit `Codex CLI timed out after ...`
- **THEN** the scanner excludes the raw adapter diagnostic
- **AND** timeout investigations are sourced from `session_records`, where the finding includes session identifiers and timeout status
- **WHEN** `session_records` is unavailable or disabled
- **THEN** the log scanner includes the timeout entry to preserve degraded-mode coverage

#### Scenario: Adapter-managed session timeout duplicates excluded
- **WHEN** an OpenCode adapter timeout is logged by `butlers.core.runtimes.opencode`
- **OR** the matching spawner wrapper log is `Runtime invocation failed: TimeoutError: OpenCode CLI timed out after ...`
- **THEN** the scanner excludes the log entry from the finding set
- **AND** the timeout remains discoverable through `session_records`, which carries structured session evidence
- **AND** deployments that disable `session_records` intentionally opt out of structured session-timeout coverage

#### Scenario: OpenCode empty-response attempt duplicates excluded
- **WHEN** the log scanner sees `butlers.core.runtimes.opencode` emit `OpenCode CLI returned no response: ...`
- **THEN** the scanner excludes the adapter-level raw log entry from the finding set
- **AND** recovered same-tier failover attempts do not create autonomous QA cases from adapter attempt logs
- **WHEN** the matching spawner wrapper log is `Runtime invocation failed: RuntimeError: OpenCode CLI returned no response: ...`
- **AND** the scanner is registered with `session_records` available in the same patrol configuration
- **THEN** the scanner excludes the spawner wrapper log from the finding set
- **AND** the terminal failure remains discoverable through `session_records`
- **WHEN** `session_records` is unavailable or disabled
- **AND** the matching spawner wrapper log is present
- **THEN** the log scanner includes the spawner wrapper log to preserve degraded-mode coverage

#### Scenario: OpenCode non-zero-exit attempt duplicates excluded
- **WHEN** the log scanner sees `butlers.core.runtimes.opencode` emit `OpenCode CLI exited with code ...`
- **THEN** the scanner excludes the adapter-level raw log entry from the finding set
- **AND** recovered same-tier failover attempts do not create autonomous QA cases from adapter attempt logs
- **WHEN** the matching spawner wrapper log is `Runtime invocation failed: RuntimeError: OpenCode CLI exited with code ...`
- **AND** the scanner is registered with `session_records` available in the same patrol configuration
- **THEN** the scanner excludes the spawner wrapper log from the finding set
- **AND** the terminal failure remains discoverable through `session_records`
- **WHEN** `session_records` is unavailable or disabled
- **AND** the matching spawner wrapper log is present
- **THEN** the log scanner includes the spawner wrapper log to preserve degraded-mode coverage

#### Scenario: Expected Switchboard classification timeout excluded
- **WHEN** a log entry is a `butlers.core.spawner` Switchboard runtime timeout
- **AND** `trigger_source` is `"classification"` (or the historical `"tick"`; both values are treated as the same call site so older log lines still match)
- **AND** the event has the Switchboard mini-model classification timeout signature
- **AND** the timeout duration is no more than 60 seconds
- **THEN** it is excluded from the finding set as expected routing fallback telemetry
- **AND** longer or non-classification Switchboard timeouts remain included when their level otherwise qualifies

#### Scenario: WARNING entries with crash patterns included
- **WHEN** a log entry has `level = "warning"` and its `event` or `exception` field matches a crash sentinel pattern (e.g., `OOM`, `SIGKILL`, `ConnectionRefused`, `TimeoutError`, `deadlock`)
- **THEN** it is included in the finding set

#### Scenario: INFO and below excluded
- **WHEN** a log entry has `level = "info"`, `"debug"`, or `"trace"`
- **THEN** it is excluded from the finding set
