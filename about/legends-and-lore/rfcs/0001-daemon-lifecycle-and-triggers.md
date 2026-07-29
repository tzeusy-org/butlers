# RFC 0001: Daemon Lifecycle and Triggers

**Status:** Accepted
**Date:** 2026-03-24

## Summary

The ButlerDaemon is the central orchestrator for a single butler instance. It manages a 17-phase startup sequence, accepts triggers from two sources (external MCP calls and the internal scheduler), and dispatches them through a Spawner that enforces two-tier concurrency control. Every session carries a UUIDv7 request_id for end-to-end tracing. Spawner timeouts are session-scoped; longer recovery workflows may chain multiple sessions under a separate orchestrator-owned deadline. Graceful shutdown drains in-flight sessions before tearing down modules in reverse topological order.

## Motivation

A butler must initialize database connections, telemetry, modules, migrations, tool registrations, and the scheduler in a deterministic order with well-defined failure semantics. The trigger-to-session pipeline must enforce concurrency limits at both the per-butler and global level to prevent resource exhaustion when multiple butlers fire simultaneously. Crash recovery must replay orphaned route inbox entries without duplication.

## Design

### Startup Phases

The daemon executes these phases in strict order. A failure at a fatal phase aborts startup. Module-phase failures (phases 8, 11, and 14) are non-fatal — the butler continues with the failed module marked unavailable and its dependents cascade-failed.

| Phase | Action | Failure Mode |
|-------|--------|--------------|
| 1 | Load config from `butler.toml` and configure structured logging | Fatal -- no config, no butler |
| 2 | Initialize telemetry/metrics and scan config values for inline secrets | Telemetry falls back to no-op providers; secret findings warn |
| 3 | Initialize modules via topological sort and skip modules whose required config is omitted | Fatal if dependency cycle detected |
| 4 | Validate module config schemas (Pydantic) | Non-fatal per module |
| 5 | Validate `butler.env` credentials (env-only fast-fail for non-secret config) | Fatal on missing required env vars |
| 6 | Provision or receive the database pool and assign `db.owner_butler` from configured identity | Fatal |
| 7 | Run core and butler-specific Alembic migrations | Fatal |
| 8 | Run module migrations; build CredentialStore; validate module credentials; initialize storage and bootstrap state | Module migration/credential failures are non-fatal; storage/bootstrap work is best-effort |
| 9 | Resolve runtime config from DB (seed from `[butler.runtime_seed]` on first boot) | Fatal -- cannot operate without runtime config |
| 10 | Sync TOML schedules to DB **before module startup** | Fatal -- establishes schedule provenance before recovery is evaluated |
| 11 | Call module `on_startup()` in topological order | Non-fatal (degraded -- failed module + dependents marked unavailable) |
| 12 | Create Spawner, audit/runtime wiring, and Switchboard client connection | Runtime setup is fatal; connection retry is non-fatal |
| 13 | Create FastMCP server and register core tools | Fatal |
| 14 | Register module MCP tools; apply approval gates; wire module runtime | Module tool failures are non-fatal |
| 15 | Start FastMCP SSE server on configured port | Fatal |
| 16 | Launch route recovery, Switchboard heartbeat, and internal scheduler loop | Background services are non-fatal |
| 17 | Start liveness reporter and mark the daemon accepting connections | Non-fatal |

### Graceful Shutdown

Shutdown executes in this order:

1. Stop the MCP server (stop accepting new connections).
2. Stop accepting new triggers.
3. Drain in-flight runtime sessions up to a configurable timeout.
4. Cancel Switchboard heartbeat task.
5. Close Switchboard MCP client.
6. Cancel scheduler loop (wait for in-progress `tick()` to finish).
7. Cancel liveness reporter loop.
8. Shut down modules in **reverse** topological order via `on_shutdown()`.
9. Close database pool.

### Trigger Sources

Two trigger sources converge at the Spawner:

**External MCP triggers:**

- `trigger(prompt, context?)` -- ad-hoc invocation from any MCP client. Trigger source recorded as `"trigger"`.
- `route.execute(envelope)` -- dispatched by the Switchboard. Persists to `route_inbox` in `accepted` state and returns `{"status": "accepted"}` immediately. A background task transitions the row through `processing` to `processed`/`errored`. Trigger source recorded as `"route"`.

**Scheduled triggers:**

- The scheduler evaluates cron expressions on every tick. Due tasks dispatch through the spawner with trigger source `"schedule:<task-name>"`.
- TOML-to-DB sync on startup: new tasks are inserted, changed tasks are updated, removed tasks are disabled. Runtime-created tasks (source `"db"`) are preserved.
- Deterministic stagger offset: SHA-256 of butler name, bounded by `min(max_stagger_seconds, cadence - 1s)`. Default `max_stagger_seconds` is 900 (15 minutes).

### Route Inbox State Machine

```
accepted --> processing --> processed (session_id recorded)
                       \--> errored   (error message stored)
```

On startup, the daemon scans for rows in `accepted` or `processing` state older than a configurable grace period (default 10 seconds) and claims them for recovery. Ordinary work is re-dispatched through its fenced claim; dashboard `processing` recovery follows the no-replay exception below. Both states are scanned because a crash can leave rows in either state with no completing task.

