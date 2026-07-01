# Telegram User Client Connector

> **Purpose:** Profile the Telegram user-client connector -- live MTProto ingestion, per-chat buffering, discretion filtering, deployment, and privacy requirements.
> **Audience:** Developers deploying or operating the Telegram user-client connector.
> **Prerequisites:** [Connector Architecture Overview](overview.md), [Connector Interface Contract](../api_and_protocols/ingestion-envelope.md).

## Overview

The Telegram user-client connector (`src/butlers/connectors/telegram_user_client.py`) runs a Telegram **user client** (not a bot) using Telethon (MTProto) to continuously ingest message activity visible to a user's personal Telegram account. Its primary goal is to keep butler context current with life events, commitments, relationships, and facts that appear in Telegram conversations.

This connector is **live-stream first**: it maintains a persistent Telegram session and subscribes to real-time message events rather than polling. It is transport-only and ingestion-only -- the Switchboard owns classification, routing, and request-context assignment.

This is a **v2-only feature** with privacy-sensitive characteristics. Explicit user consent and proper credential management are required before deployment.

## Architecture

The connector uses a per-chat buffering model rather than submitting messages individually:

1. **Event subscription** -- Registers a Telethon `NewMessage` handler for all incoming messages.
2. **Per-chat buffering** -- Messages accumulate in `ChatBuffer` instances keyed by chat ID.
3. **Flush scanner** -- A background task periodically checks buffers and flushes chats that exceed the configured interval (`TELEGRAM_USER_FLUSH_INTERVAL_S`, default 600s) or buffer cap (`TELEGRAM_USER_BUFFER_MAX_MESSAGES`, default 200).
4. **History fetch** -- On flush, the connector fetches recent history from the chat to fill any gaps.
5. **Discretion evaluation** -- An LLM-based FORWARD/IGNORE filter determines which flushed messages are worth ingesting.
6. **Normalization and submission** -- Approved messages are normalized to `ingest.v1` and submitted to the Switchboard.

## Request Context Mapping

| Envelope field | Telegram source |
|---|---|
| `source.channel` | `telegram` |
| `source.provider` | `telegram` |
| `source.endpoint_identity` | Auto-resolved via `get_me()` (e.g., `telegram:user:@username`) |
| `event.external_event_id` | Telegram message/update event ID |
| `event.external_thread_id` | Chat ID / thread ID |
| `event.observed_at` | Connector-observed timestamp (RFC 3339) |
| `sender.identity` | Telegram sender ID for the message author |
| `payload.raw` | Full provider event payload |
| `payload.normalized_text` | Extracted plain text |
| `control.idempotency_key` | Fallback key when event ID is unavailable |

## Ingestion Scope

The user client can ingest messages from:

- Direct messages
- Group chats and supergroups
- Channels and threaded discussions visible to the account
- Edits, deletes, and metadata updates where relevant

The connector evaluates two ingestion policy scopes:

1. **Connector scope** (`connector:telegram-user-client:<endpoint>`) -- pre-ingest block/pass-through.
2. **Global scope** (`global`) -- skip/metadata-only/route-to/low-priority-queue.

## Discretion Layer

The connector includes an LLM-based discretion layer that filters messages before submission:

- **Contact weight resolver** -- Maps sender to contact role to weight tier.
- **Discretion evaluator** -- Per-chat evaluator with configurable context window.
- **Discretion dispatcher** -- Routes LLM calls with a hard timeout.

Messages that receive an `IGNORE` verdict are not submitted to the Switchboard.

## Credentials

Telegram user-client credentials are resolved **exclusively from owner entity_info** in the database, not from environment variables:

| Credential | Storage |
|---|---|
| `telegram_api_id` | Owner entity_info (type: `telegram_api_id`) |
| `telegram_api_hash` | Owner entity_info (type: `telegram_api_hash`) |
| `telegram_user_session` | Owner entity_info (type: `telegram_user_session`) |

### Obtaining Credentials

