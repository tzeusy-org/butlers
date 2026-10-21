# Telegram Bot Connector

> **Purpose:** Profile the Telegram bot connector -- how it polls or receives webhook updates, normalizes them, and submits to Switchboard ingest.
> **Audience:** Developers deploying or operating the Telegram bot connector.
> **Prerequisites:** [Connector Architecture Overview](overview.md), [Connector Interface Contract](../api_and_protocols/ingestion-envelope.md).

## Overview

The Telegram bot connector (`src/butlers/connectors/telegram_bot.py`) is a transport-only adapter that ingests Telegram updates via bot API polling or webhooks. It normalizes updates into `ingest.v1` envelopes and submits them to the Switchboard. It does not classify or route messages.

The connector is implemented by the `TelegramBotConnector` class, which handles polling, normalization, checkpoint persistence, health reporting, heartbeat, and ingestion policy evaluation.

## Approval Callbacks

Owner approval buttons use a deterministic dashboard round trip, not Switchboard,
LLM, or MCP routing. The connector verifies the owner channel and callback HMAC,
then uses the shared-public Tier-1 credential `APPROVAL_CALLBACK_CONNECTOR_TOKEN`
in `X-Butlers-Approval-Callback-Token` for only the approval-detail and
approve/deny API routes. Store it, along with `APPROVAL_CALLBACK_SECRET`, through
the Dashboard Secrets page; neither credential is read from connector environment
variables. A failed callback authentication leaves the action pending and never
claims the decision succeeded.

## Decision Callbacks

Decision buttons use the separate `dsk1` prefix. Before ingestion, the connector verifies the
sender's primary verified owner channel, loads the prompt, and verifies its HMAC against the
prompt creation time. It forwards the **original** callback token to
`POST /api/decisions/prompts/{prompt_id}/choose` as JSON `token` alongside `option_index`, with
`X-Butlers-Approval-Callback-Token` and `X-Butlers-Decision-Actor: owner@telegram`. These existing
scoped credentials do not authorize the owner-intent or digest routes; an owner API key cannot
substitute at choose. Use compatible connector/API versions together; tokenless requests have no
fallback.

The API independently resolves the shared Tier 1 `APPROVAL_CALLBACK_SECRET` from the database
with `env_fallback=False`, locks the current prompt until the intent transaction commits, and
checks its current creation-time signature plus exact URL prompt id and body option index before
any reconciliation or write. Missing DB custody returns `503 callback_verification_unavailable`.
Invalid or missing tokens return `403 invalid_callback_token`; malformed whole requests, invalid
path UUIDs and other field errors return `422 invalid_callback_request` (also for mixed errors).
Missing prompt remains 404, unoffered index 422, changed options and consumed/conflicting choices
409, and unavailable decision evidence 503. A repeated identical valid tap returns the existing
intent with `meta.created=false` and the existing Already handled toast.

Audit events retain request/effect metadata with a fixed redacted body, while response errors,
logs and telemetry omit raw callback tokens, signing secrets and credentials. A token-bearing
POST transport error logs only a categorical failure, asks the owner to retry and retains the
keyboard; it does not ingest or error-buffer the update. Malformed `dsk1` Unicode receives only
the generic acknowledgement. No token expiry, routing activation or reoffer policy is introduced;
see [Decision beads](../operations/decision-beads.md) for the transaction and refusal contract.

## Insight feedback callbacks

Insight deliveries use `ifb1:<candidate UUID>:<u|n|x>:<hmac16>` (60 bytes),
with Useful, Not now and Never buttons on standalone and digest deliveries.
The prefix is inside the RFC 0021 signature, using the existing Tier-1
`APPROVAL_CALLBACK_SECRET`; `apr1`, `dsk1` and `ifb1` cannot cross-verify.
The candidate's `created_at` is loaded from the server, never from the tap.

Before ingestion, the connector checks the primary owner sender and matching
private chat, loads the content-blind state, verifies the HMAC, and calls
`POST /api/switchboard/insights/{insight_id}/telegram-feedback`. The scoped
connector credential admits exactly that POST and
`GET /api/switchboard/insights/{insight_id}/callback-state`, with no neighboring
route access. POST requires `X-Butlers-Decision-Actor: owner@telegram`, an
original signed `token`, a bounded `callback_query_id` and the bounded `verdict`.
The server supplies the actor itself, verifies the token again under the
candidate lock, and computes Not now's 72-hour snooze. Caller actor/snooze
fields are rejected. Repeating a verdict or replaying a recorded query adds no row.

