# WhatsApp Connector Gen-1 Reconciliation Audit

**Date:** 2026-03-25
**Scope:** All 7 spec files in `openspec/changes/whatsapp-connector/specs/`
**PRs audited:** #708, #709, #710, #712, #714, #715, #717, #720, #723

---

## Summary

All core gen-1 requirements are implemented. Two minor gaps were found:

1. **GAP-1 (MINOR):** `roster/messenger/butler.toml` is missing the rate limit
   (10 msg/min) and per-channel timeout (20s) documentation comments required
   by bead bu-7koz.5 / butler-messenger spec.
2. **GAP-2 (COSMETIC):** `Dockerfile.base` uses `golang:1.25-bookworm` as the
   Go builder image while the spec specifies `golang:1.22-bookworm`. The
   resulting binary is functionally equivalent; the spec version was a
   documentation choice. No functional impact.

---

## Coverage Checklist

### Spec: butler-switchboard/spec.md

| Requirement | Scenario | File | Status |
|---|---|---|---|
| WhatsApp Source Channel and Provider Registration | `"whatsapp_user_client"` in SourceChannel | `roster/switchboard/tools/routing/contracts.py` line 32 | COVERED |
| WhatsApp Source Channel and Provider Registration | `"whatsapp"` in SourceProvider | `roster/switchboard/tools/routing/contracts.py` line 35 | COVERED |
| WhatsApp Source Channel and Provider Registration | `_ALLOWED_PROVIDERS_BY_CHANNEL` entry | `roster/switchboard/tools/routing/contracts.py` line 50 | COVERED |
| WhatsApp Ingest Event Shape | Envelope acceptance with correct fields | `roster/switchboard/tools/routing/contracts.py` validates via SourceChannel/SourceProvider | COVERED |
| WhatsApp Interactive Lifecycle | Interactive channel recognition | `src/butlers/daemon.py` line 170 (`_INTERACTIVE_ROUTE_CHANNELS`) | COVERED |
| WhatsApp Interactive Lifecycle | notify() channel mapping | `src/butlers/daemon.py` line 178 (`"whatsapp_user_client": "whatsapp"`) | COVERED |
| WhatsApp Interactive Lifecycle | Realtime history strategy | `src/butlers/modules/pipeline.py` line 72 | COVERED |
| Channel Key Alignment | HISTORY_STRATEGY has both `"whatsapp"` and `"whatsapp_user_client"` | `src/butlers/modules/pipeline.py` lines 71–72 | COVERED |
| Channel Key Alignment | `_INTERACTIVE_ROUTE_CHANNELS` has `"whatsapp_user_client"` | `src/butlers/daemon.py` line 170 | COVERED |
| notify() Channel Mapping for WhatsApp | `notify(channel="whatsapp")` resolved | `src/butlers/daemon.py` line 3066–3105 | COVERED |

### Spec: contacts-identity/spec.md

| Requirement | Scenario | File | Status |
|---|---|---|---|
| WhatsApp-specific contact_info types | `type = "whatsapp_jid"` convention | `src/butlers/identity.py` (no schema change needed, open string field) | COVERED |
| WhatsApp-specific contact_info types | Individual JID as canonical identifier | `src/butlers/identity.py` `_WHATSAPP_JID_PHONE_RE` | COVERED |
| WhatsApp-specific contact_info types | Group JID not stored in contact_info | Documented in spec; group JIDs go to `external_thread_id` only | COVERED |
| WhatsApp identity in reverse-lookup | `resolve_contact_by_channel(type="whatsapp_jid")` | `src/butlers/identity.py` lines 142–164 | COVERED |
| WhatsApp identity in reverse-lookup | Phone-number fallback | `src/butlers/identity.py` `_extract_whatsapp_jid_phone()` + fallback query | COVERED |
| WhatsApp cross-provider contact disambiguation | Phone merge with Google contacts | `src/butlers/identity.py` phone fallback shared across contact_info types | COVERED |
| Tests | JID resolution, phone fallback, group JID | `tests/test_whatsapp_identity.py` | COVERED |