1. Visit https://my.telegram.org and log in with your phone number.
2. Navigate to "API development tools" and create a new application.
3. Record the `api_id` (numeric) and `api_hash` (string).
4. Generate a session string using Telethon:

```python
from telethon.sync import TelegramClient
from telethon.sessions import StringSession

api_id = 12345
api_hash = "your-api-hash"

with TelegramClient(StringSession(), api_id, api_hash) as client:
    print("Session string:", client.session.save())
```

Store all credentials in a secret manager. Never commit to version control.

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `SWITCHBOARD_MCP_URL` | Yes | SSE endpoint for Switchboard MCP server |
| `CONNECTOR_PROVIDER` | Yes (default: `telegram`) | Provider name |
| `CONNECTOR_CHANNEL` | Yes (default: `telegram_user_client`) | Channel name |
| `CONNECTOR_MAX_INFLIGHT` | No (default: 8) | Max concurrent ingest submissions |
| `CONNECTOR_BACKFILL_WINDOW_H` | No | Bounded startup replay window (hours) |
| `CONNECTOR_BUTLER_DB_NAME` | No | Local butler DB for per-butler overrides |
| `BUTLER_SHARED_DB_NAME` | No (default: `butlers`) | Shared credential DB |
| `TELEGRAM_USER_FLUSH_INTERVAL_S` | No (default: 600) | Seconds between per-chat flushes |
| `TELEGRAM_USER_HISTORY_MAX_MESSAGES` | No (default: 50) | History fetch limit per flush |
| `TELEGRAM_USER_HISTORY_TIME_WINDOW_M` | No (default: 30) | History lookback window (minutes) |
| `TELEGRAM_USER_BUFFER_MAX_MESSAGES` | No (default: 200) | Per-chat buffer cap before force-flush |
| `TELEGRAM_USER_DISCRETION_WINDOW_SIZE` | No (default: 10) | Discretion context window size |
| `TELEGRAM_USER_DISCRETION_WINDOW_SECONDS` | No (default: 300) | Discretion context window age cap |
| `TELEGRAM_USER_DISCRETION_WEIGHT_BYPASS` | No (default: 1.0) | Weight threshold to skip LLM |
| `TELEGRAM_USER_DISCRETION_WEIGHT_FAIL_OPEN` | No (default: 0.5) | Weight threshold for fail-open |

## Deployment

### Prerequisites

Install Telethon support:

```bash
uv pip install telethon>=1.36.0
# Or install with optional dependencies
uv sync --extra connectors
```

### Run Order

1. Start the Switchboard API service.
2. Start the Telegram user-client connector daemon.
3. Verify accepted ingest events and connector lag metrics.

### Systemd Service

```ini
[Unit]
Description=Telegram User-Client Connector
After=network.target switchboard.service
Requires=switchboard.service

[Service]
Type=simple
User=butlers
Group=butlers
WorkingDirectory=/opt/butlers
EnvironmentFile=/etc/butlers/connectors/telegram-user-client.env
ExecStart=/opt/butlers/.venv/bin/telegram-user-client-connector
Restart=always
RestartSec=10

NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict

[Install]
WantedBy=multi-user.target
```

### Docker

```yaml
services:
  telegram-user-client-connector:
    image: butlers:latest
    command: telegram-user-client-connector
    environment:
      SWITCHBOARD_MCP_URL: http://switchboard:41100/sse
      CONNECTOR_PROVIDER: telegram
      CONNECTOR_CHANNEL: telegram_user_client
      CONNECTOR_MAX_INFLIGHT: "8"
      CONNECTOR_BACKFILL_WINDOW_H: "24"
    depends_on:
      - switchboard
    restart: unless-stopped
```

## Idempotency and Resume

- Dedupe identity: stable Telegram event/message ID + auto-resolved endpoint identity.
- Duplicate ingest responses are success, not failures.
- Per-dialog ordering is preserved where practical; cross-dialog global ordering is not guaranteed.
- Checkpoint (`last_message_id`) is DB-backed via `cursor_store`.
- Checkpoint advances only after ingest acceptance.

