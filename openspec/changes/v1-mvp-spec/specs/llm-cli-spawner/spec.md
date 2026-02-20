# LLM CLI Spawner

The LLM CLI Spawner manages ephemeral LLM CLI instances via the Claude Agent SDK. It is a core component present in every butler, responsible for generating locked-down MCP configurations, spawning Claude Code sessions, logging results, and cleaning up temporary resources.

## ADDED Requirements

### Requirement: Ephemeral MCP config generation

The LLM CLI Spawner SHALL generate a temporary MCP configuration file before each LLM CLI invocation. The config MUST contain only the owning butler's MCP endpoint, ensuring the spawned runtime instance cannot access any other butler's tools.

The config file SHALL be written to `/tmp/butler_<name>_<uuid>/mcp.json` where `<name>` is the butler's name and `<uuid>` is a unique identifier for the invocation.

The config JSON MUST conform to the following structure:

```json
{
  "mcpServers": {
    "<butler-name>": {
      "url": "http://localhost:<port>/sse"
    }
  }
}
```

#### Scenario: Config is generated with correct butler endpoint

WHEN the LLM CLI Spawner is triggered on a butler named "health" running on port 8001
THEN a directory `/tmp/butler_health_<uuid>/` SHALL be created
AND a file `mcp.json` SHALL be written to that directory
AND the file SHALL contain a single MCP server entry keyed by "health"
AND the entry's URL SHALL be "http://localhost:8001/sse"

#### Scenario: Config contains only the owning butler

WHEN the LLM CLI Spawner generates an MCP config for butler "relationship"
THEN the `mcpServers` object MUST contain exactly one entry
AND that entry MUST be keyed by "relationship"

#### Scenario: Each invocation gets a unique temp directory

WHEN the LLM CLI Spawner is triggered twice on the same butler
THEN each invocation MUST use a distinct temp directory with a different UUID

---

### Requirement: LLM CLI invocation via SDK

The LLM CLI Spawner SHALL spawn Claude Code using the `claude_agent_sdk.query` function with the generated MCP config, the butler's CLAUDE.md as system prompt, and the butler's config directory as the working directory.

```python
from claude_agent_sdk import query

result = await query(
    prompt=task_prompt,
    options={
        "system_prompt": butler_claude_md,
        "mcp_config": "/tmp/butler_<name>_<uuid>/mcp.json",
        "cwd": str(butler_config_dir),
        "max_turns": 20,
    }
)
```

#### Scenario: CC is spawned with correct parameters

WHEN the LLM CLI Spawner is triggered with prompt "Check overdue tasks"
THEN it SHALL call `claude_agent_sdk.query` with `prompt` set to "Check overdue tasks"
AND `options.system_prompt` SHALL be the contents of the butler's `CLAUDE.md` file
AND `options.mcp_config` SHALL be the path to the generated ephemeral MCP config file
AND `options.cwd` SHALL be the butler's config directory path

#### Scenario: Default max_turns is 20

WHEN the LLM CLI Spawner is triggered without specifying max_turns
THEN `options.max_turns` SHALL default to 20

#### Scenario: max_turns is configurable per trigger call

WHEN the LLM CLI Spawner is triggered with max_turns set to 5
THEN `options.max_turns` SHALL be 5

---

### Requirement: runtime instance capabilities

A spawned LLM CLI instance SHALL have access to all MCP tools registered on the owning butler (core and module tools), bash access for running skill scripts from the `skills/` directory, and file read/write access within the butler's config directory. The runtime instance MUST NOT have access to any other butler's MCP servers.

#### Scenario: CC can call butler MCP tools

WHEN a runtime instance is spawned for a butler with state store and scheduler tools registered
THEN the runtime instance SHALL be able to call `state_get`, `state_set`, `schedule_list`, and all other registered tools on that butler

#### Scenario: CC can run skill scripts

WHEN a runtime instance is spawned for a butler with a `skills/daily-review/` skill directory
THEN the runtime instance SHALL have bash access to execute scripts within `skills/daily-review/`