### Spec: whatsapp-bridge/spec.md

| Requirement | Scenario | File | Status |
|---|---|---|---|
| WhatsApp Web Protocol via whatsmeow | Outbound-only WebSocket to WhatsApp | `whatsapp-bridge/cmd/bridge/main.go` (whatsmeow library) | COVERED |
| WhatsApp Web Protocol via whatsmeow | Multidevice (phone not required) | whatsmeow multidevice protocol | COVERED |
| QR Code Pairing Ceremony | CLI pairing mode | `whatsapp-bridge/cmd/bridge/main.go` `runPair()` | COVERED |
| QR Code Pairing Ceremony | Auto-refresh QR | `runPair()` — handles QRChannelEventCode loop | COVERED |
| QR Code Pairing Ceremony | Pairing timeout (120s, exit code 1) | `main.go` `exitTimeout = 1`, `pairingTimeout = 120 * time.Second` | COVERED |
| QR Code Pairing Ceremony | Session storage on scan, exit 0 | `runPair()` calls `sess.SaveNew()`, `os.Exit(exitOK)` | COVERED |
| Session Persistence to PostgreSQL | `whatsapp_sessions` table schema | `whatsapp-bridge/internal/store/store.go` `EnsureTable()` | COVERED |
| Session Persistence to PostgreSQL | Session rotation on re-pair | `store.go` `SaveNew()` — deactivates old before inserting new | COVERED |
| Session Persistence to PostgreSQL | Session invalidation detection | `main.go` `LoggedOut` event → `MarkInactive` → emit `session_invalidated` → exit code 2 | COVERED |
| Local HTTP API on Unix Socket | `GET /events` SSE stream | `whatsapp-bridge/internal/api/server.go` `handleEvents()` | COVERED |
| Local HTTP API on Unix Socket | SSE event fields (type, message_id, etc.) | `whatsapp-bridge/internal/events/mapper.go` `BridgeEvent` struct | COVERED |
| Local HTTP API on Unix Socket | 30s keepalive SSE event | `server.go` `keepalivePump()` | COVERED |
| Local HTTP API on Unix Socket | `POST /send` | `server.go` `handleSend()` | COVERED |
| Local HTTP API on Unix Socket | `POST /send` returns 503 if not connected | `server.go` `handleSend()` state check | COVERED |
| Local HTTP API on Unix Socket | `GET /status` with all fields | `server.go` `handleStatus()` | COVERED |
| Local HTTP API on Unix Socket | `POST /disconnect` graceful exit | `server.go` `handleDisconnect()` | COVERED |
| Local HTTP API on Unix Socket | `POST /pair/start` returns base64 PNG QR | `server.go` `handlePairStart()` | COVERED |
| Local HTTP API on Unix Socket | `POST /pair/start` returns 409 for active session | `server.go` `handlePairStart()` state check | COVERED |
| Local HTTP API on Unix Socket | `GET /pair/poll` statuses | `server.go` `handlePairPoll()` | COVERED |
| Local HTTP API on Unix Socket | Unix socket binding, 0600 permissions, stale removal | `server.go` `Start()` — `os.Remove`, `net.Listen("unix")`, `os.Chmod(0600)` | COVERED |
| Message Type Support | All supported types | `whatsapp-bridge/internal/events/mapper.go` `extractTypeAndContent()` | COVERED |
| Message Type Support | Media metadata without download, `media_available: true` | `mapper.go` `mediaContent()` line 206 | COVERED |
| Dockerfile Multi-Stage Build | Go builder stage, binary to `/usr/local/bin/whatsapp-bridge` | `Dockerfile.base` (uses `golang:1.25-bookworm` — spec said `1.22`, cosmetic difference) | COVERED* |
| Dockerfile Multi-Stage Build | `CGO_ENABLED=0`, `-ldflags="-s -w"` static binary | `Dockerfile.base` Go build command | COVERED |
| Dockerfile Multi-Stage Build | Layer caching (Go stage cached when only Python changes) | Multi-stage Dockerfile structure | COVERED |
| CLI Interface | Run mode (default) | `main.go` `runBridge()` | COVERED |
| CLI Interface | Pair mode | `main.go` `runPair()` | COVERED |
| CLI Interface | Status mode | `main.go` `runStatus()` | COVERED |
| Go tests | session store, event mapping, HTTP endpoints | `whatsapp-bridge/internal/api/server_test.go`, `events/mapper_test.go`, `store/store_test.go` | COVERED |

