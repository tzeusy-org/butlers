# MCP Model

> **Purpose:** Explain how each butler exposes tools via MCP and how ephemeral LLM sessions interact with them.
> **Audience:** Developers building modules, extending core tools, or understanding the runtime tool surface.
> **Prerequisites:** [Butler Lifecycle](butler-lifecycle.md), [Trigger Flow](trigger-flow.md).

## Overview

Every butler is a long-running FastMCP server. Its tool surface is assembled from two layers: core tools (always present) and module tools (opt-in per butler). When a trigger fires, the spawner creates an ephemeral LLM CLI session that connects exclusively to its own butler's MCP endpoint. The LLM can only call tools registered on that butler --- it cannot reach other butlers directly.

## FastMCP Server

At startup, the butler daemon creates a `FastMCP` instance and binds it to an SSE (Server-Sent Events) HTTP server on the butler's configured port. The server remains running for the lifetime of the daemon process. All tool registrations happen during daemon initialization, before the server begins accepting connections.

The startup sequence for tool registration is:

1. Create the `FastMCP` instance with the butler's name.
2. Register core tools (status, trigger, route.execute, and others).
3. Resolve module dependency order via topological sort.
4. Call `register_tools(mcp, config, db)` on each enabled module in dependency order.
5. Start the SSE server.

## Core Tools

Every butler registers these core tools regardless of its module configuration:

- **`status()`** --- Returns butler identity, loaded modules, health, and uptime. This is the primary health-check endpoint used by the dashboard and monitoring.
- **`trigger(prompt, context?)`** --- Spawns a new LLM session with the given prompt. This is how external MCP clients (or other butlers via the Switchboard) invoke a butler.
- **`route.execute(...)`** --- Accepts routed requests from the Switchboard. Handles the full route envelope (schema version, request context, input, subrequest, source metadata, trace context) and dispatches to the spawner or the durable route inbox.

Core tools are wrapped with OpenTelemetry spans (`butler.tool.<name>`) and tool-call logging for session attribution.

## Module Tools

Modules add domain-specific tools by implementing the `Module` abstract base class from `src/butlers/modules/base.py`. The key method is `register_tools(mcp, config, db)`, where the module calls `mcp.tool()` to register its handlers. Examples of module tools include email send/search/read, Telegram messaging, calendar event management, memory store/search, and contact lookup/update.

Modules declare their dependencies via the `dependencies` property. The daemon resolves these using topological sort so that dependent modules are initialized after their prerequisites. A module only adds tools --- it never touches core infrastructure (scheduler, spawner, session log).

## Tool Call Logging Proxy

Module tool registrations pass through a `_ToolCallLoggingMCP` proxy rather than the raw FastMCP instance. This proxy intercepts every `mcp.tool()` call and wraps the handler with:

1. **OpenTelemetry span creation** --- a `butler.tool.<name>` span with `butler.name` attribute.
2. **Tool call capture** --- records the tool name, module name, input payload, outcome, and result in the session's tool call buffer. This is how the session log gets ground-truth tool execution data.
3. **Error handling** --- catches and logs exceptions from tool handlers without crashing the MCP server.

## Skills Infrastructure

Each butler can have a skills directory at `roster/<butler>/.agents/skills/`. Skills are directories containing a `SKILL.md` file that describes a capability the LLM can use. The skills infrastructure (`src/butlers/core/skills.py`) provides:

- **`read_system_prompt(config_dir, butler_name)`** --- Reads `CLAUDE.md` from the butler's config directory, resolves `<!-- @include path.md -->` directives relative to the roster directory, and appends shared snippets (`BUTLER_SKILLS.md`, `MCP_LOGGING.md`).
- **`get_skills_dir(config_dir)`** --- Returns the path to `.agents/skills/` if it exists.
- **`list_valid_skills(skills_dir)`** --- Lists skill directories with valid kebab-case names, warning and skipping invalid ones.
- **`read_agents_md` / `write_agents_md` / `append_agents_md`** --- Read/write access to `AGENTS.md`, the runtime agent notes file that LLM sessions can use for persistent inter-session memory.

Skill names must follow kebab-case: start with a lowercase letter, allow lowercase letters, digits, and hyphens between segments. The pattern is `^[a-z][a-z0-9]*(-[a-z0-9]+)*$`.

## Ephemeral MCP Config

When the spawner invokes an LLM session, it generates a temporary MCP configuration that points exclusively at the butler's own MCP endpoint. The config includes the butler's MCP URL (SSE endpoint) with a `runtime_session_id` query parameter for tool call attribution, and nothing else. The LLM is sandboxed to its own butler's tools, maintaining the architectural boundary where inter-butler communication flows exclusively through the Switchboard.

## Tool Sensitivity Metadata

The `ToolMeta` dataclass allows modules to declare per-argument sensitivity information via `arg_sensitivities`. This is used by the approvals module to determine which tool calls require human approval before execution. Arguments not explicitly listed fall back to a heuristic-based sensitivity classifier.

## Related Pages

- [Trigger Flow](trigger-flow.md) --- how triggers create sessions that connect to the MCP server
- [Modules and Connectors](modules-and-connectors.md) --- the module lifecycle and dependency resolution
- [Tool Call Capture](../runtime/tool-call-capture.md) --- how tool executions are recorded for session logs
- [LLM CLI Spawner](../runtime/spawner.md) --- how ephemeral MCP configs are generated
