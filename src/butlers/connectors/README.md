# Butlers Connectors

Transport-only adapters for external message ingestion.

## Overview

Connectors are responsible for:
- Reading events from external systems (Telegram, Gmail, etc.)
- Normalizing to canonical `ingest.v1` format
- Submitting to Switchboard MCP server via ingest tool
- Handling checkpointing and crash-safe resume

Connectors do NOT:
- Classify messages
- Route to specialist butlers
- Mint canonical request_id values

All classification and routing happens downstream in Switchboard after ingest acceptance.

## Telegram Bot Connector

### Running in Polling Mode (Dev)

```bash
export SWITCHBOARD_MCP_URL="http://localhost:40100/sse"
export CONNECTOR_PROVIDER="telegram"
export CONNECTOR_CHANNEL="telegram"
export CONNECTOR_ENDPOINT_IDENTITY="your_bot_username"
export BUTLER_TELEGRAM_TOKEN="your-telegram-bot-token"
export CONNECTOR_CURSOR_PATH="/path/to/checkpoint.json"
export CONNECTOR_POLL_INTERVAL_S="1.0"

python -m butlers.connectors.telegram_bot
```

### Running in Webhook Mode (Prod)

```bash
export SWITCHBOARD_MCP_URL="http://localhost:40100/sse"
export CONNECTOR_PROVIDER="telegram"
export CONNECTOR_CHANNEL="telegram"
export CONNECTOR_ENDPOINT_IDENTITY="your_bot_username"
export BUTLER_TELEGRAM_TOKEN="your-telegram-bot-token"
export CONNECTOR_WEBHOOK_URL="https://yourdomain.com/telegram/webhook"

python -m butlers.connectors.telegram_bot
```

In webhook mode, the connector registers the webhook with Telegram and exits. 
Incoming updates should be POSTed to your webhook endpoint and processed via 
`connector.process_webhook_update(update)`.

### Configuration

See `docs/connectors/telegram_bot.md` for full configuration reference.

Required environment variables:
- `SWITCHBOARD_MCP_URL`: SSE endpoint URL for Switchboard MCP server
- `CONNECTOR_ENDPOINT_IDENTITY`: Bot username or ID
- `BUTLER_TELEGRAM_TOKEN`: Telegram bot token

Polling mode requires:
- `CONNECTOR_CURSOR_PATH`: Checkpoint file path
- `CONNECTOR_POLL_INTERVAL_S`: Poll interval in seconds (default: 1.0)

Webhook mode requires:
- `CONNECTOR_WEBHOOK_URL`: Public HTTPS webhook URL

Optional:
- `CONNECTOR_MAX_INFLIGHT`: Max concurrent ingest submissions (default: 8)

### Checkpoint Persistence

In polling mode, the connector persists a checkpoint file with the last 
processed `update_id`. On restart, it resumes from the checkpoint to ensure 
at-least-once delivery semantics.

Switchboard ingest deduplication ensures exactly-once effect at the canonical 
request layer even with connector replays.

### Idempotency

Each Telegram update is normalized with:
- `control.idempotency_key`: `telegram:<endpoint_identity>:<update_id>`

This stable key enables safe retries and crash recovery. Duplicate submissions 
return the same canonical `request_id` from Switchboard.

## Testing

```bash
# Run connector tests
uv run pytest tests/connectors/ -v

# Run specific connector test
uv run pytest tests/connectors/test_telegram_bot_connector.py::test_name -v
```
