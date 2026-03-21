# Telegram Bot Connector

> **Purpose:** Profile the Telegram bot connector -- how it polls or receives webhook updates, normalizes them, and submits to Switchboard ingest.
> **Audience:** Developers deploying or operating the Telegram bot connector.
> **Prerequisites:** [Connector Architecture Overview](overview.md), [Connector Interface Contract](interface.md).

## Overview

The Telegram bot connector (`src/butlers/connectors/telegram_bot.py`) is a transport-only adapter that ingests Telegram updates via bot API polling or webhooks. It normalizes updates into `ingest.v1` envelopes and submits them to the Switchboard. It does not classify or route messages.

The connector is implemented by the `TelegramBotConnector` class, which handles polling, normalization, checkpoint persistence, health reporting, heartbeat, and ingestion policy evaluation.

## Request Context Mapping

Telegram updates are mapped to the `ingest.v1` envelope as follows:

| Envelope field | Telegram source |
|---|---|
| `source.channel` | `telegram` |
| `source.provider` | `telegram` |
| `source.endpoint_identity` | Auto-resolved via `getMe()` (e.g., `telegram:bot:@mybot`) |
| `event.external_event_id` | `update_id` |
| `event.external_thread_id` | `chat.id` |
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

The connector supports these interactive tools:

- `telegram_send_message` -- Send a message to a chat.
- `telegram_reply_to_message` -- Reply to a specific message.
- `telegram_get_updates` -- Read recent updates.

Lifecycle reactions on inbound messages (best-effort):

| Stage | Emoji |
|---|---|
| In-progress | Eyes |
| Success | Checkmark |
| Failure | Alien |

If Telegram rejects a reaction with a 400 error, processing continues with a logged warning.

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

## Related Pages

- [Connector Architecture Overview](overview.md)
- [Connector Interface Contract](interface.md) -- Full `ingest.v1` envelope spec
- [Heartbeat Protocol](heartbeat.md) -- Liveness reporting
- [Metrics](metrics.md) -- Prometheus instrumentation
