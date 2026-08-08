# Mailbox Module

> **Purpose:** Local message queue for inter-butler and external communication, providing durable message storage with status lifecycle tracking.
> **Audience:** Contributors and module developers.
> **Prerequisites:** [Module System](module-system.md).

## Overview

The Mailbox module provides a local message queue for each butler. It stores inbound messages from other butlers, external channels, the scheduler, and system events in a persistent `mailbox` table with status lifecycle tracking.

This is distinct from channel-specific ingestion (Telegram messages, emails) -- the mailbox is the butler's internal inbox for structured messages that need explicit processing and status tracking.

Sources: `src/butlers/modules/mailbox/__init__.py`, `src/butlers/modules/mailbox/migrations/001_create_mailbox_table.py`, and `src/butlers/modules/mailbox/migrations/002_add_actioned_at.py`.

## Configuration

Enable in `butler.toml`:

```toml
[modules.mailbox]
# No configuration options currently. Placeholder for future settings.
```

The module requires no configuration beyond being listed in the modules section.

## Tools Provided

When `[modules.mailbox]` is configured, the module registers these five local MCP tools:

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
| `body` | TEXT | Message content in a fresh mailbox schema; JSONB bodies remain supported through schema-adaptive handling. |
| `priority` | INT | Database default 0; `mailbox_post` supplies a tool default of 2. |
| `status` | TEXT | Current status |
| `metadata` | JSONB | Arbitrary metadata |
| `created_at` | TIMESTAMPTZ | When the message was posted |
| `read_at` | TIMESTAMPTZ | When first read |
| `actioned_at` | TIMESTAMPTZ | When actioned |
| `archived_at` | TIMESTAMPTZ | When archived |
| `updated_at` | TIMESTAMPTZ | Last status change |

### Known Channels

The module recognizes these sender-channel labels: `mcp`, `telegram_bot`, `telegram_user_client`, `email`, `api`, `scheduler`, `system`. Unknown values are accepted with a warning log; these labels do not authenticate a caller or verify sender provenance.

### Status Lifecycle

Valid statuses: `unread`, `read`, `actioned`, `archived`.

- Messages are created as `unread`.
- Reading a message via `mailbox_read` auto-transitions `unread` -> `read`.
- `mailbox_update_status` accepts any valid status directly; it does not enforce an ordered transition graph. For `read` and `archived`, it updates `read_at` and `archived_at` only when those columns exist; for `actioned`, it unconditionally updates `actioned_at`, which `mailbox_001` and `mailbox_002` guarantee.

## Database Tables

The module owns the `mailbox` table in the hosting butler's schema (Alembic branch: `mailbox`).

On a fresh schema, `mailbox_001` creates a TEXT `body` column, a database priority default of 0, and the mailbox lifecycle columns including `actioned_at`. `mailbox_002` only adds `actioned_at` when a legacy table lacks it; its downgrade is a no-op.

The module dynamically introspects the table schema at runtime via `information_schema.columns` to handle TEXT and JSONB body and metadata variants. For a JSONB body it passes `{"text": body}` to the asyncpg JSONB codec; for a TEXT body it writes a string. It sends metadata as a mapping for JSONB columns and serializes it as JSON otherwise.

## Current Scope and Limitations

The module persists caller-provided message content in the hosting butler schema and returns stored message fields through its list, read, and status-update APIs. It does not, by itself, define or guarantee caller authorization, sender-provenance verification, redaction, or retention/purge semantics. Those policies require an explicit cross-cutting design and implementation change.

## Dependencies

None.

## Related Pages

- [Module System](module-system.md)
- [Pipeline Module](pipeline.md) -- pipeline routes messages that may end up in mailboxes
