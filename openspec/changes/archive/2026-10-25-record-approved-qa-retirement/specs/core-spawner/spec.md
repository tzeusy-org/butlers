## MODIFIED Requirements

### Requirement: Trigger Source Tracking
Valid trigger sources include `tick`, `external`, `trigger`, `route`, `qa`, and `schedule:<task-name>`. Historical `healing` provenance SHALL remain readable and its existing isolation/recursion safeguards SHALL remain intact; it SHALL NOT enable the retired local dispatcher. The trigger source SHALL be passed through to session creation for audit.

ID: REQ-core-spawner-006
Source: core-spawner trigger audit; RFC 0015 centralized QA; bu-lsxqb0.6 and bu-1fe7xv rounds 3-4
Scope: v1-mandatory

#### Scenario: Schedule trigger source
- **WHEN** a task named `daily_digest` fires via the scheduler
- **THEN** the session's `trigger_source` is `"schedule:daily_digest"`

#### Scenario: Healing trigger source
- **WHEN** an existing session carries historical `trigger_source="healing"` provenance
- **THEN** its original trigger source remains unchanged
- **AND** neither the core QA relay nor the Spawner crash handler starts a local investigation
- **AND** new centralized QA investigations retain `trigger_source="qa"`

#### Scenario: Healing sessions skip fallback dispatcher
- **WHEN** a session with `trigger_source = "healing"` fails
- **THEN** its failure remains recorded without invoking a per-butler fallback dispatcher
- **AND** the centralized QA self-recursion barrier remains authoritative for QA-origin findings

### Requirement: Healing Session Semaphore Bypass
When the spawner's `trigger()` is called with `trigger_source = "healing"`, the per-butler session semaphore SHALL be bypassed. The global semaphore SHALL still be acquired. This retained isolation behavior does not register or enable the retired local dispatcher.

#### Scenario: Healing bypasses per-butler semaphore
- **WHEN** `trigger(prompt, trigger_source="healing")` is called and the per-butler semaphore has 0 available slots
- **THEN** the healing session proceeds without acquiring the per-butler semaphore

#### Scenario: Healing acquires global semaphore
- **WHEN** `trigger(prompt, trigger_source="healing")` is called
- **THEN** the global semaphore is still acquired

### Requirement: Core QA Relay Availability
The daemon SHALL register `report_error` and `get_healing_status` as logged core tools on every roster, independently of module declarations and `core_groups`. The retired `self_healing` module, its configuration and its skill SHALL NOT be required or registered. The Spawner SHALL have no local healing fallback.

ID: REQ-core-spawner-007
Source: RFC 0015 centralized QA; approved-A QA retirement bu-dss7he.1; REQ-core-spawner-006
Scope: v1-mandatory

#### Scenario: Core relay without module configuration
- **WHEN** a daemon starts without `[modules.self_healing]`, including a staffer or a daemon with no enabled core groups
- **THEN** both core QA tools are registered with trusted daemon identity and read-only history pool
- **AND** `error_message`, `traceback`, and `context` retain sensitive argument metadata and normal tool-call logging/session/trace binding
- **AND** no local investigation, startup recovery, reaper or publisher is installed

#### Scenario: Late transport and cache ownership
- **WHEN** a report is submitted after Switchboard client creation or replacement
- **THEN** the relay resolves the daemon's current client and binds its sixty-second QA availability cache to that exact client
- **AND** Switchboard may use its own registered local MCP transport
- **AND** no reporting argument selects source identity or publication authority

#### Scenario: Confirmed reception and bounded refusal
- **WHEN** a core report is submitted
- **THEN** fingerprinting, severity, summary truncation and relay response shapes remain compatible
- **AND** QA availability and routing share one two-second absolute deadline
- **AND** only confirmed QA reception returns `accepted=true`; missing QA, malformed/error envelopes or unconfirmed transport return an honest refusal without local fallback
- **AND** a timeout does not promise cancellation of already-buffered reception or license a blind replay
- **AND** QA alone retains triage, concurrency, cooldown, breaker, timeout and publication policy

## RENAMED Requirements

- FROM: `### Requirement: Healing Configuration in butler.toml`
- TO: `### Requirement: Core QA Relay Availability`
