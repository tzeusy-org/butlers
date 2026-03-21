# Contacts Module

> **Purpose:** Unified identity model and address-book sync for the butler system, providing canonical contact resolution and multi-provider sync into the shared contact registry.
> **Audience:** Contributors and module developers.
> **Prerequisites:** [Module System](module-system.md).

## Overview

The Contacts module serves two deeply related purposes:

1. **Identity model** -- `shared.contacts` and `shared.contact_info` are the canonical identity store for every person or system actor interacting with the butler system. All channels (Telegram, Email, etc.) resolve to a contact record before any routing or delivery decision is made.

2. **Address-book sync** -- A multi-provider sync engine that imports contacts from external sources (Google Contacts, Telegram) into the canonical contact model and backfills the Relationship Butler's CRM schema.

These are intentionally unified: the sync module enriches the same `shared.contacts` records that the identity resolution path reads at runtime.

Source: `src/butlers/modules/contacts/__init__.py`, `src/butlers/modules/contacts/sync.py`, `src/butlers/modules/contacts/backfill.py`.

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

### Credentials

- **Google**: OAuth credentials from the shared credential store (`GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`), refresh token from `shared.entity_info`.
- **Telegram**: API ID, API hash, and user session string resolved from owner `entity_info` entries.

## Tools Provided

| Tool | Description |
|------|-------------|
| `contacts_sync_now` | Trigger an immediate sync cycle (incremental or full) for one or all providers |
| `contacts_sync_status` | Return current sync state: last sync timestamps, cursor age, errors, contact count |
| `contacts_source_list` | List connected source accounts with their status |
| `contacts_source_reconcile` | Trigger re-evaluation of source links for a contact or all contacts |

## Identity Resolution

The identity model powers several critical runtime flows:

### Reverse Lookup

Maps a channel identifier to a contact:

```
(channel_type, channel_value) -> ResolvedContact
```

Implemented by `resolve_contact_by_channel()` in `src/butlers/identity.py`. Returns `contact_id`, `name`, `roles`, and `entity_id`.

### Owner Bootstrap

On every daemon startup, `_ensure_owner_contact()` idempotently creates the owner contact in `shared.contacts` with `roles = ['owner']`. The owner singleton is enforced by a partial unique index.

### Temporary Contacts

When Switchboard receives a message from an unknown sender, it creates a temporary contact with `metadata.needs_disambiguation = true` and notifies the owner once per new unknown sender.

### Contact-Based notify()

When a butler calls `notify(contact_id=..., channel=...)`, the daemon resolves the channel identifier from `shared.contact_info`, using `is_primary` ordering.

## Sync Engine

The provider-agnostic sync engine follows this pattern:

1. **Full sync**: Fetches all contacts from the provider (paginated). Used on first run or when the sync cursor expires.
2. **Incremental sync**: Uses provider-specific delta cursors/tokens to fetch only changes since last sync.
3. **Backfill**: Upserted contacts are mapped to local `shared.contacts` and `shared.contact_info` rows via the `ContactBackfillEngine`.

Google sync tokens expire after approximately 7 days. The module schedules forced full refreshes every 6 days as a safety margin. On `EXPIRED_SYNC_TOKEN`, the module drops the cursor and runs a full sync immediately.

### Telegram Post-Sync Enrichment

After each Telegram sync cycle, `_enrich_telegram_chat_ids()` resolves private chat IDs from Telegram dialogs and upserts `telegram_chat_id` entries in `shared.contact_info` for matched contacts.

## Database Tables

The module owns tables in the hosting butler's schema (Alembic branch: `contacts`):

- `contacts_source_accounts` -- provider, account_id, connection metadata
- `contacts_sync_state` -- sync cursors, timestamps, errors per provider/account
- `contacts_source_links` -- mapping of external contact IDs to local contact IDs with etags

The shared identity tables live in the `shared` schema (owned by core migrations):

- `shared.contacts` -- canonical contact registry
- `shared.contact_info` -- per-channel identifiers (UNIQUE on `(type, value)`)

## Dependencies

None. The contacts module is a leaf module.

## Related Pages

- [Module System](module-system.md)
- [Knowledge Base](knowledge-base.md) -- entity data model that contacts link to via `entity_id`
