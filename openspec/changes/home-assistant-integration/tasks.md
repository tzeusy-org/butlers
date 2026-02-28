## 1. Module Foundation

- [ ] 1.1 Create `src/butlers/modules/home_assistant.py` with `HomeAssistantModule` class implementing `Module` ABC: `name`, `config_schema`, `dependencies`, `migration_revisions`, `tool_metadata`, empty `register_tools`/`on_startup`/`on_shutdown` stubs
- [ ] 1.2 Implement `HomeAssistantConfig` Pydantic model with fields: `url` (str, required), `verify_ssl` (bool, default false), `websocket_ping_interval` (int, default 30), `poll_interval_seconds` (int, default 60), `snapshot_interval_seconds` (int, default 300); extra="forbid"
- [ ] 1.3 Implement credential resolution in `on_startup`: call `resolve_owner_contact_info(pool, "home_assistant_token")`, cache result, raise `RuntimeError` if not found

## 2. HA Client Layer

- [ ] 2.1 Implement `httpx.AsyncClient` creation in `on_startup` with base_url, Bearer token header, Content-Type header, SSL verification; close in `on_shutdown`
- [ ] 2.2 Implement WebSocket connection in `on_startup`: connect to `ws://<url>/api/websocket`, authenticate (auth_required → auth → auth_ok flow), send supported_features with coalesce_messages
- [ ] 2.3 Implement WebSocket message loop as a background asyncio task: read messages, dispatch by type (event, result, pong), handle connection errors
- [ ] 2.4 Implement keepalive ping task: send ping every `websocket_ping_interval` seconds, detect missed pongs
- [ ] 2.5 Implement auto-reconnect with exponential backoff (1s initial, 60s cap, jitter): on disconnect, start REST polling fallback, attempt reconnection, re-subscribe and re-fetch state on success
- [ ] 2.6 Implement WebSocket command helper: send command with auto-incrementing ID, correlate response by ID, return result (used by registry queries and statistics)

## 3. Entity and Registry Cache

- [ ] 3.1 Implement `CachedEntity` dataclass and `dict[str, CachedEntity]` cache; populate from `GET /api/states` on startup
- [ ] 3.2 Subscribe to `state_changed` events via WebSocket; update cache on each event (replace entry, delete on null new_state)
- [ ] 3.3 Implement REST polling fallback: poll `GET /api/states` at `poll_interval_seconds` when WebSocket is down; replace full cache
- [ ] 3.4 Implement area registry cache: query `config/area_registry/list` via WebSocket at startup; subscribe to `area_registry_updated` for refresh
- [ ] 3.5 Implement entity registry cache: query `config/entity_registry/list` via WebSocket at startup; subscribe to `entity_registry_updated` for refresh; map entity_id → area_id/device_id/platform

## 4. MCP Tools — Query

- [ ] 4.1 Register `ha_get_entity_state(entity_id: str)` tool: serve from cache, include area name from registry, return None if not found
- [ ] 4.2 Register `ha_list_entities(domain: str | None, area: str | None)` tool: filter cache by entity_id prefix (domain) and registry area mapping; return sorted summaries (entity_id, state, friendly_name, area_name, domain)
- [ ] 4.3 Register `ha_list_areas()` tool: return sorted area list (area_id, name) from registry cache
- [ ] 4.4 Register `ha_list_services(domain: str | None)` tool: query via REST `GET /api/services` or WebSocket `get_services`; filter by domain if provided
- [ ] 4.5 Register `ha_get_history(entity_ids: list[str], start: str, end: str | None)` tool: call REST `GET /api/history/period/<start>` with filter_entity_id, end_time, minimal_response, significant_changes_only; raise ValueError if entity_ids is empty
- [ ] 4.6 Register `ha_get_statistics(statistic_ids: list[str], start: str, end: str, period: str)` tool: send WebSocket `recorder/get_statistics_during_period` command; validate period is one of 5minute/hour/day/week/month
- [ ] 4.7 Register `ha_render_template(template: str)` tool: call REST `POST /api/template`; return rendered plaintext

## 5. MCP Tools — Control

- [ ] 5.1 Register `ha_call_service(domain: str, service: str, target: dict | None, data: dict | None)` tool: call REST `POST /api/services/<domain>/<service>`, log to ha_command_log, return response
- [ ] 5.2 Register `ha_activate_scene(entity_id: str, transition: float | None)` tool: validate entity_id starts with `scene.`, delegate to ha_call_service
- [ ] 5.3 Implement `tool_metadata()` returning `ToolMeta(arg_sensitivities={"domain": True, "service": True})` for `ha_call_service`

