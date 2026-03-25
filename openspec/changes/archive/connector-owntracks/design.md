## Context

The butler framework has a mature connector pattern: standalone processes that poll or receive events from external systems, normalize them to `ingest.v1` envelopes, and submit to the Switchboard via MCP. Existing connectors (Gmail, Telegram bot, Telegram user-client, live-listener) are all poll-based or long-polling clients that reach out to external APIs. OwnTracks inverts this: the connector is a webhook server that receives HTTP POSTs from the OwnTracks mobile app.

OwnTracks publishes JSON payloads via HTTP POST. The payload types relevant to the butler ecosystem are: `location` (periodic GPS fix), `transition` (entering/leaving a geofenced waypoint), and `lwt` (last will and testament / device offline). Each payload includes a `_type` field and a `tst` (Unix timestamp). The `tid` field (tracker ID, 2 chars) identifies the device.

The situational context bus (RFC 0009, `openspec/changes/situational-context-bus/`) defines a shared `user_context` table with signals like `traveling`, `commuting`, `at_home`. Location events from OwnTracks are a natural feeder for these signals. Context signal derivation is a downstream butler concern -- the connector's job is to normalize and submit location events. Butlers consuming these events write context signals as described in the Context Bus Integration section below (D9).

Location data is the most privacy-sensitive data type in the butler ecosystem. Unlike messages (which the user intentionally sent), location is passively tracked. The design must enforce conservative defaults and explicit opt-in.

## Goals / Non-Goals

**Goals:**
- Receive OwnTracks HTTP webhook POSTs, validate authentication, and normalize `location` and `transition` events into `ingest.v1` envelopes for Switchboard ingestion
- Follow the connector base contract: heartbeat, Prometheus metrics, health endpoint, filtered event batch flush, replay queue drain
- Default to `metadata` ingestion tier (Tier 2) -- normalized text summary only, no raw GPS coordinates in the ingest payload -- unless the user explicitly opts into `full` (Tier 1)
- Configurable data retention with auto-purge (default 30 days)
- Webhook authentication via bearer token or shared secret
- Single-process, single-endpoint design (one OwnTracks device per connector instance)

**Non-Goals:**
- MQTT transport (HTTP mode is sufficient and simpler; MQTT can be added later)
- Multi-device support in a single connector instance (run multiple connector instances for multiple devices)
- Reverse geocoding (translating coordinates to place names -- future enhancement)
- Geofence management (OwnTracks manages waypoints on the device; the connector just receives transitions)
- Real-time location streaming to dashboards (events are ingested, not streamed live)
- Context signal derivation (that is a downstream butler concern, not a connector concern)

## Decisions

### D1: Webhook server using FastAPI (not polling)

OwnTracks pushes events via HTTP POST. The connector runs a FastAPI HTTP server that receives these POSTs, unlike poll-based connectors that call external APIs. The same FastAPI instance serves both the webhook endpoint and the standard `/health` + `/metrics` endpoints.

**Why FastAPI:** Already a project dependency. The connector health server pattern (`health_socket.py`) already uses FastAPI. Combining the webhook endpoint with the health server reduces to a single HTTP server.

**Alternative considered:** Separate webhook server + health server.
**Rejected because:** Unnecessary complexity. A single FastAPI app serves both purposes on the same port.

### D2: Authentication via bearer token with dashboard setup UX

The OwnTracks app supports sending a custom HTTP header with each request. The connector validates an `Authorization: Bearer <token>` header on every POST. The token is stored in `CredentialStore` under key `owntracks_webhook_token`, with env var `OWNTRACKS_WEBHOOK_TOKEN` as fallback.

**Dashboard setup UX:** A dedicated "OwnTracks" section on the Butlers dashboard settings page (`/butlers/settings`) provides the complete setup flow:

