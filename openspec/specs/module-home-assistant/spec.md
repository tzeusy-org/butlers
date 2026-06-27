# Home Assistant Module

## Purpose

The Home Assistant module provides MCP tools for bidirectional smart-home control: querying entity state from an in-memory cache, calling HA services, fetching history and statistics, rendering Jinja2 templates, logging all issued commands, and tracking recurring home-maintenance items. It maintains a persistent WebSocket connection for real-time state updates and falls back to REST polling when the WebSocket is unavailable. A `read_only` mode restricts the surface to query tools only.

## ADDED Requirements

### Requirement: HomeAssistantConfig Schema

Configuration controls the HA connection URL, SSL verification, WebSocket keepalive, polling fallback interval, and snapshot persistence frequency.

#### Scenario: Config structure

- **WHEN** `[modules.home_assistant]` is configured in butler.toml
- **THEN** it SHALL include `url` (string, required — HA base URL e.g. `http://homeassistant.tail1234.ts.net:8123`)
- **AND** `verify_ssl` (bool, default `false`)
- **AND** `websocket_ping_interval` (int, default `30` — seconds between WebSocket keepalive pings)
- **AND** `poll_interval_seconds` (int, default `60` — REST polling interval when WebSocket is disconnected)
- **AND** `snapshot_interval_seconds` (int, default `300`; interval for the snapshot loop)
- **AND** `read_only` (bool, default `false`; when `true`, only query tools are registered and all mutation tools (`ha_call_service`, `ha_activate_scene`, `ha_maintenance_create`, `ha_maintenance_complete`, `ha_maintenance_remove`) are skipped)

#### Scenario: Config validation

- **WHEN** `url` is missing or empty
- **THEN** a `ValidationError` SHALL be raised at config parse time

#### Scenario: Pydantic extra fields rejected

- **WHEN** an unrecognized field is present in `[modules.home_assistant]`
- **THEN** a `ValidationError` SHALL be raised (extra="forbid")

### Requirement: Credential Resolution via Owner Contact Info

The HA long-lived access token is resolved from the owner contact's `contact_info` entry at startup.

#### Scenario: Token resolved from contact_info

- **WHEN** `on_startup` is called with a database pool
- **THEN** the module SHALL call `resolve_owner_contact_info(pool, "home_assistant_token")`
- **AND** cache the resolved token for all subsequent API calls

#### Scenario: Token not found

- **WHEN** `resolve_owner_contact_info` returns `None`
- **AND** the token is not available in environment variables
- **THEN** `on_startup` SHALL raise `RuntimeError` with a message indicating the owner contact must have a `home_assistant_token` contact_info entry

#### Scenario: Token never logged in full

- **WHEN** the module logs connection status or errors
- **THEN** only the first 8 characters of the token SHALL appear in log output

### Requirement: HTTP Client Lifecycle

The module uses `httpx.AsyncClient` for REST API calls, created at startup and closed at shutdown.

#### Scenario: Client initialization

- **WHEN** `on_startup` completes credential resolution
- **THEN** an `httpx.AsyncClient` SHALL be created with:
  - `base_url` set to the configured HA URL
  - `Authorization: Bearer <token>` default header
  - `Content-Type: application/json` default header
  - SSL verification matching `verify_ssl` config

#### Scenario: Client cleanup

- **WHEN** `on_shutdown` is called
- **THEN** the `httpx.AsyncClient` SHALL be closed via `aclose()`

### Requirement: WebSocket Connection Lifecycle

The module maintains a persistent WebSocket connection for real-time state updates and registry queries.

#### Scenario: WebSocket connection and authentication

- **WHEN** `on_startup` establishes the WebSocket connection
- **THEN** it SHALL connect to `ws://<ha-url>/api/websocket` (or `wss://` if `verify_ssl` is true)
- **AND** wait for `auth_required` message
- **AND** send `{"type": "auth", "access_token": "<token>"}`
- **AND** wait for `auth_ok` response
- **AND** send `supported_features` with `coalesce_messages: 1`

#### Scenario: Authentication failure

- **WHEN** the server responds with `auth_invalid`
- **THEN** the module SHALL raise `RuntimeError` with the server's error message

#### Scenario: Keepalive pings

- **WHEN** the WebSocket connection is active
- **THEN** the module SHALL send `{"type": "ping"}` every `websocket_ping_interval` seconds
- **AND** expect a `{"type": "pong"}` response

#### Scenario: Auto-reconnect on disconnect

- **WHEN** the WebSocket connection is lost (network error, HA restart, missed pings)
- **THEN** the module SHALL attempt reconnection with exponential backoff (1s initial, 60s cap, with jitter)
- **AND** fall back to REST polling at `poll_interval_seconds` until reconnected
- **AND** re-subscribe to all event types after successful reconnection
- **AND** re-fetch full entity state to rehydrate the cache

