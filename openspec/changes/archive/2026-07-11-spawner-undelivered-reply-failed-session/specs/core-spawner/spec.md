## MODIFIED Requirements

### Requirement: Spawner Session Lifecycle
Each invocation creates a session record before the runtime call and completes it after, regardless of success or failure. Sessions are trace-correlated via OpenTelemetry span context. After completing a runtime invocation, the spawner SHALL check `runtime.last_process_info` and, if non-null and a session_id and database pool are available, write the process metadata to the `session_process_logs` table via `session_process_log_write()`. This applies to both the success path (after `session_complete` with `success=True`) and the error path (after `session_complete` with `success=False`). The write is best-effort: exceptions are caught and logged at DEBUG level without affecting the session result or propagating to the caller. On the error path, after all existing error handling (session_complete, process log, runtime reset, audit entry), the spawner SHALL invoke the self-healing dispatcher as a **fallback** - this catches hard crashes where the butler agent never got a chance to call the `report_error` MCP tool.

On the normal-completion (non-raising) path the spawner SHALL additionally run delivery accounting (see the **Interactive Reply Delivery Accounting** requirement) before persisting the session. Delivery accounting MAY downgrade the persisted session record to `success=False` even though the runtime invocation itself completed cleanly. Because this runs on the success path and does not raise, it SHALL NOT trigger same-tier failover or the self-healing fallback dispatcher.

#### Scenario: Successful session
- **WHEN** a runtime invocation completes successfully
- **AND** delivery accounting does not flag the session as an undelivered interactive reply
- **THEN** `session_create()` is called before invocation and `session_complete()` is called after with `success=True`, output text, tool calls, duration, and token counts

#### Scenario: Failed session - spawner fallback dispatch
- **WHEN** a runtime invocation raises an exception
- **THEN** `session_complete()` is called with `success=False`, the error message, and duration
- **AND** the runtime adapter's `reset()` method is called for cleanup
- **AND** the self-healing dispatcher is invoked via `asyncio.create_task()` as a **fallback** with the raw exception, traceback, session_id, butler config, and trigger_source

#### Scenario: Fallback is secondary to module path
- **WHEN** a butler agent called `report_error` during its session for the same error before the session crashed
- **AND** the spawner fallback also fires for the same exception
- **THEN** the novelty gate deduplicates - the second dispatch (fallback) sees the active attempt from the first (module) and appends the session ID instead of creating a duplicate

#### Scenario: Dispatcher receives exception and traceback
- **WHEN** the spawner invokes the fallback dispatcher from the except block
- **THEN** it captures `sys.exc_info()` BEFORE any cleanup code runs
- **AND** passes the live traceback to `dispatch_healing()` for fingerprinting

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

#### Scenario: Healing dispatcher failure is non-fatal
- **WHEN** the fallback dispatcher task raises an exception
- **THEN** the exception is logged at WARNING level
- **AND** the original `SpawnerResult` is unaffected (already returned)

#### Scenario: Finally block exceptions do not trigger healing
- **WHEN** an exception occurs in the spawner's `finally` block (metrics, span cleanup, context clearing)
- **THEN** no healing dispatch occurs for that exception

## ADDED Requirements

### Requirement: Interactive Reply Delivery Accounting
On the normal-completion (non-raising) path, the spawner SHALL evaluate whether a route-triggered interactive session attempted a reply via `notify()` but delivered nothing, and SHALL persist that session record with `success=False` and a human-readable reason in the session `error` column.

This is a **third session outcome**, distinct from the two in the Spawner Session Lifecycle requirement: the runtime invocation completed successfully (it did not raise), yet the user received no reply. It is detected on the success path, NOT via a raised exception. Therefore it SHALL NOT trigger same-tier model failover and SHALL NOT trigger the self-healing fallback dispatcher. This is the explicit difference from the "Failed session - spawner fallback dispatch" scenario, which DOES heal: an undelivered interactive reply is not a crash, and re-running the runtime would not have helped.

The in-memory `SpawnerResult.success` SHALL remain `True` for this outcome so that downstream memory extraction and the route reply flow are unaffected; only the persisted session record reflects the undelivered delivery.

**Delivered-status set.** A `notify()` tool-call counts as delivered only when its captured result is a dict whose `status` is in the delivered set `{ok, deferred}`. Every other outcome is undelivered, including `suppressed_quiet_hours`, `pending_approval`, `pending_missing_identifier`, `error`, a record whose `outcome` is `error`, and a record with no result dict at all (the schema-rejection / null-result incident shape). `deferred` is delivered because the notification is persisted to the deferred queue with a concrete `deliver_at` and WILL be delivered later; `suppressed_quiet_hours` is undelivered because the message is dropped with no queue entry and no later delivery.

**Scope guards** (deliberately conservative, to avoid false positives):
- only sessions whose `trigger_source` is `route` are considered;
- only sessions whose captured routing-context source channel is in the interactive set (`telegram_bot`, `whatsapp`) are considered;
- a session that made zero `notify()` attempts is left alone (the runtime may have legitimately decided no reply was warranted);
- if any single `notify()` attempt delivered, the session is not flagged.

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

#### Scenario: suppressed_quiet_hours counts as undelivered
- **WHEN** a `route`-triggered interactive session's only `notify()` attempt returned `status="suppressed_quiet_hours"`
- **THEN** the attempt is treated as undelivered (the message was dropped with no later delivery)
- **AND** the session is recorded with `success=False`

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

## Source References
- Non-Negotiable Rule 4 (the daemon is deterministic infrastructure; recorded session outcomes must be testable, predictable, and honest).
- Non-Negotiable Rule 7 (transport is connector responsibility; the spawner reads the captured routing context's source channel rather than learning transport details).