Recorded feedback marks the chosen button while keeping reversal and sibling
digest actions. API failures retain the keyboard for retry; a purged candidate
removes only its own row. Callback bodies, including malformed JSON, are
redacted before audit and validation errors never reflect tokens. No token or
credential is persisted in feedback evidence; only the Telegram query reference
is retained. Approval rollout gating does not govern insight feedback.

## Request Context Mapping

Telegram updates are mapped to the `ingest.v1` envelope as follows:

| Envelope field | Telegram source |
|---|---|
| `source.channel` | `telegram` |
| `source.provider` | `telegram` |
| `source.endpoint_identity` | Auto-resolved via `getMe()` (e.g., `telegram:bot:@mybot`) |
| `event.external_event_id` | `update_id` |
| `event.external_conversation_id` | `telegram:<chat.id>`, plus `:topic:<message_thread_id>` when Telegram marks the message `is_topic_message` |
| `event.reply_target_ref` | `<chat.id>:<message_id>` |
| `event.observed_at` | Connector-observed timestamp (RFC 3339) |
| `sender.identity` | `message.from.id` |
| `payload.raw` | Full Telegram update JSON |
| `payload.normalized_text` | Extracted text (see text extraction below) |
| `control.idempotency_key` | `telegram:<endpoint_identity>:<update_id>` |

## Text Extraction

The connector extracts normalized text from Telegram messages using a tiered strategy:

1. **Text** -- `message.text` for standard text messages.
2. **Caption** -- `message.caption` for media messages with captions.
3. **Media descriptor** -- Synthesized tags for media-only messages:
   - `[Photo]`, `[Sticker: emoji]`, `[Voice message]`, `[Video]`, `[GIF]`, `[Document]`, `[Audio]`, `[Location]`, `[Contact: Name]`, `[Poll: Question]`, `[Dice]`
4. **None** -- Service messages with no user content (e.g., `new_chat_members`).

## Update Retrieval Modes

### Polling (Development)

Use polling for local development. The connector calls Telegram `getUpdates` in a loop. No public URL is required.

```toml
[modules.telegram]
mode = "polling"
poll_interval = 1.0

[modules.telegram.bot]
enabled = true
token_env = "BUTLER_TELEGRAM_TOKEN"
```

The polling loop implements exponential backoff with jitter (capped at 60 seconds) on consecutive failures.

### Webhook (Production)

Use webhook mode for production. On startup, the connector calls `setWebhook` with the configured URL.

```toml
[modules.telegram]
mode = "webhook"
webhook_url = "https://<public-domain>/<telegram-webhook-path>"

[modules.telegram.bot]
enabled = true
token_env = "BUTLER_TELEGRAM_TOKEN"
```

The daemon must be reachable at the public HTTPS URL. Incoming webhook updates use the same normalization path as polling.

## Ingestion Policy

The connector evaluates two ingestion policy scopes in order before submitting events:

1. **Connector scope** (`connector:telegram-bot:<endpoint>`) -- pre-ingest block/pass-through rules.
2. **Global scope** (`global`) -- post-ingest skip/metadata-only/route-to/low-priority-queue rules.

Policy rules are DB-backed with TTL refresh. On DB error, evaluation fails open (events pass through).

## Interactivity Surface

The connector only ingests. Replies and reactions are MCP tools of the
[Telegram module](../modules/telegram.md) (`TelegramModule.register_tools` in
`src/butlers/modules/telegram.py`), which send, reply to, and react to messages.

Lifecycle reactions on inbound messages (in progress, success, failure) are set best-effort by the
Switchboard through the module's `react_for_ingest()`; the emoji constants live in
`src/butlers/core/channel_reactions.py`. A rejected reaction is logged and never blocks processing.

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `SWITCHBOARD_MCP_URL` | Yes | SSE endpoint for Switchboard MCP server |
| `CONNECTOR_PROVIDER` | Yes (default: `telegram`) | Provider name |
| `CONNECTOR_CHANNEL` | Yes (default: `telegram_bot`) | Channel name |
| `BUTLER_TELEGRAM_TOKEN` | Yes | Bot token (resolved from DB first, then env) |
| `CONNECTOR_POLL_INTERVAL_S` | For polling | Poll interval in seconds (default: 1.0) |
| `CONNECTOR_WEBHOOK_URL` | For webhook | Public webhook URL |
| `CONNECTOR_MAX_INFLIGHT` | No (default: 8) | Max concurrent ingest submissions |
| `CONNECTOR_HEALTH_PORT` | No (default: 40081) | HTTP port for health endpoint |
| `CONNECTOR_BUTLER_DB_NAME` | No | Local butler DB for per-butler secret overrides |
| `BUTLER_SHARED_DB_NAME` | No (default: `butlers`) | Shared credential DB name |

