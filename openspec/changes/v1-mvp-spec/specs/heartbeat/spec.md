# Heartbeat Butler

The Heartbeat butler is an infrastructure butler that calls `tick()` on every registered butler on a periodic cycle (default every 10 minutes). It has no modules — only core components. The heartbeat cycle is itself a scheduled task: when due, the scheduler dispatches a prompt to an ephemeral LLM CLI instance, which queries the Switchboard for all registered butlers and calls `tick()` on each one via the Switchboard's `route()` tool.

## Config

```toml
[butler]
name = "heartbeat"
description = "Infrastructure butler. Calls tick() on all registered butlers every 10 minutes."
port = 8199

[butler.db]
name = "butler_heartbeat"

[[butler.schedule]]
name = "heartbeat-cycle"
cron = "*/10 * * * *"
prompt = "Query the Switchboard for all registered butlers via list_butlers(). Call tick() on each one. Log results."
```

## ADDED Requirements

### Requirement: Heartbeat butler configuration

The Heartbeat butler SHALL be configured as a core-only butler with no modules, a dedicated database, and a single scheduled task for the heartbeat cycle.

#### Scenario: Heartbeat butler starts with valid config

WHEN the Heartbeat butler starts with the `butler.toml` shown above
THEN it MUST bind its MCP server to port 8199
AND it MUST provision and connect to the `butler_heartbeat` PostgreSQL database
AND it MUST expose only core MCP tools (no module-specific tools)

#### Scenario: Heartbeat butler has no modules

WHEN the Heartbeat butler's configuration is loaded
THEN the modules list MUST be empty
AND no module `register_tools()`, `migration_revisions()`, `on_startup()`, or `on_shutdown()` hooks SHALL be invoked

---

### Requirement: Heartbeat cycle is a scheduled task

The heartbeat cycle SHALL be defined as a `[[butler.schedule]]` entry in `butler.toml` and dispatched to an ephemeral LLM CLI instance by the task scheduler, just like any other scheduled task.

#### Scenario: Scheduler syncs the heartbeat-cycle task on startup

WHEN the Heartbeat butler starts and syncs TOML tasks to the database
THEN a scheduled task with name `heartbeat-cycle` and cron expression `*/10 * * * *` MUST exist in the `scheduled_tasks` table
AND the task's `source` MUST be `toml`

#### Scenario: Scheduler dispatches the heartbeat-cycle prompt to CC

WHEN `tick()` is called on the Heartbeat butler and the `heartbeat-cycle` task is due
THEN the scheduler MUST dispatch the task's prompt to the LLM CLI Spawner
AND the LLM CLI Spawner MUST spawn an ephemeral LLM CLI instance with that prompt

---

### Requirement: CC enumerates butlers via the Switchboard

The ephemeral LLM CLI instance spawned for the heartbeat cycle SHALL call `list_butlers()` on the Switchboard to discover all registered butlers before ticking them.

#### Scenario: CC calls list_butlers() and receives the butler registry

WHEN the heartbeat-cycle runtime instance calls `list_butlers()` via the Switchboard
THEN it MUST receive a list of all registered butlers, including each butler's name and endpoint
AND the list MUST reflect the current state of the Switchboard's butler registry

---

### Requirement: CC ticks each butler via the Switchboard

The ephemeral LLM CLI instance SHALL call `tick()` on each registered butler by invoking the Switchboard's `route()` tool, targeting each butler in turn.

#### Scenario: CC routes a tick to a registered butler

WHEN the heartbeat-cycle runtime instance calls `route(butler_name, "tick", {})` via the Switchboard for a given butler
THEN the Switchboard MUST forward the `tick()` call to the target butler's MCP server
AND the target butler's tick handler MUST execute (checking for due scheduled tasks)

#### Scenario: All registered butlers are ticked in a single cycle

WHEN the heartbeat-cycle runtime instance has retrieved the list of registered butlers
THEN it MUST call `tick()` on every butler in the list (except itself)
AND each `tick()` call MUST be routed through the Switchboard's `route()` tool

---

### Requirement: Heartbeat butler does not tick itself

The Heartbeat butler SHALL NOT be ticked by its own heartbeat cycle. This prevents an infinite loop where ticking itself triggers another heartbeat-cycle dispatch.

#### Scenario: Heartbeat butler is excluded from the tick list

WHEN the heartbeat-cycle runtime instance retrieves the list of registered butlers from `list_butlers()`
THEN it MUST skip any butler with the name `heartbeat`
AND it MUST NOT call `route("heartbeat", "tick", {})` during the heartbeat cycle

#### Scenario: Heartbeat butler is registered but not self-ticked

