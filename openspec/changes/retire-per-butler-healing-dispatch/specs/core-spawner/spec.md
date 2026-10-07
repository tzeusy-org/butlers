## MODIFIED Requirements

### Requirement: Spawner Session Lifecycle
Each invocation creates a session record before the runtime call and completes it after, regardless of success or failure. Sessions are trace-correlated via OpenTelemetry span context. After completing a runtime invocation, the spawner SHALL check `runtime.last_process_info` and, if non-null and a session_id and database pool are available, write the process metadata to the `session_process_logs` table via `session_process_log_write()`. This applies to both the success path (after `session_complete` with `success=True`) and the error path (after `session_complete` with `success=False`). The write is best-effort: exceptions are caught and logged at DEBUG level without affecting the session result or propagating to the caller. The error path SHALL preserve ordinary failure evidence and cleanup without dispatching a per-butler investigation; QA owns independent session/log discovery under RFC 0015.

On the normal-completion (non-raising) path the spawner SHALL additionally run delivery accounting (see the **Interactive Reply Delivery Accounting** requirement) before persisting the session. Delivery accounting MAY downgrade the persisted session record to `success=False` even though the runtime invocation itself completed cleanly. Because this runs on the success path and does not raise, it SHALL NOT trigger same-tier failover or a per-butler investigation dispatcher.

ID: REQ-core-spawner-005
Source: RFC 0015 centralized QA discovery/dispatch; bu-1fe7xv rounds 3-4; core-spawner existing session evidence contract
Scope: v1-mandatory

#### Scenario: Successful session
- **WHEN** a runtime invocation completes successfully
- **AND** delivery accounting does not flag the session as an undelivered interactive reply
- **THEN** `session_create()` is called before invocation and `session_complete()` is called after with `success=True`, output text, tool calls, duration, and token counts

#### Scenario: Failed session — spawner fallback dispatch
- **WHEN** a runtime invocation raises an exception
- **THEN** `session_complete()` is called with `success=False`, the original error message, and duration
- **AND** the runtime adapter's `reset()` method is called for cleanup
- **AND** no per-butler investigation task is created by the Spawner

#### Scenario: Process log written after successful runtime invocation
- **WHEN** the spawner completes a runtime invocation successfully
- **AND** `runtime.last_process_info` returns a non-null dict
- **THEN** the spawner writes the process info to `session_process_logs` after calling `session_complete`

#### Scenario: Process log written after failed runtime invocation
- **WHEN** the spawner catches an exception from `runtime.invoke()`
- **AND** `runtime.last_process_info` returns a non-null dict
- **THEN** the spawner writes the process info to `session_process_logs` after calling `session_complete`

#### Scenario: Process log write failure is non-fatal
- **WHEN** the `session_process_log_write()` call raises any exception
- **THEN** the exception is logged at DEBUG level and the spawner continues normally

#### Scenario: Fallback is secondary to module path
- **WHEN** a registered relay reports an agent-observed error and a runtime invocation independently fails
- **THEN** the report is received through the centralized QA boundary and ordinary Spawner failure evidence remains available
- **AND** no additional per-butler fallback investigation is dispatched

#### Scenario: Dispatcher receives exception and traceback
- **WHEN** a runtime raises an exception with traceback evidence
- **THEN** the Spawner preserves its ordinary failure, process and captured-tool evidence without passing an exception/traceback to a local investigation dispatcher
- **AND** agent-reported structured exceptions retain fingerprint/traceback handling at the centralized report_error relay boundary

#### Scenario: Healing dispatcher failure is non-fatal
- **WHEN** QA reception is unavailable while a runtime invocation fails
- **THEN** the original session error, reset and finally cleanup remain authoritative
- **AND** no per-butler fallback dispatcher is invoked or allowed to mask that error

#### Scenario: Finally block exceptions do not trigger healing
- **WHEN** an exception occurs in the spawner's `finally` block (metrics, span cleanup, context clearing)
- **THEN** no healing dispatch occurs for that exception

### Requirement: Trigger Source Tracking
Valid trigger sources are: `tick`, `external`, `trigger`, `route`, `healing`, and `schedule:<task-name>`. The trigger source SHALL be passed through to session creation for audit.

ID: REQ-core-spawner-006
Source: core-spawner trigger audit; RFC 0015 centralized QA; bu-lsxqb0.6 and bu-1fe7xv rounds 3-4
Scope: v1-mandatory

#### Scenario: Schedule trigger source
- **WHEN** a task named `daily_digest` fires via the scheduler
- **THEN** the session's `trigger_source` is `"schedule:daily_digest"`

#### Scenario: Healing trigger source
- **WHEN** an existing shared legacy healing dispatcher explicitly spawns an investigation session
- **THEN** the session's `trigger_source` is `"healing"`
- **AND** this does not enable direct investigation dispatch by the relay module or Spawner crash handler

#### Scenario: Healing sessions skip fallback dispatcher
- **WHEN** a session with `trigger_source = "healing"` fails
- **THEN** its failure remains recorded without invoking a per-butler fallback dispatcher
- **AND** the centralized QA self-recursion barrier remains authoritative for QA-origin findings

