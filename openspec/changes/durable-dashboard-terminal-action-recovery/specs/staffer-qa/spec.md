## MODIFIED Requirements

### Requirement: Pluggable Discovery Source Architecture

The QA Staffer SHALL support a pluggable `DiscoverySource` protocol for error
detection across multiple channels. Each source produces `QaFinding` objects
with computed fingerprints. Sources are registered at startup and polled during
each patrol cycle. The ordinary non-dashboard `report_finding` relay remains volatile. In `ISOLATED_CANDIDATE` and `SWITCHED_RELEASE`, reports SHALL use the named QA service-plane relay owned
by `bu-2c1g7v`, on the service listener only, with its fixed schema, one QA
target and per-endpoint calling-daemon allowlist. The relay SHALL start no goal
or reasoning session. QA SHALL validate the transport-authenticated
Switchboard-router subject/client and QA audience before inbox writes or
receipt lookup, using that relay owner's released authentication interface and
existing credential boundary. It SHALL NOT introduce a second credential or
choose a parallel bearer/auth mechanism here. QA SHALL derive authorization
from verified request/access-token context, never `source_butler`,
`trigger_source`, dashboard identity, model output or a claimed contract in the
payload. Anonymous, wrong-subject/audience, session-listener and unlisted callers
SHALL be refused before data access. Only the validated relay with the complete
canonical dashboard identity uses the durable inbox. The underlying service
credential provisioning remains separately authorized; this contract activates
none. Profile selection SHALL be trusted immutable server configuration as in
REQ-butler-switchboard-006, never report/model input. Before the actual switch,
current legacy deployment and `KEY_ABSENT_STAGE` SHALL retain the existing
route-based relay. `ISOLATED_CANDIDATE` SHALL exercise the successor only from
the closed trusted test harness, with no live dashboard wiring. Only the
owner-authorized `SWITCHED_RELEASE` SHALL wire live dashboard reports to the
successor and delete the legacy route; this contract authorizes no activation.

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

The following legacy registration and binding clauses SHALL apply only before
the actual switch, including `KEY_ABSENT_STAGE`; they SHALL NOT apply in
`ISOLATED_CANDIDATE` or `SWITCHED_RELEASE`:

- **THEN** it registers: `report_finding` (receives findings from butler relay via Switchboard route), `force_patrol` (triggers immediate patrol), `get_qa_status` (returns QA staffer operational summary)
- **AND** `report_finding` is the tool called by butlers' self-healing modules via `switchboard_client.call_tool("route", {"target_butler": "qa", "tool_name": "report_finding", "args": ...})`

In `ISOLATED_CANDIDATE` and `SWITCHED_RELEASE`, the following successor clauses
SHALL apply instead: QA registers `report_finding` (receives findings from the
named relay), `force_patrol` and `get_qa_status` with their existing control
semantics, and the durable implementation additionally registers the
authenticated internal `get_dashboard_report_receipt` under its released
service interface. Deterministic self-healing callers reach `report_finding`
only through the named authenticated QA service-plane relay, with no generic
`route` or `route.execute` fallback. Candidate calls are closed test-harness
calls only; live wiring and route deletion require the actual owner-authorized
switch. All argument, sensitivity and ordinary-buffer guarantees below apply in
both phases, while dashboard durable mode additionally requires the released
trusted service interface and complete validated identity.

- **AND** `report_finding` and `get_dashboard_report_receipt` are service-only,
  never LLM-presentable or reachable from the session listener; `force_patrol`
  and `get_qa_status` retain their existing non-presentable control descriptors
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

#### Scenario: Reactive finding buffer is volatile
- **WHEN** the QA staffer daemon restarts with ordinary non-dashboard reports in its volatile buffer
- **THEN** any such buffered findings from `report_finding` that were not yet processed by a patrol cycle are lost
- **AND** this is acceptable because: (a) the `session_records` source will rediscover these failures from the DB on the next patrol, and (b) the `log_scanner` source will find them in logs
- **AND** no duplicate investigation is created because the triage layer deduplicates by fingerprint
### Requirement: V1 Discovery Sources

