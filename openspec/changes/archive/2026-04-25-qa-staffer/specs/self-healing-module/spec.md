# Self-Healing Module

## MODIFIED Requirements

### Requirement: Module Delegates to Core Healing Package
The module SHALL NOT implement fingerprinting, dispatch logic, worktree management, or anonymization directly. It delegates to the QA staffer's unified investigation pipeline via Switchboard routing (preserving non-negotiable rule #3: inter-butler communication is MCP-only through Switchboard). When the QA staffer is not reachable, the module falls back to direct dispatch via the legacy `core.healing.dispatch` path.

#### Scenario: QA staffer available (primary path — via Switchboard `route()` tool)
- **WHEN** `report_error` is called and the QA staffer is registered with the Switchboard
- **THEN** the module computes the fingerprint via `core.healing.fingerprint`
- **AND** relays the finding to the QA staffer via the Switchboard `route()` MCP tool: `switchboard_client.call_tool("route", {"target_butler": "qa", "tool_name": "report_finding", "args": {fingerprint, exception_type, call_site, severity, event_summary, context}})`
- **AND** the QA staffer's `report_finding` tool receives the finding directly (no LLM session spawned — this is a tool-to-tool call via Switchboard routing, not a `route.execute` that spawns a session)
- **AND** returns `{"accepted": true, "fingerprint": "<hex>", "message": "Finding relayed to QA staffer via Switchboard"}`
- **AND** the QA staffer's next patrol cycle (or immediate mini-patrol for severity 0) handles dispatch

#### Scenario: QA staffer unavailable (fallback path)
- **WHEN** `report_error` is called and the QA staffer is not registered with the Switchboard (or Switchboard unreachable)
- **THEN** the module falls back to the existing direct dispatch via `core.healing.dispatch`
- **AND** behavior is identical to the pre-QA-staffer self-healing flow (10-gate sequence, worktree, PR)

#### Scenario: Detecting QA staffer availability
- **WHEN** the self-healing module needs to determine if the QA staffer is available
- **THEN** it checks the Switchboard registry via `switchboard_client.call_tool("list_butlers")` for an agent named "qa" (cached with TTL to avoid per-error roundtrip)
- **AND** if the Switchboard client itself is not connected (phase 11b not complete), falls back immediately

#### Scenario: Route call uses `allow_stale=True`
- **WHEN** the self-healing module relays a finding to the QA staffer via Switchboard `route()`
- **THEN** it passes `allow_stale=True` so findings are still delivered if the QA staffer is briefly stale (liveness TTL expired but still running)
- **AND** this prevents silent finding loss during transient liveness gaps

#### Scenario: Route call failure triggers fallback
- **WHEN** the Switchboard `route()` call to the QA staffer returns `{"error": "..."}` (QA staffer down, tool not found, timeout)
- **THEN** the self-healing module falls back to direct dispatch via `core.healing.dispatch`
- **AND** the error is logged at WARNING level with the route error message
- **AND** subsequent relay attempts continue (the cached availability check is not invalidated by a single failure)

### Requirement: Healing Agent Spawning from Module
When the module dispatches a healing agent (fallback path only), it SHALL spawn the agent as a new session on the SAME butler's spawner, with `trigger_source="healing"` and `complexity="self_healing"`. When the QA staffer handles dispatch (primary path), the healing agent is spawned by the QA staffer, not the reporting butler.

#### Scenario: Healing agent spawned while butler session is active (fallback path)
- **WHEN** a butler agent calls `report_error` during its session and dispatch gates pass (QA staffer unavailable)
- **THEN** the healing agent is spawned as a separate session via the spawner
- **AND** the original butler session continues unblocked (the MCP tool returns immediately after dispatch)
- **AND** the healing session bypasses the per-butler semaphore (so it doesn't deadlock against the calling session)

#### Scenario: QA staffer handles dispatch (primary path)
- **WHEN** a butler agent calls `report_error` and the QA staffer is available
- **THEN** no healing agent is spawned by the reporting butler
- **AND** the QA staffer spawns the investigation agent in its own daemon context, using its own worktree, credentials, and semaphore

#### Scenario: report_error is non-blocking
- **WHEN** a butler agent calls `report_error`
- **THEN** the tool returns within 1-2 seconds (fingerprint computation + Switchboard route call or gate checks only)
- **AND** any actual healing agent spawn happens asynchronously in the QA staffer's context
