# Spotify Connector

## Purpose

The Spotify connector is a standalone polling process that reads the user's current playback state and recently-played tracks from the Spotify Web API, detects listening state transitions, aggregates logical listening sessions, normalizes events into `ingest.v1` envelopes, and submits them to the Switchboard. It provides butlers with passive awareness of the user's music listening patterns — a rich, low-effort signal for situational context, memory enrichment, and session awareness.

Unlike messaging connectors, the Spotify connector has no discretion layer (all events are the user's own activity), no per-chat buffering (no "chats" exist), and no interactive routing (listening events are not messages requiring a reply). It is a pure polling-and-ingest connector.

## ADDED Requirements

### Requirement: Polling-Based Ingestion Loop

The connector SHALL poll the Spotify Web API at configurable intervals to detect playback state changes.

#### Scenario: Active playback polling

- **WHEN** the connector detects active playback (Spotify returns `is_playing: true`)
- **THEN** it SHALL poll `GET /me/player/currently-playing` every `SPOTIFY_POLL_ACTIVE_S` seconds (default 60)
- **AND** each poll response SHALL be compared against the previous state to detect track changes and context changes

#### Scenario: Idle polling with backoff

- **WHEN** the connector detects no active playback (no device, `is_playing: false`, or private session)
- **THEN** it SHALL increase the poll interval using exponential backoff up to `SPOTIFY_POLL_IDLE_S` seconds (default 300)
- **AND** any state change (playback resumes) SHALL reset the poll interval to `SPOTIFY_POLL_ACTIVE_S`

#### Scenario: Recently-played polling

- **WHEN** the connector completes a poll cycle
- **THEN** it SHALL also poll `GET /me/player/recently-played` with the `after` cursor parameter set to the last-seen play timestamp
- **AND** gap-fill polling SHALL be throttled to `SPOTIFY_GAP_FILL_IDLE_INTERVAL_S` (default 10800)
- **AND** tracks not already observed via the `currently-playing` endpoint SHALL be emitted as a single batched gap-fill digest (`external_event_id = "spotify:gapfill:<first_ms>:<last_ms>"`), not as per-track events

#### Scenario: Private session handling

- **WHEN** the Spotify API indicates a private session (no playback data returned despite an active device)
- **THEN** the connector SHALL treat this as idle state and back off polling
- **AND** it SHALL NOT emit any events for the private session period

### Requirement: Context Start Event

The connector SHALL emit one context-start event per listening context when playback begins from idle, to minimize LLM ingestion cost. It SHALL NOT emit per-track events during continuous playback. Track changes within a context are accumulated silently.

#### Scenario: Context start detection

- **WHEN** playback begins from idle (a new listening context starts)
- **THEN** the connector SHALL emit a `spotify.context_start` event containing: first track name, artist name(s), album name, context URI, device name, and playback timestamp
- **AND** subsequent track changes within the same context SHALL be accumulated silently with no per-track event

#### Scenario: Context start ingest.v1 envelope

- **WHEN** a context-start event is emitted
- **THEN** the `ingest.v1` envelope SHALL have:
  - `source.channel = "spotify_user_client"`
  - `source.provider = "spotify"`
  - `source.endpoint_identity = "spotify:<spotify_user_id>"`
  - `event.external_event_id = "spotify:ctx:<timestamp_ms>:<context_uri_or_track_id>"`
  - `event.external_thread_id = "<playlist_uri|album_uri|null>"`
  - `event.observed_at` = poll timestamp (RFC3339, timezone-aware)
  - `sender.identity = "<spotify_user_id>"`
  - `payload.raw` = full Spotify `currently-playing` API response dict
  - `payload.normalized_text = "Started listening to <context> first track: <track> by <artist>"` (falls back to `"Started listening to <track> by <artist>"` when no context label)
  - `control.idempotency_key = "spotify:<endpoint_identity>:ctx:<timestamp_ms>:<context_uri_or_track_id>"`
  - `control.policy_tier = "default"`
  - `control.ingestion_tier = "full"`

#### Scenario: Listening digest event

- **WHEN** active playback continues past `SPOTIFY_DIGEST_INTERVAL_S` (default 3600)
- **THEN** the connector SHALL emit a `spotify.listening_digest` event (`external_event_id = "spotify:digest:<digest_start_ms>"`) summarizing the tracks accumulated since the context start or previous digest

#### Scenario: Repeated poll with same track

- **WHEN** consecutive polls return the same track ID
- **THEN** the connector SHALL NOT emit any event
- **AND** the internal session state SHALL be updated with the latest poll timestamp

### Requirement: Listening Session Aggregation

The connector SHALL aggregate contiguous playback into logical listening sessions and emit session summary events.

#### Scenario: Session lifecycle state machine

- **WHEN** the connector tracks playback state
- **THEN** it SHALL maintain a state machine with states: `idle`, `active`, `draining`
- **AND** transitions SHALL follow:
  - `idle` + playback detected → `active` (start new session, emit `context_start`)
  - `active` + track changed → `active` (accumulate silently, no event)
  - `active` + context changed mid-playback (autoplay, radio, DJ) → `active` (accumulated silently, not a session boundary)
  - `active` + playback stopped → `draining`
  - `draining` + idle timeout (default 300s) exceeded → `idle`, emit `session_summary`
  - `draining` + playback resumed with same context → `active` (continue session, no event)

#### Scenario: Session summary event

- **WHEN** a listening session ends (context change or idle timeout after playback stop)
- **THEN** the connector SHALL emit a `spotify.session_summary` event containing: session start time, session end time, total duration, track count, playlist or album context, and list of track names played

#### Scenario: Session summary ingest.v1 envelope

- **WHEN** a session summary event is emitted
- **THEN** the `ingest.v1` envelope SHALL have:
  - `event.external_event_id = "spotify:session:<session_start_timestamp_ms>"`
  - `event.external_thread_id = "<playlist_uri|album_uri|null>"`
  - `payload.normalized_text = "Listening session: <N> tracks over <duration> from <playlist_or_album>"`
  - All other fields follow the same pattern as track change events

### Requirement: Spotify API Client

The connector SHALL use an async HTTP client to communicate with the Spotify Web API.

#### Scenario: API authentication

- **WHEN** the connector makes a Spotify API call
- **THEN** it SHALL include the access token in the `Authorization: Bearer <token>` header
- **AND** the token SHALL be resolved from `CredentialStore` at startup

#### Scenario: Automatic token refresh

- **WHEN** a Spotify API call returns HTTP 401 (Unauthorized)
- **THEN** the connector SHALL attempt to refresh the access token using the stored refresh token via `POST https://accounts.spotify.com/api/token` with `grant_type=refresh_token`, `refresh_token`, and `client_id`
- **AND** the new access token (and new refresh token if rotated) SHALL be stored in `CredentialStore`
- **AND** the original API call SHALL be retried once with the new token

#### Scenario: Token refresh failure

- **WHEN** the token refresh fails (HTTP 400 with `invalid_grant` or similar)
- **THEN** the connector SHALL set its heartbeat state to `error` with message "Spotify authorization expired. Re-connect via dashboard settings."
- **AND** the connector SHALL stop polling and wait for a new token (periodic credential re-check every 60s)
- **AND** it SHALL NOT crash or exit

#### Scenario: Rate limit handling

- **WHEN** the Spotify API returns HTTP 429 (Too Many Requests)
- **THEN** the connector SHALL honor the `Retry-After` header
- **AND** if no `Retry-After` is present, it SHALL use exponential backoff with jitter (initial 30s, max 600s)

#### Scenario: Proactive token refresh

- **WHEN** the connector knows the access token expiry time (from the OAuth response `expires_in` field)
- **THEN** it SHALL proactively refresh the token 5 minutes before expiry
- **AND** this avoids the latency of a failed request + retry cycle

### Requirement: Endpoint Identity Auto-Resolution

The connector SHALL auto-resolve its `endpoint_identity` at startup by calling the Spotify API.

#### Scenario: Identity resolution via Spotify profile

- **WHEN** the connector starts and has valid credentials
- **THEN** it SHALL call `GET /me` to retrieve the user's Spotify profile
- **AND** `endpoint_identity` SHALL be set to `"spotify:<spotify_user_id>"` where `spotify_user_id` is the `id` field from the profile response

#### Scenario: Identity resolution failure

- **WHEN** the `GET /me` call fails at startup
- **THEN** the connector SHALL retry with exponential backoff
- **AND** it SHALL NOT begin polling until identity is resolved

### Requirement: Connector Lifecycle

The connector SHALL follow the standard connector lifecycle defined in `connector-base-spec`.

#### Scenario: Startup sequence

- **WHEN** the connector process starts
- **THEN** it SHALL: resolve credentials from `CredentialStore`, auto-resolve endpoint identity via `GET /me`, load the last checkpoint from `cursor_store`, initialize the source filter gate via `IngestionPolicyEvaluator`, send an initial heartbeat, and begin the polling loop

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

### Requirement: Checkpoint Persistence

The connector SHALL persist resume checkpoints for crash-safe restart.

#### Scenario: Checkpoint after successful poll

- **WHEN** a poll cycle completes and events are successfully submitted to the Switchboard
- **THEN** the connector SHALL persist the poll timestamp as a cursor via `cursor_store.save_cursor()` keyed by `("spotify", "<endpoint_identity>")`

#### Scenario: Resume from checkpoint

- **WHEN** the connector restarts
- **THEN** it SHALL load the last checkpoint from `cursor_store`
- **AND** for `recently-played`, it SHALL use the checkpoint timestamp as the `after` cursor to avoid re-processing
- **AND** for `currently-playing`, it SHALL start polling from current state (no backlog)

### Requirement: Environment Variables

The connector SHALL use standard connector environment variables plus Spotify-specific configuration.

#### Scenario: Required environment variables

- **WHEN** the connector starts
- **THEN** `SWITCHBOARD_MCP_URL` and `CONNECTOR_PROVIDER=spotify` and `CONNECTOR_CHANNEL=spotify` SHALL be set
- **AND** credentials SHALL be resolved from `CredentialStore` (not from environment variables)

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

### Requirement: Prometheus Metrics

The connector SHALL export Spotify-specific Prometheus metrics in addition to the standard connector metrics.

#### Scenario: Spotify-specific metrics

- **WHEN** the connector is running
- **THEN** it SHALL export:
  - `connector_spotify_polls_total` (Counter, labels: `endpoint_identity`, `status=success|error|rate_limited|idle`)
  - `connector_spotify_context_starts_total` (Counter, labels: `endpoint_identity`)
  - `connector_spotify_digests_total` (Counter, labels: `endpoint_identity`)
  - `connector_spotify_sessions_total` (Counter, labels: `endpoint_identity`)
  - `connector_spotify_session_duration_seconds` (Histogram, labels: `endpoint_identity`)
  - `connector_spotify_token_refreshes_total` (Counter, labels: `endpoint_identity`, `status=success|error`)

### Requirement: Required Spotify API Scopes

The OAuth authorization request SHALL include the scopes needed for both connector operation and module tools.

#### Scenario: Scope specification

- **WHEN** the OAuth authorization URL is constructed
- **THEN** the `scope` parameter SHALL include read scopes:
  - `user-read-playback-state` — read current playback state
  - `user-read-recently-played` — read recently played tracks
  - `user-top-read` — read top artists and tracks
  - `playlist-read-private` — read private playlists
  - `playlist-read-collaborative` — read collaborative playlists
  - `user-library-read` — read saved tracks/albums
- **AND** write scopes:
  - `playlist-modify-public` — create/edit public playlists
  - `playlist-modify-private` — create/edit private playlists
  - `user-modify-playback-state` — control playback (Premium)
  - `user-library-modify` — save/remove library tracks

### Requirement: Source Filter Gate

The connector SHALL implement the source filter gate per `connector-base-spec`.

#### Scenario: Filter gate evaluation

- **WHEN** the connector normalizes a listening event into an `ingest.v1` envelope
- **THEN** it SHALL evaluate the envelope against its `IngestionPolicyEvaluator` with `scope = 'connector:spotify:<endpoint_identity>'`
- **AND** blocked events SHALL be dropped and recorded in the filtered events buffer

#### Scenario: Filtered event flush

- **WHEN** a poll cycle completes
- **THEN** the connector SHALL flush any filtered events to `connectors.filtered_events` via batch INSERT
