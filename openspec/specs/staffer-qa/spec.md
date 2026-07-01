# QA Staffer

## Purpose

Defines the QA Staffer — a permanently-running infrastructure agent (type = "staffer") that acts as the system-wide SRE for the butlers ecosystem. Owns the patrol loop lifecycle, pluggable discovery source architecture, roster identity, and operational contract. Subsumes the existing per-butler self-healing into a unified quality assurance function: discovers errors across multiple channels, triages and deduplicates findings, dispatches investigations, and raises anonymized PRs.

## ADDED Requirements

### Requirement: QA Staffer Identity
The QA Staffer SHALL be a staffer-typed agent in the roster at `roster/qa/` with `type = "staffer"` in its `butler.toml`. It is excluded from user-message routing and daily briefing contributions per the staffer archetype contract.

#### Scenario: Roster configuration
- **WHEN** the QA Staffer's `butler.toml` is loaded
- **THEN** `config.type` is `ButlerType.STAFFER`
- **AND** `config.name` is `"qa"`
- **AND** `config.permissions.cross_butler_access` is `["*"]`

#### Scenario: Staffer behaviors apply automatically
- **WHEN** the QA Staffer daemon starts
- **THEN** it is excluded from switchboard user-message classification
- **AND** it does not register daily briefing contribution schedules
- **AND** it registers with the switchboard for reachability (butler-to-staffer routing)

### Requirement: Infrastructure Contract (MANIFESTO.md)
The QA Staffer's MANIFESTO.md SHALL define an infrastructure contract with SRE-like framing: service responsibilities, SLAs, failure modes, dependency graph, and escalation procedures.

#### Scenario: Contract covers QA responsibilities
- **WHEN** the QA Staffer's MANIFESTO.md is authored
- **THEN** it defines: discovery scope (all registered sources), patrol cadence (configurable), issue triage policy (severity thresholds, dedup rules), investigation dispatch policy, PR creation standards, anonymization requirements, and permissions model
- **AND** it specifies failure modes: discovery source unavailable, DB pool exhaustion, GitHub API rate limiting, worktree creation failures
- **AND** it specifies escalation: circuit breaker trips → log WARNING + dashboard alert; repeated anonymization failures → halt PR pipeline

### Requirement: Pluggable Discovery Source Architecture
The QA Staffer SHALL support a pluggable `DiscoverySource` protocol for error detection across multiple channels. Each source produces `QaFinding` objects with computed fingerprints. Sources are registered at startup and polled during each patrol cycle.

#### Scenario: DiscoverySource protocol definition
- **WHEN** a new discovery source is implemented
- **THEN** it implements the `DiscoverySource` protocol with: `name` (str property), `async discover(lookback_minutes: int) -> list[QaFinding]` method
- **AND** the protocol requires no LLM invocation — all sources use tool-based filtering (regex, SQL queries, file parsing) to avoid context wastage

#### Scenario: Source registration at startup
- **WHEN** the QA Staffer daemon starts
- **THEN** it registers all enabled discovery sources from `[modules.qa].enabled_sources` config
- **AND** default enabled sources are: `["log_scanner", "session_records", "butler_reports", "tool_call_failures"]`
- **AND** disabled sources are logged at INFO level and skipped during patrol
- **AND** adapter diagnostics that are only useful through structured session
  records are suppressed by `log_scanner` only when `session_records` actually
  registered successfully

