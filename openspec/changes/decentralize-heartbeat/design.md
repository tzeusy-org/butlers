## Context

Every butler has a task scheduler with a `tick()` function that evaluates due cron tasks. Currently, `tick()` is only called externally by the Heartbeat Butler, which queries the Switchboard for all registered butlers every 10 minutes and calls `tick()` on each one via `route()`. This creates a single point of failure for all scheduling and a chicken-and-egg problem for the Heartbeat Butler's own schedule.

The Switchboard already has liveness infrastructure (`last_seen_at`, `eligibility_state`, `liveness_ttl_seconds`, `quarantined_at`) from migration sw_009, but nothing actively drives these transitions — `last_seen_at` is only updated passively when `route()` succeeds.

## Goals / Non-Goals

**Goals:**
- Make each butler self-sufficient for cron scheduling (no external dependency)
- Provide active liveness monitoring via push-based heartbeats
- Eliminate the Heartbeat Butler as a component
- Reuse the existing Switchboard liveness schema (sw_009 columns)

**Non-Goals:**
- Changing how `tick()` works internally (query due tasks, dispatch serially) — that logic is unchanged
- Adding pull-based health checks (Switchboard actively polling butlers)
- Changing the cron resolution below 60 seconds
- Adding distributed consensus or leader election for scheduling

## Decisions

### 1. Internal scheduler loop as an asyncio background task

Each butler daemon starts an `asyncio.Task` after the FastMCP server is ready. The task runs a `while True` loop: sleep 60 seconds, call `tick()`, repeat. This is step 10 in the startup sequence, after the server is listening.

**Why 60 seconds?** Cron's minimum resolution is 1 minute. Checking every 60s ensures tasks fire within their cron window without wasteful polling. The interval is configurable via `[butler.scheduler]` in butler.toml for butlers that want tighter or looser checking.

**Why not `asyncio.Timer` or an external scheduler like APScheduler?** An asyncio loop is the simplest mechanism with zero additional dependencies. The scheduler logic already exists in `tick()` — we just need something to call it periodically.

**Alternative considered:** Having the scheduler compute the next due time and sleep exactly until then. Rejected because multiple tasks can have different schedules, and runtime-created tasks would require waking the sleeper. A fixed-interval check is simpler and sufficient.

### 2. Liveness via HTTP POST to Switchboard

Each butler daemon starts a second asyncio background task that periodically sends an HTTP POST to the Switchboard's `/api/heartbeat` endpoint with `{"butler_name": "<name>"}`. The Switchboard updates `last_seen_at` and returns 200.

**Why HTTP POST instead of MCP tool call?** MCP-over-SSE requires establishing a persistent client connection for each ping — heavy for a simple liveness signal. An HTTP POST is stateless, lightweight, and the Switchboard already runs FastAPI alongside its MCP server.

**Why not the other direction (Switchboard polls butlers)?** Pull-based monitoring means the Switchboard must know every butler's endpoint and actively probe them. This couples the Switchboard to butler availability and makes the Switchboard itself a single point of failure for monitoring. Push-based reporting is simpler: each butler is responsible for its own heartbeat.

**Switchboard URL discovery:** Butlers resolve the Switchboard URL from the `BUTLERS_SWITCHBOARD_URL` environment variable, defaulting to `http://localhost:40200`. This avoids coupling every butler.toml to the Switchboard's deployment.

**Heartbeat interval:** Default 120 seconds (half of the default `liveness_ttl_seconds` of 300s). This gives each butler at least one retry window before being marked stale.

### 3. Switchboard eligibility sweep as a scheduled task

The Switchboard gains a TOML-scheduled task (`eligibility-sweep`, cron `*/5 * * * *`) that queries `butler_registry` for butlers whose `last_seen_at + liveness_ttl_seconds < now()` and transitions them from `active` to `stale`. Butlers already `stale` for more than 2× TTL are transitioned to `quarantined`. Each transition is logged to `butler_registry_eligibility_log`.

**Why a scheduled task and not inline in the heartbeat endpoint?** Sweeps handle the case where a butler silently dies and stops sending heartbeats — there's no incoming request to trigger inline logic. A periodic sweep catches these cases.

### 4. `tick()` MCP tool retained but demoted

The `tick()` MCP tool remains on every butler for manual invocation and debugging. Its description changes to reflect that it's no longer the primary scheduling mechanism. No functional change to the tool itself.

### 5. Heartbeat Butler fully removed

The `roster/heartbeat/` directory, `butler_heartbeat` database, and all Heartbeat-specific specs, tests, and beads are deleted. No migration needed — the database simply won't be provisioned anymore.

## Risks / Trade-offs

**[Risk] Clock skew between scheduler loop start and cron boundaries** → The 60s polling interval means tasks may fire up to 59s late. This is acceptable for the project's use cases (10-minute heartbeats, daily reviews) and matches the current behavior where the Heartbeat Butler only ticks every 10 minutes.

**[Risk] Switchboard unavailability prevents liveness reporting** → If the Switchboard is down, butlers can't report alive. Their eligibility will go stale. Mitigation: this is actually correct behavior — if the Switchboard can't be reached, something is wrong and staleness is the right signal. Butlers continue operating normally for scheduling regardless.

**[Risk] HTTP heartbeat endpoint abuse** → The `/api/heartbeat` endpoint accepts a butler name and updates `last_seen_at`. A rogue client could fake liveness. Mitigation: v1 runs on localhost only. Future: add a shared secret or mTLS for production deployments.

**[Trade-off] Slightly higher resource usage** → Each butler now runs two background asyncio tasks (scheduler loop + liveness reporter) instead of passively waiting for external ticks. The overhead is negligible (one function call per minute + one HTTP POST per 2 minutes).

## Migration Plan

1. Add the internal scheduler loop and liveness reporter to `daemon.py`
2. Add the `/api/heartbeat` endpoint to the Switchboard
3. Add the `eligibility-sweep` scheduled task to the Switchboard's `butler.toml`
4. Update specs (task-scheduler, butler-daemon, switchboard)
5. Delete `roster/heartbeat/` and its spec
6. Delete/close heartbeat-related beads
7. Drop `butler_heartbeat` database (manual cleanup, not automated)

Rollback: If issues arise, re-add the Heartbeat Butler. The internal scheduler loop is additive and doesn't conflict with external ticking.
