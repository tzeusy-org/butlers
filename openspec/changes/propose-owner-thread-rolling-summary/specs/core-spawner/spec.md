# Draft candidate: private rolling-summary custody

Status: NOT ADOPTED. Future additions require exact review/adoption and owning integrations. Full current requirements and prior scenarios are retained.

## MODIFIED Requirements

### Requirement: Spawner Session Lifecycle
Each invocation creates a session record before the runtime call and completes it after, regardless of success or failure. Sessions are trace-correlated via OpenTelemetry span context. After completing a runtime invocation, the spawner SHALL check `runtime.last_process_info` and, if non-null and a session_id and database pool are available, write the process metadata to the `session_process_logs` table via `session_process_log_write()`. This applies to both the success path (after `session_complete` with `success=True`) and the error path (after `session_complete` with `success=False`). The write is best-effort: exceptions are caught and logged at DEBUG level without affecting the session result or propagating to the caller. The error path SHALL preserve ordinary failure evidence and cleanup without dispatching a per-butler investigation; QA owns independent session/log discovery under RFC 0015.
On the normal-completion (non-raising) path the spawner SHALL additionally run delivery accounting (see the **Interactive Reply Delivery Accounting** requirement) before persisting the session. Delivery accounting MAY downgrade the persisted session record to `success=False` even though the runtime invocation itself completed cleanly. Because this runs on the success path and does not raise, it SHALL NOT trigger same-tier failover or a per-butler investigation dispatcher.

ID: REQ-core-spawner-005
Source: RFC 0015 centralized QA discovery/dispatch; bu-1fe7xv rounds 3-4; core-spawner existing session evidence contract
Scope: v1-mandatory

After independent exact adoption and all owned custody integrations, for a private-summary turn only, the session record SHALL be created from a derivative-free safe base prompt before overlay resolution. The leased untrusted user overlay is supplied only to an eligible attempt, outside the exact system receipt; session_create/session_complete/process/error persistence never receives a protected copy. Completion/output/tool admission and cleanup revalidate custody epoch. Invalidation/unknown disposal fences copy-bearing writes and cancels/clears users; best-effort logging is not custody proof. Accounting/error taxonomy/failover/tool effects/lifecycle evidence remain. Unsupported adapter/timeout/failover/native-resume modes cannot regain text via legacy final_prompt; only verified execution or marked raw-tail fallback is permitted.

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

#### Scenario: Private runtime overlay is excluded from session creation

- **WHEN** a supported owner/thread attempt has valid private history
- **THEN** session_create stores safe base prompt and derivative-free exact system receipt before the runtime receives the overlay
- **AND** completion keeps normal accounting/evidence without copying overlay

#### Scenario: Late completion after revocation does not persist a copy

- **WHEN** source/thread invalidation races completion or failover
- **THEN** custody checks reject copy-bearing output/tool/error/process writes and fence/clear old attempt
- **AND** normal gates and actual executed-effect audit remain; no provider rerun merely repairs a lease

### Requirement: Memory Context Injection
When the memory module is enabled, the Spawner SHALL fetch memory context via `fetch_memory_context()` before invocation and append it to the system prompt. On successful completion, it SHALL store non-empty session output as an episode via `store_session_episode()` unless the trigger source is exactly `schedule:consolidation`. This automatic episode-write exclusion SHALL NOT suppress the normal session record or change memory-context retrieval. Both memory operations SHALL be fail-open (log and continue).

After independent exact adoption and all owned custody integrations, for a private-summary turn only, memory retrieval SHALL use permitted base prompt, never private derivative as query/fact. Automatic episode admission excludes protected payload copies. A result lacking independent raw-source/copy-exclusion admission is not eligible non-empty visible output for automatic storage. Normal admitted output, schedule/consolidation distinctions, routing-derived authority and fail-open retrieval stay unchanged; no summary-as-steering write is added.

ID: REQ-core-spawner-011
Source: core-spawner § Memory Context Injection; private summary custody R1; RFC 0003/0007 unchanged wire/source identity
Scope: proposed-owner-gated


#### Scenario: Memory context injected into system prompt
- **WHEN** the memory module is enabled and context is available
- **THEN** the memory context is appended to the base system prompt separated by a blank line

#### Scenario: Memory failure does not block invocation
- **WHEN** memory context retrieval fails
- **THEN** the failure is logged and the invocation proceeds with the base system prompt only

#### Scenario: Consolidation session skips automatic episode persistence
- **WHEN** memory is enabled and a runtime invocation with
  `trigger_source="schedule:consolidation"` succeeds with non-empty output
- **THEN** the Spawner completes the ordinary session record
- **AND** the Spawner SHALL NOT call `store_session_episode()` for that output

#### Scenario: Ordinary scheduled session still persists an episode
- **WHEN** memory is enabled and a runtime invocation with
  `trigger_source="schedule:daily_digest"` succeeds with non-empty output
- **THEN** the Spawner SHALL call `store_session_episode()` once for that
  output after normal session completion

#### Scenario: Episode storage carries trigger and routing context
- **WHEN** the Spawner stores a session episode
- **THEN** it SHALL pass the invocation's routing context (not the session
  output's text) to `store_session_episode()` so the memory module can stamp the episode's
  server-derived content authority (see module-memory "Content authority and
  steering-class admission")

#### Scenario: Failed session remains ineligible for automatic episode persistence
- **WHEN** memory is enabled and a runtime invocation fails
- **THEN** the Spawner SHALL NOT call `store_session_episode()` regardless of
  its trigger source

#### Scenario: Private overlay cannot become an automatic memory episode

- **WHEN** summary-bearing output carries a protected copy or admission is unknown
- **THEN** that copy is not stored as episode/fact/steering input or memory query
- **AND** ordinary admitted raw-source output and existing consolidation/failure exclusions retain their contracts
