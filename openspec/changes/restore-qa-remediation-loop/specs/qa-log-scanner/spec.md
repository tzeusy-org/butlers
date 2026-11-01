## MODIFIED Requirements

### Requirement: Temporal Filtering
The scanner SHALL use the configured lookback as a replay-overlap window while retaining bounded durable source progress and explicit gap coverage. Catch-up may include older unread entries; overlap alone is not proof of complete discovery.

ID: REQ-qa-log-scanner-001
Source: docs/plans/2026-10-14-qa-month-packet.md; heart-and-soul/development.md; RFC0015; restore-qa-remediation-loop/design.md
Scope: v1-mandatory

#### Scenario: Entries within lookback window
- **WHEN** `log_lookback_minutes = 15` and the patrol starts at T
- **THEN** normal overlapping reads include entries with `timestamp >= T - 15min`, plus unread catch-up entries after the durable progress boundary
- **AND** already acknowledged entries outside the overlap can be skipped; unacknowledged retained entries cannot be silently skipped

#### Scenario: Efficient seeking (current file only)
- **WHEN** scanning a large log file
- **THEN** the scanner reads from the end of the active `.log` file backwards (or uses file size heuristics) to avoid reading the entire file
- **AND** seeks from a validated source-generation cursor and bounds work before reading/parsing additional bytes
- **AND** continues through a retained rotated generation when that is necessary and supported, otherwise records a coverage gap; session-record redundancy never proves omitted log-only events were read

### Requirement: Scan Performance Guardrails
The scanner SHALL have configurable limits (max candidate entries, max unique findings, max total lines parsed, and a wall-clock deadline) to prevent unbounded resource consumption during a patrol cycle. On any cap hit the scanner records the truncation time and reason (exposed as `last_truncated` and `last_truncated_reason`) and returns findings plus a partial-coverage receipt and resumable progress. Fair persisted continuation covers sources/directories, not only shuffled files within the first directory.

ID: REQ-qa-log-scanner-002
Source: docs/plans/2026-10-14-qa-month-packet.md; heart-and-soul/development.md; RFC0015; restore-qa-remediation-loop/design.md
Scope: v1-mandatory

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

#### Scenario: Cap before materializing a large source
- **WHEN** a byte, line, candidate, finding or wall-clock budget would be exceeded
- **THEN** reading/parsing stops within that bound and returns an explicit partial result with the last safely persisted boundary
- **AND** the next eligible scan continues fairly without certifying the unread tail as complete
