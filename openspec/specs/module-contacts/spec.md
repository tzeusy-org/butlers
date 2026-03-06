# Contacts Module

## Purpose

The Contacts module synchronizes external address-book sources into a canonical contact model and backfills the Relationship Butler's CRM schema with provider-agnostic upsert logic, provenance tracking, and conflict-aware field resolution. The module supports multiple concurrent providers (e.g., Google Contacts and Telegram) through a pluggable `ContactsProvider` interface.

## ADDED Requirements

### Requirement: ContactsModule Configuration and Scaffold

The module is configured under `[modules.contacts]` in `butler.toml` with `providers` (list of provider configs, required), `include_other_contacts` (bool, default false), and `sync` sub-config (`enabled`, `run_on_startup`, `interval_minutes` default 15, `full_sync_interval_days` default 6).

Each entry in `providers` is a table with `type` (required, e.g. `"google"`, `"telegram"`), plus provider-specific keys. The legacy single-provider `provider` string field is accepted for backward compatibility and interpreted as `providers = [{type = "<value>"}]`.

Example multi-provider config:
```toml
[modules.contacts]
include_other_contacts = false

[[modules.contacts.providers]]
type = "google"

[[modules.contacts.providers]]
type = "telegram"
```

#### Scenario: Valid multi-provider config

- **WHEN** `ContactsConfig` is provided with `providers = [{type = "google"}, {type = "telegram"}]`
- **THEN** each provider type is normalized to lowercase and trimmed
- **AND** sync defaults are applied (15-minute incremental, 6-day full sync)
- **AND** a `ContactsProvider` instance is created for each entry

#### Scenario: Legacy single-provider config

- **WHEN** `ContactsConfig` is provided with `provider = "google"` (no `providers` list)
- **THEN** it is treated as `providers = [{type = "google"}]`
- **AND** behavior is identical to the multi-provider form

#### Scenario: Unsupported provider at startup

- **WHEN** a provider entry has a `type` not in the supported set (`{"google", "telegram"}`)
- **THEN** startup raises a `RuntimeError` with a descriptive message listing supported providers

#### Scenario: Duplicate provider types

- **WHEN** `providers` contains two entries with the same `type`
- **THEN** startup raises a `RuntimeError` indicating duplicate provider types are not allowed

#### Scenario: Sync disabled

- **WHEN** `sync.enabled = false`
- **THEN** the module skips runtime startup and logs a message
- **AND** MCP tools return a clear error indicating the sync runtime is not running

### Requirement: Google OAuth Credential Resolution

Credentials are resolved from the DB-backed credential store (`butler_secrets`), not environment variables.

#### Scenario: Credentials resolved from credential store

