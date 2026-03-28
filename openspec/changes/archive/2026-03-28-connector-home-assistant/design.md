## Context

The Home butler (`roster/home/`) is configured with a `home_assistant` module that provides MCP tools for querying and controlling Home Assistant (HA) devices. However, the butler has no real-time event feed from HA. It can only observe home state when an LLM session is active and explicitly queries entities. This means the butler is blind to events happening between sessions: a door unlocked, a motion sensor triggered, an automation firing, an energy spike. The `home-butler-enhancements` change adds deterministic job handlers for periodic monitoring, but these still poll on fixed schedules (hourly/daily) rather than reacting to events in real time.

Home Assistant exposes two APIs for external consumption:
1. **REST API** (`GET /api/states`, `GET /api/events`, etc.) — stateless HTTP, good for polling snapshots
2. **WebSocket API** (`ws://<host>:8123/api/websocket`) — persistent connection, subscribe to event types, receive real-time pushes

The WebSocket API is the natural fit for a connector: it provides a continuous event stream with no polling overhead. The REST API serves as a degraded fallback when the WebSocket connection fails.

HA generates a high volume of events — a typical installation with 50+ entities produces hundreds of `state_changed` events per minute (sensor updates every 30-60s, motion sensors, weather updates, etc.). The connector must filter aggressively to avoid overwhelming the Switchboard with noise. Filtering happens at three layers: domain allowlist (structural), significance thresholds (quantitative), and the shared discretion layer (semantic).

Authentication uses HA's long-lived access tokens, which are static bearer tokens generated in the HA UI (Profile -> Security -> Long-Lived Access Tokens). These tokens do not expire and have full API access. The token is stored in the butler's CredentialStore (DB-first) and configured via the Butlers dashboard settings page.

## Goals / Non-Goals

**Goals:**
- Stream real-time HA events into the butler ecosystem via the Switchboard's canonical ingest path
- Filter HA event noise aggressively to submit only meaningful state changes (target: <5% of raw events forwarded)
- Support WebSocket streaming as primary transport with REST polling fallback
- Provide a dashboard settings UX for configuring the HA connection (URL + access token) with connection validation
- Follow the established connector contract: heartbeat, checkpoint, metrics, filtered event persistence, replay queue
- Enable the Home butler to react proactively to significant home events without requiring an active LLM session

**Non-Goals:**
- Bidirectional control — the connector is ingestion-only; outbound HA commands remain in the `home_assistant` module's MCP tools
- Replacing the `home_assistant` module — the module continues to provide query/control tools for LLM sessions
- Processing HA add-on or supervisor events — only core HA events (state changes, automations, scripts, services)
- Multi-instance HA support — one connector per HA instance; multi-home is out of scope per the Home butler manifesto
- Custom HA integration development — the connector consumes HA's standard WebSocket/REST APIs, not custom components

## Decisions

### D1: WebSocket-first with REST polling fallback

**Decision:** The connector subscribes to HA's WebSocket event stream as the primary ingestion method. When the WebSocket connection fails and cannot be re-established within a backoff window, the connector falls back to REST polling (`GET /api/states`) at a configurable interval (default 60s).

**Rationale:** WebSocket provides true real-time events with no polling overhead. HA's WebSocket API is stable and well-documented. REST polling as a fallback ensures the connector continues operating during HA restarts or network disruptions, avoiding a complete ingestion gap.

**Alternative considered:** REST-only polling. Rejected because polling introduces latency (minimum 30s between checks), misses transient state changes (entity goes on then off between polls), and generates unnecessary API load for unchanged entities.

### D2: Three-layer filtering pipeline

**Decision:** Events pass through three sequential filters before Switchboard submission:

1. **Domain allowlist** (structural) — only entity domains in the configurable allowlist proceed. Default: `light`, `switch`, `sensor`, `climate`, `lock`, `cover`, `binary_sensor`, `automation`, `script`. Events from excluded domains (e.g., `weather`, `sun`, `update`, `persistent_notification`) are dropped immediately.

2. **Significance filter** (quantitative) — for numeric sensor entities, ignore state changes where the delta from the previous known value is below a domain-specific threshold. Default thresholds: temperature ±0.5 units, humidity ±2%, energy ±0.1 kWh, illuminance ±50 lux. Binary entities (on/off, open/closed, locked/unlocked) always pass.

3. **Discretion layer** (semantic) — events that pass domain and significance filters are evaluated by the shared `DiscretionEvaluator` to determine if they warrant butler attention. Uses the same `DiscretionDispatcher` and model catalog as other connectors.

**Rationale:** Three layers progressively narrow the event stream. Layer 1 is zero-cost (string comparison). Layer 2 is cheap (numeric comparison against cached previous state). Layer 3 is the expensive LLM call, invoked only for events that passed both cheaper filters. This minimizes LLM spend while maintaining semantic filtering for ambiguous events.

**Alternative considered:** Single discretion-only filter. Rejected because sending hundreds of events/minute to an LLM is wasteful and slow — most can be eliminated by simple structural/numeric rules.

### D3: HA event ID as checkpoint cursor

**Decision:** The connector uses HA's `event.data.entity_id + ":" + event.time_fired` as the checkpoint cursor, persisted via `cursor_store`. On restart, events with timestamps at or before the cursor are skipped.

**Rationale:** HA does not expose monotonic event IDs in its WebSocket API. The `time_fired` timestamp (ISO 8601 with microsecond precision) combined with `entity_id` provides sufficient ordering for deduplication. The Switchboard's dedup layer handles any edge-case replays.

