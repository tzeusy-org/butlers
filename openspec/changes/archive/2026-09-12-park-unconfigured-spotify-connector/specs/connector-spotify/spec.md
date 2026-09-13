## MODIFIED Requirements

### Requirement: Connector Lifecycle

The connector SHALL follow the standard connector lifecycle defined in `connector-base-spec`, and SHALL treat an owner who has never connected a Spotify account as an expected steady state rather than a startup failure.

#### Scenario: Startup sequence

- **WHEN** the connector process starts
- **THEN** it SHALL: resolve the app client ID from `CredentialStore`; resolve
  personal-account tokens from owner `public.entity_info` through
  `resolve_owner_entity_info()`; auto-resolve endpoint identity via `GET /me`;
  load the last checkpoint from `cursor_store`; initialize the source filter
  gate via `IngestionPolicyEvaluator`; send an initial heartbeat; and begin
  the polling loop

#### Scenario: Unconfigured account parks instead of exiting

- **WHEN** the connector starts and no Spotify app client ID or owner refresh
  token has ever been stored
- **THEN** it SHALL NOT raise out of startup, exit non-zero, or restart
- **AND** it SHALL skip identity resolution and checkpoint load, start its
  health server and heartbeat under the sentinel endpoint identity
  `spotify:unconfigured`, and report state `degraded` with the fixed local
  message `awaiting_credentials` through both the heartbeat and `/health`
- **AND** it SHALL emit at most one log line per entry into the parked state,
  never a traceback per re-check
- **AND** it SHALL NOT attribute any ingest envelope or checkpoint to the
  sentinel identity

#### Scenario: Configuring an account activates the parked connector in place

- **WHEN** a parked connector's periodic 60s credential re-check first
  resolves an app client ID and owner refresh token
- **THEN** it SHALL resolve endpoint identity via `GET /me`, rebind metrics,
  the ingestion policy scope, the filtered-event buffer, and the heartbeat to
  the resolved `spotify:<spotify_user_id>` identity, load the checkpoint, and
  begin polling without a process restart

#### Scenario: Credential faults after configuration remain loud

- **WHEN** credential resolution fails for any reason other than a
  never-connected account, or a credential fault occurs after the connector
  has successfully configured
- **THEN** the parked path SHALL NOT swallow it
- **AND** the connector SHALL surface it exactly as before: heartbeat state
  `error` for a proven token-endpoint revocation, and a non-zero exit for a
  fault raised out of startup

#### Scenario: Graceful shutdown

- **WHEN** the connector receives SIGTERM or SIGINT
- **THEN** it SHALL complete the current poll cycle, persist the checkpoint, send a final heartbeat, and exit cleanly

#### Scenario: Heartbeat protocol

- **WHEN** the connector is running
- **THEN** it SHALL send heartbeats via `connector.heartbeat` MCP tool at `CONNECTOR_HEARTBEAT_INTERVAL_S` (default 120s)
- **AND** heartbeats SHALL include: `connector_type = "spotify"`, `endpoint_identity`, `instance_id`, state (`healthy`/`degraded`/`error`), uptime, and operational counters

#### Scenario: Health and metrics endpoint

- **WHEN** the connector is running
- **THEN** it SHALL expose `/health` and `/metrics` endpoints on `CONNECTOR_HEALTH_PORT` (default 40083)
