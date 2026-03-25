## 1. Switchboard Routing Registration

- [ ] 1.1 Add `"whatsapp_user_client"` to `SourceChannel` literal in `roster/switchboard/tools/routing/contracts.py`
- [ ] 1.2 Add `"whatsapp"` to `SourceProvider` literal in `roster/switchboard/tools/routing/contracts.py`
- [ ] 1.3 Add `"whatsapp_user_client": frozenset({"whatsapp"})` to `_ALLOWED_PROVIDERS_BY_CHANNEL`
- [ ] 1.4 Add `"whatsapp_user_client": "realtime"` to `HISTORY_STRATEGY` in `pipeline.py` (the existing `"whatsapp"` key stays for future bot connector)
- [ ] 1.5 Add `"whatsapp_user_client"` to `_INTERACTIVE_ROUTE_CHANNELS` in `daemon.py` (the existing `"whatsapp"` entry stays)
- [ ] 1.6 Add `"whatsapp"` to the `notify()` channel-to-delivery mapping in `daemon.py` so `notify(channel="whatsapp", ...)` routes to the WhatsApp module's send tool

## 2. Contacts Identity Integration

- [ ] 2.1 Use `whatsapp_jid` as the `contact_info.type` convention for WhatsApp identities (no schema change needed — open string field)
- [ ] 2.2 Add phone-number fallback logic to `resolve_contact_by_channel` for `whatsapp_jid` type (extract E.164 prefix from `<number>@s.whatsapp.net` JID, try `type="phone"`)
- [ ] 2.3 Write tests for WhatsApp JID → contact resolution and phone-number cross-reference merge

## 3. Database Migration

- [ ] 3.1 Create Alembic migration for `whatsapp_sessions` table (id UUID PK, phone_number TEXT UNIQUE, device_id TEXT, session_data JSONB, paired_at TIMESTAMPTZ, last_seen_at TIMESTAMPTZ, active BOOLEAN DEFAULT true)
- [ ] 3.2 Write migration test verifying table creation and unique constraint on phone_number

## 4. Go Bridge Binary — Scaffolding

