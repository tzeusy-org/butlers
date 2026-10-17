## MODIFIED Requirements

### Requirement: QA Staffer Identity
The QA Staffer SHALL be a staffer-typed agent in the roster at `roster/qa/` with `type = "staffer"` in its `butler.toml`. It is excluded from daily briefing contributions per the staffer archetype contract. Message classification no longer exists, so no user-message routing exclusion applies to it.

Source: RFC 0038 D1
Scope: v1-mandatory

#### Scenario: Roster configuration
- **WHEN** the QA Staffer's `butler.toml` is loaded
- **THEN** `config.type` is `ButlerType.STAFFER`
- **AND** `config.name` is `"qa"`
- **AND** `config.permissions.cross_butler_access` is `["*"]`

#### Scenario: Staffer behaviors apply automatically
- **WHEN** the QA Staffer daemon starts
- **THEN** it does not register daily briefing contribution schedules
- **AND** it registers with the switchboard for reachability

### Requirement: Pluggable Discovery Source Architecture
The QA Staffer SHALL support a pluggable `DiscoverySource` protocol for error detection across multiple channels. Each source produces `QaFinding` objects with computed fingerprints. Sources are registered at startup and polled during each patrol cycle. Findings that other butlers report SHALL reach the QA staffer through a Switchboard service-plane relay that is not LLM-presentable and is not on the session listener; the generic `route` tool is deleted at cutover.

Source: RFC 0038 D7
Scope: v1-mandatory

#### Scenario: DiscoverySource protocol definition
- **WHEN** a new discovery source is implemented
- **THEN** it implements the `DiscoverySource` protocol with: `name` (str property), `async discover(lookback_minutes: int) -> list[QaFinding]` method
- **AND** the protocol requires no LLM invocation — all sources use tool-based filtering (regex, SQL queries, file parsing) to avoid context wastage

#### Scenario: Source registration at startup
- **WHEN** the QA Staffer daemon starts
- **THEN** it registers all enabled discovery sources from `[modules.qa].enabled_sources` config
- **AND** default enabled sources are: `["log_scanner", "session_records", "butler_reports", "tool_call_failures", "infra_state"]`
- **AND** disabled sources are logged at INFO level and skipped during patrol
- **AND** adapter diagnostics that are only useful through structured session
  records are suppressed by `log_scanner` only when `session_records` actually
  registered successfully

