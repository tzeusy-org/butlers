# Gmail Connector

> **Purpose:** Profile the Gmail connector -- live ingestion via history polling and Pub/Sub push, backfill support, tiered processing, and multi-account operation.
> **Audience:** Developers deploying or operating the Gmail connector.
> **Prerequisites:** [Connector Architecture Overview](overview.md), [Connector Interface Contract](../api_and_protocols/ingestion-envelope.md).

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
| `event.external_conversation_id` | Gmail `threadId` |
| `event.reply_target_ref` | Gmail `threadId` |
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

Tier assignment happens in the connector before classification: the label filter, then
connector-scope and global-scope Switchboard ingestion rules evaluated in priority order. Default is
Tier 1 for safety.

## Bearer-Material Scrubbing

The Gmail connector withholds detected one-time authentication codes (OTPs),
password-reset links, and magic links before submitting Tier 1 or Tier 2
`ingest.v1` envelopes. It scrubs subject-derived text, normalized body text, and
base64url-decoded Gmail `body.data` inside `payload.raw`, re-encoding changed
body blobs. The shared detector also handles Telegram login codes from service
sender `777000`, described in the [Telegram user-client guide](telegram-user-client.md#bearer-material-scrubbing).

| Detected material | Replacement in message content |
|---|---|
| One-time authentication code, including a Telegram login code | `[auth-code withheld: <provider-domain>]` |
| Password-reset link | `[reset-link withheld: <provider-domain>]` |
| Magic link | `[magic-link withheld: <provider-domain>]` |

For codes, the domain label comes from the sender's email domain, or the source
provider when there is no email domain (`telegram` for Telegram service codes).
For links, it is the URL hostname. These are descriptive labels, not proof of
sender identity. The original code or URL is replaced, while the rest of the
message remains. Already-scrubbed placeholders are unchanged on repeat passes.
Ordinary numbers, dates, and amounts outside authentication wording or bearer
links survive. The known exception is aggressive Telegram `777000` handling:
code-shaped 4–8 digit numbers in message text can be withheld even when benign;
date/time-shaped runs are excluded from that aggressive numeric rule.

Scrubbing rewrites message content, never envelope metadata: existing `source`,
`event`, `sender`, and `control` identities, event IDs, timestamps, and idempotency
keys are preserved. A hit adds `control.bearer_scrubbed=true`. Switchboard scrubs
again before inbox persistence and records `raw_payload.bearer_artifacts` with
`kind`, `provider_domain`, and `observed_at` (plus `expires_hint` when detected),
without storing the withheld value. The scrub does not change the existing
policy decision.

If the Switchboard scrubber raises, it fails closed: the persisted row has
lifecycle state `metadata_ref` and `raw_payload.payload.raw=null`. Both
`raw_payload.payload.normalized_text` and `message_inbox.normalized_text` contain
`[content withheld: bearer scrub failed]` instead of the original text. It sets `control.bearer_scrubbed=true` and
`control.bearer_scrub_failed=true`; envelope metadata remains intact.

This describes the connector envelope and Switchboard inbox scrub, as required
by [Bearer material never persists](../../openspec/specs/connector-gmail/spec.md#requirement-bearer-material-never-persists)
and the [security constraints](../../about/heart-and-soul/security.md#constraints).
The [custody audit](bearer-material-custody-audit.md) separately records gaps in
filtered payloads/replay, earlier prompts/logs, alternate writers, and readers;
this scrub is not evidence that those paths or a deployed fleet are protected.

## Label Filtering

`GMAIL_LABEL_INCLUDE` and `GMAIL_LABEL_EXCLUDE` are normative production controls:

- Label filters are applied before ingestion-rule evaluation.
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
- Switchboard enforces the job's `daily_cost_cap_cents` and transitions it to `cost_capped` when
  exceeded (`roster/switchboard/tools/backfill/connector.py`).

### Lifecycle Controls

HTTP and MCP pause/cancel/resume delegate to
`roster/switchboard/tools/backfill/controls.py`. Each transition compares the
observed status atomically, so a concurrent terminal transition cannot be overwritten.

| Action | Allowed current states | Result | Idempotent current state |
| --- | --- | --- | --- |
| Pause | pending, active | paused | paused |
| Cancel | pending, active, paused, error, cost_capped | cancelled (sets completed_at) | cancelled |
| Resume | paused | pending | none |

Resume refuses cost-capped jobs on both surfaces; it cannot bypass the cost cap.
HTTP malformed IDs return 422, missing jobs 404, state conflicts 409 and database
failures 503. Successful HTTP lifecycle requests retain dashboard audit events.

### Backfill Modes

- **Selective batch** (primary): Category-targeted windows (e.g., finance last 7 years, health all history).
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

Read by `GmailConnectorConfig.from_env` and `GmailProcessConfig.from_env`
(`src/butlers/connectors/gmail.py`, whose module docstring also lists them); the names and
defaults live there. Families: Switchboard and connector identity (`SWITCHBOARD_MCP_URL`,
`CONNECTOR_*`), polling and watch renewal (`GMAIL_POLL_*`, `GMAIL_WATCH_*`), label filtering
(`GMAIL_LABEL_*`), Pub/Sub push (`GMAIL_PUBSUB_*`), sent-mail lookback for priority-tier (queue
ordering) assignment (`GMAIL_SENT_*`, `GMAIL_USER_EMAIL`), and backfill (`CONNECTOR_BACKFILL_*`).
Non-obvious points:

- `SWITCHBOARD_MCP_URL` is the only hard-required variable; OAuth credentials come from the
  database (above), never from env.
- `GMAIL_LABEL_EXCLUDE` defaults to `SPAM,TRASH`; setting it replaces that default rather than
  extending it.
- `GMAIL_PUBSUB_TOPIC` is required once `GMAIL_PUBSUB_ENABLED` is true.

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

## Verification

To confirm the Gmail connector is operating as described:

```bash
# 1. Connector health endpoint reports healthy state
curl -s http://localhost:40082/health | python3 -m json.tool
# Expected: {"state": "healthy", ...} with per-account details; no "error" state

# 2. historyId cursor is persisted and advances after new emails
psql -h localhost -U butlers -d butlers -c \
  "SELECT connector_type, endpoint_identity, cursor_value, updated_at
   FROM switchboard.connector_registry
   WHERE connector_type='gmail';"
# Expected: cursor_value is a numeric Gmail historyId; updated_at advances as emails arrive

# 3. Label filters are applied (SPAM and TRASH excluded by default)
# Send a test message to the Gmail address, then verify it lands in ingestion_events
psql -h localhost -U butlers -d butlers -c \
  "SELECT source_provider, source_endpoint_identity, received_at
   FROM switchboard.ingestion_events
   WHERE source_provider='gmail'
   ORDER BY received_at DESC LIMIT 5;"
# Expected: inbox emails appear; no rows with spam/trash labels

# 4. Multi-account operation shows distinct endpoint identities
psql -h localhost -U butlers -d butlers -c \
  "SELECT connector_type, endpoint_identity FROM switchboard.connector_registry
   WHERE connector_type='gmail';"
# Expected: one row per Gmail account being monitored (each with unique endpoint_identity)

# 5. Tier assignment metric emits for ingested messages
curl -s "http://localhost:9090/api/v1/query?query=butlers_connector_gmail_priority_tier_assigned_total" \
  | python3 -m json.tool | grep -E "policy_tier|value"
# Expected: non-zero counts for at least one tier (high_priority, interactive, or default)
```

## Implementation Notes

- `run_gmail_connector` discovers accounts from `public.google_accounts` and resolves each account's
  Google OAuth credentials through `GmailConnectorManager._resolve_credentials_for_account` and
  `load_google_credentials`. App credentials come from `butler_secrets` through `CredentialStore`;
  the refresh token comes from the account's companion entity in `public.entity_info`. OAuth
  credentials never fall back to environment variables. `_create_shared_db_pool` keeps the
  schema-scoped `search_path` needed to resolve the credential store in the one-DB topology.
- `src/butlers/connectors/gmail.py::_format_google_error` is the connector's one parser for Google
  API and OAuth error payloads. Log its compact
  `code/status/reason/message` for `history.list` 404 cursor resets, other non-2xx `history.list`
  responses and failed token refreshes, before raising; never dump full payloads.
- Source health reports actionable reauthorization only for a literal top-level
  `invalid_grant` or `unauthorized_client` on the actual response-bound POST to
  `https://oauth2.googleapis.com/token`. Data-API failures and missing or ambiguous request
  provenance remain visible as degraded health, with Gmail's existing diagnostics and
  successful reset. This classification never changes shared accounts or credentials.

### Known-contact query evidence

`GmailPolicyEvaluator` exposes an immutable local snapshot independently of provider health. Successful empty and nonempty queries are loaded; no pool, failed/cancelled unfinished queries and the existing 900-second TTL are unknown. Failed refreshes retain cached contacts and their last successful timestamp for lower-bound positive classification. A per-message assigner and frozen `drop_context.classification` use one snapshot. Its UTC observation is captured at the drop decision, independently of Gmail `internalDate`, `event.observed_at`, and the persisted `received_at` window. All three drop paths retain `raw={}`; replay removes the entire context.

When heartbeat is enabled, startup attempts an unloaded publication with a two-second bound before provider work. Refresh transitions publish through the same serialized source-owned heartbeat path. Query success remains loaded when publication fails; publication never changes priority tiers, rules or provider/auth health. Current dashboard availability separately requires all last-admitted applicable runtime accounts plus complete historical classification. No server read can discover an entirely unobserved restart or failed send. Prior admitted evidence remains bounded by 300-second heartbeat and 900-second query freshness; source delivery does not imply deployment or provider verification.

## Related Pages

- [Connector Architecture Overview](overview.md)
- [Connector Interface Contract](../api_and_protocols/ingestion-envelope.md) -- Full `ingest.v1` envelope spec
- [Gmail Ingestion Policy](gmail-ingestion-policy.md) -- Tiered email processing
- [Attachment Handling](attachment-handling.md) -- Attachment fetch policy
- [Heartbeat Protocol](heartbeat.md) -- Liveness reporting
- [Metrics](metrics.md) -- Prometheus instrumentation

### Shared configuration parsing

The process manager and legacy runtime loader share parser stages for their
fifteen common non-secret settings. Each keeps its original boolean spellings
and validation order. Four runtime-only settings and the process-only account
rescan interval remain separate. Account metadata overrides process defaults;
OAuth credentials still come from the injected DB-backed resolver, and account
workers keep health port zero. Static provider `gmail` and channel `email` come
from the canonical descriptor, not identity environment variables.
