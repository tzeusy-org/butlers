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

### 2.1 Pub/Sub Push Mode (Recommended for Production)
When enabled via `GMAIL_PUBSUB_ENABLED=true`, the connector uses Gmail push notifications with Pub/Sub for near real-time ingestion:

1. Start Gmail watch subscription via `users.watch` API pointing to configured Pub/Sub topic
2. Run HTTP webhook server to receive Pub/Sub push notifications
3. On notification, immediately fetch history changes via `users.history.list`
4. Fetch message payload/metadata for each new email
5. Normalize and submit each event to Switchboard ingest
6. Auto-renew watch subscription before expiration (default 1 day)

Fallback polling:
- Even in Pub/Sub mode, connector runs periodic polling (every 5 minutes minimum) as a safety net
- Ensures no messages are missed if notifications are delayed or dropped

### 2.2 Polling Mode (Default for v1)
When Pub/Sub is disabled (default), connector uses polling-based history fetch:

1. Poll Gmail history API at configured interval (default 60s)
2. Fetch changed message ids from Gmail history API
3. Fetch message payload/metadata for each new email
4. Normalize and submit each event to Switchboard ingest

Trade-offs:
- Simpler setup (no Pub/Sub topic or webhook endpoint required)
- Higher latency (~60s vs near real-time)
- Sufficient for most v1 use cases

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

Provider contract note:
- Gmail ingestion MUST use `source.provider=gmail` (not `imap`).

Switchboard assigns canonical request context:
- Required: `request_id`, `received_at`, `source_channel`, `source_endpoint_identity`, `source_sender_identity`
- Optional: `source_thread_identity`, `trace_context`

## 4. Environment Variables
Base connector variables:
- `SWITCHBOARD_MCP_URL` (required; SSE endpoint for Switchboard MCP server)
- `CONNECTOR_PROVIDER=gmail` (required)
- `CONNECTOR_CHANNEL=email` (required)
- `CONNECTOR_ENDPOINT_IDENTITY` (required)
- `CONNECTOR_MAX_INFLIGHT` (optional, recommended default `8`)
- `CONNECTOR_CURSOR_PATH` (required; stores last processed Gmail `historyId`)

Gmail API auth variables (OAuth-based, DB-first resolution):

The connector resolves Google OAuth credentials using DB-first resolution with env-var
fallback. This allows credentials stored via the dashboard OAuth flow to be used
automatically without any env var configuration.

**Resolution order:**
1. Local override DB: if `CONNECTOR_BUTLER_DB_NAME` is configured, that butler DB is queried first.
2. Shared credential DB: `BUTLER_SHARED_DB_NAME` (default `butlers`).
3. Environment variables (deprecated fallback for backward compatibility).

**DB-first variables (recommended):**
- `DATABASE_URL` (optional; postgres connection URL, e.g., `postgres://user:pass@localhost:5432/butlers`)
  OR `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_USER`, `POSTGRES_PASSWORD` (individual vars)
- `CONNECTOR_BUTLER_DB_NAME` (optional; local butler DB name for per-butler override secrets)
- `BUTLER_SHARED_DB_NAME` (optional; shared credential DB name, default: `butlers`)

**App config variables (always required for OAuth bootstrap):**
- `GOOGLE_OAUTH_CLIENT_ID` (required; OAuth client ID — used by dashboard OAuth flow)
- `GOOGLE_OAUTH_CLIENT_SECRET` (required; OAuth client secret — used by dashboard OAuth flow)

**Deprecated env-var fallback (legacy, will be removed in a future release):**
- `GMAIL_CLIENT_ID` (deprecated alias for `GOOGLE_OAUTH_CLIENT_ID`)
- `GMAIL_CLIENT_SECRET` (deprecated alias for `GOOGLE_OAUTH_CLIENT_SECRET`)
- `GMAIL_REFRESH_TOKEN` or `GOOGLE_REFRESH_TOKEN` (deprecated; store credentials in DB instead)

Optional runtime controls:
- `GMAIL_POLL_INTERVAL_S` (polling interval in seconds, default 60)
- `GMAIL_WATCH_RENEW_INTERVAL_S` (watch renewal cadence, default 86400 = 1 day)
- `GMAIL_LABEL_INCLUDE` (comma-separated label filter, future)
- `GMAIL_LABEL_EXCLUDE` (comma-separated label filter, future)

Pub/Sub push notification controls (optional, for near real-time ingestion):
- `GMAIL_PUBSUB_ENABLED` (enable Pub/Sub push mode, default false)
- `GMAIL_PUBSUB_TOPIC` (required when enabled; GCP Pub/Sub topic, e.g., `projects/my-project/topics/gmail-push`)
- `GMAIL_PUBSUB_WEBHOOK_PORT` (webhook server port, default 40083)
- `GMAIL_PUBSUB_WEBHOOK_PATH` (webhook endpoint path, default `/gmail/webhook`)

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

## 8. Pub/Sub Setup Guide

### 8.1 Prerequisites
To use Pub/Sub push mode, you need:
1. GCP project with Cloud Pub/Sub API enabled
2. Pub/Sub topic created for Gmail notifications
3. Gmail API domain-wide delegation or user OAuth consent for `https://www.googleapis.com/auth/gmail.readonly` scope
4. Public endpoint for webhook (or Cloud Run/GKE with proper ingress)

### 8.2 Creating Pub/Sub Topic
```bash
# Create topic
gcloud pubsub topics create gmail-push

# Grant Gmail permission to publish
gcloud pubsub topics add-iam-policy-binding gmail-push \
  --member=serviceAccount:gmail-api-push@system.gserviceaccount.com \
  --role=roles/pubsub.publisher
```

### 8.3 Connector Configuration
Set environment variables:
```bash
GMAIL_PUBSUB_ENABLED=true
GMAIL_PUBSUB_TOPIC=projects/my-project/topics/gmail-push
GMAIL_PUBSUB_WEBHOOK_PORT=40083
GMAIL_PUBSUB_WEBHOOK_PATH=/gmail/webhook
GMAIL_PUBSUB_WEBHOOK_TOKEN=your-secret-token  # Optional but recommended
```

### 8.4 Webhook Endpoint
The connector automatically starts an HTTP server on the configured port to receive Pub/Sub push notifications. Ensure this endpoint is:
- Publicly accessible (or accessible to GCP Pub/Sub service)
- Protected with `GMAIL_PUBSUB_WEBHOOK_TOKEN` (strongly recommended to prevent unauthorized requests)
- Behind HTTPS in production (Cloud Run/Load Balancer handles this)

When `GMAIL_PUBSUB_WEBHOOK_TOKEN` is set, the webhook verifies that incoming requests include `Authorization: Bearer <token>` header. Configure your Pub/Sub push subscription to send this header.

### 8.5 Watch Lifecycle
- Watch subscription is created on connector startup
- Auto-renewed when approaching expiration (configurable via `GMAIL_WATCH_RENEW_INTERVAL_S`)
- Watch expires after ~7 days if not renewed
- Connector logs watch expiration timestamps for monitoring

## 9. Non-Goals
This connector does not:
- Bypass Switchboard canonical ingest semantics.
- Perform direct specialist-butler routing.
- Replace outbound email delivery tooling (ingress-focused only).