1. **Token generation:** The dashboard generates a cryptographically random bearer token and stores it in `CredentialStore`. The token is displayed once for the user to copy. A "Regenerate" button creates a new token (invalidating the old one).
2. **Webhook URL display:** The dashboard computes and displays the full webhook URL (e.g., `https://<tailnet-host>:<port>/owntracks/webhook`) based on the connector's configured host and port.
3. **OwnTracks app configuration guide:** The settings section includes inline instructions for configuring the OwnTracks mobile app:
   - Set mode to **HTTP**
   - Set URL to the displayed webhook URL
   - Set authentication to **Bearer token** and paste the displayed token
   - (Optional) Configure reporting interval, waypoints
4. **Connection status:** After setup, the dashboard shows last-received event timestamp and event count (derived from connector heartbeat data), confirming the app is successfully posting.

**Why bearer token:** OwnTracks supports HTTP header configuration. Bearer token is standard, simple, and sufficient for a tailnet-internal endpoint. No OAuth complexity needed.

**Alternative considered:** URL path secret (e.g., `/webhook/<secret>`).
**Rejected because:** Secrets in URLs leak into access logs. Header-based auth is standard practice.

**Alternative considered:** Dashboard-less setup (env var only).
**Rejected because:** Poor UX. The user must coordinate a token between two systems (connector config and OwnTracks app). The dashboard centralizes this into a single setup flow with copy-paste convenience.

### D3: Default ingestion tier is `metadata` (Tier 2)

Location data is privacy-sensitive. The default ingestion tier is `metadata`: `payload.raw` is None, and `payload.normalized_text` contains only a human-readable summary (e.g., `"Location update: 48.8566N, 2.3522E, acc 10m"` or `"Entered region: Home"`). The full OwnTracks JSON payload (with exact coordinates, velocity, battery, SSID, etc.) is only stored when `CONNECTOR_INGESTION_TIER=full` is explicitly set.

This means downstream butlers see that a location event happened and can read the summary, but raw coordinates are not persisted in the Switchboard's `ingestion_events` table by default. Butlers that need precise coordinates must consume them from the ingest envelope at processing time (available in the session context) -- they are not stored at rest.

**Alternative considered:** Default to `full` (Tier 1) like messaging connectors.
**Rejected because:** Location data has a different privacy profile than messages. Messages are intentionally sent by the user; location is passively tracked. Conservative default is appropriate.

### D4: Checkpoint is timestamp-based (not offset-based)

OwnTracks has no message IDs or sequence numbers. The connector tracks the latest processed `tst` (Unix timestamp) as the checkpoint cursor. On restart, events with `tst <= checkpoint` are treated as potential duplicates. The Switchboard's deduplication layer (idempotency key) makes replays harmless.

**Why timestamp:** OwnTracks provides `tst` as the only stable identifier per event. The idempotency key `owntracks:<endpoint>:<tst>:<_type>` provides reliable dedup even if multiple events share a timestamp (different `_type` values).

### D5: No discretion layer

Unlike messaging connectors where noise filtering is important (group chats, spam), OwnTracks events are low-volume (typically 1 event every 1-30 minutes) and always from the device owner. There is no sender to evaluate -- all events are from the user's own device. The discretion layer adds no value here.

### D6: Waypoint transitions as first-class events

OwnTracks `transition` events (entering/leaving a geofenced region) are ingested as distinct events, not filtered. The `normalized_text` clearly indicates the transition type and region name: `"Entered region: Office"` or `"Left region: Home"`. These are high-signal events for downstream automation (arrival/departure triggers).

### D7: Retention purge as a scheduled task

Location data auto-purge runs as a periodic task within the connector process. Every 6 hours, the connector deletes `shared.ingestion_events` rows where `source_channel = 'owntracks'` and `created_at < NOW() - INTERVAL '<retention_days> days'`. The retention period defaults to 30 days and is configurable via `OWNTRACKS_RETENTION_DAYS`.

