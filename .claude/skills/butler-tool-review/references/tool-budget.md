# Tool Budget Reference

## Why Tool Count Matters

Every registered MCP tool costs tokens at discovery time and degrades model performance. Smaller models (gpt-5.4-mini) degrade significantly above 50 tools. Target: 30-50 tools per butler.

## Core Daemon Tools

Core tools are registered in `src/butlers/daemon.py::_register_core_tools()`. They are gated by butler type and name.

### Tool Partition

| Tier | Constant | Condition | Count | Examples |
|---|---|---|---:|---|
| Universal | `UNIVERSAL_CORE_TOOL_NAMES` | All butlers | 25 | status, trigger, route.execute, state_*, schedule_*, notify, remind, correct |
| Domain | `DOMAIN_CORE_TOOL_NAMES` | `butler_type != STAFFER` | 13 | deadline_*, event_chain_*, seasonal_period_* |
| Messenger | `MESSENGER_CORE_TOOL_NAMES` | `butler_name == "messenger"` | 4 | delivery_preferences_*, deferred_notification_* |
| Switchboard | *(in switchboard if-block)* | `butler_name == "switchboard"` | 5 | ingest, route_to_butler, connector.heartbeat, backfill.poll/progress |

### Core tools per butler type (without core_groups pruning)

- **Domain butler**: 25 universal + 13 domain = **38**
- **Staffer (switchboard)**: 25 universal + 5 switchboard-specific = **30**
- **Staffer (messenger)**: 25 universal + 4 messenger-specific = **29**
- **Staffer (qa)**: 25 universal = **25**

### Core Tool Groups

Universal core tools support the `core_groups` config in `[butler.runtime]`:

```toml
[butler.runtime]
core_groups = ["infra", "notifications", "module_mgmt"]
# omit core_groups = register ALL (backward compatible)
```

| Group | Tools | Count |
|---|---|---:|
| infra | status, trigger, route.execute, tick, correct | 5 |
| state | state_get, state_set, state_delete, state_list | 4 |
| scheduling | schedule_list, schedule_create, schedule_update, schedule_delete, schedule_trigger, schedule_costs | 6 |
| sessions | sessions_list, sessions_get, sessions_summary, sessions_daily, top_sessions | 5 |
| notifications | notify, remind | 2 |
| media | get_attachment | 1 |
| module_mgmt | module.states, module.set_enabled | 2 |
| switchboard_routing | ingest, route_to_butler, connector.heartbeat | 3 |
| switchboard_backfill | backfill.poll, backfill.progress | 2 |

Domain tools (deadline_*, event_chain_*, seasonal_period_*) and messenger tools
(delivery_preferences_*, deferred_notification_*) remain gated by butler type,
not core_groups.

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

### Group Taxonomy

| Module | Groups | Total Tools |
|---|---|---:|
| memory | core(8), feedback(3), entity(7), preferences(2), admin(5) | 25 |
| calendar | core(8), butler_events(4), attendees(2) | 14 |
| relationship | contacts(17), interactions(5), relationships(8), social(10), notes(6), tracking(10), management(3), entity(4) | 63 |
| finance | core(5), facts(5), bulk(7), subscriptions(4), bills(3), budgets(4), analytics(9), intelligence(6) | 43 |
| education | mind_maps(12), teaching(5), mastery(4), spaced_repetition(3), diagnostics(3), curriculum(3), analytics(3) | 33 |
| health | measurements(3), medications(4), conditions(3), symptoms(3), nutrition(3), reports(2), research(3) | 21 |
| home_assistant | core(6), history(3), maintenance(4) | 13 |
| approvals | actions(7), rules(6), promotions(3) | 16 |
| switchboard | routing(5), extraction(3), backfill(5), operator(7) | 20 |

### Ownership Principle

- **Domain modules on their specialist butler** keep ALL groups (no pruning). The finance butler needs all finance groups.
- **Cross-cutting modules** (memory, calendar, approvals, home_assistant) are where pruning matters. Each butler enables only the groups it uses.

### Modules Without Group Support (<10 tools)

contacts(4), email(4), general(10), travel(7), qa(3), whatsapp(2), telegram(0), spotify(0), steam(0), google_drive(0), insight_broker(1)

## Adding Group Support to a New Module

1. Add `ToolGroupMixin` to the module's config class
2. Define `_tool(group)` helper inside `register_tools()`
3. Replace `@mcp.tool()` with `@_tool("group_name")` — zero re-indentation
4. Document group taxonomy in config class docstring
5. Update butler.toml files that use this module with appropriate `groups`
