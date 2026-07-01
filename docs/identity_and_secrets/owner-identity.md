# Owner Identity

> **Purpose:** Explain how the owner contact is bootstrapped, how identity fields are configured, and how secured credentials are managed.
> **Audience:** Users setting up Butlers for the first time, developers extending identity resolution.
> **Prerequisites:** [Schema Topology](../data_and_storage/schema-topology.md), [Contact System](contact-system.md).

## Overview

![Owner Identity Bootstrap](./owner-identity-bootstrap.svg)

When Butlers starts for the first time, it seeds an **Owner contact** in the `public.contacts` table with the `owner` role on its linked entity. This contact has no channel identifiers initially -- the user must configure their identity through the dashboard so butlers can recognize them across channels (Telegram, email) and prevent duplicate contacts during sync.

## Bootstrap Flow

On first startup, the daemon:

1. Checks `public.entities` for an entity with `'owner' = ANY(roles)`.
2. If none exists, creates an owner entity in `public.entities` with `roles = ['owner']`.
3. Creates a corresponding `public.contacts` row linked via `entity_id`.
4. The owner contact starts with no `contact_info` entries.

This ensures exactly one owner entity exists across the system. Subsequent butler startups detect the existing owner and skip creation.

## Configuring Identity

Navigate to the owner contact's detail page in the dashboard (linked from the setup banner on the contacts page). Use the "Add contact info" form to add:

### Standard Identity Fields

- **Email** -- Your primary email address. Used for email identity resolution and contact sync deduplication.
- **Telegram handle** -- Your `@username`. Used for Telegram contact matching.
- **Telegram chat ID** -- Numeric ID for direct message delivery. Send `/start` to `@userinfobot` on Telegram to find yours.

### Secured Credentials

For butlers that act on your behalf (sending emails, connecting to Telegram as your user account):

- **Email password** -- App password for SMTP/IMAP access. Stored as `secured=true` in `public.entity_info`.
- **Telegram API ID** -- From [my.telegram.org](https://my.telegram.org). Required for user-client (MTProto) connections.
- **Telegram API hash** -- From [my.telegram.org](https://my.telegram.org). Paired with the API ID.
- **Telegram user session** -- MTProto session string for the Telegram user-client connector.

Secured entries are stored in PostgreSQL with `secured=true` and masked in the dashboard API. List responses exclude raw values; a "Reveal" button provides on-demand access.

## Setup Banner

A one-time setup banner appears on the contacts page when identity fields are missing. It links to the owner contact detail page where all fields can be managed. The banner checks for the presence of key `contact_info` entries and disappears once the essential fields are configured.

## Identity Resolution

The owner identity is used in several critical paths:

### Switchboard Routing

When a message arrives (e.g., from Telegram), the Switchboard calls `resolve_contact_by_channel(pool, "telegram", chat_id)` to identify the sender. If the sender matches the owner's contact_info, the identity preamble includes `[Source: Owner (contact_id: ..., entity_id: ...), via telegram]`. This allows butler prompts to understand they are interacting with the owner.

### Contact Sync Deduplication

When the contacts module syncs from Google or Telegram, it matches incoming contacts against existing `contact_info` entries. The owner's email and Telegram handle prevent the sync engine from creating a duplicate contact for the owner.

### Credential Resolution

Module startup code resolves credentials from the owner entity's `entity_info`:

```python
from butlers.credential_store import resolve_owner_entity_info

api_id = await resolve_owner_entity_info(pool, "telegram_api_id")
api_hash = await resolve_owner_entity_info(pool, "telegram_api_hash")
session = await resolve_owner_entity_info(pool, "telegram_user_session")
```

The function queries `public.entities` for the owner, then fetches the matching `entity_info` row, preferring primary entries.

## Security Model

Since Butlers is a user-federated platform (each user owns their instance), the security model is straightforward:

- Credentials are stored in PostgreSQL in the `public.entity_info` table.
- The user controls the database directly.
- API-level masking prevents accidental exposure in dashboard responses.
- No encryption at rest (the user owns the infrastructure).

## Entity Structure

```
public.entities
├── id: UUID
├── canonical_name: "Owner"
├── entity_type: "person"
└── roles: ["owner"]

public.entity_info (for the owner entity)
├── (entity_id, "email") -> "user@example.com"
├── (entity_id, "telegram") -> "@username"
├── (entity_id, "telegram_chat_id") -> "123456789"
├── (entity_id, "telegram_api_id") -> "12345" (secured)
├── (entity_id, "telegram_api_hash") -> "abc..." (secured)
├── (entity_id, "google_oauth_refresh") -> "1//..." (secured)
└── ...
```

## Verification

To confirm the owner entity bootstrap, identity fields, and credential resolution are operating as described:

```bash
# 1. Verify exactly one owner entity exists in public.entities
psql -h localhost -U butlers -d butlers -c \
  "SELECT id, canonical_name, entity_type, roles
   FROM public.entities
   WHERE 'owner' = ANY(roles);"
# Expected: exactly one row with roles including 'owner' and entity_type = 'person'

# 2. Confirm the owner contact is linked to the owner entity
psql -h localhost -U butlers -d butlers -c \
  "SELECT c.id, c.name, c.entity_id
   FROM public.contacts c
   JOIN public.entities e ON e.id = c.entity_id
   WHERE 'owner' = ANY(e.roles);"
# Expected: one row -- the owner contact linked to the owner entity

# 3. Verify identity fields are configured (email and Telegram chat ID are critical)
psql -h localhost -U butlers -d butlers -c \
  "SELECT ci.type, ci.is_primary, ci.secured,
          CASE WHEN ci.secured THEN '[REDACTED]' ELSE ci.value END AS display_value
   FROM public.contact_info ci
   JOIN public.contacts c ON c.id = ci.contact_id
   JOIN public.entities e ON e.id = c.entity_id
   WHERE 'owner' = ANY(e.roles)
   ORDER BY ci.type;"
# Expected: at minimum 'email' and 'telegram_chat_id' entries;
# secured entries (api_id, api_hash, telegram_user_session) show [REDACTED]

# 4. Confirm secured credentials are stored in entity_info with secured=true
psql -h localhost -U butlers -d butlers -c \
  "SELECT ei.type, ei.secured
   FROM public.entity_info ei
   JOIN public.entities e ON e.id = ei.entity_id
   WHERE 'owner' = ANY(e.roles)
   ORDER BY ei.type;"
# Expected: telegram_api_id, telegram_api_hash, telegram_user_session all have secured = true

# 5. Verify credential resolution works for the owner's entity_info entries
python3 -c "
import asyncio
# This illustrates the resolve_owner_entity_info call structure
# Run in an async context with a live DB connection
print('resolve_owner_entity_info(pool, \"telegram_api_id\") should return the API ID')
print('Returns None gracefully if not yet configured')
"

# 6. Confirm setup banner disappears once essential identity fields are present
# Check for the minimum required fields (email + telegram_chat_id)
psql -h localhost -U butlers -d butlers -c \
  "SELECT COUNT(*) AS required_fields_present
   FROM public.contact_info ci
   JOIN public.contacts c ON c.id = ci.contact_id
   JOIN public.entities e ON e.id = c.entity_id
   WHERE 'owner' = ANY(e.roles)
   AND ci.type IN ('email', 'telegram_chat_id');"
# Expected: 2 -- both required fields configured (banner should not show in dashboard)
```

## Related Pages

- [Contact System](contact-system.md) -- Full contact model
- [Credential Store](../data_and_storage/credential-store.md) -- DB-first secret resolution
- [OAuth Flows](oauth-flows.md) -- Google OAuth credential storage
