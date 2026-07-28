## MODIFIED Requirements

### Requirement: Pluggable Discovery Source Architecture

The QA Staffer SHALL support a pluggable `DiscoverySource` protocol for error
detection across multiple channels. Each source produces `QaFinding` objects
with computed fingerprints. Sources are registered at startup and polled during
each patrol cycle. The ordinary `report_finding` relay remains volatile. The
Switchboard router SHALL load a dedicated bearer service credential through the
existing credential store; a QA FastMCP auth provider SHALL validate it and
expose request/access-token context whose subject/client identifies that router
and whose audience identifies the QA staffer. QA SHALL derive authorization from
that context, never from caller-supplied `source_butler` or dashboard identity
arguments. Only that validated principal with the complete dashboard identity
uses the durable dashboard inbox defined by this change.

ID: REQ-staffer-qa-001
Source: staffer-qa § Pluggable Discovery Source Architecture; RFC 0015 § V1 sources; dashboard-terminal-action-recovery REQ-dashboard-terminal-action-recovery-002; design.md Decision 4
Scope: v1-mandatory

#### Scenario: DiscoverySource protocol definition

- **WHEN** a new discovery source is implemented
- **THEN** it implements the `DiscoverySource` protocol with a `name` string
  property and `async discover(lookback_minutes: int) -> list[QaFinding]`
  method
- **AND** the protocol requires no LLM invocation; all sources use tool-based
  filtering such as regex, SQL queries, and file parsing

#### Scenario: Source registration at startup

- **WHEN** the QA Staffer daemon starts
- **THEN** it registers all enabled discovery sources from
  `[modules.qa].enabled_sources` configuration
- **AND** default enabled sources remain `log_scanner`, `session_records`,
  `butler_reports`, `tool_call_failures`, and `infra_state`
- **AND** disabled sources are logged at INFO and skipped during patrol
- **AND** adapter diagnostics useful only through structured session records are
  suppressed by `log_scanner` only when `session_records` registered successfully

#### Scenario: QA module MCP tool registration

- **WHEN** the QA module's `register_tools()` is called
- **THEN** it registers `report_finding` (receives findings from butler relay via
  Switchboard route), `force_patrol` (triggers immediate patrol),
  `get_qa_status` (returns QA staffer operational summary), and the
  authenticated internal `get_dashboard_report_receipt`
- **AND** `report_finding` remains the tool called by butlers' self-healing
  modules via `switchboard_client.call_tool("route", {"target_butler": "qa",
  "tool_name": "report_finding", "args": ...})`
- **AND** `report_finding` accepts `fingerprint` (a hint; QA recomputes the
  canonical fingerprint via `compute_fingerprint_from_report` and logs a debug
  mismatch warning), `exception_type`, `call_site`, `severity` (0-4, clamped
  with WARNING when out of range; authoritative canonical scoring overrides
  caller intent for critical/high errors), `event_summary`, optional `context`,
  `source_butler`, and optional `trigger_source` propagated as
  `source_session_trigger_source` for QA self-recursion suppression
- **AND** a non-dashboard `report_finding` call queues the finding with canonical
  fingerprint/severity in the `butler_reports` source buffer and returns
  `{"accepted": true}` synchronously
- **AND** an authenticated internal Switchboard caller MAY additionally supply
  all of `terminal_action_id` (UUID), `terminal_effect_id` (UUID), and
  `terminal_effect_idempotency_key` (opaque stable string) to select the durable
  dashboard mode
- **AND** `tool_metadata()` declares `context` and `event_summary` sensitive on
  `report_finding` because they may contain agent reasoning about user-related
  errors

#### Scenario: Reactive finding buffer is volatile for ordinary reports

- **WHEN** the QA staffer daemon restarts
- **THEN** ordinary buffered findings from `report_finding` not yet processed by
  a patrol cycle are lost
- **AND** this remains acceptable because `session_records` will rediscover the
  failures from the DB and `log_scanner` will find them in logs
- **AND** no duplicate investigation is created because the triage layer
  deduplicates by fingerprint
