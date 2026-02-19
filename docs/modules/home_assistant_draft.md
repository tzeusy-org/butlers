# Home Assistant Module Research Draft

Status: **Draft** (Research Only — no implementation)
Last updated: 2026-02-19
Author: Research pass, butlers-962.5
Depends on: `src/butlers/modules/base.py`, `docs/connectors/interface.md`

---

## 1. Purpose

This document captures research into a Home Assistant module providing full
bidirectional smart-home interactivity for butlers. Unlike read-only data
ingestion, this module enables:

- Querying real-time state of any entity (sensors, lights, climate, media,
  locks, covers, switches, etc.)
- Issuing commands to devices (turn on/off, set brightness, set temperature,
  play media, etc.)
- Activating and managing scenes
- Triggering and managing automations
- Subscribing to live state changes via WebSocket
- Querying historical data and long-term statistics
- Evaluating Jinja2 templates server-side

Home Assistant runs locally and is accessible within the tailnet, so all
communication is LAN/tailnet-only — no cloud dependency.

This is a **research-only** deliverable. No implementation code accompanies
this doc. The goal is to identify the best API approach, map data models to
existing butler conventions, and surface risk factors for a future
implementation ticket.

---

## 2. Home Assistant API Surface

Home Assistant exposes two primary programmatic interfaces:

| Interface | Transport | Best For |
|---|---|---|
| REST API | HTTP/HTTPS | One-shot queries, service calls, history |
| WebSocket API | WS/WSS | Subscriptions, live state, low-latency |

Both share the same authentication model and run on port 8123 (default).

### 2.1 REST API

**Base URL:** `http://<ha-host>:8123/api/`

All requests require `Authorization: Bearer <TOKEN>` and
`Content-Type: application/json` headers.

**GET endpoints:**

| Path | Description |
|---|---|
| `/api/` | Health check — returns `{"message": "API running."}` |
| `/api/config` | System configuration (location, timezone, version, components) |
| `/api/components` | List of all loaded components |
| `/api/events` | List of available event types |
| `/api/services` | All available service calls by domain |
| `/api/states` | All entity states (potentially large — hundreds of entities) |
| `/api/states/<entity_id>` | Single entity state |
| `/api/history/period/<timestamp>` | Historical state changes (optional) |
| `/api/logbook/<timestamp>` | Human-readable activity log |
| `/api/error_log` | Session error log (plaintext) |
| `/api/camera_proxy/<camera_id>` | Camera snapshot image bytes |
| `/api/calendars` | Calendar entities |
| `/api/calendars/<calendar_id>` | Events for a calendar (requires `start` + `end` params) |

**POST endpoints:**

| Path | Description | Body |
|---|---|---|
| `/api/states/<entity_id>` | Create/update virtual entity state | `{"state": "...", "attributes": {...}}` |
| `/api/events/<event_type>` | Fire an event onto the event bus | `{event_data}` |
| `/api/services/<domain>/<service>` | Call a service action | `{service_data}` |
| `/api/template` | Render a Jinja2 template | `{"template": "..."}` |
| `/api/config/core/check_config` | Validate configuration | `{}` |
| `/api/intent/handle` | Handle a natural language intent | `{"name": "...", "data": {...}}` |

Query parameters for `/api/history/period/<timestamp>`:
- `filter_entity_id` — comma-separated entity IDs (required for performance)
- `end_time` — ISO 8601 end timestamp (URL-encoded)
- `minimal_response` — omit attributes, return only state + last_changed
- `no_attributes` — exclude attribute data entirely
- `significant_changes_only` — filter out trivial state transitions

The service call endpoint (`/api/services/<domain>/<service>`) returns an
array of changed entity states. Append `?return_response` to receive the
service-specific response object (e.g., for `conversation.process`).

**DELETE endpoints:**

| Path | Description |
|---|---|
| `/api/states/<entity_id>` | Remove a virtual entity |

**Rate limits:** The official REST API documentation does not document rate
limits. In practice, the HA HTTP server is an asyncio loop with no built-in
throttle. The practical bottleneck is the local network and HA's internal
asyncio task queue. For normal butler usage (polling every 10–60 s, issuing
commands on demand), rate limits are not a concern.

### 2.2 WebSocket API

The WebSocket API is the preferred interface for subscriptions and real-time
state tracking.

**Endpoint:** `ws://<ha-host>:8123/api/websocket`

**Connection and auth flow:**

