# Connector Interface Contract

Status: Normative (Target State)
Last updated: 2026-02-16
Primary owner: Platform/Core

## 1. Purpose
This document defines the base contract for source connectors now that ingestion is API-first:
- Switchboard owns canonical ingestion and request-context assignment.
- Connectors are transport adapters only.
- Connectors submit events to Switchboard's ingestion API instead of running routing logic inside module daemons.

Source of truth:
- `docs/roles/switchboard_butler.md` (especially section 17)
- `docs/roles/base_butler.md` (`route.execute` / request-context lineage rules)

Scope note:
- This document defines connector expectations; Switchboard remains the authoritative owner of ingress semantics.

Project-specific connector profiles:
- `docs/connectors/telegram_bot.md`
- `docs/connectors/telegram_user_client.md`
- `docs/connectors/gmail.md`
- `docs/connectors/draft_discord.md` (DRAFT, v2-only WIP)

## 2. Connector Responsibilities
Connectors MUST:
- Read source events/messages from an external system (Telegram, email, webhook, etc.).
- Normalize source payloads into `ingest.v1`.
- Submit to the canonical Switchboard ingest API.
- Persist connector-local resume state (cursor/offset/high-water mark).
- Enforce source-side and ingest-side rate limiting.
- Send periodic heartbeats to the Switchboard (see section 13).

Connectors MUST NOT:
- Classify messages.
- Route directly to specialist butlers.
- Mint canonical `request_id` values.
- Bypass Switchboard ingestion with direct target-butler calls.

## 3. Canonical Ingest Envelope
Connectors submit `ingest.v1` payloads.

```json
{
  "schema_version": "ingest.v1",
  "source": {
    "channel": "telegram|slack|email|api|mcp",
    "provider": "telegram|slack|gmail|imap|internal",
    "endpoint_identity": "bot-or-mailbox-or-client-id"
  },
  "event": {
    "external_event_id": "provider-event-id",
    "external_thread_id": "thread-or-conversation-id-or-null",
    "observed_at": "RFC3339 timestamp"
  },
  "sender": {
    "identity": "provider-sender-identity"
  },
  "payload": {
    "raw": {},
    "normalized_text": "text used for routing"
  },
  "control": {
    "idempotency_key": "optional caller key",
    "trace_context": {},
    "policy_tier": "default|interactive|high_priority"
  }
}
```

Canonical channel-provider pairings:
- `source.channel=telegram` MUST use `source.provider=telegram`.
- `source.channel=slack` MUST use `source.provider=slack`.
- `source.channel=email` with Gmail connectors MUST use `source.provider=gmail`.
- `source.channel=email` with generic IMAP connectors MUST use `source.provider=imap`.
- `source.channel=api` MUST use `source.provider=internal`.
- `source.channel=mcp` MUST use `source.provider=internal`.

Request-context rule:
- Connector sends source/event/sender facts.
- Switchboard assigns canonical request context (`request_id`, `received_at`, etc.) at ingest acceptance.

Canonical request-context fields assigned by Switchboard:
- Required: `request_id`, `received_at`, `source_channel`, `source_endpoint_identity`, `source_sender_identity`
- Optional: `source_thread_identity`, `trace_context`

## 4. Talking to Switchboard
Target boundary:
- Connectors call Switchboard ingestion API (not in-daemon routing tools).

API semantics:
- Canonical endpoint path is an implementation detail of the Switchboard API surface; semantics are the contract.
- `202 Accepted` means ingest accepted for async processing.
- Response includes canonical request reference (request id).
- Duplicate submissions for the same dedupe identity return the same canonical request reference.

Transport:
- Connectors submit envelopes via MCP tool call (`ingest`) to the Switchboard MCP server.
- Transport is SSE-based MCP (fastmcp.Client), not HTTP POST.
- The MCP server endpoint is configured via `SWITCHBOARD_MCP_URL`.

Auth and exposure:
- MCP server is private by default (localhost SSE).
- Public exposure requires explicit network-level access control.

## 5. Data Source Modes
Supported source models:
- Push/webhook connectors: source pushes events to connector, connector forwards to Switchboard.
- Pull/poll connectors: connector periodically fetches new events, then forwards each to Switchboard.

Policy:
- Polling is allowed.
- Preferred deployment is external connector processes.
- In-process/co-located connectors are allowed, but they must call the same canonical ingest handler/API path as external connectors.
- Each newly observed source message/event is ingested as one canonical ingress record.

## 6. Idempotency and Deduplication
Connector requirements:
- Always send stable source identity fields (`channel`, `endpoint_identity`, `external_event_id` when available).
- Provide `control.idempotency_key` when source has no stable event id.
- Retries must be safe and reuse the same dedupe identity.

Switchboard dedupe authority:
- Deduplication decision is made at ingest boundary.
- Connector must treat duplicate acceptance as success, not as an error.

Canonical dedupe key guidance:
- Telegram: `update_id` + receiving bot identity.
- Email: RFC `Message-ID` + receiving mailbox identity.
- API/MCP: caller idempotency key when available; otherwise deterministic normalized-payload hash + source identity + bounded time window.

## 7. Safe Resuming
Connectors MUST be crash-safe and restart-safe.

Minimum behavior:
- Persist a resume cursor/checkpoint outside process memory.
- On restart, replay from last safe checkpoint.
- Replays must be harmless because ingest is idempotent.
- Use at-least-once delivery from connector to Switchboard; rely on ingest dedupe for exactly-once effect at canonical request layer.

