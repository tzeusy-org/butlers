## Context

Butlers have no awareness of the physical home environment. Home Assistant (HA) runs locally on a machine in the same Tailscale tailnet, serving as the integration hub for Zigbee, Wi-Fi, and Z-Wave devices. HA exposes a stable REST API and WebSocket API on port 8123, both authenticated via long-lived access tokens (10-year validity, no scoping).

The existing module system (`Module` ABC in `butlers.modules.base`) provides the integration surface: `register_tools()`, `on_startup()`/`on_shutdown()`, `config_schema`, `migration_revisions()`, and `tool_metadata()` for approval sensitivity. The credential stack supports both butler-owned secrets (`CredentialStore.resolve()`) and identity-bound secrets (`resolve_owner_contact_info()`). No new infrastructure is needed — the integration is purely additive.

A research draft at `docs/modules/home_assistant_draft.md` covers the full HA API surface, entity data model, and Python client options. This design builds on that research.

## Goals / Non-Goals

**Goals:**
- Bidirectional smart-home control via MCP tools: query entity state, call HA services, fetch history/statistics, render templates
- Persistent WebSocket connection with in-memory entity cache for low-latency state reads
- Command audit logging for accountability
- Approval sensitivity for safety-critical actions (unlock, area-level power-off)
- Dedicated `home` butler with scheduled jobs for energy/environment monitoring
- Cross-butler access via Switchboard (health butler adjusting climate, general butler controlling lights)