```
1. Client connects
2. Server: {"type": "auth_required", "ha_version": "2025.x.y"}
3. Client: {"type": "auth", "access_token": "<TOKEN>"}
4. Server: {"type": "auth_ok", "ha_version": "2025.x.y"}
   — or —
   {"type": "auth_invalid", "message": "Invalid password"}
5. (optional) Client: {"id": 1, "type": "supported_features", "features": {"coalesce_messages": 1}}
6. Client sends commands with incrementing integer IDs
```

The `coalesce_messages` feature allows HA to batch multiple messages into a
single WebSocket frame for efficiency — recommended for high-volume
subscriptions.

**Message format:** Every post-auth message is a JSON object with:
- `id` — unique integer per request, used for response correlation
- `type` — command type
- additional type-specific fields

**Core commands:**

| Command | Description |
|---|---|
| `get_states` | Retrieve all current entity states |
| `get_config` | System configuration |
| `get_services` | All available service definitions |
| `get_panels` | Registered UI panels |
| `call_service` | Execute a service action (supports `return_response`) |
| `fire_event` | Emit a custom event to the event bus |
| `subscribe_events` | Subscribe to all or a specific event type |
| `subscribe_trigger` | Subscribe to an automation-style trigger |
| `unsubscribe_events` | Cancel an active subscription by subscription ID |
| `validate_config` | Validate trigger/condition/action syntax |
| `extract_from_target` | Resolve entities/devices/areas from a target spec |
| `get_triggers_for_target` | Get trigger templates for a target |
| `get_conditions_for_target` | Get condition templates for a target |
| `get_services_for_target` | Get service templates for a target |
| `ping` | Keepalive — server responds with `{"type": "pong"}` |

**Registry commands** (available but not in the main docs):

| Command | Description |
|---|---|
| `config/area_registry/list` | All defined areas (rooms/zones) |
| `config/device_registry/list` | All registered devices |
| `config/entity_registry/list` | All entity registry entries with metadata |
| `config/label_registry/list` | All entity labels |

**Statistics commands** (WebSocket only):

| Command | Description |
|---|---|
| `recorder/get_statistics_during_period` | Long-term hourly stats for a time window |
| `recorder/list_statistic_ids` | All statistic IDs with metadata |
| `recorder/statistics_during_period` | Combined short + long term stats |

**Error handling:** Failed commands return `{"success": false, "error": {"code": "...", "message": "..."}}`. The `result` key is present on success.

**Keepalive:** Send `ping` at regular intervals (recommend 30 s). If the server
misses 3 pings, treat the connection as dead and reconnect.

---

## 3. Authentication Model

### 3.1 Long-Lived Access Tokens

Long-lived access tokens (LLATs) are the recommended credential type for
service-to-service integrations like a butler module.

**Properties:**
- Valid for **10 years**
- Created via the HA web UI at `http://<ha-host>:8123/profile` under
  "Long-Lived Access Tokens"
- Can also be created programmatically via the WebSocket command
  `auth/long_lived_access_token` (requires an authenticated session first)
- **Shown only once at creation** — must be stored securely
- Tied to the creating user account — inherits all permissions of that user
- Not rotated automatically; can be manually revoked via the UI

**No scoped tokens:** Home Assistant does not support fine-grained OAuth scopes
for LLATs. A token has the same permissions as its owning user. The practical
mitigation is to create a dedicated "butler" HA user with limited permissions
(read-only, or with specific device access) rather than using an admin account.

**Standard OAuth flow:** Home Assistant also supports a full OAuth 2.0
authorization code flow with short-lived access tokens (1800 s) and refresh
tokens. This is appropriate for multi-tenant or user-facing integrations but
adds complexity not warranted for a single-butler, single-homeowner deployment.

**Recommended approach for butler:** Store the LLAT in an environment variable
(e.g., `HA_TOKEN`) referenced by name in `butler.toml`. The module reads it at
startup and uses it for all requests. No refresh logic needed.

### 3.2 Security with Tailnet

Since HA runs locally and is accessible over Tailscale:

- Communication happens entirely within the tailnet — no public internet
  exposure
- TLS is optional (HTTP acceptable on trusted LAN/tailnet) but HTTPS is
  preferable if HA is configured with SSL
- LLATs should be rotated annually as a hygiene practice even though 10-year
  validity is permitted
- The butler should never log the token; only log the first 8 characters for
  debugging identity

---

## 4. Entity Data Model

### 4.1 State Object

Every entity in HA is represented as a state object:

