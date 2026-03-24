# RFC 0002: MCP Tool Surface and Modules

**Status:** Accepted
**Date:** 2026-03-24

## Summary

Every butler is a long-running FastMCP SSE server whose tool surface is assembled from two layers: core tools (always present) and module tools (opt-in per butler). Modules implement the `Module` abstract base class and are resolved in topological dependency order. All tool registrations pass through a logging proxy that instruments each call with OpenTelemetry spans and session-attributed tool call capture. Ephemeral LLM sessions connect exclusively to their own butler's MCP endpoint via a generated config.

## Motivation

The tool surface defines the contract between a butler and the LLM instances it spawns. Separating core tools from module tools ensures every butler has a baseline capability set while allowing domain-specific extension without touching core infrastructure. The logging proxy provides ground-truth observability for every tool invocation without requiring modules to instrument themselves. Ephemeral MCP config generation enforces the architectural boundary where inter-butler communication flows exclusively through the Switchboard (see RFC 0003).

## Design

### FastMCP SSE Server

At startup (RFC 0001, phase 14), the daemon binds a `FastMCP` instance to an SSE HTTP server on the butler's configured port. The server remains running for the daemon's lifetime. All tool registrations complete before the server begins accepting connections.

### Core Tools

Every butler registers these tools regardless of module configuration:

| Tool | Signature | Purpose |
|------|-----------|---------|
| `status()` | `-> ButlerStatus` | Identity, loaded modules, health, uptime. Primary health-check endpoint. |
| `trigger(prompt, context?)` | `-> TriggerResult` | Spawn a new LLM session with the given prompt. |
| `route.execute(envelope)` | `-> {"status": "accepted"}` | Accept a routed request from the Switchboard (see RFC 0003). |
| `tick()` | `-> TickResult` | Internal scheduler tick (not exposed to LLM sessions). |
| `state_get(key)` | `-> value` | Read from KV state store. |
| `state_set(key, value)` | `-> void` | Write to KV state store. |
| `state_delete(key)` | `-> void` | Delete from KV state store. |
| `state_list(prefix?)` | `-> [key, ...]` | List state store keys. |
| `schedule_list()` | `-> [Schedule, ...]` | List scheduled tasks. |
| `schedule_create(...)` | `-> Schedule` | Create a scheduled task. |
| `schedule_update(...)` | `-> Schedule` | Update a scheduled task. |
| `schedule_delete(id)` | `-> void` | Delete a scheduled task. |
| `schedule_trigger(id)` | `-> TriggerResult` | Manually trigger a scheduled task. |
| `sessions_list(...)` | `-> [Session, ...]` | Query session history. |
| `sessions_get(id)` | `-> Session` | Get a single session. |
| `sessions_summary()` | `-> Summary` | Aggregate session statistics. |
| `sessions_daily()` | `-> [DaySummary, ...]` | Per-day session counts. |
| `top_sessions(...)` | `-> [Session, ...]` | Highest-cost sessions. |
| `schedule_costs()` | `-> CostBreakdown` | Cost attribution per schedule. |
| `notify(...)` | `-> DeliveryResult` | Send outbound notification via Switchboard. |
| `remind(...)` | `-> void` | Schedule a future reminder. |
| `get_attachment(id)` | `-> AttachmentData` | Retrieve an ingested attachment from blob storage. |
| `module.states()` | `-> ModuleStates` | List module enabled/disabled states. |
| `module.set_enabled(name, enabled)` | `-> void` | Toggle a module at runtime. |

Core tools are wrapped with OpenTelemetry spans (`butler.tool.<name>`) and tool-call logging for session attribution.

### Module ABC

Modules add domain-specific tools by implementing the `Module` abstract base class (`src/butlers/modules/base.py`):

```python
class Module(abc.ABC):
    @property
    def name(self) -> str: ...              # Unique module identifier
    @property
    def config_schema(self) -> type[BaseModel]: ...  # Pydantic config model
    @property
    def dependencies(self) -> list[str]: ...  # Names of prerequisite modules

    async def register_tools(self, mcp, config, db) -> None: ...
    def migration_revisions(self) -> str | None: ...
    async def on_startup(self, config, db, credential_store=None) -> None: ...
    async def on_shutdown(self) -> None: ...
    def tool_metadata(self) -> dict[str, ToolMeta]: ...
```

Key constraints:

- Modules MUST only add tools via `register_tools()`. They MUST NOT touch core infrastructure (scheduler, spawner, session log).
- Modules declare dependencies via the `dependencies` property. The daemon resolves these using topological sort, detecting cycles at startup (RFC 0001, phase 3).
- `on_startup()` receives an optional `CredentialStore` for DB-first credential resolution.
- `migration_revisions()` returns the Alembic branch label for the module's migration chain, or `None` if the module has no tables (see RFC 0006).