- **AND** a dashboard-mode durable inbox receipt SHALL not be lost, reclassified
  as a volatile report, or inferred from the volatile buffer

#### Scenario: Adding a new discovery source

- **WHEN** a developer wants to add a new error-detection channel
- **THEN** they implement `DiscoverySource` in `src/butlers/core/qa/sources/`
- **AND** add the source name to the enabled-sources configuration
- **AND** no changes to triage, dispatch, or dashboard layers are required

#### Scenario: Source failure is isolated

- **WHEN** a discovery source raises an exception during `discover()`
- **THEN** the error is logged at ERROR with the source name
- **AND** the patrol cycle continues with findings from other sources
- **AND** the patrol record includes the failed source in `error_detail`

### Requirement: V1 Discovery Sources

The QA Staffer SHALL ship with five discovery sources in v1.

ID: REQ-staffer-qa-002
Source: staffer-qa § V1 Discovery Sources; RFC 0015 § V1 sources; dashboard-terminal-action-recovery REQ-dashboard-terminal-action-recovery-002; design.md Decision 4
Scope: v1-mandatory

#### Scenario: Log scanner source

- **WHEN** the `log_scanner` source is enabled
- **THEN** it reads structured JSON log files from `logs/butlers/`,
  `logs/connectors/`, and `logs/uvicorn/` for ERROR/WARNING entries within the
  lookback window
- **AND** all filtering is tool-based through JSON parsing, regex, and severity
  checks; no LLM invocation occurs

#### Scenario: Session record source

- **WHEN** the `session_records` source is enabled
- **THEN** it queries the read-only SQL view `public.v_qa_recent_failures`
  (sanctioned cross-schema exception per RFC 0010) for recent failures within
  the lookback window
- **AND** the view is a UNION across butler `sessions` tables filtered to
  error/timeout/crash statuses
- **AND** the view is structurally read-only, date-filtered, and created by an
  auditable migration with explicit per-schema GRANT
- **AND** it extracts exception type, traceback, call site, and butler name from
  the session record
- **AND** it anonymizes event summaries before storage and computes fingerprints
  with the same algorithm as log-scanner findings
- **AND** it excludes expected/controlled rows: short (`<= 60s`) Switchboard
  classification timeouts (`trigger_source = "classification"` or historical
  `"tick"`), synthetic `orphaned: daemon restart` rows, and intentional
  spawner-guardrail stops whose error contains `token_budget_exceeded`,
  `tool_call_budget_exceeded`, or `degenerate_tool_loop`; longer/non-
  classification timeouts and other failures remain actionable

#### Scenario: Butler report source (reactive relay via Switchboard)

- **WHEN** the `butler_reports` source is enabled
- **THEN** ordinary butlers relay `report_error` findings to QA via
  Switchboard's `route()` MCP tool, calling QA's `report_finding` directly
  rather than session-spawning `route.execute`, preserving MCP-only
  inter-butler communication
- **AND** the QA `report_finding` handler queues ordinary reports in an
  in-memory buffer and the patrol drains that buffer alongside batch sources
- **AND** if the ordinary buffer exceeds `max_reactive_buffer` (default 50),
  oldest entries are dropped with a WARNING
- **AND** dashboard-mode reports use the durable inbox lifecycle rather than the
  ordinary volatile buffer and are claimed by patrol only under its fenced
  lifecycle

#### Scenario: Tool-call failure source

- **WHEN** the `tool_call_failures` source is enabled
- **THEN** it queries recent failed MCP tool calls within the lookback window and
  produces findings with fingerprints derived from tool name and call site
- **AND** all filtering is tool-based SQL; no LLM invocation occurs

#### Scenario: Infra state source

