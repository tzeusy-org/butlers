# Gmail Connector

Status: Draft (project-specific connector profile)  
Depends on: `docs/connectors/interface.md`

## 1. Purpose
This connector ingests new Gmail emails for a user in near real time and forwards them to the butler ecosystem through canonical Switchboard ingest.

Primary goal:
- Keep butler context current with inbox-driven life events, tasks, and facts without manual forwarding/upload.

Control-plane rule:
- Connector is transport-only and ingestion-only.
- It must not classify or route directly to specialist butlers.
- Switchboard owns canonical request-context assignment and dedupe decisions.

## 2. Live Ingestion Model
Primary live path:
- Use Gmail push notifications (`users.watch`) with history-based delta fetch (`users.history.list`) to ingest newly arrived mail.

Recommended flow:
1. Create/refresh Gmail watch subscription.
2. Receive notification for mailbox changes.
3. Fetch changed message ids from Gmail history API.
4. Fetch message payload/metadata for each new email.
5. Normalize and submit each event to Switchboard ingest.

Fallback:
- If push is unavailable or stale, run bounded catch-up polling using last durable `historyId`.

## 3. Request Context Mapping (Interface Alignment)
Use `ingest.v1` from `docs/connectors/interface.md`.

Gmail mapping:
- `source.channel`: `email`
- `source.provider`: `gmail`
- `source.endpoint_identity`: mailbox identity, for example `gmail:user:alice@gmail.com`
- `event.external_event_id`: Gmail message id (or history event id when message id is absent)
- `event.external_thread_id`: Gmail `threadId`
- `event.observed_at`: connector-observed timestamp (RFC3339)
- `sender.identity`: normalized sender address from message headers (`From`)
- `payload.raw`: full Gmail API payload (or selected safe subset, policy-dependent)
- `payload.normalized_text`: normalized subject/body text for downstream processing
- `control.idempotency_key`: optional fallback key, e.g. `gmail:<endpoint_identity>:<message_id>`

Switchboard assigns canonical request context:
- Required: `request_id`, `received_at`, `source_channel`, `source_endpoint_identity`, `source_sender_identity`
- Optional: `source_thread_identity`, `trace_context`

## 4. Environment Variables
Base connector variables:
- `SWITCHBOARD_API_BASE_URL` (required)
- `SWITCHBOARD_API_TOKEN` (required when auth is enabled)
- `CONNECTOR_PROVIDER=gmail` (required)
- `CONNECTOR_CHANNEL=email` (required)
- `CONNECTOR_ENDPOINT_IDENTITY` (required)
- `CONNECTOR_MAX_INFLIGHT` (optional, recommended default `8`)
- `CONNECTOR_CURSOR_PATH` (required; stores last processed Gmail `historyId`)

Gmail API auth variables (OAuth-based):
- `GMAIL_CLIENT_ID` (required)
- `GMAIL_CLIENT_SECRET` (required)
- `GMAIL_REFRESH_TOKEN` (required)
- `GMAIL_REDIRECT_URI` (required for token lifecycle in some setups)

Optional runtime controls:
- `GMAIL_LABEL_INCLUDE` (comma-separated label filter)
- `GMAIL_LABEL_EXCLUDE` (comma-separated label filter)
- `GMAIL_WATCH_TOPIC` (Pub/Sub topic/resource for notifications)
- `GMAIL_WATCH_RENEW_INTERVAL_S` (watch renewal cadence)

Security requirements:
- Never commit OAuth secrets/tokens.
- Use env or secret manager only.
- Treat refresh tokens as high-sensitivity credentials.

## 5. Multiple Connectors Concurrently
Multiple Gmail connectors can run concurrently and are expected to be isolated per mailbox and/or policy slice.

Concurrency model:
- Each running connector instance MUST have a unique `CONNECTOR_ENDPOINT_IDENTITY`.
- Each instance MUST use its own checkpoint file/path (`CONNECTOR_CURSOR_PATH`).
- Each instance MAY use different label filters (for example one connector for `INBOX`, another for `finance` labels).

Required uniqueness boundary:
- `(CONNECTOR_PROVIDER, CONNECTOR_CHANNEL, CONNECTOR_ENDPOINT_IDENTITY, external_event_id)`

Operational guidance:
- Run one daemon process per Gmail account by default.
- Horizontal replicas for the same endpoint identity require explicit coordination/lease ownership for the cursor.
- Duplicate accepted ingest responses are success, not failures.

## 6. Idempotency, Resume, and Ordering
Idempotency:
- Use stable Gmail message id + endpoint identity as primary dedupe identity.
- Retries must reuse the same identity fields.

Resume:
- Persist last safe `historyId` outside process memory.
- Advance checkpoint only after ingest acceptance (or accepted duplicate).

Ordering:
- Preserve per-thread ordering when practical.
- Cross-thread global ordering is not guaranteed.

## 7. Privacy and Data Handling
Because this connector processes personal email, enforce:
- Explicit user consent for mailbox scope.
- Clear include/exclude rules (labels, senders, domains).
- Optional redaction/minimization before ingest.
- Audit logging for connector lifecycle/config changes.
- Retention aligned with platform memory and ingestion policy.

## 8. Non-Goals
This connector does not:
- Bypass Switchboard canonical ingest semantics.
- Perform direct specialist-butler routing.
- Replace outbound email delivery tooling (ingress-focused only).
