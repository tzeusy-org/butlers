## Context

The butler framework has a mature messaging connector pattern established by Telegram: a **connector** (standalone process for ingestion) paired with a **module** (butler-loaded for output tools). The Telegram user-client connector uses Telethon (Python, MTProto); WhatsApp requires whatsmeow (Go, WhatsApp Web multidevice protocol). This introduces the first Go dependency into a pure-Python codebase. The existing Dockerfile is Python 3.12-slim + Node.js 22; no Go toolchain exists yet.

Prior research in `docs/archive/whatsapp-draft.md` validated whatsmeow as the recommended approach. The codebase is partially pre-wired: `HISTORY_STRATEGY["whatsapp"] = "realtime"` and `"whatsapp"` in `_INTERACTIVE_ROUTE_CHANNELS` — but these use the bare key `"whatsapp"` while the connector will use channel `"whatsapp_user_client"`, creating a key mismatch that must be fixed. `SourceChannel` and `SourceProvider` in contracts.py do NOT yet include WhatsApp.

**Key constraint from user**: WhatsApp is **readonly-first**. Outbound sending tools must be technically wired but **functionally disabled** by default until safety (ban risk, ToS implications) is confirmed through production observation. This is the inverse of Telegram where send/reply were enabled from day one.

## Goals / Non-Goals

**Goals:**
- Passive readonly ingestion of the user's personal WhatsApp inbox (DMs, groups, channels) into the butler ecosystem via ingest.v1
- Follow the telegram_user_client pattern: standalone connector process, per-chat buffering, discretion filtering, checkpoint durability
- Go sidecar (whatsmeow) packaged in the same Docker image via multi-stage build, runnable in docker-compose
- Output tools (send/reply) technically implemented but gated behind a config flag that defaults to disabled
- QR code pairing ceremony for initial session setup, with dashboard UX at `/butlers/settings` (modeled after Google OAuth account linking)
- Session persistence to PostgreSQL for restart-safe operation

**Non-Goals:**
- WhatsApp Business API integration (requires dedicated business number, public webhook endpoint — incompatible with tailnet-first architecture)
- WhatsApp bot account support (no such thing exists for personal WhatsApp)
- Audio/voice message transcription on ingest (future enhancement)
- Media storage beyond metadata (CDN URLs expire; media download is a future phase)
- Group admin operations (create/modify groups, manage members)
- Status/story ingestion

## Decisions

### D1: Go sidecar compiled into Docker image via multi-stage build

The whatsmeow library is Go-only. Rather than introducing a separate container, the Go bridge binary is compiled in a builder stage and copied into the final Python image.

**Dockerfile changes:**
```dockerfile
# ── Stage 1: Build whatsapp-bridge Go binary ──────────────────
FROM golang:1.22-bookworm AS whatsapp-bridge-builder
WORKDIR /build
COPY whatsapp-bridge/ .
RUN go build -o /whatsapp-bridge ./cmd/bridge

# ── Stage 2: Final image (existing, extended) ──────────────────
FROM python:3.12-slim
# ... existing setup ...

# Copy whatsapp-bridge binary from builder (only when EXTRAS includes whatsapp)
ARG EXTRAS=""
COPY --from=whatsapp-bridge-builder /whatsapp-bridge /usr/local/bin/whatsapp-bridge
# ... rest of existing Dockerfile ...
```

The Go build stage adds ~2 minutes to cold builds but the layer is cached independently. The final binary is ~15-20 MB static.

**Alternative considered:** Separate sidecar container for the Go bridge.
**Rejected because:** Adds compose complexity (IPC between containers requires shared network + port coordination), harder to debug, and the binary is small enough to embed. The connector Python process manages the Go bridge as a subprocess — same pattern as the LLM CLI spawner.

### D2: Connector as standalone process (not in-daemon module)

Following `connector-base-spec`, the WhatsApp connector runs as a separate Docker service — identical to how `connector-telegram-bot` and `connector-telegram-user` are deployed.