#### Scenario: CC cannot access other butlers

WHEN a runtime instance is spawned for butler "health"
THEN the MCP config SHALL NOT contain entries for "switchboard", "relationship", "general", or any other butler

---

### Requirement: Serial dispatch

The LLM CLI Spawner MUST dispatch LLM CLI instances serially in v1. Only one runtime instance SHALL be active at a time per butler. If a trigger arrives while another runtime instance is running, it MUST wait until the current instance completes before dispatching.

#### Scenario: Concurrent triggers are serialized

WHEN two triggers arrive simultaneously for the same butler
THEN the first trigger SHALL spawn a runtime instance immediately
AND the second trigger SHALL wait until the first runtime instance completes before spawning

#### Scenario: Serial dispatch uses asyncio lock

WHEN the LLM CLI Spawner is initialized
THEN it SHALL hold an asyncio lock to enforce serial dispatch
AND the lock SHALL be acquired before spawning and released after cleanup

---

### Requirement: Session logging

The LLM CLI Spawner SHALL log every runtime invocation to the butler's `sessions` table, regardless of whether the invocation succeeded or failed. The session record MUST include the prompt, recorded tool calls, output, success status, error details (if any), duration, and trace ID.

#### Scenario: Successful session is logged

WHEN a runtime instance completes successfully with output "Done. 3 tasks checked."
THEN a session record SHALL be inserted into the `sessions` table
AND the record's `success` field SHALL be `true`
AND the record's `output` field SHALL contain "Done. 3 tasks checked."
AND the record's `error` field SHALL be `null`

#### Scenario: Failed session is logged

WHEN a runtime instance fails with error "SDK timeout after 300s"
THEN a session record SHALL be inserted into the `sessions` table
AND the record's `success` field SHALL be `false`
AND the record's `error` field SHALL contain "SDK timeout after 300s"

#### Scenario: Session records duration

WHEN a runtime instance runs for 12.5 seconds
THEN the session record's duration SHALL reflect approximately 12.5 seconds

#### Scenario: Session records tool calls

WHEN a runtime instance calls `state_get(key="tasks")` and `state_set(key="last_check", value="2026-02-09")`
THEN the session record's `tool_calls` list SHALL contain both tool call records

---

### Requirement: Temp directory cleanup

The LLM CLI Spawner MUST clean up the temporary directory (`/tmp/butler_<name>_<uuid>/`) after every invocation, regardless of whether the runtime instance succeeded or failed. Cleanup MUST occur even if the CC SDK raises an exception.

#### Scenario: Temp directory is cleaned up on success

WHEN a runtime instance completes successfully
THEN the temp directory `/tmp/butler_<name>_<uuid>/` SHALL be deleted
AND the `mcp.json` file within it SHALL no longer exist on disk

#### Scenario: Temp directory is cleaned up on failure

WHEN a runtime instance raises an exception
THEN the temp directory SHALL still be deleted
AND no orphaned temp directories SHALL remain

#### Scenario: Cleanup uses try/finally

WHEN the LLM CLI Spawner dispatches a runtime instance
THEN temp directory cleanup SHALL execute in a `finally` block to guarantee execution regardless of outcome

---

### Requirement: Trace context propagation

The LLM CLI Spawner SHALL propagate trace context to the spawned runtime instance by setting the `TRACEPARENT` environment variable. This enables end-to-end distributed tracing from the trigger source through CC execution.

#### Scenario: TRACEPARENT is set for runtime instance

WHEN the LLM CLI Spawner is triggered within an active OpenTelemetry trace
THEN the `TRACEPARENT` environment variable SHALL be set for the spawned CC process
AND the value SHALL conform to the W3C Trace Context format

#### Scenario: Missing trace context does not block spawn

WHEN the LLM CLI Spawner is triggered without an active trace context
THEN the runtime instance SHALL still be spawned successfully
AND no `TRACEPARENT` variable SHALL be set

---