```json
{
  "entity_id": "light.living_room",
  "state": "on",
  "attributes": {
    "brightness": 200,
    "color_temp_kelvin": 3000,
    "friendly_name": "Living Room Light",
    "supported_features": 44
  },
  "last_changed": "2026-02-19T10:30:00+00:00",
  "last_updated": "2026-02-19T10:30:05+00:00",
  "context": {
    "id": "01JKXYZ...",
    "parent_id": null,
    "user_id": "abc123"
  }
}
```

Key fields:
- `entity_id` — `<domain>.<object_id>` format (e.g., `sensor.temperature`)
- `state` — string representation of the current state (on/off, temperature
  value, enum state like `playing`, `idle`, `unavailable`, `unknown`)
- `attributes` — domain-specific metadata dict
- `last_changed` — ISO 8601 timestamp of last state value change
- `last_updated` — ISO 8601 timestamp of last attribute or state update
- `context` — audit trail linking to the trigger (automation, user action)

### 4.2 Entity Domains and Key Attributes

**Sensor (`sensor.*`)**

| Attribute | Description |
|---|---|
| `unit_of_measurement` | e.g., `°C`, `W`, `lx`, `%` |
| `device_class` | Semantic type: `temperature`, `humidity`, `power`, `energy`, `illuminance`, `motion`, `door`, etc. |
| `state_class` | `measurement`, `total`, `total_increasing` — controls statistics |
| `friendly_name` | Human-readable name |

States: numeric string (e.g., `"22.5"`), `unavailable`, `unknown`.

**Light (`light.*`)**

| Attribute | Description |
|---|---|
| `brightness` | 0–255 integer |
| `color_temp_kelvin` | Color temperature in Kelvin |
| `hs_color` | `[hue 0–360, saturation 0–100]` |
| `rgb_color` | `[r, g, b]` 0–255 |
| `color_mode` | `color_temp`, `hs`, `rgb`, `onoff`, `brightness`, `white` |
| `supported_color_modes` | List of color modes the device supports |
| `supported_features` | Bitmask: transition, flash, effect |
| `effect_list` | Available effects (e.g., `["colorloop", "random"]`) |

States: `on`, `off`, `unavailable`.

**Switch (`switch.*`)**

States: `on`, `off`, `unavailable`.
No meaningful attributes beyond `friendly_name`.

**Climate (`climate.*`)**

| Attribute | Description |
|---|---|
| `current_temperature` | Current measured temp (float) |
| `target_temperature` | Desired setpoint |
| `target_temperature_high/low` | Range setpoint (dual-setpoint devices) |
| `hvac_mode` | `off`, `heat`, `cool`, `heat_cool`, `auto`, `dry`, `fan_only` |
| `hvac_action` | Current action: `heating`, `cooling`, `idle`, `off` |
| `preset_mode` | `away`, `home`, `sleep`, `eco`, etc. |
| `fan_mode` | `auto`, `low`, `medium`, `high` |
| `hvac_modes` | Supported HVAC modes |
| `temperature_unit` | `°C` or `°F` |

States: mirrors `hvac_mode`.

**Media Player (`media_player.*`)**

| Attribute | Description |
|---|---|
| `media_content_id` | URI or ID of current media |
| `media_content_type` | `music`, `tvshow`, `movie`, `playlist`, etc. |
| `media_title` | Current track/show title |
| `media_artist` | Artist name |
| `media_album_name` | Album |
| `volume_level` | 0.0–1.0 float |
| `is_volume_muted` | Boolean |
| `source` | Active input source |
| `source_list` | Available sources |
| `media_duration` | Seconds |
| `media_position` | Current playback position |

States: `playing`, `paused`, `idle`, `off`, `standby`, `buffering`,
`unavailable`.

**Cover (`cover.*`)** — blinds, garage doors, shutters

States: `open`, `closed`, `opening`, `closing`, `unavailable`.
Attributes: `current_position` (0–100), `current_tilt_position`.

**Lock (`lock.*`)**

States: `locked`, `unlocked`, `locking`, `unlocking`, `unavailable`.

**Binary Sensor (`binary_sensor.*`)** — motion, door, window, smoke, etc.

States: `on` (detected/open/active), `off` (clear/closed/inactive), `unavailable`.
Key attributes: `device_class` (motion, door, window, smoke, moisture, etc.).

**Scene (`scene.*`)**