Recommended checkpoint pattern:
1. Fetch batch from source.
2. Submit each event to ingest API.
3. Only advance checkpoint after successful ingest acceptance (or accepted duplicate).

## 8. Rate Limiting and Backpressure
Connectors MUST implement two independent controls:
- Source API limit handling (provider quotas, 429 handling, jittered backoff).
- Switchboard ingest protection (bounded in-flight requests, retry policy, overload handling).

Required behavior:
- Honor `Retry-After` when present.
- Use exponential backoff with jitter.
- Cap concurrent ingest submissions.
- Surface overload outcomes explicitly in logs/metrics (no silent drops).

## 9. Environment Variables (Base)
Reference connector runtime config (recommended naming):
- `SWITCHBOARD_MCP_URL` (required): SSE endpoint URL for Switchboard MCP server (e.g. `http://localhost:8100/sse`).
- `CONNECTOR_PROVIDER` (required): provider name (`telegram`, `gmail`, `imap`, etc.).
- `CONNECTOR_CHANNEL` (required): canonical channel value (`telegram`, `email`, etc.).
- `CONNECTOR_ENDPOINT_IDENTITY` (required): receiving identity (bot id, mailbox, client id).
- `CONNECTOR_CURSOR_PATH` (required for polling sources): durable checkpoint file/path.
- `CONNECTOR_POLL_INTERVAL_S` (required for polling sources): poll interval seconds.
- `CONNECTOR_MAX_INFLIGHT` (optional, default recommended: `8`): ingest concurrency cap.

Provider-specific credentials (examples):
- Telegram bot token env var(s).
- IMAP/SMTP or webhook secret env var(s).

Rule:
- Connector secrets must come from environment or secret manager, never committed config.

## 10. How to Run
Preferred model: connector processes run independently from the Switchboard daemon lifecycle.
Allowed model: in-process connectors, if they still use the same canonical ingest handler/API path.

Base run order:
1. Start Switchboard API service.
2. Start connector process(es) per source/provider.
3. Verify ingestion health using connector logs/metrics and Switchboard request lifecycle views.

Operational model:
- One process per connector type is preferred for isolation.
- Horizontal scale is allowed when dedupe identity is stable and checkpointing is coordination-safe.

## 11. Migration Notes (From In-Daemon Ingestion)
Legacy behavior exists today in some modules (for example internal polling loops). Target state is:
- Connector process owns source polling/webhook handling.
- Switchboard API owns canonical ingest/normalization/dedupe/context.
- Routing/fanout remains inside Switchboard pipeline after ingest acceptance.

During migration:
- Preserve existing dedupe identities so request lineage stays stable.
- Keep fallback paths fail-safe (no dropped accepted events).
- Use `docs/connectors/connector_ingestion_migration_delta_matrix.md` as the implementation cutover map (current-path mapping, ownership boundaries, and rollback checkpoints).

## 13. Heartbeat Protocol

Connectors MUST implement the heartbeat protocol to report liveness and operational statistics to the Switchboard.

Full specification: `docs/connectors/heartbeat.md`

Summary:
- Connectors send a `connector.heartbeat.v1` envelope every 2 minutes via MCP tool call (`connector.heartbeat`).
- Heartbeats carry self-reported health state (`healthy`, `degraded`, `error`), monotonic operational counters, and optional checkpoint state.
- Connectors self-register on first heartbeat — no manual pre-configuration required.
- Switchboard derives liveness from heartbeat recency: `online` (< 2 min), `stale` (2–4 min), `offline` (> 4 min).
- Heartbeat failures MUST NOT block or crash the ingestion loop.

Environment variables:
- `CONNECTOR_HEARTBEAT_INTERVAL_S` (optional, default: `120`): Heartbeat interval.
- `CONNECTOR_HEARTBEAT_ENABLED` (optional, default: `true`): Disable for development/testing.

## 14. Statistics and Dashboard Visibility

Connector ingestion statistics are aggregated by the Switchboard and exposed via the dashboard API.

Full specification: `docs/connectors/statistics.md`

Summary:
- Hourly and daily rollups of ingestion volume, error rates, and health metrics.
- Fanout distribution tracking: which butlers receive messages from which connectors.
- Dashboard `/connectors` page with connector cards, volume charts, fanout matrix, and error log.
- Rollup + prune retention: 7 days raw heartbeats, 30 days hourly stats, 1 year daily stats.

## 15. Authentication and Token Management

Connector authentication uses bearer tokens issued and managed by the Switchboard butler framework.

**Complete Token Lifecycle Documentation:**  
See `docs/switchboard/api_authentication.md` for comprehensive coverage of:
- Token generation and issuance procedures
- Secure distribution and storage requirements
- Rotation schedules and procedures (automated and emergency)
- Revocation processes and audit trails
- Token scope and permission model
- Security best practices and incident response

**Quick Reference for Connector Deployment:**

Required environment variable:
```bash
export SWITCHBOARD_API_TOKEN="sw_live_..."  # Obtain from platform team
```

Token scope must match connector's source identity:
- Token `channel` = envelope `source.channel`
- Token `provider` = envelope `source.provider`
- Token `endpoint_identity` = envelope `source.endpoint_identity`

Token security requirements:
- Store in secret manager (AWS Secrets Manager, GCP Secret Manager, Vault, K8s Secrets)
- Never commit to version control
- Rotate every 90 days (production) or 7 days (development)
- Revoke immediately if compromised

For detailed procedures, consult `docs/switchboard/api_authentication.md`.
