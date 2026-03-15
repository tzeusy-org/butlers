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

Connector transport compatibility note:

- Connectors continue to use the Switchboard SSE endpoint (`/sse`) during the
  spawner/runtime streamable HTTP rollout.
- Runtime sessions launched by butler spawners use `/mcp`; this is a separate
  transport path and does not require connector URL changes.

## Endpoint Identity

Each connector auto-resolves its identity at startup from the authenticated
account — no manual env var configuration needed:

- **Telegram bot:** `getMe` API → `telegram:bot:@<username>`
- **Telegram user client:** Telethon `get_me()` → `telegram:user:@<username>`
- **Gmail:** derived from `shared.google_accounts.email` → `gmail:user:<email>`
- **Discord:** `/users/@me` API → `discord:user:@<username>`

## Telegram Bot Connector

### Running in Polling Mode (Dev)

```bash
export SWITCHBOARD_MCP_URL="http://localhost:41100/sse"
export BUTLER_TELEGRAM_TOKEN="your-telegram-bot-token"
export CONNECTOR_POLL_INTERVAL_S="1.0"

python -m butlers.connectors.telegram_bot
```

### Running in Webhook Mode (Prod)

```bash
export SWITCHBOARD_MCP_URL="http://localhost:41100/sse"
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
- `BUTLER_TELEGRAM_TOKEN`: Telegram bot token

Polling mode requires:
- `CONNECTOR_POLL_INTERVAL_S`: Poll interval in seconds (default: 1.0)

Webhook mode requires:
- `CONNECTOR_WEBHOOK_URL`: Public HTTPS webhook URL

Optional:
- `CONNECTOR_MAX_INFLIGHT`: Max concurrent ingest submissions (default: 8)

### Checkpoint Persistence

In polling mode, the connector persists a checkpoint to the DB with the last
processed `update_id`. On restart, it resumes from the checkpoint to ensure
at-least-once delivery semantics.

Switchboard ingest deduplication ensures exactly-once effect at the canonical
request layer even with connector replays.

### Idempotency

Each Telegram update is normalized with:
- `control.idempotency_key`: `telegram:<endpoint_identity>:<update_id>`

This stable key enables safe retries and crash recovery. Duplicate submissions
return the same canonical `request_id` from Switchboard.

## Live Listener Connector

Captures ambient audio from local microphones, transcribes speech via a
faster-whisper service, applies LLM-based discretion filtering, and submits
actionable utterances to the Switchboard as `ingest.v1` envelopes.

### Quick Start

```bash
# Minimal setup — one microphone via Wyoming transcription
export SWITCHBOARD_MCP_URL="http://localhost:41100/sse"
export LIVE_LISTENER_DEVICES='[{"name": "kitchen", "device": "default"}]'
export LIVE_LISTENER_TRANSCRIPTION_URL="tcp://localhost:10300"

python -m butlers.connectors.live_listener.connector
```

### Common Device Configurations

Single default system microphone:
```json
[{"name": "main", "device": "default"}]
```

Named USB microphone (substring match against PortAudio device list):
```json
[{"name": "kitchen", "device": "USB Audio Device"}]
```

Multiple microphones by PortAudio index:
```json
[
  {"name": "kitchen", "device": 0},
  {"name": "bedroom", "device": 2}
]
```

Multiple named microphones:
```json
[
  {"name": "kitchen", "device": "USB PnP Sound Device: Audio"},
  {"name": "office",  "device": "Microphone (Realtek High Definition Audio)"}
]
```

### Configuration

Required environment variables:
- `SWITCHBOARD_MCP_URL`: SSE endpoint URL for Switchboard MCP server
- `LIVE_LISTENER_DEVICES`: JSON list of mic device specs
- `LIVE_LISTENER_TRANSCRIPTION_URL`: Transcription service URL
  - Wyoming: `tcp://host:10300`
  - WebSocket: `ws://host:port/transcribe`
  - HTTP: `http://host:port/transcribe`

Optional:
- `CONNECTOR_HEALTH_PORT`: HTTP port for `/health` and `/metrics` (default: 40091)
- `LIVE_LISTENER_TRANSCRIPTION_PROTOCOL`: `wyoming` | `websocket` | `http` (default: `wyoming`)
- `LIVE_LISTENER_LANGUAGE`: BCP-47 language hint (default: `en`)
- `LIVE_LISTENER_MIN_CONFIDENCE`: Minimum transcription confidence (default: `0.3`)
- `LIVE_LISTENER_VAD_ONSET_THRESHOLD`: VAD onset threshold (default: `0.5`)
- `LIVE_LISTENER_VAD_OFFSET_THRESHOLD`: VAD offset threshold (default: `0.3`)
- `LIVE_LISTENER_SESSION_GAP_S`: Silence gap (seconds) that starts a new session (default: `120`)
- `LIVE_LISTENER_DISCRETION_LLM_URL`: LLM endpoint for discretion filter (default: local Ollama)
- `LIVE_LISTENER_DISCRETION_LLM_MODEL`: LLM model name (default: `haiku`)

### Health

The health server listens on `CONNECTOR_HEALTH_PORT` (default: 40091):
- `GET /health` — JSON health state: `healthy`, `degraded`, or `error`
- `GET /metrics` — Prometheus metrics in text format

Health state derivation:
- `error` — no audio devices are capturing (all failed or none configured)
- `degraded` — any mic has a failed device, transcription is unreachable, or discretion LLM is unreachable
- `healthy` — all mic pipelines active and all services responsive

### Prometheus Metrics

Voice-specific metrics (in addition to standard `ConnectorMetrics`):
- `connector_live_listener_segments_total{mic, outcome}` — speech segments processed
- `connector_live_listener_discretion_total{mic, verdict}` — discretion verdicts
- `connector_live_listener_transcription_failures_total{mic, error_type}` — transcription failures
- `connector_live_listener_e2e_latency_seconds{mic}` — end-to-end pipeline latency
- `connector_live_listener_stage_latency_seconds{mic, stage}` — per-stage latency

## Testing

```bash
# Run connector tests
uv run pytest tests/connectors/ -v

# Run specific connector test
uv run pytest tests/connectors/test_telegram_bot_connector.py::test_name -v
```
