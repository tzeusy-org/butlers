## MODIFIED Requirements

### Requirement: Core Tool Surface
Every butler daemon registers a fixed set of core MCP tools as defined in `CORE_TOOL_NAMES`.

#### Scenario: Core tools are registered
- **WHEN** the daemon completes startup
- **THEN** the following tools are available: `status`, `trigger`, `route.execute`, `tick`, `state_get`, `state_set`, `state_delete`, `state_list`, `schedule_list`, `schedule_create`, `schedule_update`, `schedule_delete`, `sessions_list`, `sessions_get`, `sessions_summary`, `sessions_daily`, `top_sessions`, `schedule_costs`, `notify`, `remind`, `get_attachment`, `module.states`, `module.set_enabled`, `correct`