## 6. Database

- [ ] 6.1 Create Alembic migration in `alembic/versions/core/` for `home` schema: `ha_entity_snapshot` table (entity_id TEXT PK, state TEXT, attributes JSONB, last_updated TIMESTAMPTZ, captured_at TIMESTAMPTZ) and `ha_command_log` table (id BIGSERIAL PK, domain TEXT, service TEXT, target JSONB, data JSONB, result JSONB, context_id TEXT, issued_at TIMESTAMPTZ) with index on issued_at
- [ ] 6.2 Implement snapshot persistence: UPSERT all cached entities into ha_entity_snapshot every `snapshot_interval_seconds` via background task; persist final snapshot in `on_shutdown`
- [ ] 6.3 Implement command log insert helper: called by ha_call_service after each service call (success or failure)

## 7. Butler Roster

- [ ] 7.1 Create `roster/home/butler.toml`: name=home, port=40108, schema=home, codex runtime, max_concurrent_sessions=3, switchboard registration, modules (home_assistant with URL, memory, contacts with Google sync, approvals), scheduled jobs (weekly-energy-digest, daily-environment-report, device-health-check, memory-consolidation, memory-episode-cleanup)
- [ ] 7.2 Create `roster/home/MANIFESTO.md`: home automation orchestrator identity, value proposition (comfort, energy awareness, device management, scene orchestration)
- [ ] 7.3 Create `roster/home/CLAUDE.md`: system prompt with Interactive Response Mode, home-context awareness, safety-first confirmation for destructive actions, Memory Classification taxonomy (comfort_preference, scene_preference, schedule_pattern, device_issue, energy_baseline)
- [ ] 7.4 Create `roster/home/AGENTS.md`: initialize with "# Notes to self" header
- [ ] 7.5 Create skills: `roster/home/skills/comfort/SKILL.md`, `roster/home/skills/energy/SKILL.md`, `roster/home/skills/scenes/SKILL.md`, `roster/home/skills/troubleshooting/SKILL.md`

## 8. Dashboard API

- [ ] 8.1 Create `roster/home/api/models.py`: Pydantic response models for entity state, entity summary, area, command log entry, statistics response
- [ ] 8.2 Create `roster/home/api/router.py`: endpoints for listing entities (with domain/area filters), getting entity detail, listing areas, querying command log (with time range), and getting entity snapshot status

## 9. Frontend

- [ ] 9.1 Add `home_assistant` to `SecretCategory` union and `SECRET_CATEGORIES` array in `frontend/src/lib/secret-templates.ts`; add `categoryFromKey` pattern for `HOME_ASSISTANT`
- [ ] 9.2 Document in owner contact setup that `home_assistant_token` must be added as a secured contact_info entry (type: home_assistant_token, value: HA long-lived access token)

## 10. Tests

- [ ] 10.1 Write unit tests for `HomeAssistantConfig` validation: required url, extra fields rejected, default values
- [ ] 10.2 Write unit tests for credential resolution: token found via contact_info, token missing raises RuntimeError, token not logged in full
- [ ] 10.3 Write unit tests for entity cache: initial population, state_changed update, entity removal on null new_state, REST polling fallback
- [ ] 10.4 Write unit tests for query tools: ha_get_entity_state (found, not found), ha_list_entities (filters), ha_list_areas, ha_get_history (entity_ids required), ha_get_statistics (period validation), ha_render_template
- [ ] 10.5 Write unit tests for control tools: ha_call_service (success, error, audit log written), ha_activate_scene (valid scene, invalid entity_id prefix)
- [ ] 10.6 Write unit tests for tool_metadata: sensitive args on ha_call_service, no entries for query tools
- [ ] 10.7 Write unit tests for DB operations: snapshot UPSERT, command log insert, migration creates tables and index
- [ ] 10.8 Write unit tests for dashboard API routes: entity list, entity detail, command log query, area list

## 11. Integration

- [ ] 11.1 Verify module auto-discovery: HomeAssistantModule appears in `default_registry().available_modules`
- [ ] 11.2 Verify butler auto-discovery: `roster/home/butler.toml` is discovered by the daemon
- [ ] 11.3 Verify dashboard route auto-discovery: `roster/home/api/router.py` is registered by `router_discovery.py`
- [ ] 11.4 End-to-end smoke test: start home butler, verify WebSocket connection to HA, query entity state, call a service, check command log entry