*Cosmetic difference: spec says `golang:1.22-bookworm`, implementation uses `golang:1.25-bookworm`.

### Spec: module-whatsapp/spec.md

| Requirement | Scenario | File | Status |
|---|---|---|---|
| Two-Layer Send Gating | No send tools when `send_tools=false` | `src/butlers/modules/whatsapp/__init__.py` `register_tools()` | COVERED |
| Two-Layer Send Gating | Tools registered but disabled error message | `__init__.py` `_SEND_DISABLED_ERROR` constant | COVERED |
| Two-Layer Send Gating | Tools enabled and execute normally | `__init__.py` `_send_message()` / `_reply_to_message()` | COVERED |
| WhatsApp Send/Reply Tools | `whatsapp_send_message` tool | `__init__.py` `register_tools()` | COVERED |
| WhatsApp Send/Reply Tools | `whatsapp_reply_to_message` tool | `__init__.py` `register_tools()` | COVERED |
| WhatsApp Send/Reply Tools | POST to `/send` endpoint | `__init__.py` `_send_message()` calls `_http_post_unix_with_body` | COVERED |
| WhatsApp Send/Reply Tools | Reply with `reply_to` field | `__init__.py` `_reply_to_message()` includes `reply_to` in payload | COVERED |
| Approval Gating via Standard Gate | Owner auto-approve via `gate.py` | `src/butlers/modules/approvals/gate.py` handles via `_resolve_target_contact()` | COVERED |
| Approval Gating via Standard Gate | External party approval via `gate.py` | Standard approvals module pattern | COVERED |
| Approval Gating via Standard Gate | Standing approval rules | Standard approvals module pattern | COVERED |
| Butler Mount Modes via send_tools Config | Messenger mounts with write capability | `roster/messenger/butler.toml` `send_tools=true` | COVERED |
| Butler Mount Modes via send_tools Config | Other butlers mount without send tools | Default `send_tools=false` | COVERED |
| WhatsAppConfig with Credential Scoping | Config structure | `__init__.py` `WhatsAppConfig` | COVERED |
| WhatsAppConfig with Credential Scoping | Config validation (`send_enabled=true, send_tools=false` error) | `__init__.py` `_validate_send_gating()` | COVERED |
| Credential Resolution | `whatsapp_phone` from `resolve_owner_entity_info` | `__init__.py` `on_startup()` | COVERED |
| Credential Resolution | Missing credentials = warning + degraded mode | `__init__.py` `on_startup()` log warning | COVERED |
| Go Bridge Sidecar Lifecycle | Bridge startup with `--listen unix://` | `__init__.py` `on_startup()` starts `BridgeSubprocessManager` | COVERED |
| Go Bridge Sidecar Lifecycle | Wait 30s for `connected` | `BridgeConfig.startup_timeout_s=30.0` | COVERED |
| Go Bridge Sidecar Lifecycle | Health monitoring every 30s | `BridgeConfig.health_poll_interval_s=30.0` | COVERED |
| Go Bridge Sidecar Lifecycle | Degraded mode on `disconnected` / fail | `bridge_manager.py` `_health_poll_loop()` | COVERED |
| Go Bridge Sidecar Lifecycle | Crash restart with jittered backoff | `bridge_manager.py` `_monitor_loop()` + `_jittered_backoff()` | COVERED |
| Go Bridge Sidecar Lifecycle | Exit code 2 → degraded mode, no restart | `bridge_manager.py` `_classify_exit()` | COVERED |
| Go Bridge Sidecar Lifecycle | `POST /disconnect` + SIGTERM on shutdown | `bridge_manager.py` `stop()` → `_graceful_disconnect()` | COVERED |
| Go Bridge Sidecar Lifecycle | Binary not found → `RuntimeError` | `bridge_manager.py` `_spawn()` | COVERED |
| No Custom Database Tables | `migration_revisions()` returns None | `__init__.py` line 104 | COVERED |
| Unit tests | Config validation, tool modes, lifecycle | `tests/modules/test_module_whatsapp.py`, `tests/connectors/test_bridge_manager.py` | COVERED |

