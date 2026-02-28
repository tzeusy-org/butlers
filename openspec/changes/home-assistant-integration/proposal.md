## Why

Butlers currently have no awareness of the physical home environment. A Home Assistant integration unlocks bidirectional smart-home control — querying sensor data, controlling appliances (lights, climate, locks, covers, media), activating scenes, and pulling historical statistics — all over the local tailnet with no cloud dependency. Home Assistant serves as the glue layer between diverse device protocols (Zigbee, Wi-Fi, Z-Wave) and the butler, letting any butler orchestrate the home through a single, well-documented API surface.

## What Changes

- **New `home_assistant` module** — pluggable module providing MCP tools for querying entity state, calling HA services, fetching history/statistics, and rendering templates. Maintains a persistent WebSocket connection with in-memory entity cache updated via `state_changed` subscriptions. Falls back to REST polling when the WebSocket is unavailable. Authenticates using the owner's `home_assistant_token` (secured contact_info entry) and a butler.toml-configured HA base URL.
- **New `home` butler** — dedicated butler for home automation orchestration. Loads the `home_assistant` module (plus standard modules: memory, contacts, approvals). Owns scheduled jobs for home monitoring (energy summaries, environmental reports, device health checks). Skills cover comfort management, energy awareness, scene orchestration, and appliance troubleshooting.
- **Approval sensitivity metadata** — the module declares tool-level sensitivity via `tool_metadata()`: read tools are unguarded, control tools use conditional approval, and safety-critical actions (unlock, area-level power-off) require explicit approval.
- **Command audit log** — every service call issued through the module is persisted to `home.ha_command_log` for accountability and debugging.
- **Entity snapshot table** — periodically persisted entity state cache (`home.ha_entity_snapshot`) so the butler retains home context even when HA is temporarily unreachable.

## Capabilities

### New Capabilities

- `module-home-assistant`: Home Assistant module providing entity state querying, service call execution, history/statistics retrieval, template rendering, WebSocket subscription management, in-memory entity cache, and command audit logging. Covers the MCP tool surface, connection lifecycle, caching strategy, DB schema, and approval sensitivity declarations.
- `butler-home`: Home butler identity, purpose, module configuration, scheduled jobs (energy digest, environment report, device health), skills (comfort, energy, scenes, troubleshooting), and cross-butler interaction patterns via Switchboard.

### Modified Capabilities

_(none — the existing `core-credentials`, `contacts-identity`, and `core-modules` specs already support the patterns needed: secured contact_info for owner tokens, CredentialStore resolution, and module registration with tool_metadata)_

## Impact

- **New files:**
  - `src/butlers/modules/home_assistant.py` — module implementation (register_tools, on_startup/on_shutdown, config schema, migration_revisions, tool_metadata)
  - `roster/home/` — full butler config directory per adding-butlers-to-roster checklist:
    - `butler.toml` — identity (name, port, db schema), modules config, scheduled jobs
    - `MANIFESTO.md` — public-facing identity and value proposition
    - `CLAUDE.md` — system prompt with Interactive Response Mode and Memory Classification
    - `AGENTS.md` — runtime agent notes (initialized empty)
    - `skills/` — workflow skills (comfort management, energy awareness, scene orchestration)
  - `roster/home/api/router.py` + `models.py` — dashboard API routes (entity browser, command log viewer)
  - Alembic migration for `home` schema tables (`ha_entity_snapshot`, `ha_command_log`)
  - `tests/` — unit tests for module tools, API routes, connection lifecycle, and caching behavior
- **New dependency:** `hass-client` (python-hass-client) for asyncio WebSocket/REST HA communication, with fallback to raw `httpx` + `aiohttp` if the library proves limiting.
- **Frontend:** New `home_assistant` secret category in `secret-templates.ts`. Owner contact_info entry for `home_assistant_token` (secured). Optional dashboard page for entity state browsing and command history.
- **Switchboard:** Register `home` butler for routing. Other butlers (health, general) can request home actions via Switchboard MCP calls.
- **Existing code unchanged** — no modifications to core infrastructure, existing modules, or other butlers. The integration is purely additive.
