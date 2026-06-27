# QA Log Scanner

## Purpose

Cross-butler log scanning discovery source — one of multiple pluggable sources in the QA staffer's discovery architecture. Reads structured JSON log files from all deployed butlers, staffers, and connectors. Uses tool-based filtering (JSON parsing, regex, severity checks) to extract error/warning events without LLM invocation. Computes fingerprints and produces a normalized finding set for the triage layer.

## ADDED Requirements

### Requirement: DiscoverySource Protocol Compliance
The log scanner SHALL implement the `DiscoverySource` protocol, making it interchangeable with other discovery sources.

#### Scenario: Protocol implementation
- **WHEN** the log scanner is registered as a discovery source
- **THEN** it exposes `name = "log_scanner"` and `async discover(lookback_minutes: int) -> list[QaFinding]`
- **AND** the `discover()` method performs all filtering using tool-based approaches (JSON parsing, regex matching) with zero LLM calls

### Requirement: Log Source Discovery
The scanner SHALL discover log files from the configured log root directory, covering all butler, staffer, and connector logs.

#### Scenario: Discover all log sources
- **WHEN** the scanner starts a scan cycle
- **THEN** it reads from `logs/butlers/*.log` (per-butler application logs), excluding `logs/butlers/qa.log` (the QA staffer's own log — its errors are monitored via Prometheus metrics and OTel, not self-investigated)
- **AND** `logs/connectors/*.log` (standalone connector logs)
- **AND** `logs/uvicorn/*.log` (HTTP server / MCP transport logs)
- **AND** the log root is resolved from `BUTLERS_LOG_ROOT` env var or defaults to `logs/`

#### Scenario: Missing log directory is non-fatal
- **WHEN** a log subdirectory (e.g., `logs/connectors/`) does not exist
- **THEN** the scanner skips it with a DEBUG log
- **AND** continues scanning other directories

### Requirement: JSON-Lines Parsing
The scanner SHALL parse log files in JSON-lines format (one JSON object per line) as produced by structlog's JSON renderer.

#### Scenario: Valid JSON line
- **WHEN** a log line is valid JSON with at minimum `level`, `event`, and `timestamp` fields
- **THEN** it is parsed into a `LogEntry` dataclass with: `level`, `event`, `timestamp`, `butler_name` (from structlog context or filename), `logger` (module path), `exception` (optional), `traceback` (optional)

#### Scenario: Malformed JSON line
- **WHEN** a log line is not valid JSON
- **THEN** it is skipped with a TRACE-level counter increment (no per-line warning)
- **AND** the total malformed count is logged at DEBUG level at the end of the scan

### Requirement: Temporal Filtering
The scanner SHALL only process log entries within the configured lookback window relative to the patrol start time.

#### Scenario: Entries within lookback window
- **WHEN** `log_lookback_minutes = 15` and the patrol starts at T
- **THEN** only log entries with `timestamp >= T - 15min` are included
- **AND** entries before the lookback window are skipped

#### Scenario: Efficient seeking (current file only)
- **WHEN** scanning a large log file
- **THEN** the scanner reads from the end of the active `.log` file backwards (or uses file size heuristics) to avoid reading the entire file
- **AND** stops reading once it encounters entries older than the lookback window
- **AND** does NOT scan rotated files (e.g., `.log.1`) — if rotation happens mid-patrol, the `session_records` source provides redundant coverage from the DB

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

#### Scenario: Expected Switchboard classification timeout excluded
- **WHEN** a log entry is a `butlers.core.spawner` Switchboard runtime timeout
- **AND** `trigger_source = "tick"`
- **AND** the event has the Switchboard mini-model classification timeout signature
- **AND** the timeout duration is no more than 60 seconds
- **THEN** it is excluded from the finding set as expected routing fallback telemetry
- **AND** longer or non-`tick` Switchboard timeouts remain included when their level otherwise qualifies

#### Scenario: WARNING entries with crash patterns included
- **WHEN** a log entry has `level = "warning"` and its `event` or `exception` field matches a crash sentinel pattern (e.g., `OOM`, `SIGKILL`, `ConnectionRefused`, `TimeoutError`, `deadlock`)
- **THEN** it is included in the finding set

#### Scenario: INFO and below excluded
- **WHEN** a log entry has `level = "info"`, `"debug"`, or `"trace"`
- **THEN** it is excluded from the finding set

### Requirement: Finding Extraction
Each qualifying log entry SHALL be normalized into a `QaFinding` with a computed fingerprint for deduplication.

#### Scenario: Finding structure
- **WHEN** a qualifying log entry is processed
- **THEN** a `QaFinding` is produced with: `fingerprint` (str, SHA-256), `source_type` (str, `"log_scanner"`), `source_butler` (str), `source_file` (str, log filename), `severity` (int, 0=critical..3=low), `exception_type` (str or "unknown"), `event_summary` (str, first 200 chars of event, sanitized via `anonymize()` to strip PII), `call_site` (str, logger module path), `timestamp` (datetime)
- **AND** raw log line content is NOT included in the finding (privacy: raw logs may contain user data)
- **AND** `event_summary` is passed through the anonymizer before storage because error messages may contain user data (email addresses, contact names, etc.)

#### Scenario: Fingerprint computation
- **WHEN** computing a finding fingerprint
- **THEN** the fingerprint is a SHA-256 hash of: `exception_type + call_site + normalized_event_summary`
- **AND** the normalization strips variable content (UUIDs, timestamps, numeric IDs, file paths) to group semantically identical errors
- **AND** the algorithm is compatible with `src/butlers/core/healing/fingerprint.py` to enable cross-source deduplication

### Requirement: Finding Aggregation
Multiple log entries with the same fingerprint within a single scan cycle SHALL be aggregated into a single finding with occurrence count.

#### Scenario: Duplicate entries aggregated
- **WHEN** three log entries produce the same fingerprint
- **THEN** a single `QaFinding` is returned with `occurrence_count = 3`
- **AND** `first_seen` and `last_seen` timestamps bracket the occurrences

### Requirement: Scan Performance Guardrails
The scanner SHALL have configurable limits (max candidate entries, max unique findings, max total lines parsed, and a wall-clock deadline) to prevent unbounded resource consumption during a patrol cycle. On any cap hit the scanner records the truncation time and reason (exposed as `last_truncated` and `last_truncated_reason`) and returns the findings collected so far. File scan order within a subdirectory is shuffled per cycle to avoid deterministic starvation of later files.

#### Scenario: Maximum entries per scan
- **WHEN** the scanner has processed `max_entries_per_scan` entries (default: 10000)
- **THEN** it stops reading and returns the findings collected so far
- **AND** logs a WARNING indicating truncation

#### Scenario: Maximum findings per scan
- **WHEN** the scanner has produced `max_findings_per_scan` unique findings (default: 100)
- **THEN** it stops processing and returns
- **AND** logs a WARNING indicating the finding cap was hit

#### Scenario: Maximum total lines parsed
- **WHEN** the scanner has read `max_total_lines` log lines across all files (default: 200000)
- **THEN** it stops reading and returns the findings collected so far
- **AND** records the truncation time and reason in `last_truncated` and `last_truncated_reason`

#### Scenario: Wall-clock scan deadline
- **WHEN** the scanner has run for `max_scan_seconds` wall-clock seconds (default: 30.0)
- **THEN** it stops reading and returns the findings collected so far
- **AND** records the truncation time and reason in `last_truncated` and `last_truncated_reason`
