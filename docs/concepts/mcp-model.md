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
5. Apply approval wrappers to configured tools.
6. Finalize an immutable value catalog from the resulting public FastMCP definitions.
7. Start the SSE server.

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
- **`resolve_skill_identity(config_dir, skill_name)`** --- Resolves a skill to its canonical
  `.agents/skills/<name>/SKILL.md` identity and content digest. A `.claude/skills` compatibility
  alias is recorded only when it resolves to that same physical source; missing or unreadable
  canonical sources fail explicitly.
- **`read_agents_md` / `write_agents_md` / `append_agents_md`** --- Read/write access to `AGENTS.md`, the runtime agent notes file that LLM sessions can use for persistent inter-session memory.

Skill names must follow kebab-case: start with a lowercase letter, allow lowercase letters, digits, and hyphens between segments. The pattern is `^[a-z][a-z0-9]*(-[a-z0-9]+)*$`.

## Ephemeral MCP Config

When the spawner invokes an LLM session, it generates a temporary MCP configuration that points exclusively at the butler's own MCP endpoint. The config includes the butler's MCP URL (SSE endpoint) with a `runtime_session_id` query parameter for tool call attribution, and nothing else. The LLM is sandboxed to its own butler's tools, maintaining the architectural boundary where inter-butler communication flows exclusively through the Switchboard.

## Tool Sensitivity Metadata

The `ToolMeta` dataclass allows modules to declare per-argument sensitivity information via `arg_sensitivities`. This is used by the approvals module to determine which tool calls require human approval before execution. Arguments not explicitly listed fall back to a heuristic-based sensitivity classifier.

## Registered Definition Catalog

The daemon owns one immutable catalog per startup generation. It is finalized after approval
wrapping, so its model-visible input schemas and descriptions describe the same final definitions
served by FastMCP. Catalog entries record canonical name, module, group, namespace,
LLM-presentability, eager/deferred posture, sensitivity declarations, immutable definition values,
and stable digests. The checked-in inventory is validated against an executable union of core,
built-in module, and roster-module registrars, including their finite name/type/group/config gates.

An unclassified legacy tool remains presentable and eager for compatibility, while the catalog
marks classification incomplete. Classification never adds a handler or grants call authority.
Infrastructure-only entries remain in canonical FastMCP `tools/list` and remain callable by their
existing authenticated callers even though their metadata marks them unsuitable for future LLM
presentation.

The adapter work described by RFC 0027 is not implemented in this slice. No runtime host consumes
the catalog yet, and there is no host allowlist, search corpus, deferred schema loading, provider
admission, or native Tool Search activation. Spawned sessions therefore retain the existing
canonical MCP behavior until that separately reviewed adapter work lands.

## Verification

To confirm the MCP model described here matches the running system:

```bash
# 1. Butler's SSE endpoint accepts connections on the expected port
curl -s --max-time 2 -N http://localhost:41101/sse 2>&1 | head -3
# Expected: "data: ..." SSE stream, not ECONNREFUSED

# 2. Core tools are registered and respond
# Call the status tool via an MCP client, or inspect the dashboard:
curl -s http://localhost:41200/api/butlers/general/status | python3 -m json.tool
# Expected: "name", "modules", "uptime", "health" fields present

# 3. Module tools appear in tool listings
# Use an MCP client to list tools on the general butler's MCP server.
# Expected: core tools (status, trigger, route.execute) plus module tools
# (e.g., memory_store, memory_search if memory module is loaded)

# 4. Tool call logging proxy captured calls in session records
# After triggering a session, inspect tool_calls in the session record:
curl -s http://localhost:41200/api/butlers/general/sessions | python3 -m json.tool
# Expected: tool_calls array with tool_name, module_name, outcome, duration_ms fields

# 5. Ephemeral MCP config points only at this butler
# Check that a spawned LLM session cannot call tools on another butler.
# In session output, any attempt to reach another MCP server should fail
# (the ephemeral config has no other server entries).
```

## Implementation Notes

- RFC 0002 Amendment 1 and RFC 0027 separate the full registered MCP catalog from the initially
  loaded working set: the 30-50 tool target constrains initial presentation, not registered
  handlers. Reconcile older roster tool ceilings against that before deleting tools or raising
  limits.

## Related Pages

- [Trigger Flow](trigger-flow.md) --- how triggers create sessions that connect to the MCP server
- [Modules and Connectors](modules-and-connectors.md) --- the module lifecycle and dependency resolution
- [Tool Call Capture](../runtime/tool-call-capture.md) --- how tool executions are recorded for session logs
- [LLM CLI Spawner](../runtime/spawner.md) --- how ephemeral MCP configs are generated

## Private fact report admission

The source daemon freezes canonical accepted-row attribution before classification or routing.
A constructor-owned source registry selects the fixed Relationship resolver and locks the
public entity lifetime witness. A registered invocation supplies `X-Butlers-Fact-Invocation`;
a routed hop supplies `X-Butlers-Fact-Source`, verified online at the configured source daemon.
These ephemeral values bind the target, process incarnation, invocation and expiry. They never
appear in tool arguments, prompt context, URL query parameters or durable receipts. A runtime
session locator, copied entity UUID or model-supplied `verified` flag cannot mint admission.
All supported MCP runtime adapters carry the private headers. Guards clear private context on
completion, failure and cancellation; accepted-row recovery preserves the original report and
requires a current receiver processing claim. Restored-history admission is not established
by these ephemeral registries.

Relationship's public fact tool has no authority, author, `src` or `verified` selector. Internal
no-request writers preserve their existing SYSTEM policy. Actual owner HTTP requests use the
adopted OwnerAuth request-admission point: a later revocation may follow an already admitted
operation. This is not custody currentness or cross-connection commit atomicity. Approval
execution first binds the original stored tool/argument digest and executor task, then privately
normalizes obsolete keywords; fact commit and terminal acknowledgment remain separate outcomes.