### Module Registry

The `ModuleRegistry` (`src/butlers/modules/registry.py`) maps module names to their implementing classes. A `default_registry()` function returns the built-in registry. Butler TOML config references modules by name; the registry resolves names to instances during phase 3.

### Tool Call Logging Proxy

Module tool registrations pass through `_ToolCallLoggingMCP` rather than the raw `FastMCP` instance. This proxy intercepts every `mcp.tool()` call and wraps the handler with:

1. **OpenTelemetry span creation** -- A `butler.tool.<name>` span with `butler.name` attribute. The span parent is resolved from the active session context (see RFC 0005).
2. **Tool call capture** -- Records tool name, module name, input payload, outcome (success/error), and result in the session's tool call buffer. This provides ground-truth tool execution data for session logs.
3. **Error handling** -- Catches and logs exceptions from tool handlers without crashing the MCP server. Errors are recorded on the OTel span with full stack traces.

### Tool Sensitivity Metadata

The `ToolMeta` dataclass allows modules to declare per-argument sensitivity:

```python
@dataclass
class ToolMeta:
    arg_sensitivities: dict[str, bool] = field(default_factory=dict)
```

Modules return a `dict[str, ToolMeta]` from `tool_metadata()`. The approvals module (RFC 0001, phase 13b) uses this metadata to determine which tool calls require human approval. Arguments not explicitly listed fall back to a heuristic-based sensitivity classifier.

### Skills Infrastructure

Each butler can have a skills directory at `roster/<butler>/.agents/skills/`. Skills are directories containing a `SKILL.md` file describing a capability the LLM can use.

The skills subsystem (`src/butlers/core/skills.py`) provides:

- **`read_system_prompt(config_dir, butler_name)`** -- Reads `CLAUDE.md` from the butler's config directory, resolves `<!-- @include path.md -->` directives relative to the roster directory, appends shared snippets (`BUTLER_SKILLS.md`, `MCP_LOGGING.md`).
- **`get_skills_dir(config_dir)`** -- Returns path to `.agents/skills/` if it exists.
- **`list_valid_skills(skills_dir)`** -- Lists skill directories with valid kebab-case names (pattern: `^[a-z][a-z0-9]*(-[a-z0-9]+)*$`), warning and skipping invalid names.
- **`read_agents_md` / `write_agents_md` / `append_agents_md`** -- Read/write access to `AGENTS.md`, the runtime agent notes file for persistent inter-session memory.

### Ephemeral MCP Config Generation

When the Spawner (RFC 0001) invokes an LLM session, it generates a temporary MCP configuration containing:

- The butler's MCP URL (SSE endpoint) with a `runtime_session_id` query parameter for tool call attribution.
- No other MCP servers.

The `runtime_session_id` query parameter allows the tool call logging proxy to attribute tool invocations to the correct session record, even when multiple sessions run concurrently (if `max_concurrent_sessions > 1`).

The LLM is sandboxed to its own butler's tools. It cannot reach other butlers directly. Inter-butler communication flows exclusively through the Switchboard (see RFC 0003).

### Approval Gates

During phase 13b, the daemon applies approval gates to configured tools. The `apply_approval_gates()` function wraps designated tool handlers with an approval check that:

1. Evaluates the tool call against standing approval rules.
2. If no rule matches, creates a pending approval action and blocks execution.
3. Returns the approval decision (approved/rejected/expired) to the caller.

Tool sensitivity metadata from `tool_metadata()` informs which arguments are safety-critical for approval rule matching.

## Integration

- **RFC 0001:** Tool registration occurs during daemon startup phases 12-13.
- **RFC 0003:** `route.execute` is a core tool that accepts Switchboard-routed envelopes.
- **RFC 0005:** All tools are instrumented via the logging proxy with OTel spans.
- **RFC 0006:** Module migrations are discovered and executed based on `migration_revisions()`.

## Alternatives Considered

**Direct tool registration without proxy.** Rejected because per-tool instrumentation would require every module to manually add OTel spans and tool call capture, leading to inconsistent observability and duplicated boilerplate.

**Peer-to-peer MCP between butlers.** Rejected in favor of Switchboard-mediated routing. Direct connections would create O(n^2) configuration complexity and eliminate the central audit/routing/identity resolution point.

**Dynamic tool registration after server start.** Rejected because FastMCP does not support hot-adding tools to a running SSE server. All tools MUST be registered before the server begins accepting connections.
