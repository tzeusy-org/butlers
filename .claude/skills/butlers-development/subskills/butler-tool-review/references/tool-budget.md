# Tool Budget Reference

## Maintenance contract (read this first)

This file records registration and review principles, not authoritative names,
group taxonomies, counts, or retention lists. An audit derives each butler's
actual core and module inventory from registration behavior and effective
configuration. Whenever an audit (or any change to `register_tools()`) reveals
that a principle or gate shape here has changed:

1. Update the gate semantics and examples here in the same change — don't defer
   as follow-up.
2. Collect actual registrations across all relevant role contexts and update
   their behavior-level contract tests. Do not recreate a name, count, group,
   or retention catalog here.

## Why Tool Count Matters

Every registered MCP tool costs tokens at discovery time and degrades model performance. Smaller models (gpt-5.4-mini) degrade significantly above 50 tools. Target: 30-50 tools per butler.

## Core Daemon Tools

Core tools are registered by `butlers.core_tools.register_all_core_tools()`,
called from `src/butlers/daemon.py::_register_core_tools()`. Group decorators,
direct infrastructure registrations, and butler type/name gates together
determine the surface.

### Registration Discovery

The dispatcher mixes two registration shapes. Derive both mechanically; a
configured group list alone cannot describe the effective surface.

| Registration shape | Condition | Discovery evidence |
|---|---|---|
| Group-decorated | `core_groups` permits the group, plus any local type/name gate | Collect decorated registration behavior across relevant role contexts |
| Direct | Always or owning-name registration, independent of `core_groups` | Collect direct registration behavior across the same contexts |

At minimum, exercise an ordinary domain butler plus each distinct staffer,
name-gated, or type-gated context present in registration source. Add a context
when a new gate appears. Behavior-level contract tests are the regression
guard; this reference deliberately does not duplicate their inventory.

### Core Group Discovery

Group-decorated tools respect `core_groups` from DB-backed runtime config,
seeded by `[butler.runtime_seed]`:

```toml
[butler.runtime_seed]
core_groups = ["infra", "notifications", "module_mgmt"]
# omit core_groups = register ALL (backward compatible)
```

Discover core group names and membership from the owning registration
functions. Then apply effective `runtime_config`, type gates, name gates, and
direct-registration behavior. Some direct tools intentionally remain available
when `core_groups` is empty; prove that behavior from source and contract tests
rather than an always-retain list in this reference.

## Module Tool Groups

Use a module with >=10 derived tools as a review threshold for whether group
configuration may be useful; it is not evidence that the module supports groups.

```toml
[modules.memory]
groups = ["core", "entity"]  # only these groups registered
# omit groups = register ALL (backwards compatible)
```

### Implementation Pattern

Each module uses `ToolGroupMixin` on its config class and `_tool(group)` in `register_tools()`:

```python
from butlers.modules.base import ToolGroupMixin, group_enabled

class MyConfig(ToolGroupMixin, BaseModel): ...

def register_tools(mcp, module, config=None):
    def _tool(group):
        if group_enabled(config, group):
            return mcp.tool()
        return lambda fn: fn

    @_tool("core")
    async def my_tool(...): ...
```

### Module Group Discovery

Discover group names from registration source and effective configuration; do not
maintain a package-wide taxonomy here.

### Ownership Principle

- **Domain modules on their specialist butler** keep ALL groups (no pruning). The finance butler needs all finance groups.
- **Cross-cutting modules** (memory, calendar, approvals, home_assistant) are where pruning matters. Each butler enables only the groups it uses.

## Adding Group Support to a New Module

1. Add `ToolGroupMixin` to the module's config class
2. Define `_tool(group)` helper inside `register_tools()`
3. Replace `@mcp.tool()` with `@_tool("group_name")` — zero re-indentation
4. Document group taxonomy in config class docstring
5. Update butler.toml files that use this module with appropriate `groups`
