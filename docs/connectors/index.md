# Connectors

> **Scope:** External transport adapters that ingest messages into the system.
> **Belongs here:** Connector architecture overview, per-connector profiles (setup, config, ingestion flow, cursors).
> **Does NOT belong here:** Switchboard routing logic (see [Architecture](../architecture/routing.md)), module internals.

- [Overview](overview.md) — connector architecture, responsibilities, what connectors must/must not do

### Connector Profiles
- [Telegram Bot](telegram-bot.md) — Telegram bot connector
- [Telegram User Client](telegram-user-client.md) — Telegram MTProto user client
- [Gmail](gmail.md) — Gmail IMAP/API connector
- [Gmail Ingestion Policy](gmail-ingestion-policy.md) — email ingestion filtering rules
- [Heartbeat](heartbeat.md) — connector health monitoring
- [Live Listener](live-listener.md) — audio live listener connector
- [Attachment Handling](attachment-handling.md) — file/media attachment processing
- [Metrics](metrics.md) — connector metrics and statistics