The QA Staffer SHALL ship with five discovery sources in v1. The retained
session/classification source clauses below describe the legacy profile only.
At goal-runtime cutover the existing generalist `V1 Discovery Sources` clauses
supply the goal-invocation failure conversion and controlled-outcome exclusions;
that conversion remains with its existing owner. The only additional source
behavior specified here is the dashboard durable-inbox exception. Existing
fleet-condition handoff stages, non-dashboard sources and their policies remain
unchanged; this contract does not activate any of them.

ID: REQ-staffer-qa-002
Source: staffer-qa § V1 Discovery Sources; RFC 0015 § V1 sources; dashboard-terminal-action-recovery REQ-dashboard-terminal-action-recovery-002; design.md Decision 4
Scope: v1-mandatory

#### Scenario: Log scanner source

- **WHEN** the `log_scanner` source is enabled
- **THEN** it reads structured JSON log files from `logs/butlers/`, `logs/connectors/`, `logs/uvicorn/` for ERROR/WARNING entries within the lookback window
- **AND** all filtering is tool-based (JSON parsing, regex, severity checks) — no LLM invocation

#### Scenario: Session record source

- **WHEN** the `session_records` source is enabled
- **THEN** it queries a read-only SQL view `public.v_qa_recent_failures` (sanctioned cross-schema exception per RFC 0010 pattern) for recent session failures within the lookback window
- **AND** the view is a UNION across butler `sessions` tables filtered to
  error/timeout/crash statuses
- **AND** the view is read-only (structurally enforced — no INSERT/UPDATE/DELETE), date-filtered, and created via auditable migration with explicit per-schema GRANT
- **AND** extracts exception type, traceback, call site, and butler name from the session record
- **AND** event summaries extracted from session records are passed through `anonymize()` before storage (session error messages may contain user data)
- **AND** computes fingerprints using the same algorithm as log scanner findings
- **AND** excludes rows that represent expected or controlled outcomes rather than product/runtime failures:
  - short (`<= 60s`) Switchboard mini-model classification timeout rows with `trigger_source = "classification"` (or the historical `"tick"`, renamed in bu-qvnce.12) as expected routing fallback telemetry; longer or non-classification Switchboard timeouts remain actionable
  - synthetic startup-recovery rows (`orphaned: daemon restart`)
  - spawner guardrail terminations whose error text contains an intentional-stop marker (`token_budget_exceeded`, `tool_call_budget_exceeded`, `degenerate_tool_loop`) — the same markers the failover classifier treats as failover-ineligible; these remain visible in session history and the token ledger but do not spawn autonomous code-fix investigations

#### Scenario: Butler report source (reactive relay via Switchboard)

- **WHEN** the `butler_reports` source is enabled

The following legacy relay clause SHALL apply only before the actual switch,
including `KEY_ABSENT_STAGE`; it SHALL NOT apply in `ISOLATED_CANDIDATE` or
`SWITCHED_RELEASE`:

- **THEN** butlers relay `report_error` findings to the QA staffer via Switchboard's `route()` MCP tool, calling the QA staffer's `report_finding` tool directly (tool-to-tool routing, not session-spawning `route.execute`) — preserving MCP-only inter-butler communication (non-negotiable rule #3)

In `ISOLATED_CANDIDATE` and `SWITCHED_RELEASE`, the successor relay SHALL instead
forward deterministic `report_error` findings through the named authenticated
Switchboard QA service-plane relay to `report_finding`, starting no goal or
session and preserving MCP-only communication. Generic `route` and
`route.execute` SHALL NOT be used in either successor profile. Candidate calls
remain closed trusted test-harness calls; actual live wiring requires the
owner-authorized switch. The ordinary-buffer and dashboard-exception clauses
below apply to their respective report modes in each phase.

- **AND** the QA staffer's `report_finding` MCP tool handler queues received findings in an in-memory buffer
- **AND** the patrol cycle drains the buffer and includes those findings alongside batch sources
- **AND** if the buffer exceeds `max_reactive_buffer` (default: 50), oldest entries are dropped with a WARNING
- **AND** dashboard-mode reports use the durable inbox lifecycle rather than the
  ordinary volatile buffer and are claimed by patrol only under its fenced
  lifecycle

#### Scenario: Tool-call failure source

- **WHEN** the `tool_call_failures` source is enabled
- **THEN** it queries recent failed MCP tool calls within the lookback window and produces `QaFinding` objects with fingerprints derived from the tool name and call site
- **AND** all filtering is tool-based (SQL queries); no LLM invocation

#### Scenario: Infra state source

- **WHEN** the `infra_state` source is enabled
- **THEN** it checks four infra health signals every patrol tick (butler liveness is exactly one of the two stages below), ignoring `lookback_minutes` (each check is a point-in-time liveness/staleness comparison against a fixed cadence, not a rolling scan window):
  - **connector-offline**: queries `public.v_qa_connector_state` (sanctioned cross-schema view per RFC 0010, over `switchboard.connector_registry`) and flags a connector whose derived liveness (`butlers.core.liveness.derive_liveness`) is `"offline"` — excluding a `state = 'paused'` connector (a deliberate operator action), excluding a connector within a 15-minute grace window of its first registration, and excluding a storage-only row that has a checkpoint cursor but has never acquired either a process instance or a heartbeat
  - **heartbeat-stale before fleet-condition handoff**: with `BUTLERS_RECEIVER_DERIVED_ROUTE_CUTOVER=1`, queries the QA-only `public.v_qa_butler_receiver_state` view from `sw_037` and flags an unavailable, stale, or non-current-boot receiver observation; an explicit failed first probe is not hidden by the never-seen grace window, and administrative policy alone is not a liveness failure. With the flag off, queries `public.v_qa_butler_heartbeat` from `sw_024` and applies the legacy `last_seen_at` TTL and `quarantined_at` checks. A receiver observation or legacy timestamp beyond the five-minute future clock-skew tolerance cannot assert health.
  - **fleet-control after fleet-condition handoff** (`BUTLERS_FLEET_CONDITION_HANDOFF=1`): reads the independently produced active `control_plane_fleet` condition in `public.infra_conditions` and its receiver-observed per-butler impact, recording one condition-linked finding for the affected fleet rather than treating `last_seen_at`, `quarantined_at`, or a lazily updated `eligibility_state` as independent liveness authority; observer failure and owner policy remain distinct evidence
  - **backup-stale**: reads `BUTLERS_BACKUP_DIR`; an unset env var is a legitimate absence (no finding); a configured-but-unreachable directory, one with no backup on record, or a most-recent backup older than 36 hours produces a finding
  - **external-deadman-stale**: reads the last successful external-deadman ping recorded by `butlers.jobs.external_deadman` in `public.audit_log`; an unconfigured `EXTERNAL_DEADMAN_URL` is a legitimate absence (no finding); a last-success timestamp older than 3x the configured ping interval (or no successful ping ever) produces a finding
- **AND** heartbeat-stale and fleet-control are mutually exclusive stages of the butler-liveness signal
- **AND** each check's finding fingerprint is derived from a stable identity (connector type/identity, butler name before fleet-condition handoff, the fleet-condition identity after handoff, or a fixed backup/deadman call-site) via the same sanitize-then-hash pattern the other sources use, so the fingerprint stays stable across patrol ticks even though the human-readable `event_summary` carries live timestamp/age
- **AND** the fleet-control finding instead reuses the fleet condition's canonical ledger fingerprint, so dispatch suppression matches that episode, and it is not re-recorded as a separate `infra_state` episode
- **AND** all filtering is tool-based (SQL queries, env var reads, filesystem stat calls); no LLM invocation
- **AND** health-check queries against the connector view and the active receiver or fleet-condition source run before row processing; a failure (e.g. a revoked grant) is raised and logged rather than silently returning an empty (falsely clean) result

#### Scenario: Legacy per-butler liveness conditions are covered without false recovery

- **WHEN** control-plane cutover finds active per-butler `heartbeat-stale` findings or infrastructure-condition episodes for an outage now represented by the fleet condition
- **THEN** it links their bounded impact evidence to that fleet condition and suppresses duplicate per-butler investigation or owner pages
- **AND** an active legacy episode remains historically readable and is resolved only after a complete receiver-observed snapshot proves recovery for its affected butler; cutover alone is not recovery
- **AND** until that proof, QA's own complete `infra_state` snapshot carries the legacy episode forward rather than resolving it by omission

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
