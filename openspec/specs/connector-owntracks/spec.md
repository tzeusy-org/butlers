# OwnTracks Connector

## Purpose
The OwnTracks connector receives HTTP webhook POSTs from the OwnTracks mobile app, normalizes location events and waypoint transitions into `ingest.v1` envelopes, and submits them to the Switchboard via MCP. It is the location data ingestion pathway into the butler ecosystem. The connector is a webhook server (not a polling client), privacy-conservative by default, and opt-in only.

## Requirements

### Requirement: Connector Identity and Role

The implementation SHALL provide the behavior described by this requirement.
The OwnTracks connector bridges the OwnTracks mobile app into the butler ecosystem as a location data ingestion channel.

#### Scenario: Connector as location webhook receiver
- **WHEN** the OwnTracks connector runs
- **THEN** it operates an HTTP server that receives POST requests from the OwnTracks mobile app
- **AND** it normalizes `location` and `transition` payload types into `ingest.v1` envelopes
- **AND** it submits envelopes to the Switchboard via MCP
- **AND** it is a standalone OS process (not an in-daemon module)

#### Scenario: Connector identity
- **WHEN** the OwnTracks connector starts
- **THEN** `source.channel = "owntracks"`, `source.provider = "owntracks"`, and `source.endpoint_identity = "owntracks:<tid>"` where `<tid>` is the OwnTracks tracker ID configured via `OWNTRACKS_TRACKER_ID` (default: device-reported `tid`)

