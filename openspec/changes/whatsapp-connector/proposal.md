## Why

WhatsApp is the user's primary messaging platform alongside Telegram. The butler framework already has comprehensive Telegram coverage (bot connector for interactive messaging, user-client connector for passive inbox ingestion, module for outbound tools). WhatsApp is the missing half — without it, butlers lack awareness of a major slice of the user's daily conversations, relationships, and life events. Prior research (`docs/archive/whatsapp-draft.md`) has already validated the technical approach (whatsmeow Go sidecar) and mapped the data model. The codebase is pre-wired in several places (`HISTORY_STRATEGY`, interactive channel routing, pipeline config). Now is the time to formalize the spec and build it.

## What Changes

- **New WhatsApp module** (`src/butlers/modules/whatsapp.py`): Output-only MCP tools for sending and replying to WhatsApp messages, modeled after `module-telegram`. Dual credential scoping (user/bot) with DB-first resolution. Manages Go sidecar lifecycle.
- **New WhatsApp user-client connector** (`src/butlers/connectors/whatsapp_user_client.py`): Readonly passive ingestion of the user's personal WhatsApp inbox, modeled after `connector-telegram-user-client`. Per-chat buffering, discretion filtering, checkpoint durability, ingest.v1 normalization, Switchboard submission.
- **New Go sidecar binary** (`whatsapp-bridge/`): Wraps whatsmeow for WhatsApp Web multidevice protocol. QR code pairing, session persistence to PostgreSQL, local HTTP/Unix socket interface for the Python module and connector to consume events and send messages.
- **Switchboard routing registration**: Add `whatsapp_user_client` to `SourceChannel`, `whatsapp` to `SourceProvider`, validate channel-provider pair in `_ALLOWED_PROVIDERS_BY_CHANNEL`.
- **Contact identity type**: Register `whatsapp_jid` as a `contact_info.type` for identity resolution and discretion weight lookups.
- **Database migration**: `whatsapp_sessions` table for QR pairing session persistence; `whatsapp_message_inbox` for ingested message storage and deduplication.
- **Messenger butler config**: Enable `[modules.whatsapp]` in `roster/messenger/butler.toml` for outbound tool availability.
- **Docker compose**: Add whatsapp-bridge sidecar service alongside butler daemon.

## Capabilities

### New Capabilities
- `module-whatsapp`: Output-only MCP tools (send/reply) with config-driven conditional registration (following email module's `send_tools` pattern), Go sidecar lifecycle management, and webhook-free operation. Mirrors `module-telegram` pattern.
- `connector-whatsapp-user-client`: Readonly passive ingestion of user's personal WhatsApp inbox. Per-chat buffering, discretion filtering, checkpoint/replay, ingest.v1 normalization. Mirrors `connector-telegram-user-client` pattern.
- `whatsapp-bridge`: Go sidecar binary wrapping whatsmeow. QR pairing ceremony, session persistence, local IPC interface (HTTP or Unix socket), media download, outbound message relay.
- `dashboard-whatsapp-setup`: Dashboard settings page for WhatsApp account linking — QR pairing UX, connection status, session health monitoring. Modeled after Google OAuth account management pattern.

### Modified Capabilities
- `contacts-identity`: Use `whatsapp_jid` as contact_info type convention for WhatsApp identity resolution
- `butler-switchboard`: Register `whatsapp_user_client` source channel, `whatsapp` source provider, channel-provider validation, and fix channel key mismatch in `HISTORY_STRATEGY` and `_INTERACTIVE_ROUTE_CHANNELS`
- `butler-messenger`: Enable WhatsApp module in messenger butler config, add `whatsapp_send_message` to approval-gated tools

## Impact

- **Routing contracts** (`roster/switchboard/tools/routing/contracts.py`): Extend `SourceChannel` and `SourceProvider` literals, add to `_ALLOWED_PROVIDERS_BY_CHANNEL`
- **Pipeline config** (`src/butlers/modules/pipeline.py`): `HISTORY_STRATEGY` has `"whatsapp": "realtime"` but connector uses channel `"whatsapp_user_client"` — must add `"whatsapp_user_client"` key
- **Daemon interactive channels** (`src/butlers/daemon.py`): `_INTERACTIVE_ROUTE_CHANNELS` has `"whatsapp"` — must also add `"whatsapp_user_client"` or verify normalization
- **Contacts module**: Use `whatsapp_jid` as `contact_info.type` convention (open string field, no schema change needed)
- **Dashboard**: New WhatsApp settings section at `/butlers/settings` — QR pairing flow, connection status card, session health badge
- **Dashboard API**: New REST endpoints for WhatsApp pairing lifecycle (`/api/connectors/whatsapp/pair`, `/status`, `/disconnect`)
- **Database**: New Alembic migration for `whatsapp_sessions` and `whatsapp_message_inbox` tables in messenger schema
- **Docker compose**: New service definition for Go bridge sidecar
- **Go toolchain dependency**: Build requires Go 1.21+ for whatsmeow compilation
- **External dependency**: whatsmeow (Go, MIT license, `go.mau.fi/whatsmeow`) — unofficial WhatsApp Web protocol, medium ToS/ban risk (see `docs/archive/whatsapp-draft.md` section 5 for full risk assessment)
- **Security surface**: QR pairing ceremony requires one-time user interaction; session keys stored in PostgreSQL; message plaintext stored in butler DB (trusted host model, identical to Telegram user client)
