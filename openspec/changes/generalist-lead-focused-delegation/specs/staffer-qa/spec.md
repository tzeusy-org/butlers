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

The staffer-goal admission obligations in this requirement SHALL apply only in
`ISOLATED_CANDIDATE` and `SWITCHED_RELEASE`. Candidate admission SHALL be invoked
only by the closed trusted test harness, with no live dashboard wiring. Current
legacy and `KEY_ABSENT_STAGE` SHALL retain their existing deterministic QA
triage and SHALL NOT admit dashboard reports as staffer goals. The durable
inbox/receipt guarantees remain distinct from goal activation: their accepted
records SHALL survive the eventual switch and SHALL never imply that a goal was
admitted. Live staffer admission requires the actual owner-authorized switch;
this specification does not perform or authorize it.

The dashboard-to-triage work kind SHALL be `qa_triage_v1`, selected by trusted
configuration, not a report field. Its exact domain-capability ceiling SHALL be
`capabilities: []` (the explicit empty tuple of `Grant` values) and its resource
grant map SHALL be empty. An explicitly declared empty ceiling is not an absent
contract, a wildcard, or unrestricted resource access. The complete persisted,
anonymized canonical finding is supplied as exposed starting evidence; consuming
that evidence requires no model-callable inbox/finding reader. Existing
deterministic discovery, canonical fingerprint/severity scoring, ordered
deduplication and investigation admission SHALL remain infrastructure operations
under their existing owners. The model may return a bounded triage judgment and
uncertainty over the supplied finding, but its output SHALL NOT overwrite those
authorities, authenticate a source, mutate a receipt, grant capabilities, launch
an executor, approve communication, or prove that any effect occurred. Missing
or insufficient diagnostic evidence SHALL remain explicitly unknown; QA SHALL
NOT invent a read tool, substitute a broader profile or use a shell/network
capability to fill that gap. Additional collection/investigation stays with the
existing discovery sources and D18 executor contract, not this goal.

The finite descriptor disposition SHALL be exactly: `qa__report_finding`
(`record`, sensitive/untrusted, no resource selectors), `qa__force_patrol`
(`administer`, sensitive/untrusted, no resource selectors), and
`qa__get_qa_status` (`read`, sensitive/untrusted, no resource selectors) are all
ungranted because their registered descriptors are non-presentable control
surfaces. `get_dashboard_report_receipt` remains an infrastructure-only future
lookup, not a fourth grant or an invented current catalog entry. All other
catalog capabilities, notifications, domain reads/writes, `transact`,
`actuate`, `administer` and executor/shell/network operations SHALL be absent
from this ceiling. There are therefore zero granted descriptor/resource rows;
no effect or communication is justified by the report. The two owner rules
remain universal: transact is disabled for every goal, and communication to
anyone other than the owner requires per-event approval or an owner-created
standing rule. This contract grants no communication at all.

Before a new admission, the trusted staffer-source adapter SHALL construct
`StafferContract(staffer="qa", owner_domain="qa", version=1,
capabilities=(), catalog_digest=<verified current fleet digest>,
digest=<verified contract digest>)` from the git-declared work-kind definition.
The contract digest SHALL bind that definition, its version, the explicit empty
ceiling and the exact verified catalog digest using the existing canonical JSON
SHA-256 digest convention. The adapter SHALL verify the declared bytes and
select the profile from trusted configuration; a nonempty digest string supplied
by a caller is not verification. It SHALL use the existing complete published
catalog reader even for the empty ceiling; absent, expired, incomplete,
mismatched or unverifiable catalog/contract/schema/descriptor evidence SHALL
refuse new admission, never fall back to all tools or a cached unrelated
contract. Any publication change requires a newly verified binding before new
admission, with the ceiling still empty; descriptor/schema/digest changes SHALL
NOT add capabilities automatically. Existing admitted goals retain their
immutable source/contract/decision lineage; a retry cannot replace them with a
newly widened goal. QA SHALL always start exposed (`origin="staffer"`), and
exposure SHALL never be reduced by trusted storage, an owner filing the report,
a source name or a model's answer. D8 goal-control tools are not additional
domain grants: `context.search` has no permitted data domain here and child
delegation with an empty capability request is refused by D9. Only authorized
policy/guidance and the supplied exposed evidence may be used; no implicit
context, domain or executor capability is created. The existing D10 finite
staffer budget, per-invocation limits and deadline SHALL be reserved by admission
and enforced across recovery/escalation; unavailable limits refuse, and no new
budget default or unmetered model use is defined here.

After the fenced inbox claim has durably created/resolved its canonical finding
and acknowledged the immutable linkage, the QA-owned adapter SHALL derive
`SourceRef(kind="staffer", id="qa:triage:<canonical-finding-UUID>")` from that
stored finding, never from payload identities. The trusted source partition
SHALL identify QA triage; complete action/effect/idempotency/payload identity,
accepted relay provenance, acknowledged finding linkage and the winning stored
claim generation SHALL be verified before source construction. Payload text,
`source_butler`, `trigger_source`, a supplied staffer/contract/grant or an
unverified dashboard UUID SHALL grant no authority. Finding summary/context are
exposed data, with existing anonymization/sensitivity rules, never a trusted
instruction. No new cross-domain SQL access is authorized: .17's adapter SHALL
use the released service/owner evidence boundary supplied by the relay and goal
admission owners; if unavailable, it refuses instead of inventing an endpoint,
credential or direct foreign-schema reader.