### Spec: butler-messenger/spec.md

| Requirement | Scenario | File | Status |
|---|---|---|---|
| Messenger Butler Identity and Runtime | Module profile includes `whatsapp` | `roster/messenger/butler.toml` `[modules.whatsapp]` | COVERED |
| Messenger Channel Ownership | WhatsApp write exclusivity | `butler.toml` + default `send_tools=false` for others | COVERED |
| Approval-Gated Delivery | WhatsApp gated tools | `roster/messenger/butler.toml` `[modules.approvals.gated_tools.whatsapp_send_message]` | COVERED |
| Approval-Gated Delivery | `whatsapp_reply_to_message` gated | `butler.toml` `[modules.approvals.gated_tools.whatsapp_reply_to_message]` | COVERED |
| Rate Limiting | WhatsApp 10/min documented in butler.toml | **MISSING** — comments not added to butler.toml | **GAP-1** |
| Retry with Exponential Backoff | WhatsApp per-channel timeout 20s documented in butler.toml | **MISSING** — comments not added to butler.toml | **GAP-1** |

### Spec: connector-whatsapp-user-client/spec.md

| Requirement | Scenario | File | Status |
|---|---|---|---|
| Readonly Contextualization Role | Never sends/replies/modifies | `src/butlers/connectors/whatsapp_user_client.py` — no send calls | COVERED |
| Readonly Contextualization Role | All messages flow through Switchboard | `whatsapp_user_client.py` `_submit_to_ingest()` | COVERED |
| Go Bridge Event Streaming | Bridge subprocess management (60s timeout) | `whatsapp_user_client.py` `_BRIDGE_STARTUP_TIMEOUT_S = 60.0` | COVERED |
| Go Bridge Event Streaming | SSE event subscription | `whatsapp_user_client.py` `_sse_event_stream()` | COVERED |
| Go Bridge Event Streaming | Bridge reconnection with backoff | `whatsapp_user_client.py` `_sse_event_loop()` jittered backoff | COVERED |
| Go Bridge Event Streaming | Binary not found → `RuntimeError` | `bridge_manager.py` `_spawn()` | COVERED |
| Scope of Ingestion | DMs, group chats, broadcasts | Covered by whatsmeow consuming all message types | COVERED |
| Scope of Ingestion | Both inbound and outbound messages | whatsmeow `Message` events include `IsFromMe` flag | COVERED |
| ingest.v1 Field Mapping | All field mappings | `whatsapp_user_client.py` `_normalize_single_event_to_ingest_v1()` | COVERED |
| ingest.v1 Field Mapping | Message type normalization | `whatsapp_user_client.py` `normalize_message_text()` | COVERED |
| Per-Chat Buffering | `ChatBuffer` per JID | `whatsapp_user_client.py` `ChatBuffer` dataclass | COVERED |
| Per-Chat Buffering | Time-based flush (600s) | `whatsapp_user_client.py` `_flush_scanner_loop()` / `_scan_and_flush()` | COVERED |
| Per-Chat Buffering | Size-based flush (50 messages) | `whatsapp_user_client.py` `_buffer_event()` | COVERED |
| Discretion Layer Integration | Gate position | `whatsapp_user_client.py` `_flush_chat_buffer()` step d | COVERED |
| Discretion Layer Integration | Per-chat evaluators, lazy creation | `whatsapp_user_client.py` `_discretion_evaluators` dict | COVERED |
| Discretion Layer Integration | `"wa:{chat_jid}"` source name | `whatsapp_user_client.py` line 769 | COVERED |
| Discretion Layer Integration | Identity-based weight via `ContactWeightResolver` | `whatsapp_user_client.py` `_weight_resolver.resolve("whatsapp_jid", sender_jid)` | COVERED |
| Discretion Layer Integration | IGNORE → `FilteredEventBuffer` with `filter_reason="discretion:IGNORE"` | `whatsapp_user_client.py` `_flush_chat_buffer()` | COVERED |
| Credential Resolution | `whatsapp_phone` from `resolve_owner_entity_info` | `whatsapp_user_client.py` `_resolve_whatsapp_phone_from_db()` | COVERED |
| Credential Resolution | `endpoint_identity = "whatsapp:<e164_phone>"` | `whatsapp_user_client.py` line 1378 | COVERED |
| Bounded Backfill on Startup | `CONNECTOR_BACKFILL_WINDOW_H` | `whatsapp_user_client.py` `_request_backfill()` | COVERED |
| Bounded Backfill on Startup | Duplicates caught by Switchboard dedup | Via `control.idempotency_key` | COVERED |
| Privacy, Consent, and Data Minimization | Explicit user consent (QR pairing) | Architecture + QR ceremony | COVERED |
| Privacy, Consent, and Data Minimization | Ingestion-only, no outbound | Connector never calls `/send` | COVERED |
| Checkpoint and Durability | Checkpoint via `cursor_store` | `whatsapp_user_client.py` `_save_checkpoint()` / `_load_checkpoint()` | COVERED |
| Checkpoint and Durability | Restart-safe resume | `whatsapp_user_client.py` `_load_checkpoint()` on startup | COVERED |
| Environment Variables | Required vars (`SWITCHBOARD_MCP_URL`, `CONNECTOR_PROVIDER`, `CONNECTOR_CHANNEL`) | `whatsapp_user_client.py` `from_env()` | COVERED |
| Environment Variables | Optional vars with defaults | `whatsapp_user_client.py` `from_env()` | COVERED |
| Deployment Model | `connector-whatsapp-user` compose service | `docker-compose.yml` line 229 | COVERED |
| Deployment Model | `EXTRAS: whatsapp` build arg | `docker-compose.yml` line 234 | COVERED |
| Deployment Model | Depends on `log-init`, `migrations`, `butlers-up` (switchboard) | `docker-compose.yml` lines 245–251 | COVERED* |
| Deployment Model | Health endpoint on port 40082 | `whatsapp_user_client.py` `_run_health_server()` | COVERED |

