# Discord User Connector (Draft)

Status: **DRAFT** (v2-only WIP, not production-ready)  
Depends on: `docs/connectors/interface.md`

## 1. Purpose
This draft connector describes wiring Discord activity into the butler ecosystem through a user's Discord account context so butlers can stay current on life events, commitments, and evolving personal knowledge without explicit manual upload.

Control-plane contract:
- Connector is ingestion-only transport.
- Switchboard remains the canonical owner of ingest acceptance, request-context assignment, deduplication, and routing.
- No direct specialist-butler routing from the connector.

## 2. Scope (V2 WIP)
Target v2 scope:
- Live ingestion of user-visible Discord messages and relevant edits/deletes.
- Support DM and server contexts that the linked user/account context is authorized to expose.
- Optional bounded historical backfill for startup recovery.

Out of scope in this draft:
- Full implementation details for production auth/compliance posture.
- Finalized retention/redaction policy defaults.
- Outbound Discord delivery behavior (this doc is ingress-focused).

## 3. Request Context Mapping (Interface Alignment)
Use `ingest.v1` from `docs/connectors/interface.md`.

Discord mapping (target):
- `source.channel`: `discord`
- `source.provider`: `discord`
- `source.endpoint_identity`: stable user connector identity, for example `discord:user:<user_id>`
- `event.external_event_id`: Discord event/message id (Snowflake)
- `event.external_thread_id`: channel/thread/conversation id
- `event.observed_at`: connector-observed timestamp (RFC3339)
- `sender.identity`: Discord author id
- `payload.raw`: full Discord payload
- `payload.normalized_text`: extracted message text for downstream processing
- `control.idempotency_key`: optional fallback when stable event id is unavailable

Switchboard assigns canonical request context:
- Required: `request_id`, `received_at`, `source_channel`, `source_endpoint_identity`, `source_sender_identity`
- Optional: `source_thread_identity`, `trace_context`

## 4. Environment Variables (Draft)
Base connector variables:
- `SWITCHBOARD_API_BASE_URL` (required)
- `SWITCHBOARD_API_TOKEN` (required when auth is enabled)
- `CONNECTOR_PROVIDER=discord` (required)
- `CONNECTOR_CHANNEL=discord` (required)
- `CONNECTOR_ENDPOINT_IDENTITY` (required)
- `CONNECTOR_MAX_INFLIGHT` (optional, default `8`)
- `CONNECTOR_CURSOR_PATH` (required for restart-safe checkpointing)

Discord auth/config variables (candidate set, v2 WIP):
- `DISCORD_CLIENT_ID`
- `DISCORD_CLIENT_SECRET`
- `DISCORD_REDIRECT_URI`
- `DISCORD_REFRESH_TOKEN` (or equivalent secret-manager reference)
- `DISCORD_GUILD_ALLOWLIST` (optional)
- `DISCORD_CHANNEL_ALLOWLIST` (optional)

Secret handling:
- Credentials/tokens must come from env or secret manager only.
- No committed secrets in repo config.

## 5. Live Ingestion Model (Draft)
Target behavior:
- Maintain a live Discord event stream (gateway/streaming ingestion model).
- Normalize each event into `ingest.v1`.
- Submit immediately to Switchboard ingest API.

Resilience behavior:
- Durable checkpoint/high-water mark.
- On restart, replay from checkpoint and optionally run bounded backfill.
- Replays must be idempotent.

## 6. Idempotency and Ordering
Idempotency key guidance:
- Primary key: Discord message/event id + `CONNECTOR_ENDPOINT_IDENTITY`.
- Treat accepted duplicates as success.

Ordering guidance:
- Preserve per-channel/thread ordering when practical.
- Cross-channel global ordering is not guaranteed.

## 7. Privacy and Safety Guardrails (Draft)
Because this is user-account-context ingestion, v2 must include:
- Explicit user consent for account linking and ingestion scope.
- Clear include/exclude controls (guild/channel/thread allow/deny lists).
- Optional sensitive-content redaction before ingest.
- Auditable connector lifecycle and config-change logs.
- Retention controls aligned with platform memory policy.

## 8. Compliance Notes (V2 Decision Pending)
This document is intentionally draft because Discord user-account ingestion has policy/compliance implications.

Before productionization, finalize:
- Allowed auth pattern and platform ToS alignment.
- Approved scopes and least-privilege defaults.
- User revocation and connector shutdown behavior.

## 9. Non-Goals
This draft does not define:
- A bypass of Switchboard canonical ingest/request-context assignment.
- Direct routing from connector to specialist butlers.
- Production-ready compliance/legal sign-off.
