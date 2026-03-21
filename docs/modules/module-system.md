# Module System

> **Purpose:** Describes the abstract module contract, registry, dependency resolution, and lifecycle hooks that all butler modules implement.
> **Audience:** Contributors and module developers.
> **Prerequisites:** Familiarity with the [Butlers architecture overview](../architecture/index.md).

## Overview

Every butler is a long-running MCP server with a fixed core (state store, scheduler, LLM spawner, session log) and a set of opt-in **modules**. Modules are the only mechanism for adding domain-specific MCP tools to a butler. They never touch core infrastructure directly.

The module system provides three things:

1. **A contract** -- the `Module` abstract base class that every module must implement.
2. **A registry** -- auto-discovery of all built-in and roster-defined module classes.
3. **Dependency ordering** -- topological sort so that modules start only after their dependencies.

Source files:

- `src/butlers/modules/base.py` -- `Module` ABC and `ToolMeta`
- `src/butlers/modules/registry.py` -- `ModuleRegistry`, `default_registry()`, Kahn's algorithm

## The Module ABC

Every concrete module subclasses `Module` from `butlers.modules.base` and implements the following abstract members:

### Properties

| Property | Return type | Description |
|----------|-------------|-------------|
| `name` | `str` | Unique module identifier (e.g. `"email"`, `"memory"`). Used as the key in `butler.toml` config sections and dependency declarations. |
| `config_schema` | `type[BaseModel]` | Pydantic model class for the module's `[modules.<name>]` configuration block. The daemon validates config against this schema at startup. |
| `dependencies` | `list[str]` | Names of other modules that must be started before this one. An empty list means no dependencies. |

### Abstract Methods

| Method | Signature | Description |
|--------|-----------|-------------|
| `register_tools` | `async (mcp, config, db) -> None` | Register MCP tools on the butler's FastMCP server. Called after dependency resolution. The `mcp` argument is the FastMCP server instance; `config` is the validated module config; `db` is the butler's database handle. |
| `migration_revisions` | `() -> str \| None` | Return an Alembic branch label for this module's database migrations, or `None` if the module has no custom tables. |
| `on_startup` | `async (config, db, credential_store?) -> None` | Called after dependency resolution and migrations have run. Used to initialize connections, start background tasks, and resolve credentials. The optional `credential_store` parameter provides DB-first credential resolution. |
| `on_shutdown` | `async () -> None` | Called during butler shutdown. Clean up connections, stop background tasks, release resources. |

### Optional: Tool Metadata

The `tool_metadata()` method (non-abstract, defaults to `{}`) returns a `dict[str, ToolMeta]` mapping tool names to sensitivity metadata. The `ToolMeta` dataclass declares which tool arguments are safety-critical:

```python
@dataclass
class ToolMeta:
    arg_sensitivities: dict[str, bool]  # arg_name -> is_sensitive
```

When a module does not declare metadata, the approvals subsystem falls back to heuristic classification (matching argument names like `to`, `recipient`, `password`, etc.).

## Module Registry

`ModuleRegistry` is the central catalog of all available module classes. It supports two loading modes:

- **`load_from_config(modules_config)`** -- instantiate and order only the modules listed in the config dict. Raises if a module depends on something not in the enabled set.
- **`load_all(modules_config)`** -- instantiate and order every registered module, regardless of config presence. Modules in the config dict receive their explicit config; absent modules receive `{}`. This is the preferred startup path since butlers-949, allowing runtime enable/disable without static config changes.

### Auto-Discovery

`default_registry()` builds a pre-populated registry by:

1. Walking all sub-packages under `butlers.modules` via `pkgutil.walk_packages`.
2. Inspecting each discovered Python module for concrete `Module` subclasses.
3. Scanning `roster/*/modules/__init__.py` for butler-specific custom modules (loaded under synthetic names like `butlers.modules._roster_{butler}`).

Registration is idempotent -- duplicate class registrations are silently skipped.

## Dependency Resolution

Module ordering uses **Kahn's algorithm** (in-degree counting) to produce a deterministic topological sort. The implementation lives in `_topological_sort()`:

1. Build an adjacency graph from each module's `dependencies` list.
2. Seed a processing queue with all modules that have zero in-degree (no dependencies).
3. Process each batch in sorted order (alphabetical within each tier for determinism).
4. Decrement in-degree for each neighbor; add newly zero-degree nodes to the queue.
5. If any modules remain with non-zero in-degree after processing, a cycle exists and a `ValueError` is raised.

The result is a list of module instances ordered so that every module's dependencies appear before it. This order governs `on_startup` invocation, `register_tools` invocation, and migration execution.

### Error Handling

- **Unknown module**: `load_from_config` raises `ValueError` if a config key references a module name not in the registry.
- **Missing dependency**: Raises `ValueError` if module A depends on module B, but B is not in the enabled set.
- **Circular dependency**: Raises `ValueError` listing the modules involved in the cycle.

## Migration Branching

Modules that own database tables return a branch label string from `migration_revisions()`. This label corresponds to an Alembic branch in `src/butlers/migrations/versions/`. At startup, the daemon runs migrations for all enabled modules' branches before calling `on_startup`.

Modules with no custom tables (e.g. email, telegram) return `None` and require no migrations.

## Tool Registration Flow

The full module lifecycle during butler startup:

1. **Parse config** -- `butler.toml` is parsed; `[modules.*]` sections are extracted.
2. **Build registry** -- `default_registry()` discovers all module classes.
3. **Load modules** -- `load_all()` or `load_from_config()` instantiates and orders modules.
4. **Run migrations** -- For each module with a non-None `migration_revisions()`, run Alembic migrations.
5. **Register tools** -- For each module in dependency order, call `register_tools(mcp, config, db)`.
6. **Start modules** -- For each module in dependency order, call `on_startup(config, db, credential_store)`.

During shutdown, `on_shutdown()` is called in reverse dependency order.

## Writing a New Module

A minimal module implementation:

```python
from pydantic import BaseModel
from butlers.modules.base import Module

class MyConfig(BaseModel):
    setting: str = "default"

class MyModule(Module):
    @property
    def name(self) -> str:
        return "my_module"

    @property
    def config_schema(self) -> type[BaseModel]:
        return MyConfig

    @property
    def dependencies(self) -> list[str]:
        return []  # or ["memory"] if you need memory tools

    def migration_revisions(self) -> str | None:
        return None  # or "my_module" if you have tables

    async def register_tools(self, mcp, config, db) -> None:
        @mcp.tool()
        async def my_tool(arg: str) -> dict:
            """My tool description."""
            return {"result": arg}

    async def on_startup(self, config, db, credential_store=None) -> None:
        pass

    async def on_shutdown(self) -> None:
        pass
```

Place the module in `src/butlers/modules/` (single file or package) and auto-discovery will find it. For butler-specific modules, place them in `roster/<butler>/modules/__init__.py`.

## Related Pages

- [Memory Module](memory.md)
- [Approvals Module](approvals.md)
- [Calendar Module](calendar.md)
- [Contacts Module](contacts.md)
