# Gmail Connector

> **Purpose:** Profile the Gmail connector -- live ingestion via history polling and Pub/Sub push, backfill support, tiered processing, and multi-account operation.
> **Audience:** Developers deploying or operating the Gmail connector.
> **Prerequisites:** [Connector Architecture Overview](overview.md), [Connector Interface Contract](interface.md).

## Overview

The Gmail connector (`src/butlers/connectors/gmail.py`) ingests new Gmail emails in near real-time using the Gmail API's watch/history delta flow. It supports both polling-based and Pub/Sub push-based ingestion, historical backfill, tiered ingestion policy, label filtering, attachment handling, and multi-account concurrent operation.

The connector is implemented by `GmailConnectorRuntime`, managed by a `GmailConnectorManager` that supports dynamic multi-account discovery and lifecycle management.

## Live Ingestion Models

### Pub/Sub Push Mode (Recommended for Production)

When enabled via `GMAIL_PUBSUB_ENABLED=true`, the connector uses Gmail push notifications for near real-time ingestion:

1. Start Gmail watch subscription via `users.watch` API pointing to configured Pub/Sub topic.
2. Run HTTP webhook server to receive Pub/Sub push notifications.
3. On notification, immediately fetch history changes via `users.history.list`.
4. Fetch message payload/metadata for each new email.
5. Normalize and submit each event to Switchboard ingest.
6. Auto-renew watch subscription before expiration (default 1 day).

Even in Pub/Sub mode, periodic polling runs as a safety net (every 5 minutes minimum) to catch missed notifications.

### Polling Mode (Default)

When Pub/Sub is disabled (default), the connector uses polling-based history fetch:

1. Poll Gmail history API at configured interval (default 60s).
2. Fetch changed message IDs from Gmail history API.
3. Fetch message payload/metadata for each new email.
4. Normalize and submit each event to Switchboard ingest.

Polling mode is simpler to set up (no Pub/Sub topic or webhook endpoint required) but has higher latency (~60s vs. near real-time).

## Request Context Mapping

| Envelope field | Gmail source |
|---|---|
| `source.channel` | `email` |
| `source.provider` | `gmail` |
| `source.endpoint_identity` | Auto-resolved from account email (e.g., `gmail:user:alice@gmail.com`) |
| `event.external_event_id` | Gmail message ID |
| `event.external_thread_id` | Gmail `threadId` |
| `event.observed_at` | Connector-observed timestamp (RFC 3339) |
| `sender.identity` | Normalized sender address from `From` header |
| `payload.raw` | Full Gmail API payload (or safe subset per policy) |
| `payload.normalized_text` | Normalized subject/body text |
| `control.idempotency_key` | `gmail:<endpoint_identity>:<message_id>` |

## Authentication

The connector resolves Google OAuth credentials from DB-backed secret storage (`butler_secrets`). Credentials stored via the dashboard OAuth flow are used automatically.

**Resolution order:**

1. Local override DB: if `CONNECTOR_BUTLER_DB_NAME` is configured, that butler DB is queried first.
2. Shared credential DB: `BUTLER_SHARED_DB_NAME` (default `butlers`).
3. Startup fails if credentials are missing.

**Requirement:** Complete the dashboard OAuth bootstrap before starting the connector.

## Tiered Ingestion Policy

The connector applies tiered processing rules before submission (see [Gmail Ingestion Policy](gmail-ingestion-policy.md)):

| Tier | Name | Behavior |
|---|---|---|
| 1 | Full | Full `ingest.v1` envelope, normal classification/routing |
| 2 | Metadata-only | Slim envelope, bypass LLM classification, store reference only |
| 3 | Skip | Connector drops the message, metrics only |

Tier assignment happens before classification, driven by triage rules evaluated in priority order. Default is Tier 1 for safety.

## Label Filtering

`GMAIL_LABEL_INCLUDE` and `GMAIL_LABEL_EXCLUDE` are normative production controls:

- Label filters are applied before triage evaluation.
- `GMAIL_LABEL_EXCLUDE` takes precedence over include matches.
- Empty include list means "all labels allowed except excluded."
- Deployments SHOULD exclude `SPAM` and `TRASH` (this is the default).

## Attachment Handling

The connector implements a per-MIME-type attachment policy (see [Attachment Handling](attachment-handling.md)):

| Category | MIME types | Limit | Fetch mode |
|---|---|---|---|
| Images | jpeg, png, gif, webp | 5 MB | lazy |
| PDF | application/pdf | 15 MB | lazy |
| Spreadsheets | xlsx, xls, csv | 10 MB | lazy |
| Documents | docx, message/rfc822 | 10 MB | lazy |
| Calendar | text/calendar | 1 MB | eager |

Global hard ceiling: 25 MB (Gmail maximum). Calendar `.ics` files are eagerly fetched and directly routed to the calendar module, bypassing LLM classification.

## Backfill

The Gmail connector implements the optional backfill polling protocol for dashboard-triggered historical email processing.

### Backfill Loop

- Every `CONNECTOR_BACKFILL_POLL_INTERVAL_S` (default 60), polls Switchboard for pending backfill jobs.
- Live ingestion always takes priority; backfill yields to incoming live messages.
- Backfill and live ingestion share the `CONNECTOR_MAX_INFLIGHT` concurrency budget (backfill gets at most `MAX_INFLIGHT - 1` slots).

### History Traversal

