## Why

The Heartbeat Butler is a single point of failure for scheduling across the entire system. Every butler's cron-based tasks only fire when the Heartbeat Butler externally calls `tick()` — if it goes down, all scheduling stops. It also creates a chicken-and-egg problem: the Heartbeat Butler's own `heartbeat-cycle` task is a scheduled task that requires `tick()` to be called, but it excludes itself from its own tick cycle. Separating scheduling (self-driven) from liveness monitoring (push-based) eliminates this fragile coupling and makes each butler self-sufficient.

## What Changes

- **BREAKING**: Remove the Heartbeat Butler entirely (`roster/heartbeat/`, its database, its spec). It is no longer needed.
- Add an internal scheduler loop to every butler daemon — an asyncio background task that calls `tick()` locally every 60 seconds. Each butler drives its own cron schedule without external intervention.
- Add push-based liveness reporting — each butler periodically calls a `report_alive()` tool on the Switchboard to update its `last_seen_at`. The Switchboard runs its own periodic sweep to transition butlers through `active → stale → quarantined` based on `liveness_ttl_seconds`.
- The `tick()` MCP tool remains exposed for manual/debugging use, but is no longer the primary mechanism for driving scheduled tasks.

## Capabilities

### New Capabilities
- `internal-scheduler-loop`: An asyncio background task in the butler daemon that calls the scheduler's `tick()` function every 60 seconds, making each butler self-sufficient for cron execution.
- `liveness-reporting`: Push-based mechanism where each butler periodically reports to the Switchboard, which manages eligibility state transitions (`active`/`stale`/`quarantined`) based on TTL expiry.

### Modified Capabilities
- `task-scheduler`: The `tick()` entry point description changes from "called by the Heartbeat butler" to "called by the internal scheduler loop." No functional change to tick behavior itself.
- `butler-daemon`: Startup sequence gains a new step (start the internal scheduler loop after the server is ready). Shutdown sequence gains a step (cancel the scheduler loop before module shutdown).
- `switchboard`: Gains a `report_alive(butler_name)` tool for liveness reporting and a periodic eligibility sweep task.

## Impact

- **Deleted code/config**: `roster/heartbeat/` directory, `butler_heartbeat` database, heartbeat spec, heartbeat-related beads/tasks
- **Modified specs**: `task-scheduler`, `butler-daemon`, `switchboard`
- **New spec**: `heartbeat` spec replaced by `internal-scheduler-loop` and `liveness-reporting`
- **Daemon code** (`src/butlers/daemon.py`): Add asyncio background task for scheduler loop and liveness pings
- **Switchboard tools** (`roster/switchboard/tools/`): Add `report_alive()` tool, add eligibility sweep scheduled task
- **Tests**: Heartbeat-specific tests replaced by daemon scheduler loop tests and liveness reporting tests
- **Switchboard migration**: May need new migration for `report_alive` tool support (though `last_seen_at` and eligibility columns already exist from sw_009)
