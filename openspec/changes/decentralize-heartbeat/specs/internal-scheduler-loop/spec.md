# Internal Scheduler Loop

The internal scheduler loop is a core asyncio background task in every butler daemon. It calls the scheduler's `tick()` function at a fixed interval, making each butler self-sufficient for cron-based task execution. No external caller is needed to drive scheduled tasks.

## ADDED Requirements

### Requirement: Daemon starts an internal scheduler loop after server is ready

The butler daemon SHALL start an asyncio background task (the "scheduler loop") after the FastMCP server begins accepting connections. The scheduler loop SHALL call the scheduler's `tick()` function at a configurable interval (default 60 seconds). The loop SHALL run for the lifetime of the daemon until shutdown.

#### Scenario: Scheduler loop starts on daemon startup

WHEN the butler daemon completes its startup sequence and the FastMCP server is listening
THEN an asyncio background task for the scheduler loop MUST be started
AND the loop MUST call `tick()` within the first interval period (default 60 seconds)

#### Scenario: Scheduler loop calls tick periodically

WHEN the scheduler loop is running with the default interval of 60 seconds
THEN it MUST call the scheduler's `tick()` function approximately every 60 seconds
AND each call MUST behave identically to a manual `tick()` invocation (query due tasks, dispatch serially)

#### Scenario: Scheduler loop continues after tick errors

WHEN the scheduler loop calls `tick()` and the call raises an exception
THEN the exception MUST be logged with full traceback
AND the scheduler loop MUST NOT terminate
AND the next `tick()` call MUST occur after the configured interval

---

### Requirement: Scheduler loop interval is configurable

The scheduler loop interval SHALL be configurable via an optional `[butler.scheduler]` section in `butler.toml`. If the section or the `tick_interval_seconds` key is absent, the interval SHALL default to 60 seconds.

#### Scenario: Default interval when config is absent

WHEN the butler starts with a `butler.toml` that has no `[butler.scheduler]` section
THEN the scheduler loop MUST use an interval of 60 seconds

#### Scenario: Custom interval from config

WHEN the butler starts with a `butler.toml` containing:
```toml
[butler.scheduler]
tick_interval_seconds = 30
```
THEN the scheduler loop MUST use an interval of 30 seconds

#### Scenario: Invalid interval rejected

WHEN the butler starts with `tick_interval_seconds` set to 0 or a negative number
THEN the daemon MUST raise a validation error at startup
AND the daemon MUST NOT proceed to server startup

---

### Requirement: Scheduler loop is cancelled during graceful shutdown

The scheduler loop asyncio task SHALL be cancelled during the daemon's graceful shutdown sequence. Cancellation SHALL occur before module `on_shutdown()` calls, to prevent `tick()` from dispatching new tasks while modules are shutting down.

#### Scenario: Shutdown cancels the scheduler loop

WHEN the daemon receives a shutdown signal (SIGTERM/SIGINT)
THEN the scheduler loop asyncio task MUST be cancelled before `on_shutdown()` is called on any module
AND if a `tick()` call is in progress, it MUST be allowed to complete before cancellation takes effect

#### Scenario: No new tasks dispatched after shutdown initiated

WHEN the daemon has initiated shutdown and the scheduler loop has been cancelled
THEN no further `tick()` calls SHALL be made
AND any tasks that become due during shutdown SHALL NOT be dispatched