- **WHEN** the `infra_state` source is enabled
- **THEN** it checks four infra health signals every patrol tick, ignoring
  `lookback_minutes` because each check is point-in-time liveness/staleness:
  - **connector-offline**: query `public.v_qa_connector_state` over
    `switchboard.connector_registry` and flag derived liveness `offline`, while
    excluding `paused` connectors, the first 15 minutes after registration, and
    storage-only rows with a checkpoint but no process instance or heartbeat
  - **heartbeat-stale**: query `public.v_qa_butler_heartbeat` over
    `switchboard.butler_registry` and flag elapsed `last_seen_at` plus its own
    `liveness_ttl_seconds` or `quarantined_at IS NOT NULL`, recomputing rather
    than trusting lazy `eligibility_state`; a timestamp more than five minutes
    in the future is untrustworthy and stale
  - **backup-stale**: read `BUTLERS_BACKUP_DIR`; an unset value is legitimate,
    while an unreachable configured directory, no backup, or a most-recent
    backup older than 36 hours is a finding
  - **external-deadman-stale**: read the last successful external-deadman ping
    in `public.audit_log`; an unconfigured `EXTERNAL_DEADMAN_URL` is legitimate,
    while no successful ping or one older than three configured intervals is a
    finding
- **AND** each finding fingerprint derives from stable connector/butler identity
  or fixed backup/deadman call site through the same sanitize-then-hash pattern
- **AND** all filtering is tool-based SQL/environment/filesystem work; no LLM
  invocation occurs
- **AND** a health-check query against both views runs before row processing and
  a failure is raised/logged rather than silently returned as clean

## ADDED Requirements

### Requirement: Dashboard Report Durable Inbox Lifecycle

QA SHALL permit the validated Switchboard-router service principal defined in
REQ-staffer-qa-001 to call `report_finding` in dashboard mode only when it
supplies all of
`terminal_action_id` (UUID),
`terminal_effect_id` (UUID), and `terminal_effect_idempotency_key` (opaque
stable string) in addition to ordinary canonical finding arguments. QA SHALL
reject a partial dashboard identity, a caller other than the internal
Switchboard relay, a duplicate identity with a different idempotency key or
canonical payload hash, or dashboard delivery while `butler_reports` is
disabled. A source-disabled rejection SHALL create neither a receipt nor a
buffered finding and SHALL be a proven terminal delivery failure for the calling
Switchboard effect.
In dashboard mode, QA SHALL durably upsert one dashboard-report inbox record
before it returns `accepted`. The inbox uniqueness boundary SHALL be
`(terminal_action_id, terminal_effect_id)` and SHALL retain the stable
idempotency key, canonical payload hash, creation timestamp, current lifecycle
state, and any canonical finding linkage. The acceptance receipt SHALL be
`{"accepted": true, "delivery": "dashboard_durable", "receipt":
{"terminal_action_id": "...", "terminal_effect_id": "...",
"inbox_state": "pending", "created_at": "..."}}`; it SHALL NOT expose or
invent a `finding_id` before a patrol has durably acknowledged one.
When `butler_reports` is enabled, its patrol path SHALL drain durable
dashboard-report inbox records as well as the ordinary volatile relay buffer. A
durable inbox record SHALL transition only through `pending`, fenced `claimed`,
and `acknowledged`. The patrol SHALL claim `pending` with a generation/lease
fence; it MAY reclaim an expired `claimed` record only by advancing that fence.
It SHALL create or resolve exactly one patrol-owned canonical finding and then
conditionally transition the same claimed generation to `acknowledged`, storing
the immutable finding linkage. Canonical storage SHALL enforce a unique
dashboard-inbox-to-finding mapping. If the source is disabled after a receipt
exists, the inbox SHALL remain inspectable but no patrol claim SHALL occur until
the source is enabled; it SHALL never become a volatile substitute or proof of
absence.

ID: REQ-staffer-qa-003
Source: staffer-qa § Pluggable Discovery Source Architecture; staffer-qa § V1 Discovery Sources; RFC 0015 § V1 sources; dashboard-terminal-action-recovery REQ-dashboard-terminal-action-recovery-002; design.md Decision 4
Scope: v1-mandatory

#### Scenario: Dashboard report is durably accepted

- **WHEN** an authenticated Switchboard relay calls `report_finding` with all
  three dashboard identity fields
- **THEN** QA SHALL durably upsert one dashboard-report inbox record for that
  action/effect identity before it returns the `dashboard_durable` acceptance
  receipt with `inbox_state: "pending"`