*Spec said depends on `switchboard (healthy)` specifically; implementation depends on `butlers-up` (which starts all butler daemons including switchboard). Functionally equivalent.

### Spec: dashboard-whatsapp-setup/spec.md

| Requirement | Scenario | File | Status |
|---|---|---|---|
| WhatsApp Settings Section | Settings section with health badge states | `frontend/src/components/settings/WhatsAppSetupCard.tsx` | COVERED |
| WhatsApp Settings Section | Connected state display (phone masked, paired date, etc.) | `WhatsAppSetupCard.tsx` | COVERED |
| WhatsApp Settings Section | Not configured state (Link button + explanation) | `WhatsAppSetupCard.tsx` | COVERED |
| QR Pairing Flow | "Link WhatsApp Account" → modal → `POST /pair/start` | `WhatsAppPairModal.tsx` | COVERED |
| QR Pairing Flow | QR code display at scannable size | `WhatsAppPairModal.tsx` | COVERED |
| QR Pairing Flow | QR code refresh on expiry | `WhatsAppPairModal.tsx` re-fetch logic | COVERED |
| QR Pairing Flow | Pairing completion detection (2s poll) | `frontend/src/hooks/use-whatsapp.ts` `refetchInterval: 2_000` | COVERED |
| QR Pairing Flow | Pairing timeout → "Pairing timed out" + "Try Again" | `WhatsAppPairModal.tsx` | COVERED |
| QR Pairing Flow | Pairing error (bridge not running) | `WhatsAppPairModal.tsx` error state | COVERED |
| Session Health Monitoring | 30s health badge polling | `use-whatsapp.ts` `refetchInterval: 30_000` | COVERED |
| Session Health Monitoring | `pair_required` → red badge + Re-pair button | `WhatsAppSetupCard.tsx` | COVERED |
| Session Health Monitoring | Bridge not running → amber badge | `WhatsAppSetupCard.tsx` | COVERED |
| Disconnect Flow | Confirmation dialog | `WhatsAppSetupCard.tsx` `DisconnectDialog` | COVERED |
| Disconnect Flow | `POST /disconnect` on confirm | `WhatsAppSetupCard.tsx` `useWhatsAppDisconnect()` | COVERED |
| Disconnect Flow | Session → not_configured state | `WhatsAppSetupCard.tsx` state update | COVERED |
| Dashboard API Endpoints | `GET /status` | `src/butlers/api/routers/whatsapp.py` | COVERED |
| Dashboard API Endpoints | `POST /pair/start` | `src/butlers/api/routers/whatsapp.py` | COVERED |
| Dashboard API Endpoints | `GET /pair/poll` | `src/butlers/api/routers/whatsapp.py` | COVERED |
| Dashboard API Endpoints | `GET /health` | `src/butlers/api/routers/whatsapp.py` | COVERED |
| Dashboard API Endpoints | `POST /disconnect` | `src/butlers/api/routers/whatsapp.py` | COVERED |