States: `scening` (momentary when activated — HA has no persistent state for scenes).
Activation: call `scene.turn_on` or `scene.apply` (ad-hoc, no pre-definition needed).

**Automation (`automation.*`)**

States: `on` (enabled), `off` (disabled).
Activation: call `automation.trigger` to manually fire; `automation.turn_on` /
`automation.turn_off` to enable/disable.

### 4.3 Entity Registry vs. State

The **state** (via `/api/states`) reflects the current runtime value.
The **entity registry** (via WebSocket `config/entity_registry/list`) provides:
- `unique_id` — persistent identifier across renames
- `platform` — integration that owns the entity
- `device_id` — link to the device registry
- `area_id` — spatial assignment
- `name` — user-assigned name override
- `disabled_by` — if entity is disabled, who disabled it

The **device registry** groups entities by physical device (e.g., a Philips Hue
bulb is one device with 5 entities: the light, a power sensor, an energy sensor,
etc.).

The **area registry** maps `area_id` → human-readable room name (Kitchen,
Living Room, etc.). Area context is critical for natural language commands like
"turn off all lights in the bedroom."

---

## 5. Service Interface (Actions)

Services are the primary write mechanism in Home Assistant. Every controllable
device exposes services via `POST /api/services/<domain>/<service>` (REST) or
the `call_service` WebSocket command.

### 5.1 Targeting

All service calls accept a `target` object that resolves to entity IDs:

```json
{
  "target": {
    "entity_id": "light.kitchen",
    "area_id": "kitchen",
    "device_id": "abc123def456"
  }
}
```

Any combination of `entity_id`, `area_id`, and `device_id` can be mixed.
`entity_id` accepts a list.

### 5.2 Key Service Calls

**Light control:**

```json
POST /api/services/light/turn_on
{
  "target": {"entity_id": "light.living_room"},
  "brightness_pct": 80,
  "color_temp_kelvin": 3000,
  "transition": 2.0
}
```

`light.turn_on` parameters:
- `brightness` (0–255), `brightness_pct` (0–100), `brightness_step`, `brightness_step_pct`
- `color_temp_kelvin`, `hs_color`, `rgb_color`, `color_name`
- `transition` (seconds), `flash` (`short`/`long`), `effect`

`light.turn_off`: `entity_id`/target + optional `transition`, `flash`.

**Climate:**

```json
POST /api/services/climate/set_temperature
{
  "target": {"entity_id": "climate.bedroom"},
  "temperature": 21.5
}
```

Services: `set_temperature`, `set_hvac_mode`, `set_preset_mode`, `set_fan_mode`,
`turn_on`, `turn_off`.

**Media Player:**

```json
POST /api/services/media_player/play_media
{
  "target": {"entity_id": "media_player.living_room_tv"},
  "media_content_id": "spotify:playlist:37i9dQZF1DXcBWIGoYBM5M",
  "media_content_type": "music"
}
```

Services: `play_media`, `media_play`, `media_pause`, `media_stop`,
`media_next_track`, `media_previous_track`, `volume_set` (0.0–1.0),
`volume_up`, `volume_down`, `volume_mute`, `select_source`.

**Scene:**

```json
POST /api/services/scene/turn_on
{
  "target": {"entity_id": "scene.movie_night"},
  "transition": 1.5
}
```

Ad-hoc scene (no prior definition needed):

```json
POST /api/services/scene/apply
{
  "entities": {
    "light.living_room": {"state": "on", "brightness": 50},
    "media_player.tv": {"state": "on"}
  },
  "transition": 2.0
}
```

**Automation:**

```json
POST /api/services/automation/trigger
{"entity_id": "automation.morning_routine"}

POST /api/services/automation/turn_off
{"entity_id": "automation.vacation_mode"}
```

**Switch:**

```json
POST /api/services/switch/turn_on
{"target": {"entity_id": "switch.garden_pump"}}
```

**Cover:**

```json
POST /api/services/cover/set_cover_position
{"target": {"entity_id": "cover.bedroom_blind"}, "position": 50}
```

**Lock:**

```json
POST /api/services/lock/lock
{"target": {"entity_id": "lock.front_door"}}
```

Services: `lock`, `unlock`, `open` (if supported).

**Homeassistant domain (cross-domain):**

```json
POST /api/services/homeassistant/turn_on
{"target": {"area_id": "bedroom"}}

POST /api/services/homeassistant/reload_all
{}
```

The `homeassistant.turn_on` / `turn_off` / `toggle` calls work across all
entity types (lights, switches, media players, covers) in a given target.