The existing goal admission/ledger SHALL be the only logical admission journal.
The source and attempt locks, existing decision lookup and immutable attempt
bindings SHALL coalesce concurrent/restarted submissions for this one stable
finding source into at most one admitted logical goal. An inbox with no
acknowledged finding SHALL not admit; a stale claimant SHALL not acknowledge or
submit authority from a superseded generation. A crash before finding/ack
commit leaves the inbox pending/reclaimable; a crash after commit but before
admission leaves that same stored source recoverable. A crash after goal commit
but before QA records/observes the result SHALL reconcile the same source and
attempt through the existing durable admission decision, never invent a new
finding, source id or second ledger. The inbox/finding handoff may retain its
existing linkage/pending-intent metadata, not a competing goal ledger.
Commit/lookup uncertainty SHALL remain unavailable with bounded reconciliation,
never proof of absence or a second delivery/admission. A durably refused attempt
SHALL replay its recorded refusal; after its prerequisite is repaired, only the
trusted adapter may retry with a fresh attempt and the same immutable source,
under existing source deduplication. A prior admitted source always returns its
existing goal, including across contract drift, with no new work or reset.

When `butler_reports` is disabled, new dashboard deliveries and new claims or
admissions SHALL refuse without effects. Already accepted inbox/finding evidence
and authenticated receipt lookup SHALL remain readable, and already durable
admission decisions SHALL remain reconcilable; none is volatile, absent or a
new permission to run. Re-enabling resumes only the same fenced stored work.
Receipt acceptance proves durable inbox custody, `acknowledged` proves the one
finding link, and an admitted staffer decision proves only that logical goal;
none implies executor launch, fix, PR, delivery, triage completion or activation.
The terminal-action reconciler retains its existing five-attempt/15-minute bound
for unresolved delivery/lookup and explicit ambiguity, not unbounded report or
goal retries. Only an authenticated durable `not_found` for the exact effect
permits same-key/same-payload redelivery when the source is enabled. `found`
pending/claimed is positive retained evidence, and `unavailable` never is absence.

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

#### Scenario: A declared QA triage contract grants no domain tools

- **WHEN** the trusted adapter verifies `qa_triage_v1` and the complete current catalog
- **THEN** it SHALL submit the explicit empty ceiling and resource map, start QA exposed and supply only the stored canonical finding as evidence
- **AND** the three QA control descriptors, receipt lookup, all domain effects, shell/network and D18 executors SHALL remain outside the goal grant
- **AND** insufficient diagnostic evidence SHALL yield an explicit unknown judgment, not invented authority or a broader read

#### Scenario: QA contract or catalog evidence cannot be verified

- **WHEN** the contract is missing, stale, wrong-version, nonempty contrary to this profile, supplied by report text, or mismatched to an incomplete/expired/current catalog publication
- **THEN** new admission SHALL refuse under the existing source-evidence/catalog checks and SHALL create no goal or effect
- **AND** the durable report/finding evidence SHALL remain intact for authenticated inspection and a same-source trusted retry after repair, without broad fallback

#### Scenario: A report attempts to choose staffer authority

- **WHEN** report fields claim owner origin, an alternate staffer, source id, contract digest, grants, unexposed status or a different finding link
- **THEN** those fields SHALL not establish authority and a missing or mismatched trusted binding SHALL refuse before admission
- **AND** canonical scoring/fingerprint and exposed content handling SHALL remain authoritative

#### Scenario: Finding acknowledgement and logical admission race with restart

- **WHEN** concurrent claimants or retries cross finding/ack or goal-admission commit boundaries
- **THEN** only the winning inbox generation SHALL establish the immutable finding link and its one `qa:triage:<canonical-finding-UUID>` source
- **AND** the existing admission source/attempt locks and decision bindings SHALL return at most the one logical admitted goal, including a crash after commit but before the caller observes it
- **AND** an unknown commit outcome SHALL reconcile without a second ledger, source identity, delivery or fabricated success

#### Scenario: Disabled source retains accepted evidence without new admission

- **WHEN** the source is disabled after inbox acceptance or finding acknowledgement
- **THEN** authenticated receipt/decision inspection SHALL retain that evidence while new delivery, claim and admission refuse
- **AND** re-enabling SHALL reuse only the same stored source and fences, and an existing admitted goal SHALL not be recreated under a newer contract

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

#### Scenario: Lookup unavailability is not missing work

- **WHEN** the exact authenticated receipt or admission lookup cannot prove its durable result
- **THEN** the caller SHALL retain unavailable/unknown status and reconcile only within the existing finite bound
- **AND** it SHALL not redeliver, create another source/goal, expose sensitive evidence or turn an accepted inbox receipt into a fabricated finding or completed triage result
