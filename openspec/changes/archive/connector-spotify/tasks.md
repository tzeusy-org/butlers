## 1. Switchboard Routing Registration

- [ ] 1.1 Add `"spotify"` to `SourceChannel` literal in `roster/switchboard/tools/routing/contracts.py`
- [ ] 1.2 Add `"spotify"` to `SourceProvider` literal in `roster/switchboard/tools/routing/contracts.py`
- [ ] 1.3 Add `"spotify": frozenset({"spotify"})` to `_ALLOWED_PROVIDERS_BY_CHANNEL`
- [ ] 1.4 Add `"spotify": "realtime"` to `HISTORY_STRATEGY` in `pipeline.py`
- [ ] 1.5 Verify `"spotify"` is NOT added to `_INTERACTIVE_ROUTE_CHANNELS` (non-interactive channel)

## 2. CredentialStore Spotify Token Support

- [ ] 2.1 Add Spotify credential keys to any credential documentation or constants: `SPOTIFY_CLIENT_ID`, `SPOTIFY_ACCESS_TOKEN`, `SPOTIFY_REFRESH_TOKEN`, `SPOTIFY_TOKEN_EXPIRES_AT`
- [ ] 2.2 Write unit tests for storing, resolving, and deleting Spotify credentials via `CredentialStore` with `category="spotify"` and `is_sensitive=True`

## 3. Dashboard API — Spotify OAuth Endpoints

- [ ] 3.1 Create FastAPI router at `src/butlers/api/routers/spotify.py` with endpoints: `GET /status`, `POST /oauth/start`, `GET /oauth/callback`, `POST /disconnect`, `POST /config`
- [ ] 3.2 Create Pydantic response models: `SpotifyStatusResponse`, `SpotifyOAuthStartResponse`, `SpotifyConfigRequest`
- [ ] 3.3 Implement `POST /config` endpoint: validate client_id (32-char hex), store in `CredentialStore`
- [ ] 3.4 Implement `POST /oauth/start` endpoint: generate PKCE code verifier + challenge, store verifier in server-side session, return authorization URL with scopes `user-read-playback-state user-read-recently-played user-top-read`
- [ ] 3.5 Implement `GET /oauth/callback` endpoint: verify CSRF state, exchange code for tokens via Spotify API, store tokens in `CredentialStore`, redirect to settings page
- [ ] 3.6 Implement `GET /status` endpoint: check stored credentials, call Spotify `GET /me` if connected, return connection state
- [ ] 3.7 Implement `POST /disconnect` endpoint: delete all Spotify credential keys from `CredentialStore`
- [ ] 3.8 Write API tests for all endpoints (Spotify API calls mocked)

## 4. Dashboard Frontend — Spotify Settings Section

- [ ] 4.1 Create `SpotifySetupCard` React component: connection status badge (connected/disconnected/error), display name, account type, last sync time
- [ ] 4.2 Create `SpotifyClientIdInput` component: input field for Spotify app client_id with validation and submit
- [ ] 4.3 Create `SpotifyConnectButton` component: initiates OAuth flow via `POST /api/spotify/oauth/start`, redirects to authorization URL
- [ ] 4.4 Create React hooks: `useSpotifyStatus()`, `useSpotifyConfig()`
- [ ] 4.5 Integrate Spotify section into existing settings page alongside Google OAuth and other account sections
- [ ] 4.6 Implement disconnect flow with confirmation dialog

## 5. Spotify API Client

- [ ] 5.1 Create `src/butlers/connectors/spotify_client.py` with async HTTP client wrapping Spotify Web API endpoints: `get_currently_playing()`, `get_recently_played(after)`, `get_me()`
- [ ] 5.2 Implement Bearer token authentication with `CredentialStore` credential resolution
- [ ] 5.3 Implement automatic token refresh on HTTP 401: exchange refresh token for new access token via `POST https://accounts.spotify.com/api/token`, update `CredentialStore`
- [ ] 5.4 Implement proactive token refresh (refresh 5 minutes before expiry using stored `SPOTIFY_TOKEN_EXPIRES_AT`)
- [ ] 5.5 Implement rate limit handling: honor `Retry-After` header on HTTP 429, exponential backoff with jitter (initial 30s, max 600s)
- [ ] 5.6 Write unit tests for API client: auth, token refresh, rate limiting, error handling (Spotify API mocked)

## 6. Listening Session State Machine