**Why connector-side purge:** The connector owns the lifecycle of its data. Purge is a simple DELETE query, not a complex migration. Running it in the connector process keeps the responsibility co-located with the data source.

**Alternative considered:** Centralized retention policy in the Switchboard.
**Rejected because:** Not yet built. Connector-side purge is simple and sufficient. Can migrate to centralized retention later.

### D8: Single device per connector instance

Each connector instance handles one OwnTracks device (identified by `tid`). For multiple devices (e.g., phone + tablet), run multiple connector instances with different ports and endpoint identities. This avoids multi-tenant complexity and keeps the process model simple.

**Alternative considered:** Multi-device multiplexing in a single connector.
**Rejected because:** OwnTracks users typically have one device. Multi-device adds complexity (per-device checkpointing, per-device metrics labels) with little benefit. Multiple instances are trivially deployable via docker-compose.

### D9: Context bus integration via downstream butlers (RFC 0009)

OwnTracks events are a primary feeder for the situational context bus (RFC 0009). The connector itself does NOT write context signals -- it normalizes events and submits them to the Switchboard. Butlers consuming these events derive and write context signals:

**Travel butler:**
- Enter "Home" waypoint transition → `set_context("at_home", confidence=0.95, ttl=12h)`
- Leave "Home" waypoint transition → `clear_context("at_home")`
- Location >50km from home → `set_context("traveling", confidence=0.7, ttl=24h)`

**Home butler:**
- Enter "Home" waypoint transition → `set_context("at_home", confidence=0.95)`

**General butler:**
- Velocity >80km/h sustained across consecutive updates → `set_context("commuting", confidence=0.6, ttl=45min)`

**Confidence levels:**
- Explicit geofence transition (enter/leave): 0.95 (device-inferred, not user-stated, so not 1.0)
- Distance/velocity inference: 0.6-0.7

This separation preserves the connector's single responsibility (ingest) while enabling rich situational awareness downstream. The `at_home` signal type is defined in RFC 0009 with write permissions for travel, home, and general butlers, default TTL 12h, max TTL 24h.

## Risks / Trade-offs

**[Webhook reachability]** The connector must be reachable by the OwnTracks app. On a tailnet, this means the phone must be on the same Tailscale network, or the endpoint must be exposed via a Tailscale funnel.
--> **Mitigation:** Document tailnet configuration. The connector binds to `0.0.0.0` and the tailnet handles routing. For external access, Tailscale funnel or a reverse proxy can expose the webhook endpoint.

**[Duplicate events on restart]** Timestamp-based checkpointing may replay events near the checkpoint boundary. The same `tst` could appear in rapid succession for location updates.
--> **Mitigation:** Idempotency key includes both `tst` and `_type`, and the Switchboard's dedup layer makes replays harmless. At-least-once delivery is the contract.

**[Battery and accuracy trade-offs]** OwnTracks location update frequency depends on device battery optimization settings. The connector may receive sparse updates with low accuracy.
--> **Mitigation:** Not a connector concern. The connector ingests whatever the app sends. Butler-side logic must handle sparse/inaccurate data gracefully.

**[Retention purge race]** The purge task deletes rows from `shared.ingestion_events` while the Switchboard may be reading them for routing/classification.
--> **Mitigation:** Purge uses `DELETE ... WHERE created_at < threshold` which is safe for concurrent reads. The 30-day default ensures rows are well past any active processing window.

## Open Questions

1. **Health port allocation?** Telegram bot uses 40081, WhatsApp user client targets 40082, live-listener uses 40091. OwnTracks needs a port. Suggest 40083.

2. **Should the webhook path be configurable?** Default `/owntracks/webhook` is clear, but some users may want a custom path for obscurity-in-depth. Low priority -- can add later.

3. **Region metadata persistence?** OwnTracks waypoint definitions (region name, coordinates, radius) are published as `_type: waypoint` events. Should the connector ingest these for butler reference, or ignore them? Initial design: ingest as informational events, no special handling.
