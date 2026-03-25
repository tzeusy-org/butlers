## 1. Switchboard Routing Registration

- [ ] 1.1 Add `"owntracks"` to `SourceChannel` literal in `roster/switchboard/tools/routing/contracts.py`
- [ ] 1.2 Add `"owntracks"` to `SourceProvider` literal in `roster/switchboard/tools/routing/contracts.py`
- [ ] 1.3 Add `"owntracks": frozenset({"owntracks"})` to `_ALLOWED_PROVIDERS_BY_CHANNEL`
- [ ] 1.4 Write tests verifying `owntracks`/`owntracks` channel-provider pairing is accepted and invalid pairings are rejected

## 2. Webhook Authentication

- [ ] 2.1 Implement bearer token resolution: `CredentialStore` lookup for `owntracks_webhook_token` with `OWNTRACKS_WEBHOOK_TOKEN` env var fallback
- [ ] 2.2 Implement FastAPI dependency for bearer token validation using `hmac.compare_digest`
- [ ] 2.3 Fail-closed startup: connector refuses to start if no token is configured
- [ ] 2.4 Write tests for token validation (valid, invalid, missing header, constant-time comparison)

## 3. Core Connector Implementation

- [ ] 3.1 Create `src/butlers/connectors/owntracks.py` with `if __name__ == "__main__": asyncio.run(...)` entrypoint
- [ ] 3.2 Implement FastAPI application with combined webhook (`/owntracks/webhook`), health (`/health`), and metrics (`/metrics`) endpoints
- [ ] 3.3 Implement payload type dispatch: parse `_type` field, route to `location`, `transition`, `waypoints` handlers, ignore unknown types
- [ ] 3.4 Implement location event normalization to `ingest.v1` envelope per field mapping spec
- [ ] 3.5 Implement transition event normalization to `ingest.v1` envelope per field mapping spec
- [ ] 3.6 Implement waypoint sync event normalization to `ingest.v1` envelope
- [ ] 3.7 Implement normalized text generation for all event types (location summary with cardinal directions, transition enter/leave, waypoint sync)
- [ ] 3.8 Implement ingestion tier handling: default `metadata` (Tier 2, `payload.raw = None`), opt-in `full` (Tier 1, raw payload included) with startup warning
- [ ] 3.9 Implement SSID stripping in metadata tier normalized text
- [ ] 3.10 Implement Switchboard MCP submission via `CachedMCPClient`
- [ ] 3.11 Write unit tests for payload parsing, normalization, text generation, and tier handling

## 4. Checkpoint and Deduplication

- [ ] 4.1 Implement timestamp-based checkpoint persistence via `cursor_store.save_cursor()` keyed by `("owntracks", "<endpoint_identity>")`
- [ ] 4.2 Implement checkpoint loading on startup via `cursor_store.load_cursor()`
- [ ] 4.3 Implement idempotency key construction: `"owntracks:<endpoint_identity>:<tst>:<_type>"` (with `:<event>` suffix for transitions)
- [ ] 4.4 Write tests for checkpoint save/load and idempotency key generation

## 5. Data Retention

- [ ] 5.1 Implement background retention purge task running every 6 hours
- [ ] 5.2 Implement purge query: `DELETE FROM shared.ingestion_events WHERE source_channel = 'owntracks' AND created_at < NOW() - INTERVAL '<retention_days> days'`
- [ ] 5.3 Implement `OWNTRACKS_RETENTION_DAYS` configuration with default 30, minimum 1 validation
- [ ] 5.4 Implement purge logging (deleted count at INFO, failures at WARNING without crash)
- [ ] 5.5 Write tests for retention purge logic and configuration validation

## 6. Connector Lifecycle (Base Contract)

- [ ] 6.1 Implement heartbeat protocol with `connector_type = "owntracks"` and event counters
- [ ] 6.2 Implement Prometheus metrics: standard connector metrics plus `connector_owntracks_events_received_total` with `{endpoint_identity, event_type}` labels
- [ ] 6.3 Implement health endpoint returning JSON with state, uptime, last_event_at, events_today
- [ ] 6.4 Implement filtered event batch flush to `connectors.filtered_events`
- [ ] 6.5 Implement replay queue drain loop after webhook event processing
- [ ] 6.6 Implement source filter gate via `IngestionPolicyEvaluator` with `scope = 'connector:owntracks:<endpoint_identity>'`
- [ ] 6.7 Write tests for heartbeat assembly, metrics emission, and health endpoint

## 7. Dashboard Setup UX — API

- [ ] 7.1 Create `roster/switchboard/api/owntracks.py` (or appropriate dashboard API location) with FastAPI router for OwnTracks settings endpoints
- [ ] 7.2 Implement `POST /api/connectors/owntracks/token/generate`: generate 32-byte hex token, store in `CredentialStore` under `owntracks_webhook_token`, return token
- [ ] 7.3 Implement `GET /api/connectors/owntracks/status`: return connection state, last event timestamp, event count (from connector heartbeat data)
- [ ] 7.4 Implement `GET /api/connectors/owntracks/config`: return computed webhook URL and setup instructions metadata
- [ ] 7.5 Create Pydantic response models: `OwnTracksStatusResponse`, `OwnTracksTokenResponse`, `OwnTracksConfigResponse`
- [ ] 7.6 Write API tests for all endpoints (connector mocked)

## 8. Dashboard Setup UX — Frontend

- [ ] 8.1 Create `OwnTracksSetupCard` React component: connection status indicator, webhook URL (copyable), bearer token (masked with reveal/copy), and app configuration guide
- [ ] 8.2 Implement token generation/regeneration flow with confirmation dialog for regeneration
- [ ] 8.3 Implement connection status display: last event timestamp, events today, liveness badge
- [ ] 8.4 Implement inline OwnTracks app configuration instructions (iOS/Android differentiated)
- [ ] 8.5 Implement "no events received" hint after 1 hour of setup with troubleshooting guidance
- [ ] 8.6 Integrate OwnTracks section into existing settings page at `/butlers/settings`

## 9. Docker Compose Integration

- [ ] 9.1 Add `connector-owntracks` service to `docker-compose.yml` in Layer 1b with `*connector-env`, depends on log-init/migrations/switchboard
- [ ] 9.2 Set `CONNECTOR_HEALTH_PORT: "40083"`, networks `[db, backend]`, and appropriate env vars
- [ ] 9.3 Expose webhook port for OwnTracks app reachability (tailnet routing)
- [ ] 9.4 Test full stack: connector starts, authenticates webhook requests, submits to Switchboard

## 10. Integration Testing

- [ ] 10.1 Write integration test: valid webhook POST with location payload is normalized and submitted to Switchboard
- [ ] 10.2 Write integration test: valid webhook POST with transition payload is normalized and submitted to Switchboard
- [ ] 10.3 Write integration test: webhook POST without auth returns 401
- [ ] 10.4 Write integration test: webhook POST with invalid auth returns 401
- [ ] 10.5 Write integration test: webhook POST with unknown `_type` returns 200 but is not ingested
- [ ] 10.6 Write integration test: metadata tier omits `payload.raw`, full tier includes it
- [ ] 10.7 Write integration test: retention purge deletes old events and preserves recent ones
- [ ] 10.8 Write integration test: dashboard token generation stores token in CredentialStore and connector accepts it