#### Scenario: Clean shutdown

- **WHEN** `on_shutdown` is called
- **THEN** all WebSocket subscriptions SHALL be cancelled
- **AND** the WebSocket connection SHALL be closed cleanly

### Requirement: In-Memory Entity State Cache

The module maintains a `dict[str, CachedEntity]` populated at startup and updated via WebSocket events.

#### Scenario: Initial cache population

- **WHEN** `on_startup` completes WebSocket authentication
- **THEN** the module SHALL fetch all entity states via REST `GET /api/states`
- **AND** populate the cache with one `CachedEntity` per entity (entity_id, state, attributes, last_changed, last_updated)

#### Scenario: Real-time cache updates via state_changed

- **WHEN** a `state_changed` event arrives via WebSocket
- **THEN** the cache entry for `event.data.entity_id` SHALL be replaced with `event.data.new_state`
- **AND** if `new_state` is `null` (entity removed), the cache entry SHALL be deleted

#### Scenario: Cache serves tool reads

- **WHEN** `ha_get_entity_state` or `ha_list_entities` is called
- **THEN** the response SHALL be served from the in-memory cache (no HA API call)
- **AND** the response SHALL include the `last_updated` timestamp for freshness assessment

#### Scenario: REST polling fallback

- **WHEN** the WebSocket connection is down
- **THEN** the module SHALL poll `GET /api/states` at `poll_interval_seconds` intervals
- **AND** replace the entire cache contents with the poll response

### Requirement: Area and Entity Registry Cache

The module caches HA registry data for area and entity metadata resolution.

#### Scenario: Registry population at startup

- **WHEN** `on_startup` completes WebSocket authentication
- **THEN** the module SHALL query `config/area_registry/list` and `config/entity_registry/list` via WebSocket
- **AND** cache area mappings (area_id → area_name) and entity metadata (entity_id → area_id, device_id, platform)

#### Scenario: Registry refresh on updates

- **WHEN** an `area_registry_updated` or `entity_registry_updated` event arrives via WebSocket
- **THEN** the corresponding registry cache SHALL be re-fetched in full

### Requirement: Query Tool — ha_get_entity_state

Returns the full state object for a single entity by ID.

#### Scenario: Entity found in cache

- **WHEN** `ha_get_entity_state(entity_id="sensor.living_room_temperature")` is called
- **AND** the entity exists in the cache
- **THEN** the tool SHALL return entity_id, state, attributes (full dict), last_changed, last_updated, and area name (if mapped in entity registry)

#### Scenario: Entity not found

- **WHEN** `ha_get_entity_state` is called with an entity_id not in the cache
- **THEN** the tool SHALL return `None`

### Requirement: Query Tool — ha_list_entities

Returns compact summaries of entities, filtered by domain and/or area.

#### Scenario: List all entities

- **WHEN** `ha_list_entities()` is called with no filters
- **THEN** the tool SHALL return a list of summaries (entity_id, state, friendly_name, area_name, domain) for all cached entities
- **AND** results SHALL be sorted by entity_id

#### Scenario: Filter by domain

- **WHEN** `ha_list_entities(domain="light")` is called
- **THEN** only entities with entity_id prefix `light.` SHALL be included

#### Scenario: Filter by area

- **WHEN** `ha_list_entities(area="kitchen")` is called
- **THEN** only entities whose entity registry area_id maps to the given area name SHALL be included

#### Scenario: Combined filters

- **WHEN** both `domain` and `area` filters are provided
- **THEN** only entities matching both filters SHALL be included

### Requirement: Query Tool — ha_list_areas

Returns all defined areas/rooms from the HA area registry.

#### Scenario: List areas

- **WHEN** `ha_list_areas()` is called
- **THEN** the tool SHALL return a list of area objects (area_id, name) from the cached area registry
- **AND** results SHALL be sorted by name

### Requirement: Query Tool — ha_list_services

Returns available HA services, optionally filtered by domain.

#### Scenario: List all services

- **WHEN** `ha_list_services()` is called with no domain filter
- **THEN** the module SHALL query `GET /api/services` (REST) or `get_services` (WebSocket)
- **AND** return a list of domain → service names with descriptions

#### Scenario: Filter by domain

- **WHEN** `ha_list_services(domain="light")` is called
- **THEN** only services under the `light` domain SHALL be included

### Requirement: Query Tool — ha_get_history

Returns state history for entities over a time window from HA's recorder.

#### Scenario: Fetch history with entity filter