- [ ] 4.1 Create `whatsapp-bridge/` directory with Go module: `go.mod` (`go.mau.fi/whatsmeow` dependency), `cmd/bridge/main.go` entrypoint, `internal/` package structure
- [ ] 4.2 Implement CLI subcommands: `run` (default — start bridge), `pair` (interactive QR pairing), `status` (print session state and exit)
- [ ] 4.3 Implement PostgreSQL session store adapter (read/write `whatsapp_sessions` table via whatsmeow's `Store` interface)

## 5. Go Bridge Binary — QR Pairing

- [ ] 5.1 Implement CLI `pair` subcommand: terminal QR display, auto-refresh on expiry, 120s timeout, session write on success, exit codes (0=success, 1=timeout)
- [ ] 5.2 Implement HTTP `POST /pair/start` endpoint: generate QR, return base64 PNG data URI + expiry timestamp
- [ ] 5.3 Implement HTTP `GET /pair/poll` endpoint: return `waiting`/`paired`/`expired` status for dashboard polling
- [ ] 5.4 Implement session invalidation detection: mark session `active=false`, emit `session_invalidated` event, exit code 2

## 6. Go Bridge Binary — HTTP API

- [ ] 6.1 Implement Unix socket HTTP server with socket cleanup and 0600 permissions
- [ ] 6.2 Implement `GET /events` SSE stream: map whatsmeow message events to JSON, include keepalive every 30s
- [ ] 6.3 Implement `POST /send` endpoint: relay message via whatsmeow, support `reply_to` field, return message ID
- [ ] 6.4 Implement `GET /status` endpoint: report state (connected/connecting/disconnected/pair_required), phone, uptime, last_event_at
- [ ] 6.5 Implement `POST /disconnect` endpoint: graceful disconnect without invalidating session keys, exit code 0
- [ ] 6.6 Write Go tests for session store adapter, message event mapping, and HTTP API endpoints

## 7. Dockerfile Multi-Stage Build

- [ ] 7.1 Add `golang:1.22-bookworm` builder stage to `Dockerfile` that compiles `whatsapp-bridge/cmd/bridge` with `CGO_ENABLED=0 go build -ldflags="-s -w"`
- [ ] 7.2 Copy compiled binary to `/usr/local/bin/whatsapp-bridge` in the final image
- [ ] 7.3 Add `whatsapp` to EXTRAS-gated Python dependency install (for `qrcode` etc. in pyproject.toml)
- [ ] 7.4 Verify Docker build succeeds: `docker build --build-arg EXTRAS=whatsapp .`

## 8. Bridge Subprocess Manager (New Pattern)

- [ ] 8.1 Implement `BridgeSubprocessManager` class in `src/butlers/connectors/bridge_manager.py` using `asyncio.create_subprocess_exec()`
- [ ] 8.2 Implement subprocess lifecycle: start, health polling (via `/status` endpoint), stdout/stderr capture and logging
- [ ] 8.3 Implement restart with jittered exponential backoff (initial 5s, max 300s) on unexpected exit
- [ ] 8.4 Implement exit code interpretation: 0=clean shutdown, 1=pairing timeout (no restart), 2=session invalidated (no restart, log re-pair needed)
- [ ] 8.5 Implement graceful shutdown: POST `/disconnect`, wait 5s, SIGTERM fallback
- [ ] 8.6 Write unit tests for lifecycle management, restart backoff, and exit code handling

## 9. WhatsApp Module (`src/butlers/modules/whatsapp.py`)

- [ ] 9.1 Create `WhatsAppConfig` Pydantic model with `send_tools` (bool, default false), `send_enabled` (bool, default false), `bridge_socket` (str, default `/tmp/wa-bridge.sock`), and `user` credential scope
- [ ] 9.2 Add config validation: error if `send_enabled=true` and `send_tools=false`
- [ ] 9.3 Implement `WhatsAppModule(Module)` with `name="whatsapp"`, `config_schema=WhatsAppConfig`, `dependencies=[]`, `migration_revisions()=None`
- [ ] 9.4 Implement `register_tools()`: conditionally register `whatsapp_send_message` and `whatsapp_reply_to_message` only when `send_tools=true` (following email module's pattern); gate execution behind `send_enabled` runtime check
- [ ] 9.5 Implement `on_startup()`: resolve `whatsapp_phone` from entity_info, start Go bridge via `BridgeSubprocessManager`, wait for `/status` connected (30s timeout)
- [ ] 9.6 Implement `on_shutdown()`: delegate to `BridgeSubprocessManager` graceful shutdown
- [ ] 9.7 Handle bridge binary not found: raise `RuntimeError` with clear install instructions
- [ ] 9.8 Write unit tests for config validation, tool registration modes (no send_tools vs send_tools+disabled vs send_tools+enabled), and bridge lifecycle

## 10. WhatsApp User Client Connector (`src/butlers/connectors/whatsapp_user_client.py`)

- [ ] 10.1 Create single-file connector module with `if __name__ == "__main__": asyncio.run(...)` entrypoint (matching telegram_user_client pattern)
- [ ] 10.2 Implement bridge management via `BridgeSubprocessManager`: start bridge, reconnect with backoff on failure
- [ ] 10.3 Implement SSE event consumer: connect to bridge's `GET /events` via async HTTP client on Unix socket
- [ ] 10.4 Implement ingest.v1 normalization: map bridge JSON events to `IngestEnvelopeV1` per field mapping spec
- [ ] 10.5 Implement message type normalization: text verbatim, image/video/audio/sticker/location/contact/poll/reaction annotations per spec
- [ ] 10.6 Implement `ChatBuffer` per-chat buffering with configurable `flush_interval_s` (600s) and `buffer_max_messages` (50)
- [ ] 10.7 Implement time-based and size-based flush logic with Switchboard MCP submission via `CachedMCPClient`
- [ ] 10.8 Integrate shared discretion layer: `DiscretionEvaluator` per chat JID (from `discretion.py`), `ContactWeightResolver` with `type="whatsapp_jid"`, source name `"wa:{chat_jid}"`
- [ ] 10.9 Implement checkpoint persistence via `cursor_store.save_cursor()` / `load_cursor()` keyed by `(whatsapp_user_client, whatsapp:<phone>)`
- [ ] 10.10 Implement restart-safe resume from checkpoint
- [ ] 10.11 Implement bounded backfill on startup via `CONNECTOR_BACKFILL_WINDOW_H`
- [ ] 10.12 Implement health endpoint on port 40082 via `health_socket.py` pattern with `/health` and `/metrics`
- [ ] 10.13 Implement heartbeat protocol via shared `ConnectorHeartbeat` (default 120s interval)
- [ ] 10.14 Implement filtered event batch flush to `connectors.filtered_events` via shared `FilteredEventBuffer`
- [ ] 10.15 Write unit tests for normalization, buffering, discretion integration, checkpoint, and bridge reconnection

## 11. Docker Compose Integration

- [ ] 11.1 Add `connector-whatsapp-user` service to `docker-compose.dev.yml` in Layer 1b (alongside telegram connectors) with `EXTRAS: whatsapp` build arg, `*connector-env`, depends on log-init/migrations/switchboard
- [ ] 11.2 Assign `CONNECTOR_HEALTH_PORT: "40082"` and networks `[db, backend]`
- [ ] 11.3 Update `scripts/dev-compose.sh` Layer 1b to include the whatsapp connector service
- [ ] 11.4 Test full stack: `docker compose -f docker-compose.yml -f docker-compose.dev.yml build --build-arg EXTRAS=whatsapp` and verify connector starts and connects to bridge

## 12. Messenger Butler Configuration

- [ ] 12.1 Add `[modules.whatsapp]` section to `roster/messenger/butler.toml` with `send_tools = true`, `send_enabled = false`
- [ ] 12.2 Add `[modules.approvals.gated_tools.whatsapp_send_message]` with `risk_tier = "medium"` and `[modules.approvals.gated_tools.whatsapp_reply_to_message]` with `risk_tier = "medium"`
- [ ] 12.3 Document WhatsApp rate limit target (10/min) and timeout (20s) in messenger butler config comments (defer enforcement to when sending is enabled)

## 13. Dashboard API — WhatsApp Settings

- [ ] 13.1 Create `src/butlers/api/routers/whatsapp.py` FastAPI router with endpoints: `GET /status`, `POST /pair/start`, `GET /pair/poll`, `POST /disconnect`, `GET /health`
- [ ] 13.2 Create Pydantic response models in `src/butlers/api/models/whatsapp.py`: `WhatsAppStatusResponse`, `WhatsAppPairStartResponse`, `WhatsAppPairPollResponse`, `WhatsAppHealthResponse`
- [ ] 13.3 Implement bridge communication: API router connects to bridge Unix socket to proxy `/status`, `/pair/start`, `/pair/poll`
- [ ] 13.4 Write API tests for all endpoints (bridge mocked)

## 14. Dashboard Frontend — WhatsApp Settings Section

- [ ] 14.1 Create `WhatsAppSetupCard` React component: connection status card with health badge (connected/disconnected/pair_required/not_configured)
- [ ] 14.2 Create `WhatsAppPairModal` React component: QR code display with auto-refresh, pairing progress polling, timeout handling
- [ ] 14.3 Create React hooks: `useWhatsAppStatus()`, `useWhatsAppHealth()`, `useWhatsAppPairStart()`, `useWhatsAppPairPoll()`
- [ ] 14.4 Integrate WhatsApp section into existing settings page alongside Google OAuth section
- [ ] 14.5 Implement disconnect flow with confirmation dialog

## 15. Integration Testing

- [ ] 15.1 Write integration test: connector starts, bridge connects (mocked whatsmeow), events flow through to Switchboard ingest
- [ ] 15.2 Write integration test: module registers tools with `send_tools=true` (tools present but disabled) and without `send_tools` (no tools registered)
- [ ] 15.3 Write integration test: approval gate auto-approves send to owner contact; approval gate pends send to external contact
- [ ] 15.4 Write integration test: WhatsApp JID resolves to existing contact via phone-number cross-reference
- [ ] 15.5 Write integration test: Docker compose build with EXTRAS=whatsapp produces image with `/usr/local/bin/whatsapp-bridge` binary
- [ ] 15.6 Write integration test: Dashboard API `/pair/start` returns QR data URI, `/pair/poll` returns pairing status

## 16. Operator Documentation

- [ ] 16.1 Document QR pairing workflow: dashboard UX (primary) and CLI fallback for headless environments
- [ ] 16.2 Document session recovery procedure: how to detect expired sessions, how to re-pair
- [ ] 16.3 Document ban-risk mitigation guidance: established account, real SIM, low volume, monitoring signals