**Alternative considered:** Using HA's internal event firing count. Rejected because this counter is not exposed in the WebSocket API and resets on HA restart.

### D4: Sender identity is the HA entity ID

**Decision:** The `sender.identity` field in the `ingest.v1` envelope is set to the HA entity ID (e.g., `sensor.living_room_temperature`, `light.bedroom`). There is no person/contact behind HA events.

**Rationale:** HA events are device-originated, not person-originated. The entity ID is the natural "sender" — it tells the butler which device changed. Weight resolution for discretion uses weight=1.0 (owner-equivalent) for all HA events, matching the pattern used by the live-listener connector for ambient sources.

**Alternative considered:** Using `"home_assistant"` as a generic sender for all events. Rejected because per-entity sender identity enables entity-level filtering rules and per-entity discretion context windows.

### D5: Dashboard settings UX for HA connection configuration

**Decision:** The HA connection (instance URL + long-lived access token) is configured via the Butlers dashboard settings page at `/butlers/settings` under a dedicated "Home Assistant" section. The settings form accepts the HA base URL and access token, validates the connection by making a test API call (`GET /api/` with the token), and stores both values in the CredentialStore (DB-first) on success.

**Rationale:** The dashboard is the established configuration surface for butler settings. Storing credentials in the CredentialStore keeps them out of environment variables and config files. Connection validation before saving prevents misconfiguration.

**Alternative considered:** Environment variable configuration (`HA_BASE_URL`, `HA_ACCESS_TOKEN`). Rejected as the primary method because it requires container restarts to change and exposes the token in environment listings. Environment variables are supported as an override for development/testing but the dashboard is the canonical configuration path.

### D6: Single connector process, single HA instance

**Decision:** One connector process connects to one HA instance. The `endpoint_identity` is `"home_assistant:<ha_host>:<ha_port>"` derived from the configured HA URL.

**Rationale:** The Home butler manifesto explicitly scopes to one primary residence. Multi-home support is a non-goal. One connector per HA instance keeps the architecture simple.

### D7: No discretion bypass for high-priority event types

**Decision:** All events that pass domain and significance filters go through discretion evaluation. There is no bypass for specific event types (e.g., `automation_triggered`).

**Rationale:** Even automation triggers can be noisy (e.g., a motion-triggered light automation fires every time someone walks past a sensor). The discretion layer should evaluate all events in context. The owner-equivalent weight (1.0) means the discretion call always happens and failures always fail-open, so high-priority events are never lost.

**Alternative considered:** Bypassing discretion for `lock` state changes and `automation_triggered` events. Rejected because it complicates the filtering pipeline and the fail-open behavior already ensures important events are forwarded when discretion is unavailable.

## Risks / Trade-offs

**[Risk] HA WebSocket connection instability during HA updates** -> Mitigation: Exponential backoff reconnection (1s, 2s, 4s, ... capped at 60s). During reconnection, the connector transitions to REST polling fallback to maintain event coverage. Health state transitions to `degraded` while on REST fallback.

**[Risk] Event volume overwhelms discretion layer** -> Mitigation: Domain and significance filters reduce event volume by ~95% before discretion. The discretion semaphore (default 4 concurrent calls) provides backpressure. Events queued beyond the semaphore are processed in order. If the queue grows beyond a configurable limit (default 100), oldest events are dropped with a counter increment.

**[Risk] Significance thresholds miss important small changes** -> Mitigation: Thresholds are configurable per sensor domain via connector settings (dashboard-editable). Binary entities always pass regardless of thresholds. The default thresholds are conservative and can be tuned per installation.

**[Risk] Long-lived access tokens have full HA API access** -> Mitigation: The connector only reads events and states — it never calls HA services. The token is stored in the CredentialStore (encrypted at rest in the DB), not in environment variables or config files. The dashboard validates the token before storing.

**[Risk] Checkpoint cursor based on timestamp could miss events during clock skew** -> Mitigation: The Switchboard's dedup layer (advisory lock + dedupe key) provides idempotent replay safety. On restart, the connector subtracts a configurable safety margin (default 30s) from the checkpoint timestamp to ensure overlap.

## Migration Plan

1. **Register source channel/provider** — Add `home_assistant` to routing contracts.
2. **Implement connector** — `src/butlers/connectors/home_assistant.py` with WebSocket client, REST fallback, three-layer filtering.
3. **Add dashboard settings** — HA connection configuration section at `/butlers/settings` with connection validation.
4. **Add Docker service** — `connector-home-assistant` service in `docker-compose.yml`.
5. **Configure and test** — Enter HA URL + token via dashboard, verify event flow.
6. **Rollback:** Remove the Docker service entry, remove the connector source file, revert routing contract changes. CredentialStore entries persist but are inert without the connector process.

## Open Questions

- Should the significance filter thresholds be stored in `connector_registry.settings` (editable via dashboard) or hardcoded with env var overrides? Leaning toward `connector_registry.settings` for runtime tuning without restarts.
- Should the connector support subscribing to specific HA event types beyond `state_changed`? Candidates: `automation_triggered`, `script_started`, `call_service`. Including all four by default seems reasonable but increases event volume.
- Should the REST polling fallback track individual entity state diffs (comparing against previous poll) or submit all entity states on each poll? Diff-based is more efficient but requires in-memory state cache.