#### Scenario: QA module MCP tool registration
- **WHEN** the QA module's `register_tools()` is called
- **THEN** it registers: `report_finding` (receives findings from butler relay via Switchboard route), `force_patrol` (triggers immediate patrol), `get_qa_status` (returns QA staffer operational summary)
- **AND** `report_finding` is the tool called by butlers' self-healing modules via `switchboard_client.call_tool("route", {"target_butler": "qa", "tool_name": "report_finding", "args": ...})`
- **AND** `report_finding` accepts: `fingerprint` (str, treated as a hint; the QA module recomputes the canonical fingerprint via `compute_fingerprint_from_report` and logs a debug warning on mismatch), `exception_type` (str), `call_site` (str), `severity` (int 0-4, clamped to range with a WARNING if out-of-range; authoritative canonical scoring overrides caller intent for critical/high errors), `event_summary` (str), `context` (str, optional), `source_butler` (str), and `trigger_source` (str, optional, carrying the calling session's trigger_source such as "healing" or "qa", propagated as `source_session_trigger_source` for QA self-recursion suppression)
- **AND** `report_finding` queues the finding (with canonical fingerprint and severity) in the `butler_reports` source buffer and returns `{"accepted": true}` synchronously
- **AND** `tool_metadata()` declares `context` and `event_summary` as sensitive on `report_finding` (may contain agent reasoning about user-related errors)

#### Scenario: Reactive finding buffer is volatile
- **WHEN** the QA staffer daemon restarts
- **THEN** any buffered findings from `report_finding` that were not yet processed by a patrol cycle are lost
- **AND** this is acceptable because: (a) the `session_records` source will rediscover these failures from the DB on the next patrol, and (b) the `log_scanner` source will find them in logs
- **AND** no duplicate investigation is created because the triage layer deduplicates by fingerprint

#### Scenario: Adding a new discovery source
- **WHEN** a developer wants to add a new error detection channel
- **THEN** they implement `DiscoverySource` protocol in `src/butlers/core/qa/sources/`
- **AND** add the source name to the `enabled_sources` config list
- **AND** no changes to the triage, dispatch, or dashboard layers are required

#### Scenario: Source failure is isolated
- **WHEN** a discovery source raises an exception during `discover()`
- **THEN** the error is logged at ERROR level with the source name
- **AND** the patrol cycle continues with findings from other sources
- **AND** the patrol record includes the failed source in `error_detail`

### Requirement: V1 Discovery Sources
The QA Staffer SHALL ship with four discovery sources in v1.

#### Scenario: Log scanner source
- **WHEN** the `log_scanner` source is enabled
- **THEN** it reads structured JSON log files from `logs/butlers/`, `logs/connectors/`, `logs/uvicorn/` for ERROR/WARNING entries within the lookback window
- **AND** all filtering is tool-based (JSON parsing, regex, severity checks) — no LLM invocation

#### Scenario: Session record source
- **WHEN** the `session_records` source is enabled
- **THEN** it queries a read-only SQL view `public.v_qa_recent_failures` (sanctioned cross-schema exception per RFC 0010 pattern) for recent session failures within the lookback window
- **AND** the view is a UNION across butler `sessions` tables filtered to error/timeout/crash statuses
- **AND** the view is read-only (structurally enforced — no INSERT/UPDATE/DELETE), date-filtered, and created via auditable migration with explicit per-schema GRANT
- **AND** extracts exception type, traceback, call site, and butler name from the session record
- **AND** event summaries extracted from session records are passed through `anonymize()` before storage (session error messages may contain user data)
- **AND** computes fingerprints using the same algorithm as log scanner findings
- **AND** excludes rows that represent expected or controlled outcomes rather than product/runtime failures:
  - short (`<= 60s`) Switchboard mini-model classification timeout rows with `trigger_source = "tick"` as expected routing fallback telemetry; longer or non-`tick` Switchboard timeouts remain actionable
  - synthetic startup-recovery rows (`orphaned: daemon restart`)
  - spawner guardrail terminations whose error text contains an intentional-stop marker (`token_budget_exceeded`, `tool_call_budget_exceeded`, `degenerate_tool_loop`) — the same markers the failover classifier treats as failover-ineligible; these remain visible in session history and the token ledger but do not spawn autonomous code-fix investigations

#### Scenario: Butler report source (reactive relay via Switchboard)
- **WHEN** the `butler_reports` source is enabled
- **THEN** butlers relay `report_error` findings to the QA staffer via Switchboard's `route()` MCP tool, calling the QA staffer's `report_finding` tool directly (tool-to-tool routing, not session-spawning `route.execute`) — preserving MCP-only inter-butler communication (non-negotiable rule #3)
- **AND** the QA staffer's `report_finding` MCP tool handler queues received findings in an in-memory buffer
- **AND** the patrol cycle drains the buffer and includes those findings alongside batch sources
- **AND** if the buffer exceeds `max_reactive_buffer` (default: 50), oldest entries are dropped with a WARNING

#### Scenario: Tool-call failure source
- **WHEN** the `tool_call_failures` source is enabled
- **THEN** it queries recent failed MCP tool calls within the lookback window and produces `QaFinding` objects with fingerprints derived from the tool name and call site
- **AND** all filtering is tool-based (SQL queries); no LLM invocation

### Requirement: Future Discovery Source Catalog
The discovery source architecture SHALL accommodate future sources without requiring changes to the triage, dispatch, or dashboard layers.

#### Scenario: Prometheus metrics source (post-v1)
- **WHEN** a `prometheus_metrics` source is implemented
- **THEN** it executes PromQL instant queries for error rate spikes, latency anomalies, and queue depth thresholds
- **AND** produces `QaFinding` objects with fingerprints derived from the metric name + label set + anomaly type

#### Scenario: MCP reachability probe source (post-v1)
- **WHEN** an `mcp_reachability` source is implemented
- **THEN** it attempts SSE/HTTP connections to all registered butler MCP endpoints
- **AND** unreachable endpoints produce findings with severity 1 (high) and fingerprint derived from butler name + endpoint

#### Scenario: Scheduler drift source (post-v1)
- **WHEN** a `scheduler_drift` source is implemented
- **THEN** it compares expected vs. actual tick timestamps for all butlers' scheduled jobs
- **AND** drift exceeding a configurable threshold produces a finding

#### Scenario: Connector heartbeat source (post-v1)
- **WHEN** a `connector_heartbeat` source is implemented
- **THEN** it checks connector last-seen timestamps against expected heartbeat intervals
- **AND** silent connectors (no heartbeat within 2x interval) produce findings

#### Scenario: Git regression source (post-v1)
- **WHEN** a `git_regression` source is implemented
- **THEN** after merges to `main`, it proactively runs the test suite in a worktree
- **AND** test failures produce findings with the failing test as the fingerprint key

### Requirement: Patrol Loop Lifecycle
The QA Staffer SHALL run a scheduler-driven patrol loop that executes at a configurable interval (default: 10 minutes). Each patrol cycle is a discrete unit of work with its own DB record.

#### Scenario: Patrol tick fires on schedule
- **WHEN** the patrol interval elapses
- **THEN** the QA Staffer creates a new patrol record in `public.qa_patrols`
- **AND** polls all enabled discovery sources
- **AND** passes combined findings through the triage layer
- **AND** dispatches novel findings for investigation (up to concurrency cap)
- **AND** updates the patrol record with outcome (findings_count, novel_count, dispatched_count, status, sources_polled)

#### Scenario: Patrol cycle completes with no findings
- **WHEN** all discovery sources return empty finding sets
- **THEN** the patrol record is marked `status = "clean"`
- **AND** `findings_count = 0`, `novel_count = 0`, `dispatched_count = 0`

#### Scenario: Patrol cycle discovers issues
- **WHEN** one or more discovery sources return findings
- **THEN** each finding is recorded in `public.qa_findings` with its `source_type`
- **AND** the triage layer filters to novel issues
- **AND** novel issues are dispatched for investigation (up to concurrency cap)
- **AND** the patrol record reflects the full pipeline: `findings_count`, `novel_count`, `dispatched_count`

#### Scenario: Patrol overlap prevention
- **WHEN** a patrol tick fires while the previous patrol cycle is still running
- **THEN** the new tick is skipped with a log WARNING
- **AND** the skip is recorded as a patrol with `status = "skipped_overlap"`

#### Scenario: Patrol crash recovery
- **WHEN** the QA staffer daemon restarts and finds a `qa_patrols` row with `status = "running"` and no `completed_at`
- **THEN** the stale row is updated to `status = "error"`, `completed_at = now()`, `error_detail = "daemon restart during patrol"`
- **AND** any `investigating` healing attempts with `qa_patrol_id` matching that patrol are evaluated by the restart recovery logic in `healing_attempts` (deadline-based timeout or preserve — no special re-dispatch)
- **AND** findings that were novel but not yet dispatched when the crash occurred are NOT durably queued; they will be rediscovered by `session_records` or `log_scanner` sources on the next patrol cycle
- **NOTE** `dispatch_pending` is not a valid `healing_attempts` status — there is no special recovery path for it

#### Scenario: Reactive findings between patrols
- **WHEN** a butler calls `report_error` between patrol cycles
- **THEN** the finding is relayed to the QA staffer via Switchboard's `route()` tool calling the QA staffer's `report_finding` tool — this is direct butler-to-staffer tool routing (bypasses user-message classification)
- **AND** the finding is buffered in the `butler_reports` source's in-memory queue
- **AND** it is picked up on the next patrol tick
- **AND** if the finding's severity is 0 (critical), an immediate mini-patrol is triggered for that finding only

### Requirement: Patrol Configuration
The QA Staffer's patrol behavior SHALL be configurable via `butler.toml` under `[modules.qa]`.

#### Scenario: Default configuration
- **WHEN** `[modules.qa]` is absent or has no overrides
- **THEN** defaults apply: `patrol_interval_minutes = 10`, `log_lookback_minutes = 15`, `max_concurrent_investigations = 2`, `severity_threshold = 2`, `enabled = true`, `enabled_sources = ["log_scanner", "session_records", "butler_reports", "tool_call_failures"]`, `max_reactive_buffer = 50`, `log_scanner_max_entries = 10000`, `log_scanner_max_findings = 100`
- **NOTE** `log_scanner_max_entries` counts only error/warning candidates (benign INFO/DEBUG lines do not consume the budget); `log_scanner_max_findings` caps the maximum distinct fingerprints returned per scan; file order within each subdirectory is randomised to prevent systematic starvation of later-sorted files under high load
- **NOTE** the module also exposes `retention_cleanup_hour` (UTC hour for the daily raw-evidence cleanup, default 4) and log-scanner safety caps `log_scanner_max_total_lines` and `log_scanner_max_scan_seconds`; the QA roster runs `qa_evidence_cleanup` (hourly tick that acts only at the configured hour) and `qa_pr_status_check` schedules alongside `qa_patrol`

#### Scenario: Custom configuration
- **WHEN** `[modules.qa]` specifies overrides
- **THEN** those values are used: e.g., `patrol_interval_minutes = 5`, `enabled_sources = ["log_scanner"]`
- **AND** values are validated at config parse time (intervals > 0, lookback > 0, concurrency >= 1, sources are known names)

### Requirement: Permissions and Security Model
The QA Staffer SHALL operate with a least-privilege security model: dedicated credentials with no merge access, sandboxed investigation environments, and no access to butler runtime secrets.

#### Scenario: Dedicated GitHub credentials via Tier 1 system secrets
- **WHEN** the QA Staffer creates PRs
- **THEN** it retrieves the GitHub token via `CredentialStore.resolve("BUTLERS_QA_GH_TOKEN")` which checks: (1) `qa.butler_secrets` (QA staffer's own schema), (2) shared fallback pools, (3) env fallback if configured — per RFC 0006 Tier 1 resolution order
- **AND** the secret is provisioned in the QA staffer's own butler_secrets table with `category = "qa"`, `is_sensitive = true` — managed via the QA staffer's secrets page on the dashboard at /secrets
- **AND** the token is scoped to: branch push, PR creation, PR labeling on `Tzeusy/butlers`
- **AND** the token SHALL NOT have merge/approve permissions — humans remain in the merge seat
- **AND** if the secret is not found at any tier, the investigation completes but transitions to `failed` with reason `"no_gh_token"`

#### Scenario: Investigation agent sandbox
- **WHEN** an investigation agent is spawned in a worktree
- **THEN** its environment contains only: `BUTLERS_QA_GH_TOKEN` (injected from secrets store as `GH_TOKEN` for `gh` CLI compatibility), `PATH`, and build-tool variables (`UV_CACHE_DIR`, etc.)
- **AND** it does NOT have access to: butler DB connection strings, API keys, OAuth tokens, user data, or any `BUTLERS_*` env vars
- **AND** it does NOT have MCP server connections (the spawner automatically sets empty MCP server config when `trigger_source="qa"`, preventing access to live production state and suppressing the Codex adapter's MCP-discovery retry path)
- **AND** its filesystem access is limited to the worktree directory

#### Scenario: Log scanner reads are local-only
- **WHEN** the log scanner reads log files
- **THEN** log content is processed in-memory and never transmitted to external services
- **AND** only computed fingerprints, exception types, call sites, and sanitized event summaries are persisted to the DB
- **AND** raw log lines are not stored in `qa_findings`

#### Scenario: Cross-butler DB access via sanctioned SQL view
- **WHEN** the session record source queries for failed sessions
- **THEN** it queries ONLY the sanctioned `public.v_qa_recent_failures` SQL view (RFC 0010 pattern)
- **AND** it does NOT query butler-owned schemas directly
- **AND** the view is read-only, date-filtered, and created via auditable migration with explicit GRANT
- **AND** it writes findings only to `public.qa_patrols`, `public.qa_findings`, and `public.healing_attempts`

### Requirement: Concurrency Model
The QA Staffer SHALL participate in the two-tier concurrency model defined in RFC 0001: its own per-staffer semaphore for investigation agent sessions, plus the global semaphore shared across all butlers/staffers.

#### Scenario: Investigation sessions respect global semaphore
- **WHEN** the QA staffer spawns an investigation agent
- **THEN** the investigation session acquires the global semaphore (shared with all butler sessions)
- **AND** it acquires the QA staffer's per-staffer semaphore
- **AND** `max_concurrent_investigations` config caps the per-staffer concurrency independently of the global limit

#### Scenario: QA concurrency cap counts only QA-originated investigations
- **WHEN** the QA dispatcher evaluates the concurrency gate
- **THEN** it calls `count_active_attempts(pool, qa_only=True)` which counts rows with `status = 'investigating'` AND `qa_patrol_id IS NOT NULL`
- **AND** legacy per-butler self-healing attempts (`qa_patrol_id IS NULL`) do NOT consume QA concurrency budget
- **AND** QA investigations do NOT consume legacy self-healing concurrency budget (the two caps are disjoint)
- **AND** both paths still share the global semaphore (RFC 0001 two-tier model)

#### Scenario: Investigation does not deadlock reporting butler
- **WHEN** a butler calls `report_error` and the QA staffer dispatches an investigation
- **THEN** the investigation runs under the QA staffer's semaphore, NOT the reporting butler's
- **AND** no deadlock is possible because the two daemons have independent per-butler semaphores

### Requirement: Observability (RFC 0005 Compliance)
The QA Staffer SHALL integrate with the project's observability stack: OpenTelemetry tracing and Prometheus metrics, following the conventions in RFC 0005.

#### Scenario: Patrol cycle tracing
- **WHEN** a patrol cycle starts
- **THEN** a new OTel span `qa.patrol` is created with attributes: `qa.patrol_id`, `qa.sources_polled`, `butler.name = "qa"`
- **AND** each discovery source call is a child span `qa.discover.<source_name>`
- **AND** triage is a child span `qa.triage`
- **AND** each investigation dispatch is a child span `qa.dispatch`
- **AND** the span is completed when the patrol cycle finishes (not when investigations finish)

#### Scenario: Investigation tracing
- **WHEN** an investigation agent is spawned
- **THEN** a new root span `qa.investigation` is created (NOT a child of the patrol span — investigations may outlive the patrol)
- **AND** attributes include: `qa.attempt_id`, `qa.fingerprint`, `qa.source_butler`, `qa.severity`

#### Scenario: Prometheus metrics (low-cardinality per RFC 0005)
- **WHEN** the QA Staffer module starts
- **THEN** it registers the following gauges and counters:
  - `qa_patrol_total` (counter, labels: `status`) — patrol outcomes
  - `qa_findings_total` (counter, labels: `source_type`, `dedup_reason`) — findings by source and dedup outcome
  - `qa_investigations_active` (gauge) — currently running investigations
  - `qa_patrol_duration_seconds` (histogram) — patrol cycle duration
  - `qa_investigation_duration_seconds` (histogram, labels: `status`) — investigation outcome durations
  - `qa_findings_retention_purged_total` (counter): finding rows whose retained raw evidence lines were purged by the daily retention cleanup
- **AND** labels follow RFC 0005 low-cardinality discipline: no UUIDs, fingerprints, butler names, or timestamps as label values

### Requirement: Patrol Database Schema
Patrol cycles SHALL be recorded in `public.qa_patrols` for observability and dashboard display.

#### Scenario: Patrol record structure
- **WHEN** a patrol cycle starts
- **THEN** a row is inserted with: `id` (UUIDv7), `started_at` (timestamptz), `completed_at` (nullable timestamptz), `status` (text: running, clean, findings_dispatched, suppressed, error, skipped_overlap), `findings_count` (int), `novel_count` (int), `dispatched_count` (int), `log_lookback_minutes` (int), `sources_polled` (text[], list of source names), `error_detail` (nullable text)

#### Scenario: Patrol record is updated on completion
- **WHEN** a patrol cycle completes (success or failure)
- **THEN** the row's `completed_at`, `status`, and count fields are updated
- **AND** if the cycle errored, `error_detail` contains a sanitized error summary