- **WHEN** on_startup is called with a credential store
- **THEN** `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, `GOOGLE_REFRESH_TOKEN` are resolved from DB
- **AND** if any are missing, a `RuntimeError` is raised directing the user to the dashboard OAuth flow

#### Scenario: No credential store provided

- **WHEN** on_startup is called without a credential store
- **THEN** a `RuntimeError` is raised (env fallback is not supported for contacts)

### Requirement: Provider-Agnostic Sync Contract

The `ContactsProvider` abstract class defines the sync interface: `name()`, `validate_credentials()`, `full_sync()`, `incremental_sync()`, `list_groups()`. Providers return `ContactBatch` objects with contacts, groups, page tokens, sync cursors, and checkpoints.

#### Scenario: Full sync execution

- **WHEN** a full sync is requested
- **THEN** the provider fetches all contacts with `requestSyncToken=true`
- **AND** pagination is followed until `next_page_token` is exhausted
- **AND** the returned `next_sync_cursor` is persisted for future incremental syncs

#### Scenario: Incremental sync execution

- **WHEN** an incremental sync is requested with a valid cursor
- **THEN** only changed contacts since the last cursor are fetched

#### Scenario: Expired sync token recovery

- **WHEN** the Google sync token is expired (`EXPIRED_SYNC_TOKEN` error)
- **THEN** the cursor is dropped and a full sync is triggered immediately

### Requirement: Canonical Contact Model

The `CanonicalContact` Pydantic model normalizes provider payloads with fields: `external_id`, `etag`, `display_name`, `first_name`, `last_name`, `middle_name`, `nickname`, `emails[]` (`ContactEmail`), `phones[]` (`ContactPhone`), `addresses[]` (`ContactAddress`), `organizations[]` (`ContactOrganization`), `birthdays[]`/`anniversaries[]` (`ContactDate`), `urls[]` (`ContactUrl`), `usernames[]` (`ContactUsername`), `photos[]` (`ContactPhoto`), `group_memberships[]`, `deleted` (tombstone flag), `raw` (provider payload snapshot).

#### Scenario: Google payload normalization

- **WHEN** a Google People API response is received
- **THEN** it is parsed into `CanonicalContact` instances with all fields mapped
- **AND** `external_id` is the Google resource name (e.g., `people/c12345`)
- **AND** `etag` is captured for optimistic concurrency
- **AND** `deleted = true` for tombstone contacts

### Requirement: TelegramContactsProvider

The `TelegramContactsProvider` implements the `ContactsProvider` interface to fetch contacts from the user's personal Telegram account via Telethon's `client.get_contacts()`. Unlike Google (which provides sync tokens for incremental sync), Telegram returns the full contact list on every call. Incremental sync is approximated by comparing a local hash of the contact list against the previous sync's hash.

#### Scenario: Telegram credential resolution

- **WHEN** the Telegram provider initializes
- **THEN** it resolves `telegram_api_id`, `telegram_api_hash`, and `telegram_user_session` from the owner contact's `shared.contact_info` entries (secured credentials)
- **AND** if any credential is missing, a `RuntimeError` is raised directing the user to configure Telegram user-client credentials

#### Scenario: Telegram full sync

- **WHEN** a full sync is requested from the Telegram provider
- **THEN** it calls `client.get_contacts()` via Telethon to fetch the complete contact list
- **AND** each Telegram contact is mapped to a `CanonicalContact` with:
  - `external_id` = `"telegram:<user_id>"` (numeric Telegram user ID)
  - `display_name` = Telegram's `first_name + last_name`
  - `first_name`, `last_name` from Telegram contact fields
  - `phones[]` from the contact's phone number (if available)
  - `usernames[]` from the contact's `@username` (if set)
  - `deleted = false` (Telegram does not return tombstones; deletions are detected by absence)
  - `raw` = serialized Telethon contact object
- **AND** the batch includes a `next_sync_cursor` containing a hash of the full contact list for change detection
- **AND** `next_page_token` is always `None` (Telegram returns all contacts in a single call)

#### Scenario: Telegram incremental sync (hash-based change detection)

- **WHEN** an incremental sync is requested with a previous cursor (hash)
- **THEN** the provider fetches the full contact list via `client.get_contacts()`
- **AND** computes a hash of the current contact list
- **AND** if the hash matches the cursor, returns an empty batch (no changes)
- **AND** if the hash differs, returns the full contact list as the batch (the backfill engine handles diffing)

#### Scenario: Telegram contact deletion detection

- **WHEN** a contact was present in the previous sync but is absent from the current Telegram contact list
- **THEN** the provider does NOT emit a tombstone (Telegram has no delete markers)
- **AND** deletion is detected at the backfill engine level by comparing `contacts_source_links` against the current batch
- **AND** absent contacts have their source link marked with `deleted_at`
- **AND** a `contact_sync_deleted_source` activity feed entry is logged

#### Scenario: Telegram contact without phone number

- **WHEN** a Telegram contact has no phone number (username-only contact)
- **THEN** the `CanonicalContact` has an empty `phones[]` list
- **AND** cross-provider resolution falls back to name matching (if applicable)

#### Scenario: Telegram validate_credentials

- **WHEN** `validate_credentials()` is called on the Telegram provider
- **THEN** it attempts to connect to Telegram via Telethon and call `client.get_me()`
- **AND** if connection fails, raises `ContactsTokenRefreshError` with a descriptive message

#### Scenario: Telegram list_groups

- **WHEN** `list_groups()` is called on the Telegram provider
- **THEN** it returns an empty `GroupBatch` (Telegram personal contacts do not have group/label semantics)

### Requirement: Multi-Provider Sync Runtime

When multiple providers are configured, the `ContactsSyncRuntime` manages independent sync loops for each provider.

#### Scenario: Independent provider sync loops

- **WHEN** the runtime starts with providers `["google", "telegram"]`
- **THEN** each provider runs its own sync loop with the shared `interval_minutes` and `full_sync_interval_days` schedule
- **AND** sync state (cursors, timestamps, errors) is tracked independently per provider via `ContactsSyncStateStore` keyed by `(provider, account_id)`

#### Scenario: Provider failure isolation

- **WHEN** the Google provider sync fails (e.g., expired OAuth token)
- **THEN** the Telegram provider sync continues unaffected
- **AND** the failed provider's `last_error` is recorded in its own sync state
- **AND** MCP tools report per-provider status

#### Scenario: contacts_sync_now with provider filter

- **WHEN** `contacts_sync_now` is called with `provider = "telegram"`
- **THEN** only the Telegram provider sync runs
- **AND** if `provider` is omitted, all configured providers sync

#### Scenario: contacts_sync_status multi-provider

- **WHEN** `contacts_sync_status` is called with multiple providers configured
- **THEN** the response includes per-provider sync state (cursor age, last sync, last error, contact count)

### Requirement: Sync State Persistence

Sync state is persisted via `ContactsSyncStateStore` with fields: `sync_cursor`, `cursor_issued_at`, `last_full_sync_at`, `last_incremental_sync_at`, `last_success_at`, `last_error`, `contact_versions` (in-memory etag tracking).

#### Scenario: State store load and save

- **WHEN** sync state is loaded for a provider/account pair
- **THEN** the stored cursor, timestamps, and error info are returned
- **AND** contact_versions provides an approximate contact count

### Requirement: Sync Runtime with Polling Loop

`ContactsSyncRuntime` manages the background polling loop with configurable intervals.

#### Scenario: Startup sync

- **WHEN** the runtime starts
- **THEN** an immediate incremental sync runs (full sync if no cursor exists)

#### Scenario: Periodic polling

- **WHEN** the runtime is running
- **THEN** incremental sync fires every `interval_minutes` (default 15)
- **AND** a forced full refresh fires every `full_sync_interval_days` (default 6 days)

#### Scenario: Immediate sync trigger

- **WHEN** `contacts_source_reconcile` triggers an immediate sync
- **THEN** the runtime's poller wakes up and runs a sync cycle

### Requirement: CRM Backfill Pipeline

`ContactBackfillEngine` orchestrates identity resolution, table writing, and activity feed logging for each synced contact.

#### Scenario: New contact backfill

- **WHEN** a canonical contact has no existing match
- **THEN** a new CRM contact is created in the `contacts` table
- **AND** `contact_info` rows are created for emails, phones, urls, usernames (in `shared.contact_info`)
- **AND** `addresses` rows are created for postal addresses
- **AND** `important_dates` rows are created for birthdays and anniversaries
- **AND** `labels` + `contact_labels` rows are created for group memberships
- **AND** a `contacts_source_links` provenance row is created
- **AND** a `contact_synced` activity feed entry is logged

#### Scenario: Existing contact update with provenance

- **WHEN** a canonical contact matches an existing CRM contact
- **THEN** fields are updated only if they were previously source-owned (tracked in `metadata` JSONB provenance)
- **AND** local manual edits are preserved (not overwritten)
- **AND** conflict fields emit a `contact_sync_conflict` activity feed entry
- **AND** updated fields emit a `contact_sync_updated` activity feed entry

#### Scenario: Tombstone handling (source deleted)

- **WHEN** a canonical contact has `deleted = true`
- **THEN** the `contacts_source_links` row is marked with `deleted_at`
- **AND** the CRM contact record is preserved (no hard deletes)
- **AND** a `contact_sync_deleted_source` activity feed entry is logged

### Requirement: Identity Resolution Pipeline

`ContactBackfillResolver` resolves canonical contacts to local CRM records in priority order.

#### Scenario: Resolution order

- **WHEN** a canonical contact is resolved
- **THEN** the following strategies are tried in order:
  1. Source link match (`provider + account_id + external_contact_id` in `contacts_source_links`)
  2. Primary email exact match in `shared.contact_info` (type='email')
  3. Phone exact/e164 match in `shared.contact_info` (type='phone')
  4. Conservative name match (ILIKE against `name`, `first_name || last_name`, `nickname`)
- **AND** ambiguous name matches (multiple candidates) skip auto-merge and return `ambiguous_name` strategy

### Requirement: MCP Tool Surface (4 Tools)

The module registers 4 operational/sync control tools.

#### Scenario: contacts_sync_now

- **WHEN** `contacts_sync_now` is called with provider and mode (incremental/full)
- **THEN** an immediate sync cycle runs and returns a summary with fetched, applied, skipped, and deleted counts

#### Scenario: contacts_sync_status

- **WHEN** `contacts_sync_status` is called
- **THEN** the current sync state is returned including cursor age, last sync timestamps, last error, and contact count

#### Scenario: contacts_source_list

- **WHEN** `contacts_source_list` is called with optional provider filter
- **THEN** a list of connected source accounts with provider, account_id, sync_enabled, status, and timestamps is returned

#### Scenario: contacts_source_reconcile

- **WHEN** `contacts_source_reconcile` is called
- **THEN** an immediate sync trigger is signaled to the runtime
- **AND** per-contact scoping is noted as not yet supported at the engine level

### Requirement: Shared Schema Tables

The backfill writes to `shared.contact_info` for cross-butler contact data, while module-owned tables (`contacts_source_links`) live in the hosting butler's schema.

#### Scenario: Contact info in shared schema

- **WHEN** email, phone, website, or other contact info is upserted
- **THEN** rows are written to `shared.contact_info` with `contact_id`, `type`, `value`, `label`, `is_primary`
- **AND** existing duplicates (same contact_id + type + lower(value)) are not re-inserted

### Requirement: Cross-Provider Contact Backfill

When multiple providers sync contacts concurrently, the `ContactBackfillEngine` merges contacts from different providers into unified CRM records using the identity resolution pipeline.

#### Scenario: Telegram contact matches existing Google contact by phone

- **WHEN** a Telegram contact has phone number `+1-555-0100`
- **AND** a Google-sourced CRM contact already exists with the same phone in `shared.contact_info`
- **THEN** the backfill engine resolves them as the same contact via phone match (strategy 3 in resolution order)
- **AND** Telegram-specific `contact_info` entries (`telegram_username`, `telegram_user_id`, `telegram_chat_id`) are added alongside the existing Google-sourced entries
- **AND** the `contacts_source_links` table records a second provenance row with `provider = "telegram"`
- **AND** `metadata` JSONB provenance tracks which fields came from which provider (e.g., `{"display_name": {"source": "google"}, "telegram_user_id": {"source": "telegram"}}`)

#### Scenario: New Telegram-only contact (no Google match)

- **WHEN** a Telegram contact has no matching email, phone, or source link in the CRM
- **AND** the contact's name does not produce a unique name match
- **THEN** a new CRM contact is created with `contacts_source_links.provider = "telegram"`
- **AND** `contact_info` entries are created for available Telegram identifiers (`telegram_user_id`, optionally `telegram_username`)
- **AND** a `contact_synced` activity feed entry is logged

#### Scenario: Ambiguous name-only match across providers

- **WHEN** a Telegram contact named "Alex Smith" has no phone or email
- **AND** two existing CRM contacts are named "Alex Smith" (one from Google, one manually created)
- **THEN** auto-merge is skipped (ambiguous name match returns `ambiguous_name` strategy)
- **AND** the Telegram contact is created as a new CRM record
- **AND** a `contact_sync_ambiguous` activity feed entry is logged flagging the potential duplicates for dashboard disambiguation

#### Scenario: Provenance tracking across providers

- **WHEN** a CRM contact has source links from both Google and Telegram
- **THEN** `metadata` JSONB on the contact tracks field-level provenance:
  - Fields first set by Google retain `{"source": "google"}` provenance
  - Fields first set by Telegram retain `{"source": "telegram"}` provenance
  - Manual dashboard edits are tracked as `{"source": "manual"}`
- **AND** sync from either provider respects existing provenance (does not overwrite fields owned by another provider or manual edits)

#### Scenario: Telegram contact removal with Google contact surviving

- **WHEN** a contact exists with source links from both Google and Telegram
- **AND** the Telegram sync detects the contact is no longer in the Telegram contact list
- **THEN** only the Telegram `contacts_source_links` row is marked with `deleted_at`
- **AND** the CRM contact record is preserved (still linked to Google)
- **AND** Telegram-specific `contact_info` entries (`telegram_username`, `telegram_user_id`) are retained (not deleted)
- **AND** a `contact_sync_deleted_source` activity feed entry is logged for the Telegram source

### Requirement: [TARGET-STATE] Apple/CardDAV Provider

The provider abstraction supports future Apple/iCloud providers using CardDAV sync semantics.

#### Scenario: CardDAV provider registration

- **WHEN** a CardDAV provider is implemented
- **THEN** it implements the same `ContactsProvider` interface with collection-based sync, ETag-aware updates, and provider-owned cursor formats
