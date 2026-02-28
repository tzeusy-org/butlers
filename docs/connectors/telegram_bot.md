# Telegram Bot Connector

Status: Draft (project-specific connector profile)  
Depends on: `docs/connectors/interface.md`

## 1. Purpose
This connector is the Telegram chat interface for a user's butler. It receives Telegram updates, normalizes them into the canonical ingest contract, and lets Switchboard own request-context assignment and routing.

The connector is transport-only:
- It reads updates from Telegram.
- It forwards normalized events to Switchboard ingest.
- It does not classify or route directly to specialist butlers.

## 2. Request Context Mapping
Use the `ingest.v1` contract from `docs/connectors/interface.md`.

Telegram mapping:
- `source.channel`: `telegram`
- `source.provider`: `telegram`
- `source.endpoint_identity`: receiving bot identity (for example bot username or configured bot id)
- `event.external_event_id`: Telegram `update_id`
- `event.external_thread_id`: Telegram `chat.id`
- `event.observed_at`: update observed timestamp (RFC3339)
- `sender.identity`: Telegram sender id (`message.from.id` when present)
- `payload.raw`: full Telegram update JSON
- `payload.normalized_text`: extracted text from `message`, `edited_message`, or `channel_post`
- `control.idempotency_key`: optional fallback when needed, e.g. `telegram:<endpoint_identity>:<update_id>`

Switchboard then assigns canonical request context:
- Required: `request_id`, `received_at`, `source_channel`, `source_endpoint_identity`, `source_sender_identity`
- Optional: `source_thread_identity`, `trace_context`

## 3. Environment Variables
Base connector vars (from the interface contract):
- `SWITCHBOARD_MCP_URL` (required; SSE endpoint for Switchboard MCP server)
- `CONNECTOR_PROVIDER=telegram` (required)
- `CONNECTOR_CHANNEL=telegram` (required)
- `CONNECTOR_ENDPOINT_IDENTITY` (required)
- `CONNECTOR_MAX_INFLIGHT` (optional, recommended default `8`)

Polling-specific vars:
- `CONNECTOR_CURSOR_PATH` (required for polling mode checkpointing)
- `CONNECTOR_POLL_INTERVAL_S` (required for polling mode)

Telegram credential vars (resolved from module config):
- Bot scope: `modules.telegram.bot.token_env` (default `BUTLER_TELEGRAM_TOKEN`)
- User scope: credentials come from owner contact_info (not env vars)

Notes:
- If `modules.telegram.bot.enabled=true`, the bot token env var is required.
- User-scope tokens are resolved from owner contact_info at startup, not from env vars or butler_secrets.
- Bot-scope secrets must come from env/secret manager, never committed in `butler.toml`.

## 4. Update Retrieval Mode (Dev vs Prod)
### Dev: Polling
Use polling for local development.

Config:
```toml
[modules.telegram]
mode = "polling"
poll_interval = 1.0

[modules.telegram.bot]
enabled = true
token_env = "BUTLER_TELEGRAM_TOKEN"
```

Behavior:
- Connector/module calls Telegram `getUpdates` in a loop.
- No public URL is required.
- New updates are processed and forwarded to ingest/pipeline.

### Prod: Daemon + Webhook URL
Use webhook mode for production.

Config:
```toml
[modules.telegram]
mode = "webhook"
webhook_url = "https://<public-domain>/<telegram-webhook-path>"

[modules.telegram.bot]
enabled = true
token_env = "BUTLER_TELEGRAM_TOKEN"
```

Behavior:
- On startup, Telegram `setWebhook` is called with `webhook_url`.
- The daemon must be reachable on that public HTTPS URL.
- Incoming webhook updates should be handed to the same normalization/processing path as polling updates.

## 5. Interactivity Surface
Supported interactive behavior:
- Send message: `telegram_send_message`
- Reply to a specific message: `telegram_reply_to_message`
- Read updates: `telegram_get_updates`

Lifecycle reactions on inbound messages are supported as best-effort:
- In-progress: `:eye:` (mapped to Telegram eyes emoji)
- Success: `:done:` (mapped to Telegram checkmark emoji)
- Failure: `:space invader:` (mapped to Telegram alien emoji)

If Telegram rejects a reaction with expected `400` unsupported/unavailable cases, processing continues and logs a warning (non-fatal).

## 6. Idempotency and Safety
- Use `update_id` + endpoint identity as the primary dedupe identity.
- Retries must be safe and treated as success when Switchboard returns accepted duplicates.
- Persist polling cursor/high-water mark so restarts can replay safely.
- Use at-least-once delivery to ingest; rely on Switchboard ingest dedupe for exactly-once effect at canonical request layer.
