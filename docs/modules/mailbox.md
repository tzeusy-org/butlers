# Mailbox Module

> **Purpose:** Local message queue for inter-butler and external communication, providing durable message storage with status lifecycle tracking.
> **Audience:** Contributors and module developers.
> **Prerequisites:** [Module System](module-system.md).

## Overview

The Mailbox module provides a local message queue for each butler. It stores inbound messages from other butlers, external channels, the scheduler, and system events in a persistent `mailbox` table with status lifecycle tracking.

This is distinct from channel-specific ingestion (Telegram messages, emails) -- the mailbox is the butler's internal inbox for structured messages that need explicit processing and status tracking.

Source: `src/butlers/modules/mailbox/__init__.py`.

## Configuration

Enable in `butler.toml`:

```toml
[modules.mailbox]
# No configuration options currently. Placeholder for future settings.
```

The module requires no configuration beyond being listed in the modules section.

## Tools Provided

| Tool | Description |
|------|-------------|
| `mailbox_post` | Insert a new message into the butler's mailbox. Returns the message UUID. |
| `mailbox_list` | Query messages with optional status and sender filters, ordered by `created_at DESC`. |
| `mailbox_read` | Fetch full message by ID. Automatically marks `unread` messages as `read`. |
| `mailbox_update_status` | Change a message's status (sets relevant timestamp columns). |
| `mailbox_stats` | Get aggregate message counts grouped by status. |

## Message Model

Each mailbox message has these fields:

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUID | Auto-generated message identifier |
| `sender` | TEXT | Identity of the sender (butler name, user, system) |
| `sender_channel` | TEXT | Channel the message arrived on |
| `subject` | TEXT | Optional subject line |
| `body` | TEXT/JSONB | Message content |
| `priority` | INT | Priority level (default 2) |
| `status` | TEXT | Current status |
| `metadata` | JSONB | Arbitrary metadata |
| `created_at` | TIMESTAMPTZ | When the message was posted |
| `read_at` | TIMESTAMPTZ | When first read |
| `actioned_at` | TIMESTAMPTZ | When actioned |
| `archived_at` | TIMESTAMPTZ | When archived |
| `updated_at` | TIMESTAMPTZ | Last status change |

### Known Channels

The module recognizes these sender channels: `mcp`, `telegram_bot`, `telegram_user_client`, `email`, `api`, `scheduler`, `system`. Unknown channels are accepted with a warning log.

### Status Lifecycle

Valid statuses: `unread`, `read`, `actioned`, `archived`.

- Messages are created as `unread`.
- Reading a message via `mailbox_read` auto-transitions `unread` -> `read`.
- `mailbox_update_status` handles explicit transitions and sets the appropriate timestamp columns (`read_at`, `actioned_at`, `archived_at`).

## Database Tables

The module owns the `mailbox` table in the hosting butler's schema (Alembic branch: `mailbox`).

The module dynamically introspects the table schema at runtime via `information_schema.columns` to handle both legacy (TEXT body) and current (JSONB body) column types, ensuring backward compatibility across migration states.

## Dependencies

None.

## Related Pages

- [Module System](module-system.md)
- [Pipeline Module](pipeline.md) -- pipeline routes messages that may end up in mailboxes