- Uses `users.messages.list` with date-bounded queries.
- Walks result pages in reverse chronological order.
- Applies the same tiered ingestion rules as live mode.
- Persists cursor via `backfill.progress(...)`.

### Rate Limiting and Cost Controls

- Honors `rate_limit_per_hour` from backfill job params (default 100).
- Implements token bucket rate limiting.
- Also honors Gmail API quota (250 units/second per user).
- Tracks estimated cost and reports via `cost_spent_cents` on each progress call.
- Switchboard enforces `daily_cost_cap` and transitions to `cost_capped` when exceeded.

### Backfill Modes

- **Selective batch** (primary): Category-targeted windows (e.g., finance last 7 years, health all history).
- **On-demand**: User-question-driven retrieval via `email_search_and_ingest(query, max_results)`.
- **Background batch** (optional): Low-priority continuous enrichment with tight cost caps.

### Capability Advertisement

Heartbeats include `capabilities.backfill=true` in metadata, allowing the dashboard to show/hide backfill controls.

## Multi-Account Operation

Multiple Gmail connectors can run concurrently, isolated per mailbox:

- Each instance has a unique auto-resolved endpoint identity.
- Each instance has its own DB-backed cursor.
- Each instance MAY use different label filters.
- The `GmailConnectorManager` periodically rescans for new accounts (default every 300 seconds).

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `SWITCHBOARD_MCP_URL` | Yes | SSE endpoint for Switchboard MCP server |
| `CONNECTOR_PROVIDER` | Yes (default: `gmail`) | Provider name |
| `CONNECTOR_CHANNEL` | Yes (default: `email`) | Channel name |
| `CONNECTOR_MAX_INFLIGHT` | No (default: 8) | Max concurrent ingest submissions |
| `CONNECTOR_HEALTH_PORT` | No (default: 40082) | HTTP port for health endpoint |
| `DATABASE_URL` or `POSTGRES_*` | No | DB connectivity for credential lookup |
| `CONNECTOR_BUTLER_DB_NAME` | No | Butler DB name |
| `BUTLER_SHARED_DB_NAME` | No (default: `butlers`) | Shared credentials DB |
| `GMAIL_POLL_INTERVAL_S` | No (default: 60) | Polling interval in seconds |
| `GMAIL_WATCH_RENEW_INTERVAL_S` | No (default: 86400) | Watch renewal cadence |
| `GMAIL_LABEL_INCLUDE` | No | Comma-separated label include filter |
| `GMAIL_LABEL_EXCLUDE` | No (default: `SPAM,TRASH`) | Comma-separated label exclude filter |
| `GMAIL_PUBSUB_ENABLED` | No (default: false) | Enable Pub/Sub push mode |
| `GMAIL_PUBSUB_TOPIC` | If Pub/Sub enabled | GCP Pub/Sub topic |
| `GMAIL_PUBSUB_WEBHOOK_PORT` | No (default: 40083) | Webhook server port |
| `GMAIL_PUBSUB_WEBHOOK_PATH` | No (default: `/gmail/webhook`) | Webhook endpoint path |
| `GMAIL_PUBSUB_WEBHOOK_TOKEN` | No | Auth token for webhook security |
| `CONNECTOR_BACKFILL_ENABLED` | No (default: true) | Enable backfill polling |
| `CONNECTOR_BACKFILL_POLL_INTERVAL_S` | No (default: 60) | Backfill poll cadence |
| `CONNECTOR_BACKFILL_PROGRESS_INTERVAL` | No (default: 50) | Report progress every N messages |

## Pub/Sub Setup

### Prerequisites

1. GCP project with Cloud Pub/Sub API enabled.
2. Pub/Sub topic created for Gmail notifications.
3. Gmail API OAuth consent scope: `https://www.googleapis.com/auth/gmail.readonly` (or `gmail.modify`).
4. Public endpoint for webhook (or Cloud Run/GKE with proper ingress).

### Topic Creation

```bash
gcloud pubsub topics create gmail-push
gcloud pubsub topics add-iam-policy-binding gmail-push \
  --member=serviceAccount:gmail-api-push@system.gserviceaccount.com \
  --role=roles/pubsub.publisher
```

### Webhook Security

When `GMAIL_PUBSUB_WEBHOOK_TOKEN` is set, the webhook verifies that incoming requests include an `Authorization: Bearer <token>` header. Configure your Pub/Sub push subscription to send this header.

## Health Endpoint

The connector exposes a FastAPI health server on `CONNECTOR_HEALTH_PORT` (default 40082):

- `GET /health` -- Aggregated multi-account health status.
- `GET /metrics` -- Prometheus metrics endpoint.

## Idempotency and Resume

- Primary dedupe identity: Gmail message ID + endpoint identity.
- Per-thread ordering preserved where practical; cross-thread global ordering not guaranteed.
- `historyId` cursor is DB-backed via `cursor_store`.
- Checkpoint advances only after ingest acceptance.

## Related Pages

- [Connector Architecture Overview](overview.md)
- [Connector Interface Contract](interface.md) -- Full `ingest.v1` envelope spec
- [Gmail Ingestion Policy](gmail-ingestion-policy.md) -- Tiered email processing
- [Attachment Handling](attachment-handling.md) -- Attachment fetch policy
- [Heartbeat Protocol](heartbeat.md) -- Liveness reporting
- [Metrics](metrics.md) -- Prometheus instrumentation