- [ ] 6.1 Create `src/butlers/connectors/spotify_session.py` with `ListeningSessionTracker` class
- [ ] 6.2 Implement state machine: `idle` → `active` → `draining` → `idle` with transitions per spec
- [ ] 6.3 Implement track change detection: compare current track ID against previous, emit `spotify.track_change` event on change
- [ ] 6.4 Implement context change detection: detect playlist/album URI changes, close current session and start new one
- [ ] 6.5 Implement drain timeout: configurable idle timeout (default 300s) before closing session on playback stop
- [ ] 6.6 Implement session summary generation: track count, duration, playlist/album context, track list
- [ ] 6.7 Write unit tests for all state transitions, edge cases (brief pauses, rapid track changes, context switches)

## 7. Spotify Connector Core (`src/butlers/connectors/spotify.py`)

- [ ] 7.1 Create single-file connector module with `if __name__ == "__main__": asyncio.run(...)` entrypoint (matching existing connector patterns)
- [ ] 7.2 Implement startup sequence: resolve credentials from `CredentialStore`, auto-resolve endpoint identity via `GET /me`, load checkpoint, init filter gate, send initial heartbeat
- [ ] 7.3 Implement adaptive polling loop: poll `currently-playing` at `SPOTIFY_POLL_ACTIVE_S` (60s) when active, exponential backoff to `SPOTIFY_POLL_IDLE_S` (300s) when idle
- [ ] 7.4 Implement `recently-played` polling with `after` cursor for gap-filling
- [ ] 7.5 Implement ingest.v1 envelope construction for track change events per field mapping spec
- [ ] 7.6 Implement ingest.v1 envelope construction for session summary events per field mapping spec
- [ ] 7.7 Integrate `ListeningSessionTracker` for session aggregation
- [ ] 7.8 Implement source filter gate via `IngestionPolicyEvaluator` with `scope = 'connector:spotify:<endpoint_identity>'`
- [ ] 7.9 Implement filtered event batch flush to `connectors.filtered_events`
- [ ] 7.10 Implement checkpoint persistence via `cursor_store.save_cursor()` / `load_cursor()` keyed by `("spotify", "<endpoint_identity>")`
- [ ] 7.11 Implement Switchboard MCP submission via `CachedMCPClient`
- [ ] 7.12 Implement credential error recovery: stop polling on auth failure, periodic credential re-check (60s), resume on valid credentials
- [ ] 7.13 Implement graceful shutdown on SIGTERM/SIGINT: complete poll cycle, persist checkpoint, send final heartbeat

## 8. Prometheus Metrics

- [ ] 8.1 Implement standard connector metrics via `ConnectorMetrics` class (ingest submissions, source API calls, checkpoint saves, errors)
- [ ] 8.2 Implement Spotify-specific metrics: `connector_spotify_polls_total`, `connector_spotify_track_changes_total`, `connector_spotify_sessions_total`, `connector_spotify_session_duration_seconds`, `connector_spotify_token_refreshes_total`
- [ ] 8.3 Implement health endpoint on port 40083 via `health_socket.py` pattern with `/health` and `/metrics`
- [ ] 8.4 Implement heartbeat protocol via shared `ConnectorHeartbeat` (default 120s interval)

## 9. Docker Compose Integration

- [ ] 9.1 Add `connector-spotify` service to `docker-compose.yml` in connector layer with `CONNECTOR_PROVIDER: spotify`, `CONNECTOR_CHANNEL: spotify`, depends on log-init/migrations/switchboard
- [ ] 9.2 Assign `CONNECTOR_HEALTH_PORT: "40083"` and appropriate networks
- [ ] 9.3 Configure entrypoint: `uv run python -m butlers.connectors.spotify`
- [ ] 9.4 Test docker-compose build and verify connector starts and resolves credentials

## 10. Integration Testing

- [ ] 10.1 Write integration test: connector starts, polls Spotify API (mocked), events flow through to Switchboard ingest
- [ ] 10.2 Write integration test: token refresh cycle — initial 401 triggers refresh, retried call succeeds
- [ ] 10.3 Write integration test: session aggregation — play/pause/resume sequence produces correct session summary
- [ ] 10.4 Write integration test: adaptive polling — active interval during playback, backoff during idle
- [ ] 10.5 Write integration test: checkpoint persistence — connector restart resumes with correct `recently-played` cursor
- [ ] 10.6 Write integration test: Dashboard OAuth flow — `/oauth/start` returns valid URL, `/oauth/callback` stores tokens
