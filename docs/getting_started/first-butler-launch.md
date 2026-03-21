# First Butler Launch

> **Purpose:** Guide you through launching a single butler, triggering it, and viewing the results.
> **Audience:** Developers who have completed the dev environment setup.
> **Prerequisites:** [Dev Environment](dev-environment.md)

## Overview

This page walks you through the concrete experience of starting a butler, sending it a prompt, and seeing what happened. By the end, you will understand the basic interaction loop: start a daemon, trigger it, review the session log.

## Step 1: Start PostgreSQL

If not already running:

```bash
docker compose up -d postgres
```

## Step 2: Launch a Single Butler

You can start a single butler by pointing it at a configuration directory:

```bash
butlers run --config roster/general
```

This loads the `general` butler's configuration from `roster/general/butler.toml`, runs through the full startup sequence (config loading, telemetry init, database provisioning, migrations, module initialization, tool registration), and begins listening on its configured port (41101 for the general butler).

You will see log output as the butler progresses through its startup steps. Once you see a message indicating the FastMCP server is running, the butler is ready.

Alternatively, if you want to start just the Switchboard and one domain butler together:

```bash
butlers up --only switchboard,general
```

## Step 3: Trigger the Butler

The primary way to trigger a butler is through its MCP `trigger` tool. You can call this using any MCP-compatible client. The `trigger` tool accepts a prompt string and optional parameters:

```
Tool: trigger
Arguments:
  prompt: "What tools do you have available? List them briefly."
```

For testing purposes, the scheduler also triggers butlers. If the general butler has scheduled tasks configured in its `butler.toml` (for example, a morning briefing on `0 8 * * *`), those will fire automatically when their cron expressions match.

## Step 4: Understand What Happens

When a trigger arrives, the following sequence unfolds:

1. **Concurrency check** --- The spawner acquires a slot from the per-butler semaphore (and the global concurrency semaphore). If all slots are occupied, the request either queues or is rejected.

2. **Session record** --- A session record is created in the database with `status = running`, capturing the trigger source, prompt, and timestamp.

3. **MCP config generation** --- The spawner generates a locked-down MCP configuration that points the LLM CLI exclusively at this butler's MCP server. No other tools or servers are accessible.

4. **System prompt assembly** --- The butler's `CLAUDE.md` is read and composed with any memory context and routing instructions into a system prompt.

5. **Runtime invocation** --- The configured LLM CLI (e.g., `claude`) is spawned as a subprocess with the generated MCP config, system prompt, and user prompt. The CLI connects to the butler's MCP endpoint and begins reasoning.

6. **Tool calls** --- The LLM calls tools registered on the butler (state operations, module tools, schedule management, etc.). Each tool call is logged.

7. **Completion** --- When the LLM finishes (or hits the max turns limit), the spawner captures the output, token usage, and tool call history. The session record is updated to `completed`.

## Step 5: View the Session Log

Session logs are stored in PostgreSQL. You can view them through:

**The dashboard** (recommended): Navigate to the butler's page to see a list of sessions with timing, token usage, trigger source, and output summaries.

**MCP tools**: The butler exposes session management tools:

- `sessions_list` --- list recent sessions with pagination
- `sessions_get` --- get full details of a specific session by ID
- `sessions_summary` --- aggregate statistics (total sessions, token usage, success rates)
- `sessions_daily` --- daily session counts
- `top_sessions` --- sessions ranked by token usage or duration

## Scaffolding a New Butler

To create your own butler from scratch:

```bash
butlers init mybutler --port 41105
```

This creates a scaffold directory:

```
roster/mybutler/
  butler.toml       # Identity, port, schedules, modules
  CLAUDE.md         # System prompt / personality
  AGENTS.md         # Runtime agent notes
  .agents/skills/   # Skill definitions
  .claude -> .agents  # Claude Code compatibility symlink
```

Edit `butler.toml` to configure your butler's name, description, port, schedules, and modules. Edit `CLAUDE.md` to define its personality and instructions. Then start it:

```bash
butlers run --config roster/mybutler
```

For multi-schema deployments (the standard setup), make sure the scaffolded `butler.toml` has:

```toml
[butler.db]
name = "butlers"
schema = "mybutler"
```

## CLI Reference

```
butlers up [--only NAME ...]    Start all (or filtered) butler daemons
butlers run --config PATH       Start a single butler from a config directory
butlers list [--dir PATH]       List discovered butler configurations and status
butlers init NAME --port PORT   Scaffold a new butler config directory
butlers dashboard [--port PORT] Start the Dashboard API server
butlers db migrate [--only ...]  Run Alembic migrations for butler schemas
butlers db provision [--only ...] Provision PostgreSQL databases
```

## Related Pages

- [Butler Lifecycle](../concepts/butler-lifecycle.md) --- detailed explanation of the daemon startup and session cycle
- [Dashboard Access](dashboard-access.md) --- using the web UI
- [Trigger Flow](../concepts/trigger-flow.md) --- how triggers become sessions