WHEN the Heartbeat butler is registered in the Switchboard's butler registry
AND the heartbeat-cycle runtime instance enumerates butlers
THEN the Heartbeat butler MUST appear in the `list_butlers()` response (it is a valid registered butler)
BUT the heartbeat-cycle runtime instance MUST NOT invoke `tick()` on it

---

### Requirement: Error resilience during the tick cycle

If a butler fails to respond to `tick()`, the error SHALL be logged but the cycle MUST continue to the remaining butlers. A single butler failure MUST NOT abort the entire heartbeat cycle.

#### Scenario: One butler fails to respond to tick

WHEN the heartbeat-cycle runtime instance calls `tick()` on butler A and butler A returns an error or times out
THEN the error MUST be logged (including the butler name and the error detail)
AND the runtime instance MUST proceed to call `tick()` on the next butler in the list

#### Scenario: Multiple butlers fail during a single cycle

WHEN the heartbeat-cycle runtime instance calls `tick()` on butlers A, B, C, and D, and butlers B and D fail
THEN butlers A and C MUST still be ticked successfully
AND errors for butlers B and D MUST each be logged
AND the cycle MUST complete (not abort early)

#### Scenario: All butlers fail

WHEN every registered butler fails to respond to `tick()`
THEN each failure MUST be logged individually
AND the heartbeat cycle MUST complete normally (the session finishes, results are logged)
AND the Heartbeat butler MUST remain operational for the next cycle

---

### Requirement: Heartbeat cycle results are logged

The results of each heartbeat cycle SHALL be logged via the session log, recording which butlers were ticked, which succeeded, and which failed.

#### Scenario: Successful cycle with no failures

WHEN the heartbeat-cycle runtime instance completes a cycle in which all butlers respond successfully
THEN the session log entry MUST record the list of butlers that were ticked
AND the session log entry MUST indicate success for each butler

#### Scenario: Cycle with partial failures

WHEN the heartbeat-cycle runtime instance completes a cycle in which some butlers failed
THEN the session log entry MUST record which butlers succeeded and which failed
AND each failure MUST include the butler name and the error detail

#### Scenario: Session is logged with correct trigger source

WHEN the heartbeat-cycle task is dispatched and the runtime session completes
THEN the session log entry's `trigger_source` MUST be `schedule:heartbeat-cycle`

---

### Requirement: Heartbeat butler exposes only core MCP tools

The Heartbeat butler SHALL expose only the standard core MCP tools. It MUST NOT register any butler-specific or module-specific tools beyond the core set.

#### Scenario: Heartbeat butler tool inventory

WHEN a client calls `status()` on the Heartbeat butler
THEN the response MUST list only core tools: `status`, `tick`, `trigger`, `state_get`, `state_set`, `state_delete`, `state_list`, `schedule_list`, `schedule_create`, `schedule_update`, `schedule_delete`, `sessions_list`, `sessions_get`
AND no additional tools SHALL be present

---

### Requirement: Heartbeat butler uses a dedicated database

The Heartbeat butler SHALL own a dedicated PostgreSQL database named `butler_heartbeat`, containing only the core schema tables. No butler-specific Alembic version chain exists beyond the core chain.

#### Scenario: Database is provisioned on startup

WHEN the Heartbeat butler starts for the first time
THEN the `butler_heartbeat` database MUST be created if it does not already exist
AND the core Alembic chain MUST be applied, creating the `state`, `scheduled_tasks`, and `sessions` tables

#### Scenario: No butler-specific Alembic chain

WHEN the Heartbeat butler applies Alembic migrations on startup
THEN only the core Alembic chain SHALL be applied
AND no butler-specific Alembic version chain SHALL exist or be required for the Heartbeat butler

---

### Requirement: Heartbeat butler runtime instance has Switchboard access

The ephemeral LLM CLI instance spawned for the heartbeat cycle MUST be able to call the Switchboard's MCP tools (`list_butlers`, `route`) in addition to the Heartbeat butler's own core tools.

#### Scenario: CC MCP config includes the Switchboard endpoint

WHEN the Heartbeat butler's LLM CLI Spawner generates the ephemeral MCP config for the heartbeat-cycle task
THEN the generated MCP config MUST include the Switchboard's MCP server endpoint
AND the runtime instance MUST be able to call `list_butlers()` and `route()` on the Switchboard

#### Scenario: CC calls Switchboard tools during heartbeat cycle

WHEN the heartbeat-cycle runtime instance executes
THEN it MUST be able to invoke `list_butlers()` on the Switchboard to enumerate butlers
AND it MUST be able to invoke `route(butler_name, "tick", {})` on the Switchboard to tick each butler