- **WHEN** `ha_get_history(entity_ids=["sensor.temperature"], start="2026-02-01T00:00:00Z", end="2026-02-02T00:00:00Z")` is called
- **THEN** the module SHALL call `GET /api/history/period/<start>?filter_entity_id=<ids>&end_time=<end>&minimal_response&significant_changes_only`
- **AND** return the parsed response as a list of state change records

#### Scenario: Entity IDs required

- **WHEN** `ha_get_history` is called without `entity_ids`
- **THEN** the tool SHALL raise `ValueError` (unbounded history queries are prohibitively expensive)

### Requirement: Query Tool — ha_get_statistics

Returns aggregated hourly/daily statistics for sensor entities from HA's recorder.

#### Scenario: Fetch statistics

- **WHEN** `ha_get_statistics(statistic_ids=["sensor.total_energy_kwh"], start="2026-02-01T00:00:00Z", end="2026-02-28T00:00:00Z", period="day")` is called
- **THEN** the module SHALL send a `recorder/get_statistics_during_period` WebSocket command
- **AND** return the parsed response with per-period mean, min, max, sum, and state values

#### Scenario: Valid period values

- **WHEN** `period` is provided
- **THEN** it SHALL be one of: `5minute`, `hour`, `day`, `week`, `month`

### Requirement: Query Tool — ha_render_template

Evaluates a Jinja2 template server-side on the HA instance.

#### Scenario: Render template

- **WHEN** `ha_render_template(template="{{ states('sensor.temperature') }} °C")` is called
- **THEN** the module SHALL call `POST /api/template` with the template string
- **AND** return the rendered plaintext result

### Requirement: Control Tool — ha_call_service

Generic service call supporting any HA domain, service, target, and data.

#### Scenario: Successful service call

- **WHEN** `ha_call_service(domain="light", service="turn_on", target={"entity_id": "light.kitchen"}, data={"brightness_pct": 80})` is called
- **THEN** the module SHALL call `POST /api/services/<domain>/<service>` with target and data merged in the body
- **AND** log the call to `ha_command_log` (domain, service, target, data, result, context_id, issued_at)
- **AND** return the HA response (list of changed entity states)

#### Scenario: Service call with area target

- **WHEN** `target` includes `area_id`
- **THEN** the service call SHALL be issued with the area_id in the target (HA resolves to entities)

#### Scenario: Service call error

- **WHEN** the HA API returns an error response (4xx or 5xx)
- **THEN** the module SHALL log the error to `ha_command_log` with the error in the result field
- **AND** return the error message to the LLM

### Requirement: Control Tool — ha_activate_scene

Convenience wrapper for activating a scene by entity_id.

#### Scenario: Activate scene

- **WHEN** `ha_activate_scene(entity_id="scene.movie_night", transition=1.5)` is called
- **THEN** the module SHALL call `ha_call_service(domain="scene", service="turn_on", target={"entity_id": entity_id}, data={"transition": transition})`

#### Scenario: Invalid scene entity

- **WHEN** `entity_id` does not start with `scene.`
- **THEN** the tool SHALL raise `ValueError`

### Requirement: Command Audit Logging

Every service call issued through the module is persisted to the `ha_command_log` table.

#### Scenario: Successful command logged

- **WHEN** `ha_call_service` completes successfully
- **THEN** a row SHALL be inserted into `ha_command_log` with domain, service, target (JSONB), data (JSONB), result (JSONB), context_id (from HA response context), and issued_at

#### Scenario: Failed command logged

- **WHEN** `ha_call_service` receives an error from HA
- **THEN** the error SHALL still be logged to `ha_command_log` with the error in the result field

### Requirement: Entity Snapshot Persistence

Snapshot persistence to the `ha_entity_snapshot` table is currently disabled. HA state is queried live via `ha_get_entity_state` / `ha_list_entities`, so the module does not write the periodic cache snapshot (an earlier implementation that stored every entity as a property fact produced roughly 210k superseded rows in three days). When the snapshot loop does run, it persists entity state to the memory subsystem as volatile `ha_state` property facts anchored to HA entities (one active fact per entity), not to the `ha_entity_snapshot` table.

#### Scenario: Snapshot loop persistence target

- **WHEN** the snapshot loop runs (interval `snapshot_interval_seconds`)
- **THEN** the module SHALL persist entity state to the memory subsystem as `ha_state` property facts (each new fact supersedes the prior active `ha_state` fact for that entity), and SHALL NOT UPSERT into the `ha_entity_snapshot` table

#### Scenario: Shutdown

- **WHEN** `on_shutdown` is called
- **THEN** the module SHALL close its connections without writing a final entity snapshot (snapshot persistence is disabled; HA state is available live)

