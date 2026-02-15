# Scheduling — Timer-Driven Flows and Cron Lifecycle

## Overview

Not all butler triggers come from external messages. The scheduler subsystem
drives time-based triggers: cron-scheduled tasks, heartbeat ticks, and
periodic maintenance jobs. These are fundamentally different from message-driven
flows — they originate inside the system, run on timers, and must be idempotent.
Scheduling E2E tests validate the full lifecycle of timer-driven flows.

## Trigger Origins

| Origin | Entry Point | Trigger Source | How It Starts |
|--------|-------------|----------------|---------------|
| External message | `ingest_v1()` → classify → dispatch | `"external"` | User sends Telegram/Email/API message |
| Heartbeat tick | `_tick(pool)` on heartbeat butler | `"heartbeat"` | Heartbeat butler's 10-min cron |
| Scheduled task | `_tick(pool)` on any butler | `"scheduled"` | Butler's own cron schedules |
| Test harness | Direct `spawner.trigger()` | `"test"` | E2E test code |

Scheduling E2E tests focus on the second and third rows — triggers that
originate from within the system rather than from external input.

## Heartbeat Tick Flow

### Architecture

The heartbeat butler is a special butler that runs a 10-minute cron job. On
each tick, it calls `trigger()` on every butler registered in the switchboard's
`butler_registry`:

```
Heartbeat Butler (every 10 min)
    │
    ├─ _tick(heartbeat_pool)
    │   │
    │   ├─ Read scheduled_tasks WHERE due_at <= NOW()
    │   │
    │   └─ For each due task:
    │       │
    │       ├─ Resolve target butler from butler_registry
    │       │
    │       └─ route(switchboard_pool, target_butler, "trigger", {
    │              prompt: task.prompt,
    │              trigger_source: "heartbeat"
    │          })
    │           │
    │           └─ Target butler spawns CC session
    │               └─ CC executes task-specific tools
    │
    └─ Update scheduled_tasks: next_due_at = compute_next(cron_expr)
```

### E2E Heartbeat Tests

| Test | What It Validates |
|------|-------------------|
| Tick triggers all butlers | After `_tick()`, each registered butler has a new session with `trigger_source="heartbeat"` |
| Tick is idempotent | Running `_tick()` twice in quick succession does not duplicate sessions (task not due again) |
| Unavailable butler skipped | Kill one butler, run tick, verify other butlers still get triggered |
| Session metadata | Heartbeat-triggered sessions have correct `trigger_source`, `trace_id`, and `duration_ms` |

### Heartbeat Flow Test

```python
async def test_heartbeat_tick_triggers_all_butlers(butler_ecosystem):
    """Heartbeat tick should trigger every registered butler."""
    heartbeat = butler_ecosystem["heartbeat"]
    switchboard = butler_ecosystem["switchboard"]

    # Get registered butlers from registry
    butlers = await switchboard.pool.fetch(
        "SELECT name FROM butler_registry WHERE eligibility_state = 'active'"
    )
    butler_names = {row["name"] for row in butlers}

    # Record session counts before tick
    session_counts_before = {}
    for name in butler_names:
        if name in butler_ecosystem:
            count = await butler_ecosystem[name].pool.fetchval(
                "SELECT COUNT(*) FROM sessions"
            )
            session_counts_before[name] = count

    # Fire tick
    await _tick(heartbeat.pool)

    # Verify each butler got a new session
    for name in butler_names:
        if name in butler_ecosystem:
            count = await butler_ecosystem[name].pool.fetchval(
                "SELECT COUNT(*) FROM sessions"
            )
            assert count > session_counts_before[name], (
                f"Butler {name} did not receive heartbeat trigger"
            )
```

## Scheduled Task Lifecycle

### TOML Schedule Sync

Each butler declares cron schedules in its `butler.toml`:

```toml
[[schedule]]
name = "daily-summary"
cron = "0 9 * * *"
prompt = "Generate a summary of yesterday's activities"
enabled = true

[[schedule]]
name = "weekly-review"
cron = "0 10 * * 1"
prompt = "Review this week's health trends"
enabled = true
```

On startup, the daemon syncs these to the `scheduled_tasks` table:

```python
# src/butlers/core/scheduler.py
await sync_schedules(pool, config.schedules)
```

### Task State Machine

```
                sync_schedules()
TOML schedule ──────────────────► scheduled_tasks row
                                     │
                                     │  status: "pending"
                                     │  due_at: computed from cron
                                     │
                                     ▼
                              _tick() finds due task
                                     │
                                     │  status: "running"
                                     │
                                     ▼
                              spawner.trigger()
                                     │
                              ┌──────┴──────┐
                              │             │
                         (success)     (failure)
                              │             │
                              ▼             ▼
                     status: "completed"  status: "error"
                     next_due_at = ...   next_due_at = ...
                     (re-arms for        (re-arms, logged)
                      next cron cycle)
```

### E2E Schedule Lifecycle Tests

| Test | What It Validates |
|------|-------------------|
| TOML sync creates rows | After daemon start, `scheduled_tasks` has rows matching TOML schedules |
| TOML sync is idempotent | Restarting daemon does not duplicate schedule rows |
| Due task triggers | Set `due_at` to past, call `_tick()`, verify session created |
| Cron rearm | After successful tick, `due_at` advances to next cron cycle |
| Disabled schedule skipped | Schedule with `enabled=false` is not triggered |
| Schedule CRUD | `schedule_create()`, `schedule_update()`, `schedule_delete()` via MCP tools |

