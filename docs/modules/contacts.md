# Contacts Module

> **Purpose:** Multi-provider address-book sync into the shared identity registry.
> **Audience:** Contributors and module developers.
> **Prerequisites:** [Module System](module-system.md), [Identity Model](../concepts/identity-model.md).

## Overview

The Contacts module imports people from external address books (Google Contacts, Telegram) and
merges them into the shared identity registry: `public.entities` anchors each person, and channel
identifiers become `relationship.entity_facts` triples (`has-email`, `has-phone`, `has-handle`).
It does not own identity resolution — runtime sender lookup, owner bootstrap, temporary entities,
and `notify()` targeting are core behavior described in
[Identity Model](../concepts/identity-model.md).

Source: `src/butlers/modules/contacts/__init__.py` (`ContactsModule`, `ContactsConfig`),
`sync.py` (`ContactsSyncEngine`), `backfill.py` (`ContactBackfillEngine`).

## Configuration

Enable in `butler.toml`:

```toml
[modules.contacts]
# Single provider (legacy):
provider = "google"

# Or multi-provider:
providers = [
    { type = "google" },
    { type = "telegram" },
]

# Multi-account Google:
# providers = [
#     { type = "google", account = "personal@gmail.com" },
#     { type = "google", account = "work@company.com" },
# ]

include_other_contacts = false

[modules.contacts.sync]
enabled = true
run_on_startup = true
interval_minutes = 15
full_sync_interval_days = 6
```

Exactly one of `provider` (single) or `providers` (multi) must be specified. Multiple entries of the same type require distinct `account` fields.

## Tools Provided

The module registers the `contacts_sync_now`, `contacts_sync_status`, `contacts_source_list`, and
`contacts_source_reconcile` MCP tools (`ContactsModule.register_tools`).

## Multi-Provider Sync

Each configured provider (`GoogleContactsProvider`, `TelegramContactsProvider`) gets its own
state store, sync engine, and background runtime task, keyed by provider type plus `account`.

1. **Full sync** fetches every contact (paginated). It runs on first start, when a cursor is missing,
   and every `full_sync_interval_days` as a safety margin: Google sync tokens expire after about
   7 days, so the default of 6 refreshes before expiry. On `EXPIRED_SYNC_TOKEN` the module drops
   the cursor and runs a full sync immediately.
2. **Incremental sync** uses the provider's delta cursor to fetch only changes.
3. **Backfill** (`ContactBackfillEngine`) matches each canonical contact to an entity — existing
   `contacts_source_links` row, exact email, phone, then a conservative name match — and upserts the
   entity and its channel facts. Field provenance is recorded in `entities.metadata` under
   `sources.contacts.<provider>.<field>`.

Telegram private chat IDs are routing identifiers with no triple predicate, so sync does not
persist them.

## Database Tables

The module owns tables in the hosting butler's schema (Alembic branch `contacts`):

- `contacts_sync_state` -- sync cursors, timestamps, and errors per provider/account
- `contacts_source_links` -- external contact ID to local `entity_id` mapping, with etags

Shared identity tables (`public.entities`, `relationship.entity_facts`) belong to core and the
Relationship butler respectively.

## Rollout

Enabled butlers are those with a `[modules.contacts]` table in `roster/*/butler.toml`. The
Switchboard (routing plane) and Messenger (delivery plane) intentionally omit it.

### Credentials

- **Google**: `GOOGLE_OAUTH_CLIENT_ID` and `GOOGLE_OAUTH_CLIENT_SECRET` from the shared credential
  store; the per-account refresh token is an owner `entity_info` entry of type
  `google_oauth_refresh` (`src/butlers/google_credentials.py`), written by the OAuth flow — see
  [OAuth Flows](../identity_and_secrets/oauth-flows.md).
- **Telegram**: API ID, API hash, and user session string from owner `entity_info` entries.

## Dependencies

None. The contacts module is a leaf module.

## Related Pages

- [Module System](module-system.md)
- [Identity Model](../concepts/identity-model.md) -- entity anchor, channel facts, resolution