```yaml
# docker-compose.dev.yml
connector-whatsapp-user:
  build:
    context: .
    dockerfile: Dockerfile
    args:
      EXTRAS: whatsapp
  entrypoint: ["/app/scripts/dev_entrypoint.sh", "connectors/whatsapp_user",
               "uv", "run", "python", "-m", "butlers.connectors.whatsapp_user_client"]
  environment:
    <<: *connector-env
    CONNECTOR_PROVIDER: whatsapp
    CONNECTOR_CHANNEL: whatsapp_user_client
  depends_on:
    log-init: { condition: service_completed_successfully }
    migrations: { condition: service_completed_successfully }
    switchboard: { condition: service_healthy }
```

The Python connector process:
1. Starts the Go bridge as a subprocess (`whatsapp-bridge --db-dsn ... --listen unix:///tmp/wa-bridge.sock`)
2. Connects to the bridge's local HTTP/Unix socket interface for event streaming
3. Normalizes events to ingest.v1 and submits to Switchboard via MCP

### D3: Module output tools gated by `send_tools` config flag (default: false)

Following the email module's existing pattern (`email.py` uses `send_tools: bool` to conditionally register send tools), the WhatsApp module uses a config-driven flag:

```toml
# roster/messenger/butler.toml — ONLY butler that sets send_tools = true
[modules.whatsapp]
send_tools = true     # Register send/reply tools (Messenger only)
send_enabled = false  # SAFETY: tools registered but refuse to execute until true

[modules.whatsapp.user]
enabled = true
session_env = "WHATSAPP_USER_SESSION"
```

**Two-layer gating:**
1. `send_tools` (registration-time): When `false`, send/reply tools are not registered at all. Non-Messenger butlers use this to mount WhatsApp in pure readonly mode. This follows the email module's existing `send_tools` pattern — no base-class change needed.
2. `send_enabled` (runtime): When `send_tools = true` but `send_enabled = false`, tools ARE registered (so LLMs see them) but calling them returns: `{"error": "WhatsApp sending is disabled. Set modules.whatsapp.send_enabled=true in butler.toml to enable. WARNING: Sending via unofficial WhatsApp clients carries ban risk."}`

**Owner-only auto-approve:** When `send_enabled = true`, outbound messages go through the existing approval gate in `gate.py`. The gate already resolves recipient → contact → owner role check → auto-approve for owner. Messages to the owner's self-chat ("Message Yourself") are auto-approved; messages to external parties require explicit approval. No new approval logic needed — this is the standard pattern used by Telegram and email.

**Why register disabled tools?**
- The LLM can explain to the user why WhatsApp sending isn't available
- Switching to enabled requires only a config change, not a code deploy

### D4: Credential resolution follows telegram_user_client pattern (owner entity_info only)

WhatsApp credentials are personal account material — they never go through `CredentialStore` or env vars.

Resolution path:
1. `resolve_owner_entity_info(pool, "whatsapp_phone")` → user's phone number (E.164)
2. Session keys are managed by the Go bridge and persisted to `whatsapp_sessions` table
3. No env var fallback for session material (too sensitive; DB-only)

The Go bridge handles session persistence internally via its PostgreSQL store. The Python connector only needs to know the phone number to identify the endpoint.

### D5: QR pairing via dashboard UX + bridge CLI fallback

**Primary UX: Dashboard settings page** at `/butlers/settings`, modeled after the Google OAuth account linking flow:

1. User navigates to Settings → WhatsApp section
2. Clicks "Link WhatsApp Account"
3. Dashboard API calls `POST /api/connectors/whatsapp/pair/start` → bridge generates QR data
4. Frontend renders QR code in a modal (refreshes automatically on expiry)
5. User scans with WhatsApp mobile app
6. Bridge detects successful pairing → stores session → API returns success
7. Dashboard shows connected status card with phone number, connection health badge, and last sync time

**Fallback: CLI pairing** for headless/SSH environments:
1. `whatsapp-bridge pair --db-dsn ...` prints QR code to terminal
2. User scans → session stored → bridge exits with code 0

**Session recovery** (invalidation by phone logout or ban):
- Dashboard shows "Session expired" state with "Re-pair" button
- Same QR flow re-initiates
- Session health polled periodically (bridge `/status` endpoint)