### 5.3 Automation Management (Config API)

Automations can be created and deleted via the config API (not the standard
services API). This is used by the automation editor in the UI:

```
POST   /api/config/automation/config/           # create
GET    /api/config/automation/config/<auto_id>  # get by ID
POST   /api/config/automation/config/<auto_id>  # update
DELETE /api/config/automation/config/<auto_id>  # delete
```

The request body is the automation YAML converted to JSON (triggers, conditions,
actions arrays).

---

## 6. WebSocket Subscriptions

### 6.1 State Change Subscription

Subscribe to all state changes:

```json
{"id": 2, "type": "subscribe_events", "event_type": "state_changed"}
```

Each event message:

```json
{
  "id": 2,
  "type": "event",
  "event": {
    "event_type": "state_changed",
    "data": {
      "entity_id": "sensor.living_room_temperature",
      "old_state": {"state": "21.5", "attributes": {...}, ...},
      "new_state": {"state": "22.0", "attributes": {...}, ...}
    },
    "time_fired": "2026-02-19T10:31:00+00:00"
  }
}
```

To filter by entity, subscribe and filter client-side, or use
`subscribe_trigger` with a `state` trigger template:

```json
{
  "id": 3,
  "type": "subscribe_trigger",
  "trigger": {
    "platform": "state",
    "entity_id": "binary_sensor.front_door",
    "to": "on"
  }
}
```

### 6.2 Registry Subscriptions

To detect new devices added or removed:

```json
{"id": 4, "type": "subscribe_events", "event_type": "entity_registry_updated"}
{"id": 5, "type": "subscribe_events", "event_type": "device_registry_updated"}
{"id": 6, "type": "subscribe_events", "event_type": "area_registry_updated"}
```

### 6.3 Statistics Query (WebSocket)

```json
{
  "id": 7,
  "type": "recorder/get_statistics_during_period",
  "start_time": "2026-02-01T00:00:00+00:00",
  "end_time": "2026-02-19T00:00:00+00:00",
  "statistic_ids": ["sensor.total_energy_kwh"],
  "period": "hour",
  "units": {"energy": "kWh"}
}
```

`period` options: `5minute`, `hour`, `day`, `week`, `month`.

Response shape per statistic ID:

```json
{
  "sensor.total_energy_kwh": [
    {
      "start": "2026-02-01T00:00:00+00:00",
      "end": "2026-02-01T01:00:00+00:00",
      "mean": null,
      "min": null,
      "max": null,
      "sum": 1.23,
      "state": 1.23
    }
  ]
}
```

`mean`/`min`/`max` are populated for `measurement` sensors; `sum`/`state` for
`total`/`total_increasing` sensors.

---

## 7. Template Evaluation

Home Assistant uses Jinja2 for templates. The API can render them on demand:

```
POST /api/template
{"template": "{{ states('sensor.living_room_temperature') | float | round(1) }} °C"}
```

Response: `"22.0 °C"` (plaintext string).

Useful for complex aggregations and conditional logic that would otherwise
require multiple API calls. HA's Jinja2 environment includes:

- `states('entity_id')` — current state string
- `state_attr('entity_id', 'attribute')` — attribute value
- `is_state('entity_id', 'value')` — boolean predicate
- `area_entities('area_name')` — all entity IDs in an area
- `area_id('entity_id')` — area for an entity
- `expand(entity_id)` — expand groups and areas to individual entities
- `now()`, `utcnow()` — current timestamps
- `as_timestamp(dt)` — datetime to Unix timestamp
- `distance(entity1, entity2)` — GPS distance

The WebSocket equivalent is the `render_template` command (if available in the
connected HA version) or calling the REST endpoint.

---

## 8. Python Client Library Options

### 8.1 `python-hass-client` (Recommended)

**Repository:** `github.com/music-assistant/python-hass-client`
**PyPI:** `hass-client`

A lightweight asyncio-native client covering both REST and WebSocket APIs.
Used by the Music Assistant project and tested in production.

Key features:
- `HomeAssistantClient` as an async context manager
- `subscribe_events(callback, event_type=None)` for streaming events
- `call_service(domain, service, service_data, target)` for commands
- `get_states()`, `get_config()`, `get_services()` for data fetches
- WebSocket reconnect handling
- Minimal dependencies: `aiohttp`, `mashumaro` (optional)

Usage sketch:

```python
import aiohttp
from hass_client import HomeAssistantClient

async def main():
    async with aiohttp.ClientSession() as session:
        async with HomeAssistantClient(
            "ws://homeassistant.tail1234.ts.net:8123/api/websocket",
            token="ey...",
            aiohttp_session=session,
        ) as client:
            states = await client.get_states()
            await client.call_service(
                "light", "turn_on",
                service_data={"brightness_pct": 80},
                target={"entity_id": "light.kitchen"},
            )
            await client.subscribe_events(handle_event, "state_changed")
```

### 8.2 `HomeAssistant-API` (Alternative)

**PyPI:** `HomeAssistant-API`

Wraps the REST API. Has async support via `Client(use_async=True)`. Does not
cover WebSocket subscriptions natively. Heavier abstraction. Lower-value for a
butler that needs live subscriptions.

### 8.3 Raw `httpx` + `websockets` (DIY)

Using `httpx.AsyncClient` for REST calls and `websockets` or `aiohttp.ClientWebSocketResponse`
for the WebSocket API directly gives maximum control with no intermediate
abstraction. This would be appropriate if `python-hass-client` proves too
limiting.

**Recommendation:** Start with `python-hass-client`. If its abstractions
become limiting (e.g., need to call registry commands or statistics commands
not wrapped by the library), fall back to raw `httpx` + `aiohttp.ws_connect`.

---

## 9. Butler Integration Design

### 9.1 Module Config Schema

```toml
[modules.home_assistant]
enabled = true
url_env = "HA_URL"               # e.g., http://homeassistant.tail1234.ts.net:8123
token_env = "HA_TOKEN"           # long-lived access token env var name
verify_ssl = false               # set true if HA has a valid cert
poll_interval_seconds = 30       # REST poll for status (fallback when WS not needed)
use_websocket = true             # enable live subscription
websocket_ping_interval = 30     # seconds between keepalive pings
```

### 9.2 MCP Tools Surface

The module should expose the following MCP tools to the butler's LLM session:

**Query tools (reads):**

| Tool | Description |
|---|---|
| `ha_get_state` | Get current state of a single entity by ID |
| `ha_list_states` | List all entity states, with optional domain filter |
| `ha_list_entities` | List entity registry entries (name, area, device) |
| `ha_list_areas` | List all defined areas/rooms |
| `ha_list_services` | List available services by domain |
| `ha_get_history` | Get state history for an entity over a time window |
| `ha_get_statistics` | Get hourly/daily aggregated statistics for sensors |
| `ha_render_template` | Evaluate a Jinja2 template server-side |
| `ha_get_logbook` | Get recent activity log entries |

**Control tools (writes):**

| Tool | Description |
|---|---|
| `ha_call_service` | Generic service call (domain, service, data, target) |
| `ha_turn_on` | Turn on one or more entities by ID or area |
| `ha_turn_off` | Turn off one or more entities by ID or area |
| `ha_toggle` | Toggle one or more entities |
| `ha_set_light` | Set light attributes (brightness, color, temperature, transition) |
| `ha_set_climate` | Set climate setpoint, mode, fan mode, preset |
| `ha_control_media` | Play, pause, stop, skip, volume, source for media players |
| `ha_activate_scene` | Activate a named scene by entity ID |
| `ha_apply_scene` | Apply an ad-hoc scene (inline entity states, no pre-definition) |
| `ha_trigger_automation` | Manually trigger an automation |
| `ha_enable_automation` | Enable or disable an automation |
| `ha_lock` | Lock a lock entity |
| `ha_unlock` | Unlock a lock entity (requires approvals gate) |
| `ha_set_cover_position` | Set blind/shutter/cover position (0–100) |

**Approval sensitivity guidance:**
- `ha_unlock`, `ha_call_service` (arbitrary services) → `always` approval
- `ha_turn_off` (area-level), `ha_activate_scene` → `conditional` (check if
  occupied, time of day)
- `ha_get_*` tools → `none`

### 9.3 State Caching Strategy

HA can have hundreds of entities. Fetching all states on every tool call is
wasteful. Recommended approach:

1. **Startup:** Fetch all states once via `get_states` and cache in-memory.
2. **Live updates:** Subscribe to `state_changed` events over WebSocket.
   Update cache on each event.
3. **Tool calls:** Serve from cache. Include `last_updated` in the response
   so the LLM can reason about freshness.
4. **Fallback:** If WebSocket disconnects, fall back to periodic REST polling
   at `poll_interval_seconds`. Reconnect WebSocket in background.

