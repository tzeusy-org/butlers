## MODIFIED Requirements

### Requirement: Environment Variables

The connector SHALL use standard operational connector environment variables plus Spotify-specific configuration. Static identity SHALL come from the canonical descriptor.

ID: REQ-connector-spotify-001
Source: bu-dss7he.9 released S3; openspec/specs/connector-spotify/spec.md requirement Environment Variables
Scope: v1-mandatory

#### Scenario: Required environment variables

- **WHEN** the connector starts
- **THEN** `SWITCHBOARD_MCP_URL` SHALL be set; the canonical descriptor SHALL supply provider `spotify` and the existing emitted channel `spotify_user_client`. The unused configurable identity fields SHALL NOT be retained
- **AND** `SPOTIFY_CLIENT_ID` SHALL be resolved from Tier 1 `CredentialStore`
- **AND** identity-bound access and refresh tokens SHALL be resolved from RFC
  0006 Tier 2 owner `public.entity_info` via `resolve_owner_entity_info()`
- **AND** no Spotify credential SHALL be resolved from environment variables

#### Scenario: Optional environment variables

- **WHEN** the connector starts
- **THEN** the following SHALL be optionally configurable:
  - `SPOTIFY_POLL_ACTIVE_S` (default 60): polling interval during active playback
  - `SPOTIFY_POLL_IDLE_S` (default 300): maximum polling interval during idle
  - `SPOTIFY_SESSION_IDLE_TIMEOUT_S` (default 300): seconds of no playback before closing a session
  - `SPOTIFY_DIGEST_INTERVAL_S` (default 3600): seconds of continuous active playback before emitting a `listening_digest`
  - `SPOTIFY_GAP_FILL_IDLE_INTERVAL_S` (default 10800): throttle interval for recently-played gap-fill polling
  - `CONNECTOR_HEALTH_PORT` (default 40083): health/metrics HTTP port
  - `CONNECTOR_HEARTBEAT_INTERVAL_S` (default 120): heartbeat interval
  - `CONNECTOR_MAX_INFLIGHT` (default 8): max concurrent ingest submissions