**Account management** (following Google OAuth pattern):
- `GET /api/connectors/whatsapp/status` — connection state, phone, last sync, session health
- `POST /api/connectors/whatsapp/pair/start` — initiate QR pairing, return QR data URI
- `GET /api/connectors/whatsapp/pair/poll` — poll for pairing completion (long-poll or SSE)
- `POST /api/connectors/whatsapp/disconnect` — graceful disconnect, mark session inactive

### D6: ingest.v1 field mapping

```
source.channel         = "whatsapp_user_client"
source.provider        = "whatsapp"
source.endpoint_identity = "whatsapp:<e164_phone>"
event.external_event_id  = "<whatsapp_message_id>"
event.external_thread_id = "<chat_jid>"
event.observed_at        = <message timestamp, RFC3339>
sender.identity          = "<sender_jid>"
payload.raw              = { full whatsmeow message JSON }
payload.normalized_text  = <extracted text or [media type] annotation>
control.idempotency_key  = "whatsapp:<endpoint>:<message_id>"
control.policy_tier      = "default"
```

### D7: Per-chat buffering and discretion (mirrors telegram_user_client)

- `ChatBuffer` dataclass accumulates messages per chat JID
- Time-based flush (configurable, default 600s) and size-based flush (buffer cap)
- `DiscretionEvaluator` applies per-chat familiarity scoring via contact weights
- Messages below discretion threshold are dropped before Switchboard submission
- Checkpoint persisted per flush cycle to `switchboard.connector_registry`

### D8: Go bridge IPC protocol

The Go bridge exposes a minimal HTTP API on a Unix socket:

```
GET  /events          — SSE stream of incoming WhatsApp messages (JSON-per-line)
POST /send            — Send a message (used by module output tools when enabled)
GET  /status          — Bridge health, session state, connected phone
POST /disconnect      — Graceful shutdown
```

The Python connector consumes `/events` via async SSE client. The Python module calls `/send` when output tools are enabled.

### D9: EXTRAS-gated Dockerfile integration

Following the `live-listener` pattern, WhatsApp support is opt-in via build arg. The Go builder stage always runs (Docker builds all stages regardless), but the binary is always copied into the final image — it's inert unless the connector service is actually started. Python extras like `qrcode` are gated by the EXTRAS arg.

```dockerfile
# Stage 1: Build Go binary (always runs, cached independently)
FROM golang:1.22-bookworm AS whatsapp-bridge-builder
WORKDIR /build
COPY whatsapp-bridge/ .
RUN CGO_ENABLED=0 go build -ldflags="-s -w" -o /whatsapp-bridge ./cmd/bridge

# Stage 2: final image
FROM python:3.12-slim
# ... existing setup ...

# Binary always present (~15 MB, inert unless connector started)
COPY --from=whatsapp-bridge-builder /whatsapp-bridge /usr/local/bin/whatsapp-bridge

# Python extras gated by EXTRAS arg
RUN if echo "$EXTRAS" | grep -q "whatsapp"; then \
      uv sync --no-dev --extra "whatsapp"; \
    fi
```

### D10: Bridge subprocess lifecycle manager (new pattern)

No existing connector manages a persistent external binary as a subprocess. Telethon is used as an imported Python library; the LLM CLI spawner handles ephemeral SDK calls. The WhatsApp connector introduces a genuinely new pattern: a long-running Go subprocess managed by asyncio.

A `BridgeSubprocessManager` utility handles:
- `asyncio.create_subprocess_exec()` for bridge startup
- stdout/stderr capture and logging
- Health polling via bridge `/status` endpoint
- Restart with jittered exponential backoff (initial 5s, max 300s) on crash/disconnect
- Graceful shutdown via `/disconnect` endpoint → SIGTERM fallback
- Exit code interpretation (0 = clean, 1 = pairing timeout, 2 = session invalidated)

This is shared between the connector (which manages the bridge for event streaming) and the module (which manages the bridge for send operations). Both use the same manager, but only one bridge process runs per container.

### D11: Dashboard WhatsApp settings section

A dedicated section on the settings page at `/butlers/settings`, modeled after Google OAuth account management:

**Frontend components** (React + Tailwind):
- `WhatsAppSetupCard` — connection status card with health badge (connected/disconnected/pair_required/expired)
- `WhatsAppPairModal` — QR code display with auto-refresh, pairing progress indicator
- `WhatsAppAccountInfo` — phone number, paired date, last sync time, session health