### 9.4 Database Schema

The Home Assistant module does not require complex schema. Minimal tables:

**`ha_entity_snapshot`** — periodically persisted entity state for historical
context even if HA is offline:

```sql
CREATE TABLE ha_entity_snapshot (
    entity_id   TEXT NOT NULL,
    state       TEXT NOT NULL,
    attributes  JSONB,
    last_updated TIMESTAMPTZ NOT NULL,
    captured_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (entity_id)
);
```

**`ha_command_log`** — audit log of commands issued:

```sql
CREATE TABLE ha_command_log (
    id          BIGSERIAL PRIMARY KEY,
    tool_name   TEXT NOT NULL,
    domain      TEXT,
    service     TEXT,
    target      JSONB,
    data        JSONB,
    result      JSONB,
    issued_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    context_id  TEXT  -- HA context ID from response
);
```

No history storage in butler DB — use `ha_get_history` to query HA's recorder
directly.

### 9.5 Lifecycle

```python
async def on_startup(self, config, db):
    self._client = HomeAssistantClient(config.url, config.token, ...)
    await self._client.connect()
    self._cache = {s.entity_id: s for s in await self._client.get_states()}
    if config.use_websocket:
        await self._client.subscribe_events(self._on_state_changed, "state_changed")
    await self._persist_snapshot(db)

async def _on_state_changed(self, event):
    entity_id = event["data"]["entity_id"]
    self._cache[entity_id] = event["data"]["new_state"]

async def on_shutdown(self):
    await self._client.disconnect()
```

---

## 10. Automation Engine Integration

Home Assistant's automation engine uses three phases: triggers, conditions,
actions. A butler can interact at each phase:

**Reading automations:** `GET /api/states` returns `automation.*` entities
with `state` (`on`/`off`), `last_triggered` attribute, and `friendly_name`.

**Triggering:** `POST /api/services/automation/trigger` with `entity_id`.
This bypasses trigger conditions — useful for testing or manual invocation.

**Enable/disable:** `automation.turn_on` / `automation.turn_off`. Critical for
vacation mode, schedule overrides, etc.

**Creating automations programmatically:** Via the config API (undocumented but
used by the UI). This is an advanced use case and is **not recommended** for the
initial module — too tightly coupled to HA internals and likely to break across
HA versions.

**Recommended scope for v1:** read automation state and last-triggered, trigger
manually, enable/disable. Skip creation/deletion.

---

## 11. Rate Limits and Performance

Home Assistant does not impose formal API rate limits for local integrations.
Practical constraints:

| Concern | Recommendation |
|---|---|
| State query volume | Use WebSocket subscription + cache; avoid repeated GET /api/states |
| Service call latency | < 100 ms typically for local devices; Zigbee/Z-Wave may be 500 ms–2 s |
| WebSocket message rate | HA can emit hundreds of events/s in busy homes; filter by entity in client |
| HTTP connection pooling | Reuse a single `aiohttp.ClientSession` across all REST calls |
| Concurrent service calls | No formal limit; limit to 5 concurrent calls via `asyncio.Semaphore(5)` |

**Backoff policy:** On 5xx errors or connection failures, use exponential
backoff starting at 1 s, capped at 60 s, with jitter.

---

## 12. Privacy Considerations

Home Assistant stores and reports rich household data: presence/occupancy,
energy usage, sleep patterns (bedroom motion), door open/close logs, camera
imagery. Key considerations:

- **Data minimization:** The butler module should only request entity data
  relevant to the butler's defined purpose. A finance butler has no need to
  subscribe to motion sensors.
- **No cloud exfiltration:** All communication stays on-tailnet. The module
  must not relay raw HA state to external APIs without user consent (e.g.,
  summarizing to external LLM services).
- **Audit log:** The `ha_command_log` table provides a durable record of every
  action taken. The butler should surface this log on request.
- **Approval gate for sensitive actions:** `ha_unlock` (front door), area-level
  power-off, disabling security automations should require approvals module
  confirmation before execution.
- **Camera snapshots:** `ha_camera_proxy` returns raw JPEG. The butler must not
  store or transmit camera images without explicit user consent. Treat as
  `always` approval sensitivity.
- **Long-lived token rotation:** Recommend annual token rotation as a hygiene
  practice. The module AGENTS.md should note the rotation schedule.

---

## 13. Risks and Open Questions

