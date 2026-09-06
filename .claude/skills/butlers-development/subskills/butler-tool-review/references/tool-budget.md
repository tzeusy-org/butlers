# Tool Budget Reference

## Maintenance contract (read this first)

This file records group and registration semantics, not authoritative module
counts. An audit derives each butler's actual core and module counts from its
registration behavior and effective configuration. Whenever an audit (or any
change to `register_tools()`) changes that behavior:

1. Update group names, gate semantics, and examples here in the same change —
   don't defer as follow-up.
2. If a core registration function or its dispatcher changes, collect the
   actual registrations across role contexts and update the "Core Tool Groups"
   tables to match. Do not recreate a daemon-level name catalog.

## Why Tool Count Matters

Every registered MCP tool costs tokens at discovery time and degrades model performance. Smaller models (gpt-5.4-mini) degrade significantly above 50 tools. Target: 30-50 tools per butler.

## Core Daemon Tools

Core tools are registered by `butlers.core_tools.register_all_core_tools()`,
called from `src/butlers/daemon.py::_register_core_tools()`. Group decorators,
direct infrastructure registrations, and butler type/name gates together
determine the surface.

### Registration Inventory

The dispatcher has **79** unique registrations: **71** group-decorated across
14 groups and **8** direct registrations. The contract test derives this
inventory by running the dispatcher for domain, Switchboard, Messenger, and
Chronicler contexts; it is the regression guard, not a second catalog.

| Registration shape | Condition | Count | Examples |
|---|---|---:|---|
| Group-decorated | `core_groups` permits the group, plus any local type/name gate | 71 | state, scheduling, temporal, delegation, graph |
| Direct | Always or owning-name registration, independent of `core_groups` | 8 | cancel_session, route.execute, Messenger preferences |

### Core tools per butler (all groups enabled)

- **Domain butler**: **64**
- **Chronicler**: **65** (domain surface plus its control)
- **Staffer (switchboard)**: **41**
- **Staffer (messenger)**: **39**
- **Staffer (qa)**: **33**

### Core Tool Groups

Group-decorated tools respect `core_groups` from DB-backed runtime config,
seeded by `[butler.runtime_seed]`:

```toml
[butler.runtime_seed]
core_groups = ["infra", "notifications", "module_mgmt"]
# omit core_groups = register ALL (backward compatible)
```

| Group | Tools | Count |
|---|---|---:|
| infra | status, trigger, tick, correct, memory/conversation controls, shutdown, Chronicler control | 11 |
| state | state_get, state_set, state_delete, state_list | 4 |
| scheduling | schedule_list, schedule_create, schedule_update, schedule_delete, schedule_trigger, schedule_costs | 6 |
| sessions | sessions_list, sessions_get, sessions_summary, sessions_daily, top_sessions | 5 |
| notifications | notify, remind | 2 |
| temporal | deadline_*, event_chain_*, seasonal_period_* | 13 |
| media | get_attachment | 1 |
| module_mgmt | module.states, module.set_enabled | 2 |
| delegation | delegate_ask, delegate_receive, delegate_answer, delegate_wake | 4 |
| domain_events | publish/subscribe, receive, reaction tools | 6 |
| fleet_cases | case read/write/contribution tools | 7 |
| graph | entity_graph_walk, entity_graph_path | 2 |
| switchboard_routing | ingest, route_to_butler, routing helpers, connector.heartbeat | 6 |
| switchboard_backfill | backfill.poll, backfill.progress | 2 |

Temporal, delegation, and domain-event groups exclude staffers. Switchboard
groups require the Switchboard name; Messenger preference tools are direct and
require the Messenger name. `route.execute` and `cancel_session` are direct
infrastructure registrations and remain available even when `core_groups` is
empty.

## Module Tool Groups

Modules with >=10 tools support the `groups` config in butler.toml:

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
