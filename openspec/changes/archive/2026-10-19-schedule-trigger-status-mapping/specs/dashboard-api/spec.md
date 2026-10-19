## MODIFIED Requirements

### Requirement: MCP Client Proxy
`src/butlers/api/deps.py` SHALL provide `MCPClientManager` for lazy FastMCP client connections to running butler MCP daemons. Write operations (state set/delete, schedule CRUD, triggers) are proxied through MCP to preserve the architectural constraint that only the butler mutates its own database.

ID: REQ-dashboard-api-073
Source: bu-h3ihis (CE authorization for bu-ukf1u0); existing MCP Client Proxy contract
Scope: v1-mandatory

#### Scenario: Lazy client connection
- **WHEN** `get_client(butler_name)` is called for the first time
- **THEN** a FastMCP SSE client is created, connected to `http://localhost:{port}/sse`, and cached
- **AND** subsequent calls return the cached client if still connected

#### Scenario: Client reconnection
- **WHEN** a cached client is found to be disconnected
- **THEN** the old client is closed and a new connection is established

#### Scenario: Butler unreachable
- **WHEN** a butler's MCP server is not running or connection fails
- **THEN** `ButlerUnreachableError` is raised with the butler name and cause

#### Scenario: Tool invocation proxy
- **WHEN** the dashboard calls `POST /api/butlers/{name}/mcp/call` with `{ "tool_name": "...", "arguments": {...} }`
- **THEN** the backend connects to the butler via MCP and calls `client.call_tool(tool_name, arguments)`
- **AND** the MCP result is parsed (JSON when possible, raw text as fallback) and returned in an `MCPToolCallResponse` envelope

#### Scenario: State write via MCP
- **WHEN** `PUT /api/butlers/{name}/state/{key}` is called
- **THEN** the backend calls the butler's MCP `state_set` tool with `{ "key": key, "value": value }`
- **AND** an audit log entry is recorded on success or failure

#### Scenario: State write/delete to a group-gated butler returns 409
- **WHEN** `PUT` or `DELETE /api/butlers/{name}/state/{key}` targets a butler whose `state` core group is not enabled (e.g. switchboard), so `state_set` / `state_delete` are not registered and `client.call_tool` raises `ToolError("Unknown tool: ...")`
- **THEN** the endpoint returns **409** (not 500), with `detail` naming the missing tool and the disabled core group (e.g. `tool 'state_set' not available on butler 'switchboard' (core group 'state' not enabled)`) — a structural configuration state, not a transient fault or retryable condition
- **AND** a genuine `ToolError` raised by an ENABLED `state_set` / `state_delete` tool is NOT masked — it still surfaces as a 5xx

#### Scenario: Module toggle to a group-gated butler returns 409
- **WHEN** `PUT /api/butlers/{name}/module-states/{module}/enabled` targets a butler whose `module_mgmt` core group is not enabled (e.g. finance, relationship), so `module.set_enabled` is not registered and the call raises `ToolError("Unknown tool: ...")`
- **THEN** the endpoint returns **409** (not 500), with `detail` naming the missing tool and the disabled core group (`... (core group 'module_mgmt' not enabled)`)
- **AND** a genuine `ToolError` from an ENABLED `module.set_enabled` tool still surfaces as a 5xx

#### Scenario: Module runtime states degrade gracefully when module_mgmt is gated
- **WHEN** `GET /api/butlers/{name}/module-states` targets a butler whose `module_mgmt` core group is not enabled, so `module.states` is not registered and the call raises `ToolError("Unknown tool: ...")`
- **THEN** the endpoint returns **200** with an empty `data` list and `meta.module_states_unavailable: true` (following the fleet-wide degraded-envelope convention), rather than the 502 a raw tool-absent error would produce
- **AND** a genuine `ToolError` from an ENABLED `module.states` tool still surfaces as a 502 (not a truthful-looking empty 200)
- **AND** frontends SHALL gate the module list on `meta.module_states_unavailable` and render an "unavailable" note instead of an empty module list

#### Scenario: Schedule CRUD via MCP
- **WHEN** schedule create/update/delete/toggle endpoints are called
- **THEN** the backend proxies to the butler's `schedule_create`, `schedule_update`, `schedule_delete`, or `schedule_toggle` MCP tools respectively
- **AND** each operation records an audit log entry

#### Scenario: A schedule trigger's outcome maps to the HTTP status
- **WHEN** `POST /api/butlers/{name}/schedules/{schedule_id}/trigger` proxies `schedule_trigger`
- **THEN** a `triggered` reply answers 200, a `busy` reply answers 409, a nonexistent schedule answers 404, and any other reply (a dispatch failure or an unrecognised reply) answers 502
- **AND** each non-200 outcome carries its reason in `detail`, so a trigger that did not happen never reads as a successful one
- **AND** a failed MCP call (a raised tool error or an unreachable butler) is not a reply and stays 503