#### Scenario: QA module MCP tool registration
- **WHEN** the QA module's `register_tools()` is called
- **THEN** it registers: `report_finding` (receives findings relayed through the Switchboard service-plane relay), `force_patrol` (triggers immediate patrol), `get_qa_status` (returns QA staffer operational summary)
- **AND** `report_finding` is a service-plane tool, not LLM-presentable and not on the session listener; butlers' self-healing modules reach it only through the Switchboard service-plane relay, never through the deleted generic `route` tool
- **AND** `report_finding` accepts: `fingerprint` (str, treated as a hint; the QA module recomputes the canonical fingerprint via `compute_fingerprint_from_report` and logs a debug warning on mismatch), `exception_type` (str), `call_site` (str), `severity` (int 0-4, clamped to range with a WARNING if out-of-range; authoritative canonical scoring overrides caller intent for critical/high errors), `event_summary` (str), `context` (str, optional), `source_butler` (str), and `trigger_source` (str, optional, set by the reporting butler's deterministic relay code from the reporting work's provenance — an engineering-executor session's `trigger_source` such as "healing" or "qa", or the goal origin of the verified invocation lineage — and propagated as `source_session_trigger_source` for QA self-recursion suppression)
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
The QA Staffer SHALL ship with five discovery sources in v1. Because domain butlers no longer run reasoning sessions and the goal ledger, not session logs, is the record of a goal, the `session_records` source SHALL discover reasoning failures from failed goal invocations in the ledger.

Source: RFC 0038 D2, D11 and D17
Scope: v1-mandatory

#### Scenario: Log scanner source
- **WHEN** the `log_scanner` source is enabled
- **THEN** it reads structured JSON log files from `logs/butlers/`, `logs/connectors/`, `logs/uvicorn/` for ERROR/WARNING entries within the lookback window
- **AND** all filtering is tool-based (JSON parsing, regex, severity checks) — no LLM invocation

#### Scenario: Session record source
- **WHEN** the `session_records` source is enabled
- **THEN** it queries a read-only SQL view `public.v_qa_recent_failures` (sanctioned cross-schema exception per RFC 0010 pattern) for recent goal-invocation failures within the lookback window
- **AND** the view reads Switchboard's goal-ledger invocation records filtered to the `failed` and `timed_out` invocation states, UNION engineering-executor rows of butler `sessions` tables filtered to error/timeout/crash statuses
- **AND** the view is read-only (structurally enforced — no INSERT/UPDATE/DELETE), date-filtered, and created via auditable migration with explicit per-schema GRANT
- **AND** extracts exception type, call site, `goal_id`, `invocation_id` and the goal's origin from an invocation record, never goal arguments or results, and attributes a goal-invocation finding to `source_butler = "switchboard"`, the owner of the goal runner, except a failed staffer-origin goal, which is attributed to the staffer whose contract admitted it (for example `"qa"`); it extracts exception type, traceback, call site and butler name from an executor session row and attributes that finding to the butler whose `sessions` table holds it
- **AND** event summaries extracted from invocation records are passed through `anonymize()` before storage (failure messages may contain user data)
- **AND** computes fingerprints using the same algorithm as log scanner findings
- **AND** excludes invocations that represent expected or controlled outcomes rather than product/runtime failures:
  - invocations ended by owner Stop or replaced by escalation (`stopped`, `escalated`)
  - lead invocations superseded by lead recovery after a lease lapse
  - invocations the runner terminated for exceeding their own turn, token or spend limits, and invocations of goals that ended `budget_exhausted`; these remain visible in the goal ledger but do not spawn autonomous code-fix investigations
  - synthetic startup-recovery executor rows (`orphaned: daemon restart`)
  - executor rows ended by a spawner guardrail whose error text contains an intentional-stop marker (`token_budget_exceeded`, `tool_call_budget_exceeded`, `degenerate_tool_loop`)

#### Scenario: Butler report source (reactive relay via Switchboard)
- **WHEN** the `butler_reports` source is enabled
- **THEN** butlers relay `report_error` findings to the QA staffer through the Switchboard service-plane relay, which forwards to the QA staffer's `report_finding` tool directly (tool-to-tool, starting no goal) — preserving MCP-only inter-butler communication (non-negotiable rule #3)
- **AND** the QA staffer's `report_finding` MCP tool handler queues received findings in an in-memory buffer
- **AND** the patrol cycle drains the buffer and includes those findings alongside batch sources
- **AND** if the buffer exceeds `max_reactive_buffer` (default: 50), oldest entries are dropped with a WARNING

#### Scenario: Tool-call failure source
- **WHEN** the `tool_call_failures` source is enabled
- **THEN** it queries recent failed MCP tool calls within the lookback window and produces `QaFinding` objects with fingerprints derived from the tool name and call site
- **AND** all filtering is tool-based (SQL queries); no LLM invocation

#### Scenario: Infra state source
- **WHEN** the `infra_state` source is enabled
- **THEN** it checks four infra health signals every patrol tick, ignoring `lookback_minutes` (each check is a point-in-time liveness/staleness comparison against a fixed cadence, not a rolling scan window):
  - **connector-offline**: queries `public.v_qa_connector_state` (sanctioned cross-schema view per RFC 0010, over `switchboard.connector_registry`) and flags a connector whose derived liveness (`butlers.core.liveness.derive_liveness`) is `"offline"` — excluding a `state = 'paused'` connector (a deliberate operator action), excluding a connector within a 15-minute grace window of its first registration, and excluding a storage-only row that has a checkpoint cursor but has never acquired either a process instance or a heartbeat
  - **heartbeat-stale before fleet-condition handoff**: with `BUTLERS_RECEIVER_DERIVED_ROUTE_CUTOVER=1`, queries the QA-only `public.v_qa_butler_receiver_state` view from `sw_037` and flags an unavailable, stale, or non-current-boot receiver observation; an explicit failed first probe is not hidden by the never-seen grace window, and administrative policy alone is not a liveness failure. With the flag off, queries `public.v_qa_butler_heartbeat` from `sw_024` and applies the legacy `last_seen_at` TTL and `quarantined_at` checks. A receiver observation or legacy timestamp beyond the five-minute future clock-skew tolerance cannot assert health.
  - **backup-stale**: reads `BUTLERS_BACKUP_DIR`; an unset env var is a legitimate absence (no finding); a configured-but-unreachable directory, one with no backup on record, or a most-recent backup older than 36 hours produces a finding
  - **external-deadman-stale**: reads the last successful external-deadman ping recorded by `butlers.jobs.external_deadman` in `public.audit_log`; an unconfigured `EXTERNAL_DEADMAN_URL` is a legitimate absence (no finding); a last-success timestamp older than 3x the configured ping interval (or no successful ping ever) produces a finding
- **AND** each check's finding fingerprint is derived from a stable identity (connector type/identity, butler name, or a fixed backup/deadman call-site) via the same sanitize-then-hash pattern the other sources use, so the fingerprint stays stable across patrol ticks even though the human-readable `event_summary` carries the live timestamp/age
- **AND** all filtering is tool-based (SQL queries, env var reads, filesystem stat calls); no LLM invocation
- **AND** a health-check query against both views runs before row processing; a failure (e.g. a revoked grant) is raised and logged rather than silently returning an empty (falsely clean) result

### Requirement: Patrol Loop Lifecycle
The QA Staffer SHALL run a scheduler-driven patrol loop that executes at a configurable interval (default: 10 minutes). Each patrol cycle is a discrete unit of work with its own DB record. Reactive findings SHALL arrive through the Switchboard service-plane relay, not the deleted generic `route` tool.

Source: RFC 0038 D7
Scope: v1-mandatory

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
- **THEN** the finding is relayed to the QA staffer through the Switchboard service-plane relay calling the QA staffer's `report_finding` tool — a deterministic butler-to-staffer forward that starts no goal
- **AND** the finding is buffered in the `butler_reports` source's in-memory queue
- **AND** it is picked up on the next patrol tick
- **AND** if the finding's severity is 0 (critical), an immediate mini-patrol is triggered without waiting for the next timer tick
- **AND** that mini-patrol is a full patrol across every enabled source, recorded with `origin='scheduled'`, and counts as scheduled patrol coverage exactly as a timer-driven patrol does (a full patrol by the QA module, timer or reactive, qualifies)
