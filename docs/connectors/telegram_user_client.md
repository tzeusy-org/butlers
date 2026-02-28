# Telegram User Client Connector

Status: Draft (v2-only feature, implementation complete)
Depends on: `docs/connectors/interface.md`
Implementation: `src/butlers/connectors/telegram_user_client.py`
Deployment guide: `docs/connectors/telegram_user_client_deployment.md`

## 1. Purpose
This connector runs a Telegram **user client** (not a bot) and continuously ingests message activity visible to the user's Telegram account into the butler ecosystem.

Primary goal:
- Keep butler context current with life events, commitments, relationships, and facts that appear in Telegram, without requiring explicit manual upload.

Control-plane rule:
- The connector is transport-only and ingestion-only.
- It does not classify, route, or assign canonical request ids.
- Switchboard remains the authority for canonical request context and downstream routing.

## 2. Scope of Ingestion
The user client may ingest messages from:
- Direct messages
- Group chats and supergroups
- Channels and threaded discussions visible to the account
- Edits/deletes and metadata updates where relevant

Recommended ingestion policy:
- Ingest new inbound and outbound user-visible messages live.
- Support limited historical backfill on first startup or after downtime.
- Keep raw payload + normalized text for downstream extraction/classification.

## 3. Request Context Mapping (Interface Alignment)
Use `ingest.v1` from `docs/connectors/interface.md`.

Telegram user-client mapping:
- `source.channel`: `telegram`
- `source.provider`: `telegram`
- `source.endpoint_identity`: stable user-client identity (for example `telegram:user:<account_id>`)
- `event.external_event_id`: Telegram update/message event id (stable per provider event)
- `event.external_thread_id`: Telegram dialog/thread identity (chat id / thread id)
- `event.observed_at`: connector-observed timestamp (RFC3339)
- `sender.identity`: Telegram sender id for the message author
- `payload.raw`: full provider event payload
- `payload.normalized_text`: extracted plain text used for downstream routing/extraction
- `control.idempotency_key`: optional fallback key when event id is unavailable

Switchboard assigns canonical request context on ingest acceptance:
- Required: `request_id`, `received_at`, `source_channel`, `source_endpoint_identity`, `source_sender_identity`
- Optional: `source_thread_identity`, `trace_context`

## 4. Environment Variables
Base connector variables (shared contract):
- `SWITCHBOARD_MCP_URL` (required; SSE endpoint for Switchboard MCP server)
- `CONNECTOR_PROVIDER=telegram` (required)
- `CONNECTOR_CHANNEL=telegram` (required)
- `CONNECTOR_ENDPOINT_IDENTITY` (required, user-client identity)
- `CONNECTOR_MAX_INFLIGHT` (optional, recommended default `8`)

State/checkpoint variables:
- `CONNECTOR_CURSOR_PATH` (required for restart-safe checkpointing)
- `CONNECTOR_BACKFILL_WINDOW_H` (optional, bounded startup replay window)

Telegram user-client credentials (MTProto):
- `telegram_api_id` — configured via owner contact_info (type `telegram_api_id`)
- `telegram_api_hash` — configured via owner contact_info (type `telegram_api_hash`)
- `telegram_user_session` — configured via owner contact_info (type `telegram_user_session`)

Security requirements:
- Never commit Telegram credentials or session artifacts.
- Store session material in secret manager or encrypted local secret storage.
- Rotate/revoke sessions promptly after credential exposure.

## 5. Live Ingestion Mechanism
This connector is **live-stream first** (not periodic bot polling):
- Maintain a persistent Telegram user-client session.
- Subscribe to account updates/events in near real time.
- Normalize each event and submit to Switchboard ingest immediately.

Fallback behavior:
- On disconnect/restart, replay from last durable checkpoint.
- Optionally run bounded backfill to close short gaps.
- Preserve idempotency keys so replay is safe.

## 6. Runtime and Deployment
Recommended runtime:
- Dedicated daemon process per user account.
- Independent lifecycle from Switchboard process (still calling canonical ingest API).

Run order:
1. Start Switchboard API.
2. Start Telegram user-client connector daemon.
3. Verify accepted ingest events and connector lag metrics.

Operational controls:
- Bounded ingest concurrency (`CONNECTOR_MAX_INFLIGHT`).
- Provider reconnect with jittered backoff.
- Explicit degraded mode when Telegram/API or Switchboard ingest is unavailable.

## 7. Idempotency, Ordering, and Resume
Idempotency:
- Use stable Telegram event/message identity + `CONNECTOR_ENDPOINT_IDENTITY`.
- Duplicate accepted ingest responses are success, not failures.

Ordering:
- Preserve per-dialog ordering where practical.
- Cross-dialog global ordering is not guaranteed.

Resume safety:
- Persist high-water mark/checkpoint outside process memory.
- Advance checkpoint only after ingest acceptance (or accepted duplicate).

## 8. Privacy, Consent, and Data Minimization
Because this connector ingests personal account traffic, apply strict safeguards:
- Explicit user consent before enabling account-wide ingestion.
- Clear scope disclosure (which chats/types are included or excluded).
- Optional allow/deny lists for chats and senders.
- Optional redaction for sensitive content classes before ingest.
- Retention limits aligned with memory/ingest policy.
- Full audit trail of connector start/stop/config changes.

## 9. Interactivity Considerations
This connector is primarily ingress-focused. Outbound messaging/reply actions should remain on messenger delivery surfaces (for example bot/user send/reply tools), not through ingestion workers.

However, ingestion should carry enough context for reply-capable flows:
- Source thread/dialog identity
- Source sender identity
- Canonical request context assigned by Switchboard

## 10. Non-Goals
This connector does not:
- Replace canonical Switchboard ingestion semantics.
- Bypass dedupe/request-context assignment at ingress.
- Perform direct specialist-butler routing.
- Require manual export/upload as the primary data path.

## 11. Implementation Status

### Completed Features (2026-02-15)
- ✅ Live user-client session via Telethon (MTProto)
- ✅ Real-time message event subscription
- ✅ Normalization to `ingest.v1` format
- ✅ Idempotent submission to Switchboard ingest API
- ✅ Durable checkpoint with restart-safe replay
- ✅ Bounded in-flight concurrency control
- ✅ Optional bounded backfill on startup
- ✅ Graceful degradation when Telethon not installed
- ✅ Comprehensive test coverage

### Remaining v2-Only Gaps
The following features are documented but not yet implemented:

1. **Privacy/Consent Guardrails (Section 8)**
   - Explicit consent flow before enabling
   - Chat/sender allow/deny lists
   - Content redaction for sensitive patterns
   - Audit trail of connector lifecycle events

2. **Feature Gating**
   - Explicit feature flag enforcement
   - Configuration validation before startup

3. **Scope Controls**
   - Per-chat filtering (allowlist/denylist)
   - Per-sender filtering
   - Message type filtering (e.g., exclude media, only text)

4. **Advanced Monitoring**
   - Structured metrics export
   - Health check endpoint
   - Lag monitoring and alerting

These gaps are marked as v2-only and should be implemented before production deployment with real user accounts. See `docs/connectors/telegram_user_client_deployment.md` for deployment guidance and interim safeguards.