### Requirement: Healing Configuration in butler.toml
The daemon SHALL admit self_healing startup only when `[modules.self_healing]` is declared. Its relay config SHALL accept the existing six keys without permitting the reporting butler to override QA investigation policy. The Spawner SHALL have no separately enabled healing fallback.

ID: REQ-core-spawner-007
Source: RFC 0015 centralized QA; bu-1fe7xv rounds 2-4; core-modules explicit startup selection
Scope: v1-mandatory

#### Scenario: Default healing config
- **WHEN** `butler.toml` has no `[modules.self_healing]` section
- **THEN** self_healing remains available to registry discovery but is not admitted to startup or tool registration
- **AND** no Spawner fallback is enabled

#### Scenario: Healing config fields
- **WHEN** `[modules.self_healing]` is present
- **THEN** its schema accepts `enabled` (bool, default true), `severity_threshold` (int, default 2), `max_concurrent` (int, default 2), `cooldown_minutes` (int, default 60), `circuit_breaker_threshold` (int, default 5), and `timeout_minutes` (int, default 30)
- **AND** `enabled=false` prevents report admission while the five legacy dispatch thresholds remain inert compatibility inputs
- **AND** unknown extra fields are rejected

#### Scenario: Spawner fallback uses module config
- **WHEN** a reporting butler sets legacy self_healing threshold values and submits a valid report while relay admission is enabled
- **THEN** the report uses the same QA relay boundary without local dispatch gates or a local investigation
- **AND** QA retains its own authoritative triage, concurrency, cooldown, breaker and timeout policy

### Requirement: Interactive Reply Delivery Accounting
On the normal-completion (non-raising) path, the spawner SHALL evaluate whether a route-triggered interactive session attempted a reply via `notify()` but delivered nothing, and SHALL persist that session record with `success=False` and a human-readable reason in the session `error` column.

This is a **third session outcome**: the runtime invocation returned normally but the user received no reply. It is detected on the success path and SHALL NOT trigger same-tier model failover or a per-butler investigation dispatcher. Ordinary crash evidence and centralized QA discovery remain separate.

The in-memory `SpawnerResult.success` SHALL remain `True` for this outcome so that downstream memory extraction and the route reply flow are unaffected; only the persisted session record reflects the undelivered delivery.

**Delivered-status set.** A `notify()` tool-call counts as delivered only when its captured result is a dict whose `status` is in the delivered set `{ok, deferred}`. Every other outcome is undelivered, including legacy suppression results, `pending_approval`, `pending_missing_identifier`, `error`, a record whose `outcome` is `error`, and a record with no result dict at all (the schema-rejection / null-result incident shape). `deferred` is delivered because the notification is persisted to the deferred queue with a concrete `deliver_at` and will be attempted later.

**Scope guards** (deliberately conservative, to avoid false positives):
- only sessions whose `trigger_source` is `route` are considered;
- only sessions whose captured routing-context source channel is in the interactive set (`telegram_bot`, `whatsapp`) are considered;
- a session that made zero `notify()` attempts is left alone (the runtime may have legitimately decided no reply was warranted);
- if any single `notify()` attempt delivered, the session is not flagged.

ID: REQ-core-spawner-008
Source: core-spawner delivery accounting; RFC 0015 central dispatch; bu-1fe7xv rounds 3-4
Scope: v1-mandatory

#### Scenario: Undelivered interactive reply recorded as failed without healing
- **WHEN** a `route`-triggered session whose source channel is `telegram_bot` completes successfully without raising
- **AND** it made one or more `notify()` attempts and none of them delivered (every notify result status is outside `{ok, deferred}`, or carries no result dict)
- **THEN** `session_complete()` is called with `success=False` and a reason describing the undelivered interactive reply
- **AND** the spawner SHALL NOT raise, SHALL NOT attempt same-tier model failover, and SHALL NOT invoke the self-healing fallback dispatcher
- **AND** the in-memory `SpawnerResult.success` SHALL remain `True`

#### Scenario: Delivered reply leaves the session successful
- **WHEN** a `route`-triggered interactive session made at least one `notify()` attempt whose result `status` is `ok` or `deferred`
- **THEN** delivery accounting does not flag the session
- **AND** `session_complete()` is called with `success=True`

#### Scenario: Deferred owner-default reply remains delivered
- **WHEN** a `route`-triggered interactive session's eligible owner-default
  notify call returns `status="deferred"` with a queued notification id and
  `deliver_at`
- **THEN** delivery accounting leaves the session successful

#### Scenario: Null-result notify attempt counts as undelivered
- **WHEN** a `route`-triggered interactive session's `notify()` tool-call record has no result dict (a schema rejection left an unexecuted parser-side record) or an `outcome` of `error`
- **THEN** the attempt is treated as undelivered
- **AND** the session is recorded with `success=False`

#### Scenario: Zero notify attempts leaves the session untouched
- **WHEN** a `route`-triggered interactive session made no `notify()` attempt at all
- **THEN** delivery accounting does not flag the session
- **AND** `session_complete()` is called with `success=True`

#### Scenario: Non-route or non-interactive sessions are exempt
- **WHEN** a session's `trigger_source` is not `route`, or its source channel is not in the interactive set (`telegram_bot`, `whatsapp`)
- **THEN** delivery accounting does not run and the session outcome is unchanged from the ordinary success path