#### Scenario: Multiple devices per instance
- **WHEN** several physical OwnTracks devices (e.g. household members' phones) post to the same connector instance's webhook URL
- **THEN** the connector resolves each device's own `owntracks:<tid>` identity independently and gives it its own heartbeat lifecycle, Prometheus metrics labels, filtered-event buffer, and checkpoint cursor
- **AND** each resolved device registers its own row in `connector_registry`, keyed by `(connector_type, endpoint_identity)`, independently of sibling devices -- one device's activity never stops, replaces, or corrupts another device's heartbeat, checkpoint, or metrics
- **AND** without `OWNTRACKS_TRACKER_ID`, only a device-reported `tid` of one or two ASCII alphanumeric characters may allocate that identity-scoped state; missing, malformed, or overlong values are ignored before they can create a heartbeat task, checkpoint row, or identity-labeled metric series
- **AND** setting `OWNTRACKS_TRACKER_ID` pins the connector to one fixed identity (ignoring device-reported `tid`) for deployments that intentionally run one connector instance per device

### Requirement: Webhook Server

The implementation SHALL provide the behavior described by this requirement.
The connector runs a FastAPI HTTP server that receives OwnTracks webhook POSTs and serves health/metrics endpoints on the same port.

#### Scenario: Webhook endpoint
- **WHEN** the OwnTracks app sends an HTTP POST to `/owntracks/webhook`
- **THEN** the connector validates authentication, parses the JSON payload, and processes the event
- **AND** returns HTTP 200 with an empty JSON array `[]` on success (OwnTracks protocol requirement)
- **AND** returns HTTP 401 if authentication fails
- **AND** returns HTTP 400 if the payload is malformed

#### Scenario: Combined server
- **WHEN** the connector starts
- **THEN** a single FastAPI application serves the webhook endpoint (`/owntracks/webhook`), the health endpoint (`/health`), and the Prometheus metrics endpoint (`/metrics`) on the port specified by `CONNECTOR_HEALTH_PORT`

#### Scenario: Request content type
- **WHEN** an OwnTracks POST is received
- **THEN** the connector accepts `application/json` content type
- **AND** the JSON body MUST contain a `_type` field identifying the payload type

### Requirement: Webhook Authentication
Every incoming webhook POST MUST be authenticated via a bearer token before processing.

#### Scenario: Bearer token validation
- **WHEN** an HTTP POST arrives at `/owntracks/webhook`
- **THEN** the connector validates the `Authorization: Bearer <token>` header against the configured token
- **AND** returns HTTP 401 with body `{"error": "Unauthorized"}` if the header is missing, malformed, or the token does not match
- **AND** unauthenticated requests MUST NOT be processed or logged with payload content (prevent information leakage)

#### Scenario: Token resolution
- **WHEN** the connector starts
- **THEN** it resolves the webhook token from `CredentialStore` under key `owntracks_webhook_token`
- **AND** falls back to env var `OWNTRACKS_WEBHOOK_TOKEN` if not found in `CredentialStore`
- **AND** refuses to start if no token is configured (fail-closed)

#### Scenario: Constant-time comparison
- **WHEN** the connector compares the provided token to the configured token
- **THEN** it MUST use constant-time string comparison (`hmac.compare_digest`) to prevent timing attacks

#### Scenario: HTTP Basic auth compatibility
- **WHEN** the app sends `Authorization: Basic <base64(user:password)>` (OwnTracks native username/password fields)
- **THEN** the connector compares the Basic-auth password to the configured token (username ignored) using `hmac.compare_digest`
- **AND** a valid password authenticates identically to a matching bearer token

### Requirement: Dashboard Setup UX

The implementation SHALL provide the behavior described by this requirement.
A dedicated "OwnTracks" section on the Butlers dashboard settings page provides the complete setup flow for connecting the OwnTracks mobile app.

#### Scenario: Settings section layout
- **WHEN** the user navigates to `/butlers/settings`
- **THEN** an "OwnTracks" section is displayed with: connection status indicator, webhook URL, bearer token (masked with reveal toggle), and inline app configuration guide

#### Scenario: Token generation
- **WHEN** the user clicks "Generate Token" (first setup) or "Regenerate Token"
- **THEN** the dashboard generates a cryptographically random 32-byte hex token
- **AND** stores it in `CredentialStore` under key `owntracks_webhook_token`
- **AND** displays the token once in a copyable field with a "Copy" button
- **AND** if regenerating, the previous token is immediately invalidated

#### Scenario: Webhook URL display
- **WHEN** the OwnTracks settings section is rendered
- **THEN** the dashboard computes and displays the full webhook URL based on the connector's host and port configuration (e.g., `https://<tailnet-host>:<port>/owntracks/webhook`)
- **AND** the URL is displayed in a copyable field with a "Copy" button

#### Scenario: App configuration guide
- **WHEN** the OwnTracks settings section is rendered
- **THEN** inline instructions are displayed for configuring the OwnTracks mobile app:
  1. Open OwnTracks app, navigate to Settings (Preferences)
  2. Set **Mode** to **HTTP**
  3. Set **URL** to the displayed webhook URL
  4. Under **Authentication**, select **Bearer token** and paste the displayed token
  5. (Optional) Configure reporting interval and waypoints
- **AND** the instructions distinguish between iOS and Android where the UX differs

#### Scenario: Connection status display
- **WHEN** a token has been generated and the connector is running
- **THEN** the dashboard displays: last-received event timestamp (from connector heartbeat), total events received today, and connector liveness badge (online/stale/offline)
- **AND** if no events have been received within 1 hour of setup, a hint is displayed: "No events received yet. Verify the OwnTracks app is configured and has location permissions."

#### Scenario: Dashboard API endpoints
- **WHEN** the dashboard interacts with OwnTracks settings
- **THEN** the following API endpoints are available:
  - `GET /api/connectors/owntracks/status` -- connection state, last event, event count
  - `POST /api/connectors/owntracks/token/generate` -- generate or regenerate bearer token
  - `GET /api/connectors/owntracks/config` -- webhook URL and setup instructions metadata

### Requirement: Supported Payload Types

The implementation SHALL provide the behavior described by this requirement.
The connector processes a defined subset of OwnTracks payload types and silently ignores the rest.

#### Scenario: Location payload (`_type: "location"`)
- **WHEN** a payload with `_type = "location"` is received
- **THEN** the connector extracts: `lat` (latitude), `lon` (longitude), `alt` (altitude, optional), `vel` (velocity, optional), `acc` (accuracy in meters), `tst` (Unix timestamp), `tid` (tracker ID), `batt` (battery percentage, optional), `conn` (connectivity type, optional), `SSID` (WiFi network, optional), `inregions` (list of region names the device is currently in, optional)
- **AND** the event is normalized to an `ingest.v1` envelope and submitted to the Switchboard

#### Scenario: Transition payload (`_type: "transition"`)
- **WHEN** a payload with `_type = "transition"` is received
- **THEN** the connector extracts: `event` (`"enter"` or `"leave"`), `desc` (region description/name), `lat`, `lon`, `tst`, `tid`, `acc`
- **AND** the event is normalized to an `ingest.v1` envelope and submitted to the Switchboard

#### Scenario: Waypoint payload (`_type: "waypoints"`)
- **WHEN** a payload with `_type = "waypoints"` is received
- **THEN** the connector normalizes it as an informational event with `normalized_text` summarizing the waypoint definitions (e.g., `"Waypoint sync: 3 regions (Home, Office, Gym)"`)
- **AND** the event is submitted to the Switchboard for butler reference

#### Scenario: Ignored payload types
- **WHEN** a payload with `_type` not in `{"location", "transition", "waypoints"}` is received (e.g., `"lwt"`, `"cmd"`, `"steps"`, `"card"`)
- **THEN** the connector logs the event type at DEBUG level and returns HTTP 200 without ingesting
- **AND** the event is NOT recorded in `connectors.filtered_events` (these are protocol-level ignores, not policy-filtered)

### Requirement: ingest.v1 Field Mapping

The implementation SHALL provide the behavior described by this requirement.
Each OwnTracks event is normalized to the canonical `ingest.v1` envelope.

#### Scenario: Location event field mapping
- **WHEN** a location event is normalized
- **THEN** the mapping is:
  - `source.channel` = `"owntracks"`
  - `source.provider` = `"owntracks"`
  - `source.endpoint_identity` = `"owntracks:<tid>"`
  - `event.external_event_id` = `"<tst>:location"` (timestamp + type for uniqueness)
  - `event.external_thread_id` = `"owntracks:<tid>"` (all events from same device share a thread)
  - `event.observed_at` = connector-received timestamp (RFC3339)
  - `sender.identity` = `"owntracks:<tid>"` (device is the sender)
  - `payload.normalized_text` = human-readable summary (see Normalized Text Generation)
  - `payload.raw` = full OwnTracks JSON payload (Tier 1 only; None for Tier 2)
  - `control.idempotency_key` = `"owntracks:<endpoint_identity>:<tst>:location"`
  - `control.policy_tier` = `"default"`
  - `control.ingestion_tier` = configured tier (default `"metadata"`)

#### Scenario: Transition event field mapping
- **WHEN** a transition event is normalized
- **THEN** the mapping follows the location pattern with these differences:
  - `event.external_event_id` = `"<tst>:transition:<event>"` (includes enter/leave)
  - `control.idempotency_key` = `"owntracks:<endpoint_identity>:<tst>:transition:<event>"`
  - `payload.normalized_text` = transition-specific summary (see Normalized Text Generation)

### Requirement: Normalized Text Generation

The implementation SHALL provide the behavior described by this requirement.
The connector generates human-readable summaries for `payload.normalized_text` based on event type.

#### Scenario: Location event text (Tier 2 / metadata)
- **WHEN** a location event is normalized with `ingestion_tier = "metadata"`
- **THEN** `normalized_text` is formatted as: `"Location update: {lat}N/S, {lon}E/W, acc {acc}m"` with cardinal direction suffixes based on sign
- **AND** if `vel` is present and > 0: appends `", {vel} km/h"`
- **AND** if `inregions` is present and non-empty: appends `" (in: {region1}, {region2})"`

#### Scenario: Location event text (Tier 1 / full)
- **WHEN** a location event is normalized with `ingestion_tier = "full"`
- **THEN** `normalized_text` follows the same format as Tier 2 (the summary is always human-readable)
- **AND** `payload.raw` additionally contains the full OwnTracks JSON payload

#### Scenario: Transition event text
- **WHEN** a transition event is normalized
- **THEN** `normalized_text` is formatted as: `"Entered region: {desc}"` for `event = "enter"` or `"Left region: {desc}"` for `event = "leave"`

#### Scenario: Waypoint sync text
- **WHEN** a waypoint sync event is normalized
- **THEN** `normalized_text` is formatted as: `"Waypoint sync: {count} regions ({name1}, {name2}, ...)"` listing up to 5 region names, with `"and N more"` suffix if more than 5

### Requirement: Privacy Controls

The implementation SHALL provide the behavior described by this requirement.
Location data is privacy-sensitive. The connector enforces conservative defaults and explicit opt-in for full data capture.

#### Scenario: Default ingestion tier
- **WHEN** the connector starts without `CONNECTOR_INGESTION_TIER` set
- **THEN** the default ingestion tier is `"metadata"` (Tier 2)
- **AND** `payload.raw` is None for all submitted envelopes
- **AND** the Switchboard ingestion path persists only the human-readable
  `normalized_text` summary; the restricted durable point evidence described
  below remains a separate Chronicler read surface

#### Scenario: Full ingestion tier opt-in
- **WHEN** `CONNECTOR_INGESTION_TIER=full` is explicitly set
- **THEN** the ingestion tier is `"full"` (Tier 1)
- **AND** `payload.raw` contains the complete OwnTracks JSON payload including exact coordinates, velocity, battery, connectivity, and SSID
- **AND** a warning is logged at startup: `"OwnTracks ingestion tier set to 'full' -- raw GPS coordinates will be stored at rest"`

#### Scenario: SSID stripping in metadata tier
- **WHEN** the ingestion tier is `"metadata"`
- **THEN** the SSID field is NOT included in `normalized_text` (WiFi network names can reveal location)

### Requirement: Durable Location Evidence Fidelity

Every successfully persisted OwnTracks location evidence write SHALL preserve
the accepted webhook payload in `connectors.owntracks_points` without deleting,
renaming, normalizing, or synthesizing optional fields. Evidence persistence is
a non-fatal side write after successful ingestion, so a failed evidence write
is logged without changing webhook success. This evidence store is distinct
from the `ingest.v1` envelope's privacy tier: metadata-tier envelopes still omit
`payload.raw`, while the restricted durable evidence row retains the accepted
location payload required by deterministic adapters.

#### Scenario: SSID and region membership survive evidence persistence

- **WHEN** an accepted location payload contains uppercase `SSID` and an
  `inregions` list
- **THEN** `connectors.owntracks_points.raw_payload` contains those fields with
  the same names, values, list ordering, and element values received from the
  phone
- **AND** the persistence path does not mutate the caller's payload object

#### Scenario: Optional evidence fields stay absent when not reported

- **WHEN** the phone omits `SSID` or `inregions`
- **THEN** evidence persistence does not fabricate that field

### Requirement: Operator-Owned Phone Reporting Configuration

Butlers SHALL document the phone-side configuration needed for useful
OwnTracks evidence. The operator, not the connector, owns these app changes:
enable available extended location data, verify Wi-Fi SSID reporting on
platforms that support it, create stable `Home` and `Office` regions, and
consider Move monitoring mode during waking hours when cadence is sparse. The
guidance SHALL explain that OwnTracks currently documents `SSID` as an optional
iOS field, that Move mode improves reporting cadence at a battery cost, and that
platform/version labels can differ.

#### Scenario: Operator follows the OwnTracks runbook

- **WHEN** an operator needs to improve movement and place evidence
- **THEN** the connector documentation identifies the phone settings for SSID
  reporting and `Home`/`Office` regions
- **AND** it describes how to verify that location payloads carry `SSID` and
  `inregions`
- **AND** it presents waking-hours Move mode as an explicit battery/cadence
  tradeoff rather than silently changing phone behavior

### Requirement: Sparse Durable-Point Cadence Diagnostic

`GET /api/ingestion/connectors/summaries` SHALL compute a read-only OwnTracks
cadence diagnostic from `connectors.owntracks_points`, the durable evidence
surface consumed by movement inference. Each active OwnTracks identity with
fewer than 24 points in the trailing 24 hours SHALL receive an additive
`operational_warnings` entry that names the observed count, the 24-point
minimum, and the waking-hours Move-mode remediation. The threshold is a minimum
operational baseline, not a sufficiency guarantee.

The warning SHALL NOT change connector `state`, `liveness`, fleet-health
rollups, or transport-health semantics. Archived OwnTracks identities SHALL NOT
receive new cadence warnings. The response SHALL include additive
`owntracks_cadence_available`; it is `false` only when the durable-point query
fails, in which case warnings remain empty and the dashboard SHALL name the
degraded source rather than present an all-clear.

#### Scenario: Healthy transport has sparse movement evidence

- **WHEN** an active, healthy OwnTracks identity has 3 durable location points
  in the trailing 24 hours
- **THEN** its summary keeps the existing healthy state and online liveness
- **AND** its operational warning reports the 3-point observation, 24-point
  minimum, and waking-hours Move-mode remediation

#### Scenario: Cadence meets the minimum

- **WHEN** an active OwnTracks identity has at least 24 durable location points
  in the trailing 24 hours
- **THEN** no sparse-cadence operational warning is added

#### Scenario: Cadence source is unavailable

- **WHEN** the durable-point cadence query fails
- **THEN** `owntracks_cadence_available` is `false`
- **AND** no sparse-cadence warning is fabricated from missing evidence
- **AND** connector health and liveness remain unchanged

### Requirement: Data Retention

The implementation SHALL provide the behavior described by this requirement.
Location events are automatically purged after a configurable retention period.

#### Scenario: Retention purge schedule
- **WHEN** the connector is running
- **THEN** a background task runs every 6 hours to delete expired location events
- **AND** the task deletes rows from `public.ingestion_events` where `source_channel = 'owntracks'` AND `received_at < NOW() - (<retention_days> * INTERVAL '1 day')`

#### Scenario: Default retention period
- **WHEN** `OWNTRACKS_RETENTION_DAYS` is not set
- **THEN** the default retention period is 30 days

#### Scenario: Configurable retention
- **WHEN** `OWNTRACKS_RETENTION_DAYS` is set to a positive integer
- **THEN** the retention period is that many days
- **AND** the minimum allowed value is 1 day (setting 0 or negative values causes a startup error)

#### Scenario: Purge logging
- **WHEN** the retention purge task runs
- **THEN** it logs the number of deleted rows at INFO level
- **AND** purge failures are logged at WARNING level but do NOT crash the connector

### Requirement: Checkpoint and Resume

The implementation SHALL provide the behavior described by this requirement.
The connector persists a timestamp-based checkpoint for crash-safe restart.

#### Scenario: Checkpoint persistence
- **WHEN** an event is successfully submitted to the Switchboard (accepted or duplicate)
- **THEN** the connector updates its checkpoint cursor to the event's `tst` value via `cursor_store.save_cursor()` keyed by `("owntracks", "<endpoint_identity>")`

#### Scenario: Resume on restart
- **WHEN** the connector starts
- **THEN** it loads the last checkpoint via `cursor_store.load_cursor()`
- **AND** events received with `tst <= checkpoint` are still submitted (dedup makes replays harmless) but a debug log notes the potential replay

#### Scenario: No checkpoint on first start
- **WHEN** the connector starts with no prior checkpoint
- **THEN** all received events are processed normally (no backfill window -- OwnTracks only sends live events via HTTP)

### Requirement: Connector Lifecycle

The implementation SHALL provide the behavior described by this requirement.
The connector follows the connector base contract for heartbeat, metrics, health, filtered events, and replay queue.

#### Scenario: Heartbeat
- **WHEN** the connector is running
- **THEN** it sends periodic heartbeats to the Switchboard per the connector base heartbeat protocol
- **AND** `connector_type = "owntracks"`
- **AND** heartbeat counters reflect events received, submitted, and failed
- **AND** when multiple devices post through the same instance, each resolved device runs its own independent heartbeat task under its own `endpoint_identity` (see Multiple devices per instance) rather than sharing or thrashing one heartbeat between devices

#### Scenario: Prometheus metrics
- **WHEN** the connector processes events
- **THEN** it emits standard connector Prometheus metrics: `connector_ingest_submissions_total`, `connector_ingest_latency_seconds`, `connector_errors_total`, `connector_checkpoint_saves_total`
- **AND** an additional counter `connector_owntracks_events_received_total` with labels `{endpoint_identity, event_type}` where `event_type` is `"location"`, `"transition"`, `"waypoints"`, or `"ignored"`

#### Scenario: Health endpoint
- **WHEN** a GET request is made to `/health`
- **THEN** the connector returns JSON status including: `state` (healthy/degraded/error), `uptime_s`, `last_event_at`, `events_today`

#### Scenario: Filtered event batch flush
- **WHEN** the connector filters or errors on events
- **THEN** it records them in the in-memory buffer and flushes to `connectors.filtered_events` per the base contract batch flush obligation

#### Scenario: Replay queue drain
- **WHEN** the connector completes processing a batch of webhook events
- **THEN** it checks for pending replay requests per the base contract replay queue drain loop

### Requirement: Environment Variables

The implementation SHALL provide the behavior described by this requirement.
The connector is configured via environment variables following the base connector contract plus OwnTracks-specific variables.

#### Scenario: Required environment variables
- **WHEN** the connector starts
- **THEN** `SWITCHBOARD_MCP_URL` and either `OWNTRACKS_WEBHOOK_TOKEN` or a `CredentialStore` entry for `owntracks_webhook_token` MUST be set
- **AND** the connector refuses to start if no authentication token is available

#### Scenario: OwnTracks-specific environment variables
- **WHEN** the connector starts
- **THEN** the following optional variables are available:
  - `OWNTRACKS_TRACKER_ID` -- override the device tracker ID (default: use device-reported `tid`)
  - `OWNTRACKS_RETENTION_DAYS` -- data retention period in days (default: 30)
  - `CONNECTOR_INGESTION_TIER` -- `"metadata"` (default) or `"full"`
  - `CONNECTOR_HEALTH_PORT` -- HTTP server port (default: 40083)
  - `CONNECTOR_HEARTBEAT_INTERVAL_S` -- heartbeat interval (default: 120)

### Requirement: Context Bus Integration

The implementation SHALL provide the behavior described by this requirement.
OwnTracks events feed the situational context bus (RFC 0009). Context signal derivation is a butler-side concern -- the connector only ingests and normalizes events. Butlers consuming OwnTracks events interpret them and write context signals via `set_context()` / `clear_context()`.

#### Scenario: Travel butler derives at_home from geofence transition
- **WHEN** the travel butler processes an OwnTracks transition event with `event = "enter"` and `desc = "Home"`
- **THEN** it calls `set_context("at_home", confidence=0.95, ttl=12h)` with `metadata` referencing the OwnTracks transition event
- **AND** when a transition event with `event = "leave"` and `desc = "Home"` is processed, it calls `clear_context("at_home")`

#### Scenario: Travel butler derives traveling from distance
- **WHEN** the travel butler processes an OwnTracks location event with coordinates >50km from the user's home location
- **THEN** it calls `set_context("traveling", confidence=0.7, ttl=24h)`

#### Scenario: Home butler derives at_home from geofence transition
- **WHEN** the home butler processes an OwnTracks transition event with `event = "enter"` and `desc = "Home"`
- **THEN** it calls `set_context("at_home", confidence=0.95)` with `metadata` referencing the OwnTracks transition event

#### Scenario: General butler derives commuting from velocity
- **WHEN** the general butler processes OwnTracks location events with `vel > 80` km/h sustained over multiple consecutive updates
- **THEN** it calls `set_context("commuting", confidence=0.6, ttl=45min)`

#### Scenario: Confidence levels for OwnTracks-derived signals
- **WHEN** a context signal is derived from an explicit geofence transition (enter/leave event)
- **THEN** the confidence level is 0.95 (high, but not 1.0 since it is device-inferred, not user-stated)
- **AND** when a signal is derived from distance or velocity inference, the confidence level is 0.6-0.7

### Requirement: Docker Compose Integration

The implementation SHALL provide the behavior described by this requirement.
The connector is deployed as a standalone service in the docker-compose stack.

#### Scenario: Service definition
- **WHEN** the connector is deployed via docker-compose
- **THEN** a `connector-owntracks` service is defined in Layer 1b alongside other connectors
- **AND** it depends on `log-init` and `migrations` (completed successfully) and `butlers-up` (healthy)
- **AND** it uses the standard `*connector-env` anchor plus OwnTracks-specific env vars
- **AND** `CONNECTOR_HEALTH_PORT` is set to `40086` (the code default is 40083; the compose deployment overrides it to 40086 to avoid collisions with sibling connector ports)

#### Scenario: Network and port exposure
- **WHEN** the connector runs
- **THEN** it is on the `db` and `backend` networks
- **AND** the health port (40086) is exposed for monitoring (bound to 127.0.0.1 via `OWNTRACKS_HOST_PORT`)
- **AND** the webhook port MUST be reachable by the OwnTracks mobile app (tailnet routing or reverse proxy)
