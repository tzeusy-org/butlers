# Trigger Flow

> **Purpose:** Describe the two trigger sources that create butler sessions and the processing pipeline from trigger to completion.
> **Audience:** Developers working on the daemon, scheduler, or spawner; anyone debugging why a session fired.
> **Prerequisites:** [Modules and Connectors](modules-and-connectors.md), [MCP Model](mcp-model.md).

## Overview

Every butler session begins with a trigger. There are two trigger sources: external MCP calls (arriving via `trigger()` or `route.execute` tools) and the internal scheduler (cron-driven task dispatch). Both paths converge at the Spawner, which acquires a concurrency slot, creates a session record, invokes an ephemeral LLM runtime, and logs the outcome.

## External Triggers

External triggers originate from outside the butler daemon. The two primary entry points are:

### trigger(prompt) Tool

Any MCP client connected to a butler can call the `trigger` tool with a prompt string. This is used for ad-hoc invocations, testing, and direct user interaction. The trigger source is recorded as `"trigger"` in the session log.

### route.execute Tool

The Switchboard butler dispatches classified messages to domain butlers via the `route.execute` MCP tool. When a target butler receives a route envelope, it persists the request to the `route_inbox` table in `accepted` state and returns `{"status": "accepted"}` immediately. A background task then transitions the row to `processing` and calls `spawner.trigger()` with the routed prompt. The trigger source is recorded as `"route"`.

The `route_inbox` provides crash recovery. On startup, each butler scans for rows stuck in `accepted` or `processing` state (older than a configurable grace period, default 10 seconds) and re-dispatches them. Both states are scanned because a daemon crash or graceful shutdown can leave rows in either state with no task to complete them.

The route inbox tracks four lifecycle states:

- **accepted** --- persisted, awaiting processing
- **processing** --- background dispatch has started
- **processed** --- spawner returned successfully (session_id recorded)
- **errored** --- spawner raised an exception (error message stored)

## Scheduled Triggers

The scheduler (`src/butlers/core/scheduler.py`) evaluates cron expressions on every tick and dispatches due tasks through the spawner. Trigger sources follow the pattern `"schedule:<task-name>"` (e.g., `"schedule:daily-digest"`).

### Tick Loop

The daemon calls `tick()` periodically. Each tick:

1. Queries `scheduled_tasks` for rows where `enabled = true AND next_run_at <= now()`.
2. For each due task, calls `dispatch_fn()` with the task's prompt (or job name/args for job-mode dispatch).
3. Records the result and updates `next_run_at` using croniter for the next occurrence.
4. Emits a `butler.tick` OpenTelemetry span with `tasks_due` and `tasks_run` attributes.

Tasks that fail during dispatch are logged but do not block subsequent tasks.

### TOML-to-DB Sync

On startup, the scheduler synchronizes `[[butler.schedule]]` entries from `butler.toml` to the `scheduled_tasks` database table. New tasks are inserted, changed tasks are updated (cron, prompt, dispatch mode, complexity), and tasks removed from TOML are disabled. This ensures the database always reflects the declared configuration while preserving runtime-created tasks (source `"db"`) that were added via the dashboard API.

### Deterministic Staggering

When multiple butlers share the same cron expression (e.g., `0 * * * *` for hourly), simultaneous dispatch creates resource contention. The scheduler computes a deterministic stagger offset from a SHA-256 hash of the butler name, bounded by the lesser of `max_stagger_seconds` (default 15 minutes) and the cron cadence minus one second. This spreads concurrent tasks without requiring coordination.

## Concurrency Control

The spawner enforces two layers of concurrency control:

1. **Per-butler semaphore** --- Each `Spawner` instance holds an `asyncio.Semaphore` configured by `max_concurrent_sessions` in `butler.toml` (default 1, meaning serial dispatch).
2. **Global semaphore** --- A process-wide `asyncio.Semaphore` limits total concurrent LLM sessions across all butlers. Defaults to 3, configurable via `BUTLERS_MAX_GLOBAL_SESSIONS`.

Both semaphores must be acquired before a session starts. When a trigger arrives and all slots are occupied, the trigger queues behind the semaphore. Metrics track queue depth at both levels.

## Request Context

Every session carries a `request_id` (UUIDv7 format). Connector-sourced sessions inherit the request ID from the ingestion request context. Internally-triggered sessions (tick, schedule, trigger) generate a fresh UUID. The request ID propagates to session records, tool call captures, and OpenTelemetry spans for end-to-end tracing.

## Session Creation and Completion

Once the spawner acquires its concurrency slot:

1. A session row is created in the `sessions` table with the prompt, trigger source, model, request ID, and optional complexity tier.
2. An MCP config is generated pointing exclusively at this butler's FastMCP server.
3. The runtime adapter (Claude Code, Codex, Gemini) is invoked with the config and prompt.
4. On return, the session is marked complete with output, tool call records, duration, token usage, and success/failure status.
5. Concurrency slots are released.

## Related Pages

- [Spawner](../runtime/spawner.md) --- detailed spawner mechanics
- [Scheduler Execution](../runtime/scheduler-execution.md) --- cron evaluation and dispatch
- [Session Lifecycle](../runtime/session-lifecycle.md) --- session creation through completion
- [Switchboard Routing](switchboard-routing.md) --- how route.execute triggers arrive