### Requirement: MCP trigger tool

The LLM CLI Spawner SHALL expose an MCP tool named `trigger` with the signature `trigger(prompt: str, context: str | None = None) -> SpawnerResult`. The `context` parameter provides optional additional context prepended to the prompt. The tool SHALL return a `SpawnerResult` containing the output, recorded tool calls, success status, and error (if any).

#### Scenario: Trigger with prompt only

WHEN the `trigger` tool is called with `prompt="Summarize today's health data"`
THEN the LLM CLI Spawner SHALL spawn a runtime instance with that prompt
AND return a `SpawnerResult` with the runtime output

#### Scenario: Trigger with context

WHEN the `trigger` tool is called with `prompt="Process this"` and `context="User sent: hello"`
THEN the LLM CLI Spawner SHALL include the context in the prompt sent to CC

#### Scenario: Trigger returns SpawnerResult on success

WHEN the `trigger` tool is called and CC completes successfully
THEN the returned `SpawnerResult.success` SHALL be `true`
AND `SpawnerResult.output` SHALL contain the runtime output text
AND `SpawnerResult.tool_calls` SHALL list all MCP tool calls made during the session
AND `SpawnerResult.error` SHALL be `None`

#### Scenario: Trigger returns SpawnerResult on failure

WHEN the `trigger` tool is called and CC fails with an error
THEN the returned `SpawnerResult.success` SHALL be `false`
AND `SpawnerResult.error` SHALL contain the error message
AND `SpawnerResult.output` SHALL contain any partial output produced before the failure

---

### Requirement: SpawnerResult dataclass

The LLM CLI Spawner SHALL define a `SpawnerResult` dataclass with the following fields:

- `output: str` -- the text output produced by the runtime instance
- `tool_calls: list` -- a list of recorded MCP tool calls made during the session
- `success: bool` -- whether the runtime invocation completed without error
- `error: str | None` -- the error message if the invocation failed, otherwise `None`

#### Scenario: SpawnerResult for a successful invocation

WHEN a runtime instance completes successfully with output "All tasks done" and 3 tool calls
THEN the `SpawnerResult` SHALL have `output="All tasks done"`, `tool_calls` with 3 entries, `success=True`, and `error=None`

#### Scenario: SpawnerResult for a failed invocation

WHEN a runtime instance fails with error "Connection refused"
THEN the `SpawnerResult` SHALL have `success=False` and `error="Connection refused"`

---

### Requirement: MockSpawner for testing

The framework SHALL provide a `MockSpawner` class that replaces the real LLM CLI Spawner in tests. The `MockSpawner` MUST record all invocations (prompt, context) and return canned responses matched by prompt substring. It SHALL provide assertion helpers for test verification.

#### Scenario: MockSpawner records invocations

WHEN `MockSpawner.trigger(prompt="Check tasks")` is called
THEN the invocation SHALL be recorded in the MockSpawner's invocation log
AND the log entry SHALL contain the prompt "Check tasks"

#### Scenario: MockSpawner returns canned response by prompt substring

WHEN the MockSpawner is configured with a canned response for substring "health"
AND `trigger(prompt="Check health data")` is called
THEN the MockSpawner SHALL return the canned response associated with "health"

#### Scenario: MockSpawner assert_triggered helper

WHEN the MockSpawner has been triggered 3 times
THEN `assert_triggered(times=3)` SHALL pass
AND `assert_triggered(times=2)` SHALL raise an AssertionError

#### Scenario: MockSpawner assert_prompted_with helper

WHEN the MockSpawner has been triggered with prompt "Review contacts for birthdays"
THEN `assert_prompted_with("birthdays")` SHALL pass
AND `assert_prompted_with("medications")` SHALL raise an AssertionError

#### Scenario: MockSpawner default response

WHEN the MockSpawner is triggered with a prompt that matches no canned response
THEN it SHALL return a default `SpawnerResult` with `success=True`, an empty `output`, an empty `tool_calls` list, and `error=None`