- **AND** it SHALL not return a `finding_id` until a patrol-owned finding is
  durably acknowledged

#### Scenario: Dashboard inbox is claimed after a restart

- **WHEN** QA restarts after accepting a dashboard report whose inbox remains
  `pending` or whose `claimed` lease has expired
- **THEN** the enabled `butler_reports` source SHALL fence a claim and create or
  resolve exactly one patrol-owned canonical finding
- **AND** it SHALL record that finding linkage only by conditionally advancing
  the claimed inbox record to `acknowledged`

#### Scenario: Dashboard inbox source is disabled after acceptance

- **WHEN** a dashboard-report inbox receipt exists but `butler_reports` is
  disabled before patrol acknowledgement
- **THEN** the receipt lookup SHALL continue to report the durable inbox record
  and its `pending` or `claimed` lifecycle state
- **AND** QA SHALL not create a volatile substitute, a second receipt, or a
  canonical finding until the source is enabled and a fenced patrol claim runs

#### Scenario: Caller supplies invalid identity or authority

- **WHEN** a caller supplies only part of the dashboard identity, a mismatched
  duplicate idempotency key or canonical payload hash, is anonymous, presents
  the wrong subject or audience, spoofs `source_butler` without the validated
  Switchboard principal, or calls while `butler_reports` is disabled
- **THEN** QA SHALL reject the request without creating a receipt, finding, or
  buffered report

### Requirement: Dashboard Report Receipt Lookup

The QA staffer SHALL register an authenticated internal MCP tool named
`get_dashboard_report_receipt`. Only the validated Switchboard-router service
principal may invoke it with
`{terminal_action_id: UUID, terminal_effect_id: UUID}`. Direct callers SHALL NOT
enumerate receipts by supplying tool arguments alone. It SHALL return
exactly either `{"status": "found", "receipt": {"terminal_action_id": "...",
"terminal_effect_id": "...", "inbox_state":
"pending|claimed|acknowledged", "created_at": "...", "finding_id": "..."}}`,
where `finding_id` is present only for `acknowledged`, `{"status":
"not_found"}`, or `{"status": "unavailable"}`. The result SHALL query the
durable inbox store and SHALL NOT infer a receipt from the volatile
`butler_reports` buffer. `not_found` SHALL mean a successful durable lookup
proved that no record exists for that exact action/effect identity; only then
may Switchboard repeat the same-key, same-payload-hash delivery. `unavailable`
shall mean the lookup itself could not establish that proof (for example, the
durable store is unavailable); it is not proof of absence. Switchboard SHALL
retain that effect pending for bounded lookup and mark it ambiguous without
another delivery call if proof remains unavailable.

ID: REQ-staffer-qa-004
Source: staffer-qa § Pluggable Discovery Source Architecture; RFC 0015 § V1 sources; dashboard-terminal-action-recovery REQ-dashboard-terminal-action-recovery-003; design.md Decision 4
Scope: v1-mandatory

#### Scenario: Switchboard reconciles a possibly delivered report

- **WHEN** the terminal-action reconciler needs to determine whether a
  dashboard QA-report effect completed after a crash
- **THEN** it SHALL call `get_dashboard_report_receipt` with the stable action
  and child-effect identities
- **AND** QA SHALL return `found`, `not_found`, or `unavailable` exactly as the
  durable receipt lookup establishes

#### Scenario: Acknowledged finding is looked up

- **WHEN** a fenced patrol claim has created and linked the one canonical
  finding for a dashboard inbox record
- **THEN** the lookup SHALL return `found` with `inbox_state: "acknowledged"`
  and its safe `finding_id`
- **AND** a pending or claimed record SHALL return `found` without a
  `finding_id`, never a fabricated acknowledgement

#### Scenario: Direct caller probes a receipt identity

- **WHEN** an anonymous caller, wrong-subject or wrong-audience service, or
  source-spoofing caller invokes `get_dashboard_report_receipt`
- **THEN** QA SHALL reject the request before querying or disclosing receipt data
