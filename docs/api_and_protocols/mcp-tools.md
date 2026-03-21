# MCP Tool Registration

> **Purpose:** Explain how butler modules register MCP tools, naming conventions, and the tool lifecycle.
> **Audience:** Module developers, anyone building or extending butler capabilities.
> **Prerequisites:** [Architecture Overview](../architecture/index.md), familiarity with FastMCP.

## Overview

Every butler is a long-running MCP server backed by FastMCP. Domain-specific capabilities are delivered through **modules** -- pluggable units that register MCP tools at startup. Modules never touch core infrastructure directly; they add tools and nothing else. The module system resolves dependencies via topological sort, runs Alembic migrations, and then calls each module's `register_tools()` method to wire tools onto the butler's shared FastMCP server.

## The Module Base Class

All modules subclass `Module` from `src/butlers/modules/base.py`. The abstract interface requires:

| Member | Type | Purpose |
|--------|------|---------|
| `name` | `str` property | Unique module identifier (e.g., `"email"`, `"telegram"`) |
| `config_schema` | `type[BaseModel]` property | Pydantic model for this module's config section |
| `dependencies` | `list[str]` property | Names of modules this one depends on |
| `register_tools(mcp, config, db)` | async method | Register MCP tools on the FastMCP server |
| `migration_revisions()` | method | Return Alembic branch label, or `None` |
| `on_startup(config, db, credential_store)` | async method | Post-init hook (after migrations) |
| `on_shutdown()` | async method | Cleanup on butler shutdown |
| `tool_metadata()` | method (optional) | Return `ToolMeta` dicts for sensitivity declarations |

## Tool Registration

Inside `register_tools()`, a module calls FastMCP's decorator API on the `mcp` server instance to expose tools. For example:

```python
async def register_tools(self, mcp, config, db):
    @mcp.tool()
    async def email_search(query: str, max_results: int = 10) -> str:
        """Search the inbox for messages matching a query."""
        ...
```

Each tool becomes an MCP tool callable by the butler's spawned LLM CLI runtime. The `mcp` object is the butler's `FastMCP` server instance shared across all modules.

**Key patterns:**
- **Closure capture**: Tools capture `self` via a local variable to resolve runtime state at call-time, after `on_startup()` has initialized providers and runtimes.
- **Typed parameters**: Python type annotations become the MCP tool input schema. Optional parameters with defaults become optional in the schema.
- **Docstring as description**: The function docstring becomes the tool's description in MCP listings. Write clear `Args:` sections so the LLM understands parameter semantics.
- **Dict return values**: FastMCP serializes returned dicts as JSON in the MCP response.
- **Error handling**: Return error dicts rather than raising exceptions, so the LLM receives structured errors.

## Naming Conventions

Tool names follow a `{domain}_{action}` pattern derived from the function name:

- `email_search`, `email_send`, `email_reply`
- `telegram_send_message`, `telegram_get_chat`
- `calendar_list_events`, `calendar_create_event`
- `state_get`, `state_set`, `state_delete`
- `memory_store`, `memory_search`

The naming must be unique across all modules loaded by a butler. Since modules declare `dependencies`, load order is deterministic and conflicts are caught at startup.

## Tool Sensitivity Metadata

Modules can declare which tool arguments are safety-sensitive by overriding `tool_metadata()`:

```python
def tool_metadata(self) -> dict[str, ToolMeta]:
    return {
        "email_send": ToolMeta(arg_sensitivities={"to": True, "body": True}),
    }
```

The `ToolMeta` dataclass maps argument names to boolean sensitivity flags. Arguments not explicitly listed fall through to the approvals subsystem's heuristic classifier. This metadata drives the approval gating module, which can require owner authorization before executing sensitive tool calls.

## Dependency Resolution

Modules declare dependencies by name. The framework performs a topological sort to determine load order. If module `email` depends on module `contacts`, `contacts.register_tools()` runs first. Circular dependencies are detected and cause a startup error.

## Lifecycle

1. **Config loading** -- Butler TOML is parsed; enabled modules are identified.
2. **Dependency resolution** -- Topological sort on module dependency graph.
3. **Migrations** -- Each module's `migration_revisions()` runs Alembic branches.
4. **Startup** -- `on_startup(config, db, credential_store)` for each module in order. The `credential_store` parameter enables DB-first credential resolution; it may be `None` in tests.
5. **Tool registration** -- `register_tools(mcp, config, db)` wires tools onto the FastMCP server.
6. **Runtime** -- LLM CLI instances call tools via MCP protocol.
7. **Shutdown** -- `on_shutdown()` for each module in reverse order.

## Core vs Module Tools

Core tools (state store, scheduler) are registered by the daemon itself, not by modules. Module tools only add domain-specific capabilities. This separation means a butler with zero modules still has state management, scheduling, and spawner functionality.

## Related Pages

- [Module System](../concepts/index.md) -- How modules work conceptually
- [Dashboard API](dashboard-api.md) -- REST endpoints that proxy tool calls
- [Inter-Butler Communication](inter-butler-communication.md) -- Cross-butler MCP routing
- [Tool Call Capture](../runtime/tool-call-capture.md) -- How tool executions are recorded during sessions
