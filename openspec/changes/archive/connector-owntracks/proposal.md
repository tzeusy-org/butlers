## Why

Butlers lack awareness of the user's physical location. Without location data, travel detection relies on manual input, geofence-triggered automations (arrival/departure) are impossible, and the situational context bus cannot set signals like `traveling` or `commuting` from real-world movement. OwnTracks is a self-hosted location tracking app that publishes location updates via HTTP webhook with a well-defined JSON payload. Adding an OwnTracks connector gives butlers passive, privacy-preserving location awareness without depending on any cloud service.

## What Changes

- **New OwnTracks connector** (`src/butlers/connectors/owntracks.py`): An HTTP webhook server that receives POST requests from the OwnTracks app, normalizes location events and waypoint transitions into `ingest.v1` envelopes, and submits them to the Switchboard via MCP. Runs as a standalone connector process.
- **Switchboard routing registration**: Add `owntracks` to `SourceChannel` and `SourceProvider`, validate channel-provider pair in `_ALLOWED_PROVIDERS_BY_CHANNEL`.
- **Webhook authentication**: Bearer token validation on every incoming POST. Token stored in `CredentialStore`, with env var fallback.
- **Dashboard setup UX**: Dedicated "OwnTracks" section on the Butlers settings page (`/butlers/settings`) that generates/displays the webhook URL and bearer token, shows inline OwnTracks app configuration instructions, and displays connection status. Token generation, display, and regeneration handled through the dashboard.
- **Configurable data retention**: Location events auto-purge after a configurable number of days (default 30). Location data is highly personal -- conservative defaults are mandatory.
- **Waypoint transition support**: OwnTracks publishes `enter`/`leave` events when the user crosses a geofenced region. These are normalized as distinct event types for downstream butler consumption.
- **Privacy-first design**: All data stays local (user-federated model). The connector is opt-in only (not enabled by default). Default ingestion tier is `metadata` (Tier 2) -- no raw GPS coordinates stored in the ingest payload unless explicitly configured for `full` (Tier 1).
- **Docker compose integration**: New connector service alongside existing connectors.

## Capabilities

### New Capabilities
- `connector-owntracks`: OwnTracks HTTP webhook connector -- webhook server, authentication, location and waypoint event normalization to ingest.v1, privacy controls (retention, ingestion tier defaults), dashboard setup UX (token generation, webhook URL display, app configuration guide, connection status), and connector lifecycle (heartbeat, metrics, health endpoint).

### Modified Capabilities
- `connector-base-spec`: Add `owntracks` to `SourceChannel` enum and `SourceProvider` enum, add `owntracks`/`owntracks` to valid channel-provider pairings.

## Impact

- **Routing contracts** (`roster/switchboard/tools/routing/contracts.py`): Extend `SourceChannel` and `SourceProvider` literals, add to `_ALLOWED_PROVIDERS_BY_CHANNEL`.
- **Database**: No new tables required. Location events flow through existing `shared.ingestion_events` and `message_inbox`. Retention purge operates on existing tables filtered by `source_channel = 'owntracks'`.
- **Docker compose**: New `connector-owntracks` service in Layer 1b with health port allocation.
- **Network surface**: The connector runs an HTTP server accepting POSTs from the OwnTracks app. This is the reverse of poll-based connectors (Gmail, Telegram) -- the connector is a server, not a client. Requires a reachable endpoint on the tailnet.
- **Security surface**: Webhook endpoint must validate authentication on every request. Misconfigured auth exposes a location data ingestion endpoint.
- **Context bus integration**: Downstream butlers (Travel, Home, General) consume OwnTracks location events to write situational context signals (`at_home`, `traveling`, `commuting`) via RFC 0009. The connector only ingests events; butlers derive context. See design.md D9 and the spec's Context Bus Integration requirement for the full signal derivation mapping.
- **External dependency**: None. OwnTracks uses a simple HTTP POST with JSON payload. No SDK, no protocol library, no binary dependency.
