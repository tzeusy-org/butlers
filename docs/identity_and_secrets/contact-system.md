# Contact System

> **Purpose:** Document the contacts data model, identity resolution, and multi-provider sync architecture.
> **Audience:** Developers working with identity, routing, or contact management.
> **Prerequisites:** [Schema Topology](../data_and_storage/schema-topology.md).

## Overview

The contact system provides a canonical registry of people and their channel identifiers. It powers identity resolution for Switchboard routing (who sent this message?), outbound notification targeting (how do I reach this person?), and contact sync from external providers (Google Contacts, Telegram).

## Data Model

### `public.contacts`

The canonical contact table. One row per known person or actor.

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID | Primary key |
| `name` | TEXT | Display name |
| `entity_id` | UUID (FK) | Links to `public.entities` for roles and metadata |
| `metadata` | JSONB | Extensible metadata (e.g., `needs_disambiguation`) |

### `public.contact_info`

Per-channel identifiers linked to contacts.

| Column | Type | Description |
|--------|------|-------------|
| `contact_id` | UUID (FK) | Parent contact |
| `type` | TEXT | Channel type (e.g., `"email"`, `"telegram"`, `"telegram_chat_id"`) |
| `value` | TEXT | Channel identifier value |
| `label` | TEXT | Optional label |
| `is_primary` | BOOLEAN | Primary entry for this type |
| `secured` | BOOLEAN | Whether this is a credential entry (masked in API) |

UNIQUE constraint on `(type, value)` ensures no two contacts claim the same identifier.

### `public.entities`

Entity graph nodes that contacts link to. Entities carry roles (e.g., `['owner']`, `['google_account']`) and can have attached `entity_info` key-value pairs.

## Identity Resolution

The core identity resolution function is `resolve_contact_by_channel()` in `src/butlers/identity.py`:

```python
resolved = await resolve_contact_by_channel(pool, "telegram", "123456789")
```

This performs a JOIN across three tables:

```sql
SELECT c.id, c.name, COALESCE(e.roles, '{}'), c.entity_id
FROM public.contact_info ci
JOIN public.contacts c ON c.id = ci.contact_id
LEFT JOIN public.entities e ON e.id = c.entity_id
WHERE ci.type = $1 AND ci.value = $2
```

Returns a `ResolvedContact` dataclass with `contact_id`, `name`, `roles`, and `entity_id`. Returns `None` gracefully if tables don't exist yet (migration pending).

### Identity Preamble

The Switchboard builds a structured identity preamble for each routed message:

- Owner: `[Source: Owner (contact_id: <uuid>, entity_id: <uuid>), via telegram]`
- Known contact: `[Source: Alice (contact_id: <uuid>, entity_id: <uuid>), via telegram]`
- Unknown sender: `[Source: Unknown sender (contact_id: <uuid>), via telegram -- pending disambiguation]`

### Temporary Contacts

When an unknown sender is detected, `create_temp_contact()` creates:
1. A `public.entities` entry with `metadata.unidentified = true`.
2. A `public.contacts` entry with `metadata.needs_disambiguation = true`.
3. A `public.contact_info` entry linking the channel identifier.

This ensures every message has an anchored identity, even if disambiguation happens later.

## Contacts Module

The contacts module (`src/butlers/modules/contacts/__init__.py`) orchestrates sync from external providers.

### Configuration

```toml
[modules.contacts]
providers = [{type = "google"}, {type = "telegram"}]
include_other_contacts = false

[modules.contacts.sync]
enabled = true
run_on_startup = true
interval_minutes = 15
full_sync_interval_days = 6
```

Multi-provider and multi-account configurations are supported. Multiple Google accounts require distinct `account` fields:

```toml
providers = [
    {type = "google", account = "personal@gmail.com"},
    {type = "google", account = "work@gmail.com"},
    {type = "telegram"},
]
```

### Supported Providers