## Health Endpoint

The connector exposes a FastAPI health server on `CONNECTOR_HEALTH_PORT` (default 40081) with:

- `GET /health` -- Returns status (`healthy`/`unhealthy`), uptime, last checkpoint save, last ingest submit, and source API connectivity.
- `GET /metrics` -- Prometheus metrics endpoint.

## Idempotency and Resume

- Primary dedupe identity: `update_id` + endpoint identity.
- Retries reuse the same identity fields; Switchboard dedupe makes retries safe.
- Polling cursor (`last_update_id`) is persisted in the database via `cursor_store`.
- Checkpoint advances only after successful ingest acceptance.

## Verification

To confirm the Telegram bot connector is operating as described:

```bash
# 1. Connector health endpoint reports healthy
curl -s http://localhost:40081/health | python3 -m json.tool
# Expected: {"status": "healthy", "uptime": ..., "last_ingest_submit": "<recent ISO time>"}

# 2. Polling cursor (last_update_id) is persisted in the database
psql -h localhost -U butlers -d butlers -c \
  "SELECT connector_type, endpoint_identity, cursor_value, updated_at
   FROM switchboard.connector_registry WHERE connector_type='telegram_bot';"
# Expected: cursor_value is the most recent processed update_id; advances with each batch

# 3. Telegram messages land in ingestion_events with correct channel/provider
psql -h localhost -U butlers -d butlers -c \
  "SELECT source_channel, source_provider, source_endpoint_identity, received_at
   FROM switchboard.ingestion_events
   WHERE source_channel='telegram'
   ORDER BY received_at DESC LIMIT 5;"
# Expected: rows with source_channel='telegram', source_provider='telegram',
#           endpoint_identity='telegram:bot:@<botname>'

# 4. Duplicate update_id submissions are deduplicated (idempotency)
# Send the same update_id twice via a direct ingest call; only one row appears in ingestion_events
psql -h localhost -U butlers -d butlers -c \
  "SELECT external_event_id, COUNT(*) FROM switchboard.ingestion_events
   WHERE source_channel='telegram'
   GROUP BY external_event_id HAVING COUNT(*) > 1;"
# Expected: zero rows (no duplicate event IDs)

# 5. Bot endpoint identity is auto-resolved (not manually configured)
grep "endpoint_identity\|getMe" /var/log/butlers/telegram-bot-connector.log 2>/dev/null | head -5
# Expected: log shows getMe() call on startup; endpoint_identity set to telegram:bot:@<username>
```

## Implementation Notes

- `TelegramBotConnector._get_updates` (`src/butlers/connectors/telegram_bot.py`) treats
  `HTTP 409 Conflict` (another poller or a webhook) as recoverable: it
  records source status `conflict`, logs the parsed Telegram description at warning level, and
  returns `[]`.
- `event.reply_target_ref` (and its request-context alias `source_thread_identity`) is the
  per-message `<chat_id>:<message_id>` used for replies and reactions only. Conversation anchors,
  provider-session resume, and `_load_realtime_history` key on `external_conversation_id`, so
  consecutive messages in one chat share one anchor and one history. The shared derivation lives in
  `src/butlers/conversation_identity.py`.
- The bot and user-client connectors start from DB credentials when credential env vars are
  missing; only `SWITCHBOARD_MCP_URL` is a required non-credential env var, and endpoint identity is
  resolved from the Telegram API at startup.
- `TelegramBotConnector._get_updates` treats `HTTP 429` as recoverable: it records `rate_limited`
  metrics, honours
  `Retry-After` (header, then `result.parameters.retry_after`) and returns `[]` instead of raising.

## Related Pages

- [Connector Architecture Overview](overview.md)
- [Connector Interface Contract](../api_and_protocols/ingestion-envelope.md) -- Full `ingest.v1` envelope spec
- [Heartbeat Protocol](heartbeat.md) -- Liveness reporting
- [Metrics](metrics.md) -- Prometheus instrumentation