### Requirement: Database Schema Migration

The module provides an Alembic migration for its home-domain tables.

#### Scenario: Migration creates tables

- **WHEN** the Alembic migration runs
- **THEN** `ha_entity_snapshot` (entity_id TEXT PK, state TEXT, attributes JSONB, last_updated TIMESTAMPTZ, captured_at TIMESTAMPTZ) SHALL be created (retained for compatibility even though snapshot writes are currently disabled)
- **AND** `ha_command_log` (id BIGSERIAL PK, domain TEXT, service TEXT, target JSONB, data JSONB, result JSONB, context_id TEXT, issued_at TIMESTAMPTZ) SHALL be created
- **AND** `maintenance_items` SHALL be created (backing the maintenance tool suite)
- **AND** the `ha_state` predicate SHALL be seeded into `predicate_registry`
- **AND** index `ix_ha_command_log_issued_at` on `ha_command_log(issued_at)` SHALL be created

#### Scenario: Migration branch label

- **WHEN** `migration_revisions()` is called
- **THEN** it SHALL return `"home"` as the Alembic branch label

### Requirement: Tool Metadata for Approval Sensitivity

The module declares approval sensitivity for control tools via `tool_metadata()`.

#### Scenario: Sensitive args declared

- **WHEN** `tool_metadata()` is called
- **THEN** it SHALL return `ToolMeta(arg_sensitivities={"domain": True, "service": True})` for `ha_call_service`

#### Scenario: Query tools not declared

- **WHEN** `tool_metadata()` is called
- **THEN** no entries SHALL exist for `ha_get_entity_state`, `ha_list_entities`, `ha_list_areas`, `ha_list_services`, `ha_get_history`, `ha_get_statistics`, or `ha_render_template`

### Requirement: Module Identity and Dependencies

The module registers under the name `home_assistant` with a dependency on `contacts` (for owner resolution) and `approvals` (for gating).

#### Scenario: Module identity

- **WHEN** the module is registered
- **THEN** `name` SHALL be `"home_assistant"`
- **AND** `dependencies` SHALL be `["contacts", "approvals"]`
- **AND** `config_schema` SHALL be `HomeAssistantConfig`

### Requirement: Tool Registration

The module registers up to 13 MCP tools during `register_tools()`: 9 Home Assistant control/query tools plus 4 maintenance tools. Mutation tools are skipped when `read_only = true`.

#### Scenario: Tool inventory

- **WHEN** `register_tools(mcp, config, db)` is called with `read_only = false` (default)
- **THEN** the following tools SHALL be registered: `ha_get_entity_state`, `ha_list_entities`, `ha_list_areas`, `ha_list_services`, `ha_get_history`, `ha_get_statistics`, `ha_render_template`, `ha_call_service`, `ha_activate_scene`, `ha_maintenance_list`, `ha_maintenance_create`, `ha_maintenance_complete`, `ha_maintenance_remove`

#### Scenario: Read-only tool inventory

- **WHEN** `register_tools(mcp, config, db)` is called with `read_only = true`
- **THEN** only the query tools SHALL be registered: `ha_get_entity_state`, `ha_list_entities`, `ha_list_areas`, `ha_list_services`, `ha_get_history`, `ha_get_statistics`, `ha_render_template`, `ha_maintenance_list`
- **AND** the mutation tools `ha_call_service`, `ha_activate_scene`, `ha_maintenance_create`, `ha_maintenance_complete`, `ha_maintenance_remove` SHALL NOT be registered

### Requirement: Maintenance Tool Suite

The module provides recurring home-maintenance tracking backed by the `maintenance_items` table.

#### Scenario: Create a maintenance item

- **WHEN** `ha_maintenance_create(name, category, interval_days, notes=None)` is called
- **THEN** a maintenance item SHALL be created with `category` one of `filter`, `hvac`, `appliance`, `plumbing`, `electrical`, `general`
- **AND** duplicate names SHALL be rejected

#### Scenario: Complete a maintenance item

- **WHEN** `ha_maintenance_complete(name, completed_at=None)` is called
- **THEN** the item SHALL be marked complete and `next_due_at` recomputed from `interval_days` (`completed_at` defaults to now)

#### Scenario: List maintenance items

- **WHEN** `ha_maintenance_list(category=None, status=None)` is called
- **THEN** items SHALL be returned with a computed `status`: `due` (`next_due_at <= now()`), `upcoming` (within 7 days), or `ok` (more than 7 days away); never-completed items with `next_due_at IS NULL` are classified `due`

#### Scenario: Remove a maintenance item

- **WHEN** `ha_maintenance_remove(name)` is called
- **THEN** the item SHALL be deleted, or an error returned if no item with that name exists