**State machine:** `not_configured → pairing → connected → disconnected/expired → pairing`

**API endpoints** (FastAPI router at `src/butlers/api/routers/whatsapp.py`):
- `GET /api/connectors/whatsapp/status` — current connection state, phone, health
- `POST /api/connectors/whatsapp/pair/start` — initiate QR generation, return QR data URI (base64 PNG)
- `GET /api/connectors/whatsapp/pair/poll` — SSE endpoint polling for pairing completion
- `POST /api/connectors/whatsapp/disconnect` — graceful disconnect
- `GET /api/connectors/whatsapp/health` — detailed session health for status badge

**Communication path:** Dashboard API → Go bridge `/status` and `/pair` endpoints (bridge needs a `/pair` endpoint for programmatic QR generation, not just CLI mode).

## Risks / Trade-offs

**[Ban risk]** WhatsApp unofficial protocol violates Meta ToS. Low-volume personal inbox reading on established accounts has historically low ban rates, but enforcement is increasing (2024-2025).
→ **Mitigation:** Readonly-first deployment. Send disabled by default. Monitor for "your account may be at risk" signals. Document graceful degradation path. Use established account with real SIM.

**[Protocol breaks]** Meta periodically updates the WhatsApp Web binary, breaking whatsmeow.
→ **Mitigation:** whatsmeow is actively maintained (same author as mautrix-whatsapp). Pin to a known-good version. Monitor upstream releases. Connector degrades gracefully on connection failure (reconnect with backoff).

**[Go toolchain in build]** First Go dependency in a Python-only codebase. Adds build complexity and CI time.
→ **Mitigation:** Go build is a separate cached Docker stage. Only adds ~2 min to cold builds. The Go binary is a single static file — no runtime Go dependency. If Go proves too heavy, WAHA GOWS is a fallback (HTTP wrapper around whatsmeow, eliminates build-time Go).

**[Session management]** QR re-pair requires physical access to WhatsApp mobile app. Session invalidation (phone logout, ban) causes connector downtime until re-paired.
→ **Mitigation:** Health endpoint reports session state. Alert via Telegram when WhatsApp session is unhealthy. Document re-pair procedure. Future: MCP tool for remote re-pair via QR data URI.

**[Media CDN expiry]** WhatsApp media URLs are time-limited. Without eager download, media references go stale.
→ **Mitigation:** v1 stores media metadata only (type, size, caption). Media download is a future phase. `normalized_text` includes `[image]`, `[video]` annotations so butlers have awareness without the binary.

**[Docker image size]** Go binary adds ~15-20 MB to the image.
→ **Mitigation:** Acceptable. The image already includes Node.js 22 + npm packages (~200 MB). The Go binary is comparatively small. Build with `-ldflags="-s -w"` to strip debug symbols.

## Open Questions

1. **Unix socket vs TCP for bridge IPC?** Unix socket is simpler (no port allocation) but doesn't work across containers. Since bridge runs as subprocess in same container, Unix socket is preferred. Confirm this doesn't conflict with any container security policy.

2. **Should the Go bridge binary be pre-built and vendored?** Pre-building for linux/amd64 and committing the binary to the repo would eliminate the Go build stage entirely. Tradeoff: binary in git (~15 MB) vs build-time Go dependency.

3. **Discretion evaluator shared or WhatsApp-specific?** Confirmed: `DiscretionEvaluator` and `DiscretionDispatcher` are already shared modules in `src/butlers/connectors/discretion.py` and `discretion_dispatcher.py`. WhatsApp should reuse them directly with `source_name="wa:{chat_jid}"` — no new evaluator code needed.

4. **Health port allocation?** Telegram bot uses 40081, live-listener uses 40091. WhatsApp user client needs a port. Suggest 40082 (next in connector sequence).

5. **Bridge `/pair` endpoint for dashboard UX?** The bridge needs a programmatic pairing endpoint (not just CLI mode) so the dashboard API can request QR data and poll for completion. The bridge should support both CLI (`pair` subcommand) and HTTP (`POST /pair/start`, `GET /pair/poll`) modes.
