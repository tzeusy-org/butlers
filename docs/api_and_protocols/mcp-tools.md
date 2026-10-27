# MCP Tool Registration

> **Purpose:** Explain how butler modules register MCP tools, naming conventions, and the tool lifecycle.
> **Audience:** Module developers, anyone building or extending butler capabilities.
> **Prerequisites:** [Architecture Overview](../architecture/index.md), familiarity with FastMCP.

## Overview

Every butler is a long-running MCP server backed by FastMCP. Domain capabilities arrive through
**modules**, which add tools and nothing else; core infrastructure (state store, scheduler,
spawner) registers its own tools. The module contract — the `Module` ABC in
`src/butlers/modules/base.py`, its hooks, dependency ordering, and migration wiring — is defined
once in [Module System](../modules/module-system.md). This page covers only what matters when
writing the tools themselves.

## Writing a Tool

Inside `Module.register_tools(...)`, a module decorates async functions with `@mcp.tool()` on the
butler's shared FastMCP server. Each becomes a tool the butler's spawned LLM runtime can call.
`src/butlers/modules/email.py` (`email_send_message`, `email_search_inbox`) is a compact
reference.

- **Resolve state at call time.** Capture `self` and look up providers inside the tool body,
  so the tool sees whatever `on_startup` and later reconfiguration initialized.
- **The signature is the schema.** Type annotations become the MCP input schema; defaults make a
  parameter optional.
- **The docstring is the description.** Write an `Args:` section so the model understands
  parameter semantics.
- **Return structured data.** Returned dicts serialize as JSON. Return an error dict rather than
  raising, so the model receives a structured failure it can act on.

## Naming

Tool names follow `{domain}_{action}` (for example `email_send_message`,
`calendar_find_free_slots`, `state_get`). Names must be unique across the core tools and every
module a butler loads, because they share one FastMCP server.

## Sensitivity Metadata

A module marks safety-sensitive arguments by overriding `tool_metadata()` to return
`ToolMeta(arg_sensitivities=...)` per tool name (see `EmailModule.tool_metadata`). Arguments not
listed fall through to the approvals subsystem's heuristic classifier. This metadata drives the
approval gate, which can require owner authorization before a sensitive call executes.

`ToolMeta` also carries the representation fields defined by RFC 0027: canonical name, owning
group, logical namespace, LLM-presentable flag, and eager/deferred load posture. The daemon merges
the checked-in declarations in `src/butlers/core/tool_presentation_inventory.py` with each
module's sensitivity map. Presentation metadata cannot register a tool, revive a
configuration-excluded handler, or alter approval behavior.

## Final Definition Catalog

After core and module registration and after approval wrapping, startup snapshots the definitions
returned by FastMCP's public listing interface. Each immutable descriptor contains the final input
schema and description, stable SHA-256 digests of their canonical serialization, ownership and
presentation fields, and argument sensitivities. Descriptors contain no callable or private
FastMCP manager reference. Failed construction publishes no partial catalog, and later reads do
not enumerate or invoke handlers.

FastMCP remains the only execution registry. Its `tools/list` result stays complete and calls keep
resolving through the final registered handler. The catalog is an input for the separately planned
adapter presentation layer; this implementation does not filter host-visible tools, render a
search corpus, enable native tool search, or change canonical list/call behavior.

## Core vs Module Tools

Core tools (state store, scheduler) are registered by the daemon itself, not by modules. Module tools only add domain-specific capabilities. This separation means a butler with zero modules still has state management, scheduling, and spawner functionality.

## Verification

```bash
# Registered tools on a running butler match the module's register_tools and follow the naming rule
python3 - <<'PY'
import asyncio
from fastmcp import Client

async def main():
    async with Client("http://localhost:41101/sse") as client:  # butler port from butler.toml
        for t in await client.list_tools():
            print(t.name)

asyncio.run(main())
PY

# Module dependency ordering and cycle detection
uv run pytest tests/ -k "circular or topolog" -q --tb=short -n 0
```

## Related Pages

- [Module System](../modules/module-system.md) -- The `Module` contract and lifecycle
- [Dashboard API](dashboard-api.md) -- REST endpoints that proxy tool calls
- [Inter-Butler Communication](inter-butler-communication.md) -- Cross-butler MCP routing
- [Tool Call Capture](../runtime/tool-call-capture.md) -- How tool executions are recorded during sessions
