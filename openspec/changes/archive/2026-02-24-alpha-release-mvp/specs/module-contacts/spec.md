# Contacts Module

## Purpose

The Contacts module synchronizes external address-book sources (Google Contacts v1) into a canonical contact model and backfills the Relationship Butler's CRM schema with provider-agnostic upsert logic, provenance tracking, and conflict-aware field resolution.

## ADDED Requirements

### Requirement: ContactsModule Configuration and Scaffold

The module is configured under `[modules.contacts]` in `butler.toml` with `provider` (required, currently only `"google"`), `include_other_contacts` (bool, default false), and `sync` sub-config (`enabled`, `run_on_startup`, `interval_minutes` default 15, `full_sync_interval_days` default 6).

#### Scenario: Valid contacts config

- **WHEN** `ContactsConfig` is provided with `provider = "google"`
- **THEN** the provider name is normalized to lowercase and trimmed
- **AND** sync defaults are applied (15-minute incremental, 6-day full sync)

#### Scenario: Unsupported provider at startup

- **WHEN** the configured provider is not in the supported set (`{"google"}`)
- **THEN** startup raises a `RuntimeError` with a descriptive message listing supported providers

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

### Requirement: [TARGET-STATE] Apple/CardDAV Provider

The provider abstraction supports future Apple/iCloud providers using CardDAV sync semantics.

#### Scenario: CardDAV provider registration

- **WHEN** a CardDAV provider is implemented
- **THEN** it implements the same `ContactsProvider` interface with collection-based sync, ETag-aware updates, and provider-owned cursor formats