A `processing` row has an opaque ownership lease. Its worker MUST synchronously
verify/renew that lease before protected work and again immediately before it
constructs the runtime invocation; it heartbeats while live and uses the same
claim for terminal writes. A displaced worker MUST relinquish/cancel its local
work and MUST NOT settle the row terminally.

For a dashboard-sourced row linked to a durable message turn, reclaiming a
stale `processing` lease only proves that the predecessor is unprovable, not
dead. Recovery MUST mark that dashboard turn ambiguous and suppress automatic
replay, while ordinary non-dashboard recovery remains unchanged. A Stop may
still address exact durably registered predecessor sessions.

### Concurrency Control

The Spawner enforces two layers:

1. **Per-butler semaphore** -- `asyncio.Semaphore` sized by `max_concurrent_sessions` in `butler.toml` (default: 1, meaning serial dispatch).
2. **Global semaphore** -- Process-wide `asyncio.Semaphore` limiting total concurrent LLM sessions across all butlers. Default: 3, configurable via `BUTLERS_MAX_GLOBAL_SESSIONS`. Lazy-initialized on first access.

Both semaphores MUST be acquired before a session starts. When a trigger arrives and all slots are occupied, it queues behind the semaphore. Metrics track queue depth at both levels (see RFC 0005).

### Session Lifecycle

Once both semaphores are acquired:

1. A session row is created in the `sessions` table with: prompt, trigger source, model, request_id, optional complexity tier.
2. An ephemeral MCP config is generated pointing exclusively at this butler's FastMCP endpoint (see RFC 0002).
3. The runtime adapter (Claude Code, Codex, Gemini) is invoked with the config, prompt, system prompt, and the session-scoped timeout budget.
4. On return, the session is marked complete with: output, tool call records, duration, token usage, success/failure status.
5. Token usage is recorded against the model catalog for quota tracking.
6. Concurrency slots are released.

### Session Timeouts vs Workflow Deadlines

`session_timeout_s` bounds exactly one runtime invocation managed by the Spawner. It does not define the total lifetime of a higher-level workflow that may orchestrate multiple runtime sessions.

Recovery and investigation orchestrators (for example self-healing and QA) MAY chain multiple phases such as `diagnose`, `implement`, and `verify`. Each phase runs as its own session and is governed by `session_timeout_s`. The orchestrator is responsible for any broader workflow deadline, retry policy, and persisted phase state.

Admission-control outcomes that occur before a runtime session launches (for example cooldown, concurrency cap, circuit breaker, or no-model) are dispatch decisions, not execution failures. Systems that expose recovery state MUST track these separately from launched workflow outcomes.

#### Workflow Deadline Contract

When an orchestrator creates a multi-session recovery workflow, it MUST:

1. Compute `workflow_deadline_at = now() + configured_hard_limit` at row creation time.
2. Store this value in the persisted workflow record (`healing_attempts.workflow_deadline_at`) and NEVER update it subsequently.
3. Evaluate `workflow_deadline_at` — not `updated_at` — as the authoritative timeout reference during both watchdog monitoring and daemon restart recovery.

The immutability of `workflow_deadline_at` is required so that restart recovery can make timeout decisions without ambiguity: a row with `workflow_deadline_at IS NOT NULL` and `now() > workflow_deadline_at` is timed out, regardless of `updated_at`. A row within its deadline is preserved for the watchdog to evaluate.

Rows that predate the `workflow_deadline_at` column (legacy data) MUST have `workflow_deadline_at = NULL`. Restart recovery for such rows falls back to the `updated_at + timeout_minutes` heuristic as a best-effort approximation only.

#### Dispatch Decision vs Launched Execution

The Spawner MUST NOT create or mutate workflow attempt records for admission-control rejections. Specifically:

- Cooldown, concurrency-cap, circuit-breaker, and no-model rejections produce only a `dispatch_decision` record, never a `healing_attempts` row with status `failed`.
- A `healing_attempts` row is inserted only when the workflow's first runtime session is about to launch (after all gates pass and the worktree is ready).
- The status `dispatch_pending` does NOT exist in the persisted state machine. The novelty claim and row insertion are a single atomic operation: either the row exists in `investigating` state or it does not exist at all.

### Request Context

Every session carries a `request_id` in UUIDv7 format. Connector-sourced sessions inherit the request_id from the ingestion request context. Internally triggered sessions (tick, schedule, trigger) generate a fresh UUID. The request_id propagates to:

- Session records
- Tool call captures
- OpenTelemetry spans (see RFC 0005)
- Route envelope `request_context` field (see RFC 0003)

## Integration

- **RFC 0002:** Core and module tools are registered during phases 13-14.
- **RFC 0003:** `route.execute` triggers arrive from the Switchboard via the route inbox.
- **RFC 0005:** Telemetry is initialized at phase 2; trace context is injected into spawned processes.
- **RFC 0006:** Database provisioning and migrations execute at phases 6-8.

## Alternatives Considered

**Single semaphore instead of two-tier.** Rejected because per-butler serial dispatch is the common case (most butlers set `max_concurrent_sessions = 1`), and a single global semaphore would not enforce this butler-local constraint.

**Eager startup with lazy module init.** Rejected because modules may register tools that depend on database state established during migrations. Deferring module startup would create race conditions between tool registration and tool invocation.

**Background migration runner.** Rejected. Migrations must complete before the SSE server accepts connections, otherwise tool handlers could query tables that do not yet exist.
