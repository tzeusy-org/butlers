## 1. Internal Scheduler Loop

- [ ] 1.1 Add `[butler.scheduler]` config parsing to `ButlerConfig` (support `tick_interval_seconds` with default 60, `heartbeat_interval_seconds` with default 120, validation for positive values)
- [ ] 1.2 Implement the scheduler loop as an asyncio background task in `daemon.py` — `while True: await asyncio.sleep(interval); await tick(pool, dispatch_fn)` with exception logging and loop continuation
- [ ] 1.3 Start the scheduler loop in the daemon startup sequence (after FastMCP server is listening, step 10)
- [ ] 1.4 Cancel the scheduler loop during graceful shutdown (before module `on_shutdown()`, wait for in-progress `tick()` to complete)
- [ ] 1.5 Write tests for the scheduler loop: periodic tick calls, error resilience, shutdown cancellation, custom interval

## 2. Liveness Reporter

- [ ] 2.1 Implement the liveness reporter as an asyncio background task in `daemon.py` — periodic HTTP POST to `{BUTLERS_SWITCHBOARD_URL}/api/heartbeat` with `{"butler_name": "<name>"}`, WARNING-level logging on connection failure
- [ ] 2.2 Add Switchboard URL resolution: read `BUTLERS_SWITCHBOARD_URL` env var, default to `http://localhost:8200`
- [ ] 2.3 Skip liveness reporter startup for the Switchboard butler (detect by butler name)
- [ ] 2.4 Send initial heartbeat within 5 seconds of startup (before first interval sleep)
- [ ] 2.5 Cancel the liveness reporter during graceful shutdown (alongside scheduler loop cancellation)
- [ ] 2.6 Write tests for the liveness reporter: periodic POST calls, connection failure handling, Switchboard exclusion, shutdown cancellation

## 3. Switchboard Heartbeat Endpoint

- [ ] 3.1 Add `POST /api/heartbeat` route to `roster/switchboard/api/router.py` — accept `{"butler_name": str}`, update `last_seen_at`, return `{"status": "ok", "eligibility_state": "..."}`
- [ ] 3.2 Implement stale→active transition on heartbeat: update `eligibility_state`, `eligibility_updated_at`, log to `butler_registry_eligibility_log`
- [ ] 3.3 Handle quarantined butler heartbeat: update `last_seen_at` but keep `eligibility_state = 'quarantined'`
- [ ] 3.4 Return 404 for unknown butler names, 422 for malformed requests
- [ ] 3.5 Add Pydantic request/response models to `roster/switchboard/api/models.py`
- [ ] 3.6 Write tests for the heartbeat endpoint: valid heartbeat, stale reactivation, quarantined retention, unknown butler, malformed request

## 4. Eligibility Sweep

- [ ] 4.1 Add `eligibility-sweep` scheduled task to `roster/switchboard/butler.toml` (`cron = "*/5 * * * *"`, prompt for evaluating liveness)
- [ ] 4.2 Implement the eligibility sweep logic: query butler_registry for TTL-expired butlers, transition active→stale (1x TTL) and stale→quarantined (2x TTL), log transitions to eligibility_log
- [ ] 4.3 Skip butlers with `last_seen_at = NULL` during sweep
- [ ] 4.4 Write tests for the eligibility sweep: active→stale, stale→quarantined, within-TTL unchanged, null last_seen_at skipped

## 5. Update tick() MCP Tool Description

- [ ] 5.1 Update the `tick()` MCP tool docstring in `daemon.py` to reflect that it's primarily driven by the internal scheduler loop, retained for manual/debugging use
- [ ] 5.2 Update any references in CLAUDE.md or architecture docs that describe tick() as "called by the Heartbeat Butler"

## 6. Remove Heartbeat Butler

- [ ] 6.1 Delete `roster/heartbeat/` directory (butler.toml, tools/, MANIFESTO.md, CLAUDE.md, AGENTS.md, etc.)
- [ ] 6.2 Delete heartbeat-specific tests (`tests/daemon/test_heartbeat.py` and any others)
- [ ] 6.3 Delete the heartbeat spec from `openspec/changes/v1-mvp-spec/specs/heartbeat/`
- [ ] 6.4 Close heartbeat-related beads
- [ ] 6.5 Update CLAUDE.md architecture section: remove "Heartbeat Butler" from Special Butlers, update Trigger Flow description
- [ ] 6.6 Update Switchboard's `discover()` documentation/tests if they reference the heartbeat butler

## 7. Integration and Final Validation

- [ ] 7.1 Run full lint and test suite to verify no regressions
- [ ] 7.2 Verify Switchboard discovery excludes the removed heartbeat butler
- [ ] 7.3 Verify daemon startup sequence logs show scheduler loop and liveness reporter started