### Schedule Sync Test

```python
async def test_schedule_sync(butler_ecosystem):
    """TOML schedules should be synced to scheduled_tasks table."""
    health = butler_ecosystem["health"]

    # Read TOML schedules from config
    toml_schedules = health.daemon.config.schedules

    # Read DB schedules
    db_schedules = await health.pool.fetch("SELECT * FROM scheduled_tasks")

    # Every TOML schedule should have a corresponding DB row
    toml_names = {s.name for s in toml_schedules}
    db_names = {row["name"] for row in db_schedules}
    assert toml_names.issubset(db_names)
```

## Timer + External Trigger Interleaving

### Serial Dispatch Lock Interaction

The spawner's serial dispatch lock means that timer-triggered and
externally-triggered sessions cannot run concurrently on the same butler:

```
Timeline:
  t=0   External trigger arrives → acquires lock → CC session starts
  t=5   Scheduled task fires → blocks on lock
  t=30  External CC session completes → lock released
  t=30  Scheduled task acquires lock → CC session starts
  t=60  Scheduled CC session completes
```

### E2E Interleaving Tests

| Test | What It Validates |
|------|-------------------|
| Concurrent external + scheduled | Fire both simultaneously, both succeed serially |
| External blocks scheduled | External trigger holds lock, scheduled trigger queues |
| Scheduled blocks external | Scheduled trigger holds lock, external trigger queues |
| Neither starves | Under repeated alternating triggers, both sources get served |

### Interleaving Test

```python
async def test_timer_external_interleaving(butler_ecosystem):
    """External and scheduled triggers should serialize, not deadlock."""
    health = butler_ecosystem["health"]

    # Fire external trigger and tick concurrently
    external_task = asyncio.create_task(
        health.spawner.trigger("Log weight 80kg", trigger_source="external")
    )
    scheduled_task = asyncio.create_task(
        _tick(health.pool)
    )

    external_result, _ = await asyncio.gather(external_task, scheduled_task)

    # Both should complete (serial, not concurrent)
    assert external_result.success

    # Verify two sessions with different trigger_sources
    sessions = await health.pool.fetch(
        "SELECT trigger_source FROM sessions ORDER BY created_at"
    )
    sources = {row["trigger_source"] for row in sessions}
    assert "external" in sources
    # Scheduled trigger may or may not create a session depending on due_at
```

## Schedule CRUD via MCP Tools

Butlers expose schedule management via core MCP tools:

| Tool | Operation | Arguments |
|------|-----------|-----------|
| `schedule_create` | Create a new scheduled task | `name`, `cron`, `prompt`, `enabled` |
| `schedule_list` | List all scheduled tasks | (none) |
| `schedule_update` | Modify an existing task | `task_id`, `cron?`, `prompt?`, `enabled?` |
| `schedule_delete` | Remove a scheduled task | `task_id` |

### E2E CRUD Tests

| Test | What It Validates |
|------|-------------------|
| Create via MCP | Call `schedule_create` tool, verify row in `scheduled_tasks` |
| List includes new task | After create, `schedule_list` returns the new task |
| Update cron expression | Call `schedule_update`, verify `cron` and `due_at` changed |
| Delete removes task | Call `schedule_delete`, verify row gone |
| Create idempotency | Creating same-named task twice → error or upsert, not duplicate |

### CRUD Test

```python
async def test_schedule_crud_via_mcp(butler_ecosystem):
    """Schedule management tools should work end-to-end."""
    health = butler_ecosystem["health"]

    async with MCPClient(f"http://localhost:{health.port}/sse") as client:
        # Create
        result = await client.call_tool("schedule_create", {
            "name": "e2e-test-schedule",
            "cron": "0 */6 * * *",
            "prompt": "Run E2E test task",
            "enabled": True,
        })
        assert "e2e-test-schedule" in str(result)

        # List
        schedules = await client.call_tool("schedule_list", {})
        assert any("e2e-test-schedule" in str(s) for s in schedules)

        # Update
        await client.call_tool("schedule_update", {
            "name": "e2e-test-schedule",
            "enabled": False,
        })

        # Delete
        await client.call_tool("schedule_delete", {
            "name": "e2e-test-schedule",
        })

        # Verify deleted
        schedules = await client.call_tool("schedule_list", {})
        assert not any("e2e-test-schedule" in str(s) for s in schedules)
```

## Tick Idempotency

### The Idempotency Contract

Running `_tick()` should be idempotent within a scheduling period. If a task
has `cron = "0 9 * * *"` and `_tick()` runs at 09:01, the task fires. If
`_tick()` runs again at 09:02, the task should NOT fire again because its
`due_at` has been advanced to the next day.

### E2E Idempotency Tests

| Test | What It Validates |
|------|-------------------|
| Double tick no duplicate | `_tick()` × 2 in quick succession → only one session created |
| Tick after rearm | `_tick()`, advance clock past next `due_at`, `_tick()` again → two sessions |
| Manual due_at reset | Set `due_at` to past, tick, verify it fires and rearms |