**Non-Goals:**
- Automation creation/deletion via HA config API (fragile, version-dependent)
- Camera image capture or analysis (privacy-sensitive, deferred to v2)
- Presence-based butler triggering from HA device trackers
- Statistics aggregation in butler DB (query HA's recorder directly)
- Cross-butler area coordination protocol (use Switchboard ad-hoc)
- WebSocket event forwarding to other butlers

## Decisions

### D1: Transport — httpx + aiohttp WebSocket (no external HA library)

**Decision:** Use `httpx.AsyncClient` for REST calls and `aiohttp.ClientWebSocketResponse` for the WebSocket connection. Do not add `python-hass-client` or `HomeAssistant-API` as dependencies.

**Rationale:**
- The telegram module already uses `httpx.AsyncClient` directly — consistent codebase pattern
- HA's REST API is trivial to call (Bearer token header + JSON body)
- HA's WebSocket protocol is simple (JSON messages with incrementing integer IDs)
- `python-hass-client` doesn't wrap registry commands (`config/entity_registry/list`) or statistics commands (`recorder/get_statistics_during_period`), which we need
- No intermediate abstraction to fight when HA API evolves
- `aiohttp` is already a transitive dependency in the project

**Alternative considered:** `python-hass-client` — rejected because it would add a dependency that covers only ~60% of our needs, requiring raw fallback for the rest.

### D2: Credential model — owner contact_info for token, butler.toml for URL

**Decision:** The HA long-lived access token is stored as a `secured` entry in `shared.contact_info` with `type = "home_assistant_token"` on the owner contact. The HA base URL is a plain config value in `butler.toml`.

**Rationale:**
- The token is identity-bound (tied to an HA user account) — fits the `contact_info` pattern used by telegram user tokens
- `resolve_owner_contact_info(pool, "home_assistant_token")` already handles this lookup with graceful degradation
- The URL is infrastructure config (not a secret) — belongs in `butler.toml` alongside other module settings
- No new credential resolution code needed

**Config shape:**
```toml
[modules.home_assistant]
url = "http://homeassistant.tail1234.ts.net:8123"
verify_ssl = false
websocket_ping_interval = 30
poll_interval_seconds = 60
```

**Alternative considered:** Storing the token in `butler_secrets` via `CredentialStore` — rejected because the token belongs to a person (the homeowner), not the butler. Other butlers accessing HA through Switchboard would share the same owner token, reinforcing the contact-level storage.

### D3: Entity cache — in-memory dict with WebSocket subscription

**Decision:** On startup, fetch all entity states via REST `GET /api/states` and populate an in-memory `dict[str, EntityState]`. Subscribe to `state_changed` events via WebSocket and update the cache on each event. Tool calls serve from cache with `last_updated` timestamps. On WebSocket disconnect, fall back to periodic REST polling at `poll_interval_seconds` while reconnecting in the background.

**Rationale:**
- HA can have hundreds of entities; fetching on every tool call wastes round trips
- WebSocket subscription gives sub-second state freshness
- Cache is ephemeral (rehydrated on startup) — no stale data risk across restarts
- Fallback polling ensures the module degrades gracefully

**Cache data structure:**
```python
@dataclass
class CachedEntity:
    entity_id: str
    state: str
    attributes: dict[str, Any]
    last_changed: str  # ISO 8601
    last_updated: str  # ISO 8601
```

### D4: Tool surface — lean (9 tools), generic ha_call_service as escape hatch

**Decision:** Expose 7 query tools and 2 control tools. Use a single generic `ha_call_service` for all device control, with `ha_activate_scene` as the only convenience wrapper.

**Query tools:**

| Tool | Source | Description |
|------|--------|-------------|
| `ha_get_entity_state` | Cache | Single entity state by ID |
| `ha_list_entities` | Cache | Entities filtered by domain and/or area, returns summaries |
| `ha_list_areas` | WS registry | All defined areas/rooms |
| `ha_list_services` | WS/REST | Available services by domain |
| `ha_get_history` | REST | State history for entities over a time window |
| `ha_get_statistics` | WS | Aggregated hourly/daily statistics for sensors |
| `ha_render_template` | REST | Evaluate a Jinja2 template server-side |

**Control tools:**

| Tool | Transport | Description |
|------|-----------|-------------|
| `ha_call_service` | REST | Generic service call (domain, service, target, data) |
| `ha_activate_scene` | REST | Activate a scene by entity_id (convenience wrapper) |

**Rationale:**
- 9 tools keeps the LLM context budget manageable (vs. 24 in the draft or 97+ in ha-mcp)
- `ha_call_service` is universal — the LLM can call any HA service via domain + service + target + data
- The LLM discovers available services via `ha_list_services` and entity capabilities via `ha_get_entity_state` (which includes `supported_features` and `supported_color_modes` in attributes)
- `ha_activate_scene` is a standalone convenience because scenes are a very common operation with a simple parameter (just the scene entity_id)
- Approval sensitivity is enforced at the `ha_call_service` level by inspecting `domain`/`service` args in `tool_metadata()`

**Alternative considered:** 15+ domain-specific control tools (ha_turn_on, ha_set_light, ha_set_climate, etc.) — rejected because they bloat the tool surface without adding capability the LLM can't already achieve via ha_call_service. The LLM is capable of composing `{"domain": "light", "service": "turn_on", "target": {"entity_id": "light.kitchen"}, "data": {"brightness_pct": 80}}`.

### D5: Approval sensitivity — arg-level inspection on ha_call_service

**Decision:** Declare `domain` and `service` as sensitive args on `ha_call_service` via `tool_metadata()`. The approvals module uses these to classify risk tier dynamically:

| Pattern | Risk | Examples |
|---------|------|----------|
| `lock.unlock`, `lock.open` | always | Front door unlock |
| `homeassistant.turn_off` with area target | high | Area-level power-off |
| `cover.open_cover`, `cover.set_cover_position` | medium | Garage door |
| All other service calls | low | Lights, climate, media |
| All query tools | none | State reads, history |

**Rationale:**
- One generic tool with arg-level sensitivity is more maintainable than separate gated tools per domain
- The approvals module already supports `arg_sensitivities` via `ToolMeta`
- New safety-critical services can be added to the sensitivity map without changing the tool surface

### D6: Database schema — two tables in butler's schema

**Decision:** Two tables in the `home` schema:

**`home.ha_entity_snapshot`** — periodically persisted cache for offline context:
```sql
CREATE TABLE home.ha_entity_snapshot (
    entity_id    TEXT PRIMARY KEY,
    state        TEXT NOT NULL,
    attributes   JSONB,
    last_updated TIMESTAMPTZ NOT NULL,
    captured_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

**`home.ha_command_log`** — audit trail of issued commands:
```sql
CREATE TABLE home.ha_command_log (
    id          BIGSERIAL PRIMARY KEY,
    domain      TEXT NOT NULL,
    service     TEXT NOT NULL,
    target      JSONB,
    data        JSONB,
    result      JSONB,
    context_id  TEXT,
    issued_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX ix_ha_command_log_issued_at ON home.ha_command_log (issued_at);
```

**Rationale:**
- `ha_entity_snapshot` lets the butler reference home state even when HA is temporarily unreachable. Uses UPSERT (ON CONFLICT DO UPDATE) to keep one row per entity.
- `ha_command_log` provides an audit trail the butler can surface on request ("what did I do to the lights last night?"). Indexed by `issued_at` for time-range queries.
- No history storage — use `ha_get_history` to query HA's recorder directly.
- Snapshot persistence is triggered periodically (e.g., every 5 minutes) from the cache, not on every state change.

### D7: WebSocket lifecycle — persistent connection with auto-reconnect

**Decision:** The module maintains a single persistent WebSocket connection throughout the butler daemon's lifetime. Connection management:

1. **Connect** in `on_startup()` — authenticate, subscribe to `state_changed` events
2. **Keepalive** — send `ping` every `websocket_ping_interval` seconds (default 30)
3. **Reconnect** — on disconnect, exponential backoff starting at 1s, capped at 60s, with jitter. During reconnect, fall back to REST polling.
4. **Disconnect** in `on_shutdown()` — clean close

**Rationale:**
- Persistent connection gives sub-second state freshness for the cache
- HA restarts (updates, config reloads) are the primary disconnect cause — auto-reconnect handles this transparently
- Polling fallback ensures the module never goes fully blind

### D8: Home butler identity and configuration

**Decision:** Dedicated `home` butler at port 40108, schema `home`, runtime `codex`.

**Modules:** `home_assistant`, `memory`, `contacts`, `approvals`

**Scheduled jobs:**
- `weekly-energy-digest` (Sunday 9 AM) — summarize energy consumption trends
- `daily-environment-report` (8 AM) — temperature, humidity, air quality snapshot
- `device-health-check` (4 AM) — detect unavailable entities, low batteries

**Skills:**
- `comfort` — adjusting climate, lighting, and scenes for daily routines
- `energy` — monitoring consumption, identifying waste, optimizing usage
- `scenes` — creating and activating multi-device scenes
- `troubleshooting` — diagnosing unavailable devices, connectivity issues

**Rationale:**
- Port 40108 follows sequential allocation (40107 = education, the last assigned)
- Codex runtime matches other domain butlers (health, finance, education)
- Approvals module needed for the ha_call_service sensitivity gating
- Memory module lets the butler learn patterns ("owner prefers 21°C at bedtime")

### D9: ha_list_entities output — summaries with domain/area filtering

**Decision:** `ha_list_entities` returns compact summaries (entity_id, state, friendly_name, area, domain) with optional `domain` and `area_id` filters. Full attributes are only returned by `ha_get_entity_state` for a single entity.

**Rationale:**
- A large HA install has 500+ entities. Returning full attributes for all of them would consume the LLM's context budget.
- The two-step pattern (list → get detail) is how humans use HA too.
- Filters prevent the LLM from having to post-process a massive list.

### D10: Area and device registry — cached on startup, refreshed on registry events

**Decision:** Fetch area registry (`config/area_registry/list`) and entity registry (`config/entity_registry/list`) via WebSocket at startup. Subscribe to `area_registry_updated` and `entity_registry_updated` events to keep them current. These registries power the `ha_list_entities` area filtering and `ha_list_areas` tool.

**Rationale:**
- Area context is critical for natural language commands ("turn off all lights in the bedroom")
- Registry changes are rare (new devices, area renames) — subscription overhead is negligible
- Entity registry provides `area_id`, `device_id`, and `platform` metadata not available in state objects

## Risks / Trade-offs

| Risk | Severity | Mitigation |
|------|----------|------------|
| HA unavailable (restart, network) | Medium | WebSocket auto-reconnect + REST polling fallback + entity snapshot table for cached context |
| Token revocation/expiration | Low | 10-year LLAT validity; module logs first 8 chars of token for identity debugging; dashboard shows `home_assistant_token` contact_info status |
| Entity ID renames by user | Low | Entity registry subscription detects `entity_registry_updated` events; cache keys are updated atomically |
| Large entity count (500+) | Low | `ha_list_entities` returns summaries with domain/area filters; full attributes only via `ha_get_entity_state` |
| Lean tool surface insufficient | Low | `ha_call_service` is a universal escape hatch; new convenience tools can be added incrementally without breaking changes |
| Lock/unlock safety | High | `tool_metadata()` declares `lock.unlock` as `always` approval; command audit log provides forensic trail |
| aiohttp WebSocket memory | Low | Single connection, message-level processing (no buffering); HA event volume is manageable for a home deployment |

## Open Questions

1. **Snapshot persistence frequency** — Every 5 minutes seems reasonable, but should it be configurable in butler.toml? (Leaning yes: `snapshot_interval_seconds = 300`)
2. **ha_list_entities default limit** — Should the tool cap results at e.g., 100 entities, or return all matching? (Leaning: return all with a warning if > 100, since the LLM needs the full picture for area-level commands)
3. **Cross-butler HA access pattern** — When the health butler asks the home butler to "set bedroom to 20°C," does it use a specific Switchboard routing contract, or a free-form prompt? (Leaning: free-form prompt via Switchboard, since HA commands are naturally expressed in natural language)