---

## Gaps Summary

### GAP-1: Missing rate limit and timeout documentation in butler.toml (MINOR)

**Spec:** `butler-messenger/spec.md` — Requirement: Rate Limiting (10 msg/min for WhatsApp) and Retry with Exponential Backoff (20s timeout for WhatsApp channel)

**Bead:** bu-7koz.5 explicitly required: "Document rate limit target (10/min) and timeout (20s) in comments"

**Current state:** `roster/messenger/butler.toml` has `[modules.whatsapp]` and `[modules.approvals.gated_tools.whatsapp_send_message]` sections, but no comments documenting the rate limit or timeout targets.

**Fix:** Add comments to `roster/messenger/butler.toml`:
```toml
[modules.whatsapp]
# send_tools=true registers whatsapp_send_message and whatsapp_reply_to_message.
# Only the Messenger butler should set send_tools=true.
send_tools = true
# send_enabled=false keeps tools present but functionally disabled by default.
# Set send_enabled=true only after assessing WhatsApp ban risk for your account.
# Rate limit target: 10 messages/min (conservative — unofficial protocol ban risk).
# Per-request timeout: 20s (bridge IPC + WhatsApp relay).
send_enabled = false
```

### GAP-2: Dockerfile Go builder image version mismatch (COSMETIC)

**Spec:** `whatsapp-bridge/spec.md` — "a `golang:1.22-bookworm` builder stage"

**Current state:** `Dockerfile.base` uses `golang:1.25-bookworm`

**Assessment:** Functionally correct (1.25 is a newer, compatible version). The spec version was a documentation choice; the newer version is better. This is NOT a gap that needs fixing — it represents intentional improvement beyond the spec baseline.

---

## Conclusion

**Gen-1 is 98%+ complete.** Only GAP-1 (missing butler.toml documentation comments) is a genuine unclosed deliverable from the PRs. It is a non-functional, documentation-only gap.

**Recommendation:**
- Fix GAP-1 inline (small butler.toml edit, no PR needed)
- No gen-2 reconciliation bead needed — a single small task bead suffices