| Risk | Severity | Notes |
|---|---|---|
| HA API stability | Low | REST and WebSocket APIs are stable across major versions; config API for automation management is less stable |
| Token management | Medium | 10-year LLATs require a rotation practice; no automatic rotation mechanism |
| WebSocket reconnect | Low | Must handle HA restart, network blip; `python-hass-client` has some reconnect logic |
| Entity ID changes | Medium | Users can rename entities; butler state cache must handle `entity_id` updates gracefully via `entity_registry_updated` events |
| Area-level commands | Low | `homeassistant.turn_off` on an area can be destructive; needs approval gate |
| `ha_unlock` safety | High | Unlocking physical doors requires strong approval gate and audit trail |
| Large state payloads | Low | `/api/states` on a large HA install can return 500+ entities; use WebSocket subscription + cache pattern |
| Camera image handling | Medium | Raw JPEG frames are privacy-sensitive; no caching, strong approval policy required |

**Open questions for implementation ticket:**

1. Should the module maintain its own WebSocket connection permanently, or
   open connections on demand per butler session? (Permanent is better for
   live state accuracy but adds resource cost.)
2. Which butler(s) should load this module — a dedicated "home" butler, or
   should it be available to multiple butlers? (Architecture constraint: each
   butler has its own DB; entity snapshot tables would be duplicated.)
3. Should `ha_list_states` return the full cache (potentially 500+ entities) or
   require a domain/area filter? (LLM context budget concern.)
4. For the approval gate on `ha_unlock`, should it use the approvals module
   in-band (blocking the action) or out-of-band (async Telegram confirmation)?

---

## 14. Existing Art: ha-mcp

The `homeassistant-ai/ha-mcp` project is a FastMCP-based MCP server for HA
with 97+ tools. Key observations:

- Built with the same stack (FastMCP, Python) as the butlers framework
- Exposes fine-grained tools: search, bulk control, automation CRUD, dashboard
  management, statistics, system health, backups
- Runs as a standalone process, not embedded in a butler module
- No butler-specific features: no approvals gate, no state persistence, no
  session logging
- Can serve as a reference implementation for tool naming and parameter shapes

**Recommendation:** Review `ha-mcp` tool signatures when designing the butler
module's tool surface. Do not adopt it as a dependency — embedding it would
violate the module boundary (modules only add tools, never infrastructure).

---

## 15. Summary and Recommendations

### Recommended approach for v1 implementation

1. **Transport:** WebSocket (via `python-hass-client`) for subscriptions and
   live state cache; REST (via `httpx.AsyncClient`) for one-shot commands and
   history queries.
2. **Auth:** Long-lived access token stored in env var, referenced by name in
   `butler.toml`. No refresh logic needed.
3. **State management:** In-memory cache populated from `get_states` at startup,
   updated via `state_changed` WebSocket subscription.
4. **Tool surface:** ~20 tools covering read, control, scene, automation,
   statistics, template. Generic `ha_call_service` as an escape hatch.
5. **Approval gate:** `ha_unlock` → `always`; area-level off/scene → `conditional`;
   reads → `none`.
6. **Database:** Two tables — `ha_entity_snapshot` (periodically persisted
   cache) and `ha_command_log` (audit trail).
7. **Privacy:** Data minimization, no external exfiltration, camera images
   never persisted, strong approval for sensitive actuators.

### Deferred to v2

- Automation creation/deletion via config API
- Camera image analysis (depends on vision capabilities)
- Cross-butler area coordination via Switchboard
- Statistics aggregation in butler DB (energy dashboards, etc.)
- Presence-based butler triggering (subscribe to device tracker events)

---

## References

- [Home Assistant REST API Developer Docs](https://developers.home-assistant.io/docs/api/rest/)
- [Home Assistant WebSocket API Developer Docs](https://developers.home-assistant.io/docs/api/websocket/)
- [Home Assistant Authentication API](https://developers.home-assistant.io/docs/auth_api/)
- [Home Assistant Statistics (Data Science Portal)](https://data.home-assistant.io/docs/statistics/)
- [python-hass-client (music-assistant)](https://github.com/music-assistant/python-hass-client)
- [ha-mcp MCP Server (homeassistant-ai)](https://github.com/homeassistant-ai/ha-mcp)
- [Home Assistant Light Integration](https://www.home-assistant.io/integrations/light/)
- [Home Assistant Scene Integration](https://www.home-assistant.io/integrations/scene/)
- [Tailscale + Home Assistant Remote Access](https://tailscale.com/blog/remotely-access-home-assistant)