- **Google** (`GoogleContactsProvider`): Uses the Google People API with OAuth refresh tokens. Supports incremental sync via sync tokens and full sync via paginated connections listing.
- **Telegram** (`TelegramContactsProvider`): Uses the Telethon MTProto client to fetch the user's Telegram contacts. Post-sync enrichment resolves private chat IDs.

### Sync Architecture

Each provider has:
1. **Provider** -- Fetches contacts from the external API.
2. **State Store** -- Persists sync cursors and timestamps in the butler's `state` table.
3. **Backfill Engine** -- Applies fetched contacts to the database (upsert logic).
4. **Sync Engine** -- Coordinates incremental and full sync cycles.
5. **Sync Runtime** -- Background asyncio task that runs sync on a schedule.

### MCP Tools

The module registers four tools:

- **`contacts_sync_now`** -- Trigger immediate sync (incremental or full).
- **`contacts_sync_status`** -- Return current sync state (cursors, timestamps, errors).
- **`contacts_source_list`** -- List connected source accounts with status.
- **`contacts_source_reconcile`** -- Trigger re-evaluation of source links.

## Verification

To confirm the contact system's data model, identity resolution, and sync machinery are operating as described:

```bash
# 1. Verify the three shared identity tables exist in the public schema
psql -h localhost -U butlers -d butlers -c \
  "SELECT table_name FROM information_schema.tables
   WHERE table_schema = 'public'
   AND table_name IN ('contacts', 'contact_info', 'entities')
   ORDER BY table_name;"
# Expected: all three tables present

# 2. Confirm the UNIQUE constraint on contact_info(type, value)
psql -h localhost -U butlers -d butlers -c \
  "SELECT indexname, indexdef FROM pg_indexes
   WHERE schemaname = 'public' AND tablename = 'contact_info'
   AND indexdef ILIKE '%type%value%';"
# Expected: unique index covering (type, value) to prevent duplicate channel identifiers

# 3. Test identity resolution for the owner's Telegram chat ID
# Replace 'YOUR_CHAT_ID' with the actual numeric Telegram chat ID
psql -h localhost -U butlers -d butlers -c \
  "SELECT c.id, c.name, e.roles
   FROM public.contact_info ci
   JOIN public.contacts c ON c.id = ci.contact_id
   LEFT JOIN public.entities e ON e.id = c.entity_id
   WHERE ci.type = 'telegram_chat_id' AND ci.value = 'YOUR_CHAT_ID';"
# Expected: one row with the owner's contact_id and roles including 'owner'

# 4. Verify sync state is persisted for each configured provider
psql -h localhost -U butlers -d butlers -c \
  "SELECT key, updated_at FROM general.state
   WHERE key LIKE 'contacts_sync%'
   ORDER BY key;"
# Expected: sync cursors for google and/or telegram providers updated within the past 15 minutes

# 5. Confirm temporary contacts are created for unknown senders
psql -h localhost -U butlers -d butlers -c \
  "SELECT c.id, c.name, c.metadata->>'needs_disambiguation' AS pending
   FROM public.contacts c
   WHERE c.metadata->>'needs_disambiguation' = 'true'
   LIMIT 5;"
# Expected: rows for any messages received from unidentified senders

# 6. Verify entity metadata.unidentified flag on temp contacts
psql -h localhost -U butlers -d butlers -c \
  "SELECT e.id, e.canonical_name, e.metadata->>'unidentified' AS unidentified
   FROM public.entities e
   WHERE e.metadata->>'unidentified' = 'true'
   LIMIT 5;"
# Expected: one entity per temporary contact with unidentified = 'true'
```

## Related Pages

- [Owner Identity](owner-identity.md) -- Owner bootstrap and credential storage
- [OAuth Flows](oauth-flows.md) -- Google OAuth for contacts access
- [Schema Topology](../data_and_storage/schema-topology.md) -- Where shared tables live