## Privacy and Consent

Because this connector ingests personal account traffic, strict safeguards apply:

- **Explicit consent** before enabling account-wide ingestion.
- **Scope disclosure** -- clear documentation of which chats/types are included or excluded.
- **Allow/deny lists** for chats and senders (target-state feature, not yet implemented).
- **Content redaction** for sensitive content classes before ingest (target-state).
- **Retention limits** aligned with memory and ingestion policy.
- **Audit trail** of connector start/stop/config changes.

### Credential Rotation

- **Session strings:** Every 90 days (production) or immediately after suspected compromise.
- **API credentials:** Follow Telegram best practices.
- **Switchboard tokens:** Follow platform token rotation policy.

## Monitoring

Key metrics to monitor:

| Metric | Description |
|---|---|
| Connector uptime | Should remain connected without frequent disconnects |
| Message ingest rate | Compare with expected account activity |
| Ingest acceptance rate | Should be >99% (excluding duplicates) |
| Checkpoint lag | Time between last processed message and current time |
| Error rate | Failed ingest submissions or normalization errors |

### Troubleshooting

- **Telethon not found:** Install with `uv pip install telethon>=1.36.0`
- **Session expired:** Generate new session string, update owner entity_info, restart connector.
- **Duplicate messages:** Normal during restarts (checkpoint replay), backfill, and retries. Switchboard handles deduplication.

## Verification

To confirm the Telegram user-client connector is operating as described:

```bash
# 1. Connector appears in the registry as a user-client (not a bot)
psql -h localhost -U butlers -d butlers -c \
  "SELECT connector_type, endpoint_identity, state, last_heartbeat_at
   FROM switchboard.connector_registry
   WHERE connector_type='telegram_user_client';"
# Expected: endpoint_identity is 'telegram:user:@<username>' (user, not bot);
#           state=online; last_heartbeat_at < 2 minutes ago

# 2. Per-chat buffers flush periodically (messages grouped by chat)
psql -h localhost -U butlers -d butlers -c \
  "SELECT source_thread_identity AS chat_id, COUNT(*) AS messages, MAX(received_at) AS last_seen
   FROM switchboard.ingestion_events
   WHERE source_channel='telegram' AND source_provider='telegram'
     AND source_endpoint_identity LIKE 'telegram:user:%'
   GROUP BY source_thread_identity
   ORDER BY last_seen DESC LIMIT 10;"
# Expected: messages grouped by chat_id; multiple messages per chat in single submissions
#           (reflecting the per-chat buffer flush model rather than one-by-one ingestion)

# 3. Discretion filter is active (some messages are intentionally NOT submitted)
curl -s http://localhost:9090/api/v1/query?query=connector_user_client_discretion_ignored_total \
  | python3 -m json.tool | grep value
# Expected: non-zero counter for messages that failed the FORWARD/IGNORE filter

# 4. Session continuity: checkpoint survives connector restart
# Stop the connector, restart it, then verify it resumes from where it left off
psql -h localhost -U butlers -d butlers -c \
  "SELECT connector_type, endpoint_identity, cursor_value, updated_at
   FROM switchboard.connector_registry WHERE connector_type='telegram_user_client';"
# Expected: cursor_value is preserved across restarts; no messages skipped or double-processed

# 5. Session expired detection is logged clearly
grep -i "session.*expired\|FloodWait\|AuthKey" /var/log/butlers/telegram-user-client.log 2>/dev/null | tail -5
# Expected: no session-expired errors in steady state; if present, remediation instructions are clear
```

## Related Pages

- [Connector Architecture Overview](overview.md)
- [Connector Interface Contract](../api_and_protocols/ingestion-envelope.md) -- Full `ingest.v1` envelope spec
- [Telegram Bot Connector](telegram-bot.md) -- Bot API connector (separate from user client)
- [Heartbeat Protocol](heartbeat.md) -- Liveness reporting
- [Metrics](metrics.md) -- Prometheus instrumentation
