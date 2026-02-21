# Contacts Module: Target-State Specification (Google-First, Provider-Agnostic)

Status: Draft (Target-State Design)
Last updated: 2026-02-21
Primary owner: Platform/Modules + Relationship Domain
Depends on: `src/butlers/modules/base.py`, `src/butlers/google_credentials.py`, `docs/roles/relationship_butler.md`

## 1. Module

The Contacts module is a reusable module that synchronizes external address-book
sources into a canonical contact model and backfills the Relationship Butler's
CRM schema.

Initial target is Google account contacts. The interface is intentionally
provider-agnostic so we can add Apple/iCloud (and other CardDAV or native
providers) without redesigning the core contract.

## 2. Design Goals

- Google-first delivery: reliably load and continuously sync all Google account
  contacts into Relationship Butler.
- Provider-agnostic architecture: normalize provider payloads into one canonical
  model and one sync contract.
- Safe backfill into existing CRM schema: upsert into `contacts`,
  `contact_info`, `addresses`, `important_dates`, and label/group relations
  without destructive overwrites.
- Deterministic incremental sync: cursor/token based ingestion with explicit
  recovery on token expiry and conflict paths.
- Provenance and auditability: every synced field remains traceable to source
  account/provider metadata.

## 3. Scope and Boundaries

### In scope

- OAuth-backed sync from Google People API.
- Full bootstrap sync + recurring incremental sync.
- Optional secondary import stream for Google "Other contacts".
- Provider abstraction for future Apple/CardDAV adapters.
- Backfill/upsert pipeline into Relationship Butler tables.
- Sync metadata persistence (cursor/token/account mapping/provenance).

### Out of scope (v1)

- Bi-directional write-back to Google or Apple.
- Real-time push/webhook contact change subscriptions.
- Automatic destructive deletes of local records when source deletes.
- Enterprise directory ingestion defaults (Google Workspace directory).

## 4. Provider-Agnostic Runtime Contract

### 4.1 Core interfaces

Each provider implements:

- `ContactsProvider.name() -> str`
- `ContactsProvider.validate_credentials()`
- `ContactsProvider.full_sync(account, page_token?) -> ContactBatch`
- `ContactsProvider.incremental_sync(account, cursor, page_token?) -> ContactBatch`
- `ContactsProvider.list_groups(account, page_token?) -> GroupBatch`

Canonical batch return shape:

- `contacts`: list of canonical contacts
- `groups`: list of canonical groups/labels
- `next_page_token`: provider page cursor (if paginated)
- `next_sync_cursor`: provider delta cursor/token (if emitted)
- `checkpoint`: opaque provider metadata for safe resume

### 4.2 Canonical contact model

Provider adapters map payloads into:

- `external_id` (provider stable identifier/resource name)
- `etag` (provider version tag when available)
- `display_name`
- `first_name`, `last_name`, `middle_name`, `nickname`
- `emails[]` (value, label, primary, normalized_value)
- `phones[]` (value, label, primary, e164_normalized when possible)
- `addresses[]` (structured postal fields + label + primary/current hints)
- `organizations[]` (company, title, department)
- `birthdays[]` and `anniversaries[]`
- `urls[]`, `usernames[]`, `photos[]`
- `group_memberships[]`
- `deleted` (tombstone flag)
- `raw` (provider payload snapshot for forensic/debug use)

### 4.3 Sync state persistence (module-owned tables)

Target-state module tables:

- `contacts_source_accounts`
  - provider, account_id, subject_email, connected_at, last_success_at
- `contacts_sync_state`
  - provider, account_id, sync_cursor, cursor_issued_at, last_full_sync_at,
    last_incremental_sync_at, last_error
- `contacts_source_links`
  - provider, account_id, external_contact_id, local_contact_id, source_etag,
    first_seen_at, last_seen_at, deleted_at

These tables are provider-neutral and live in the hosting butler DB.

## 5. Google Provider (v1 target)

### 5.1 APIs used

Primary source:

- Google People API `people.connections.list` (`resourceName=people/me`) for
  "My Contacts" synchronization.

Optional secondary source:

- Google People API `otherContacts.list` for auto-saved/non-explicit contacts.

Group/label source:

- Google People API `contactGroups.list`.

### 5.2 OAuth and scopes

Minimum read-only scope set for v1:

- `https://www.googleapis.com/auth/contacts.readonly`

Optional scopes by feature:

- `https://www.googleapis.com/auth/contacts.other.readonly`
  (if importing Google "Other contacts")
- `https://www.googleapis.com/auth/directory.readonly`
  (future Workspace directory mode)

Future write-back scope (not enabled in v1):

- `https://www.googleapis.com/auth/contacts`

Integration note: this repo already uses shared Google OAuth credentials in
`google_oauth_credentials`; Contacts module must reuse the same credential
store and extend requested scopes when enabled.

### 5.3 Required People API request semantics

For `people.connections.list`:

- `personFields` is required and must include all mapped fields.
- For bootstrap full sync, set `requestSyncToken=true`.
- Incremental sync requires passing the returned `syncToken`.
- All subsequent pages/requests for a sync cycle must keep the same query
  parameters.

Recommended `personFields` baseline:

- `names,emailAddresses,phoneNumbers,addresses,birthdays,events,organizations,biographies,urls,memberships,photos,userDefined,metadata`

### 5.4 Incremental sync behavior and recovery

- Google sync tokens expire (documented at 7 days). Module must run
  incremental sync frequently and schedule forced full refresh before expiry
  (target: full refresh every 6 days).
- On `EXPIRED_SYNC_TOKEN`, drop cursor and run full sync immediately.
- Respect pagination (`nextPageToken`) until exhausted before committing cursor.

### 5.5 Write-related constraints (future-proofing)

Even though v1 is read-only, interface must preserve write-safe metadata:

- `etag` captured for optimistic concurrency.
- Future update/delete paths must serialize per-contact writes and include etag
  (People API update contract).
- Provider write propagation can lag by minutes; read-after-write assumptions
  must be avoided.

## 6. Apple / Interoperability Path

### 6.1 Why provider abstraction is mandatory

Apple does not expose a Google-People-equivalent public REST surface for
server-side contact sync. Interop path is CardDAV-style sync and/or
platform-specific APIs. Therefore, module contracts cannot hardcode Google
resource shapes or token semantics.

### 6.2 CardDAV-aligned contract expectations

For Apple/iCloud-compatible providers:

- collection-based sync semantics
- ETag/version-aware updates
- principal/address-book discovery and paginated sync
- credential model that may involve app authorization or app-specific passwords

Module interface already supports this by requiring provider-owned cursor and
checkpoint formats while enforcing canonical output records.

## 7. Backfill Contract into Relationship Butler

### 7.1 Upsert identity strategy

Backfill resolver order:

1. Existing `contacts_source_links` match (`provider + account + external_id`)
2. Primary email exact match in `contact_info` (`type='email'`)
3. Phone exact/e164 match in `contact_info` (`type='phone'`)
4. Conservative name match fallback (manual-review flag when ambiguous)

No hard deletes. Source deletes set `deleted_at` on link and mark contact as
candidate for archival review.

### 7.2 Table mapping

| Canonical field | Relationship destination |
|---|---|
| names/display name | `contacts.name`, `contacts.first_name`, `contacts.last_name`, `contacts.nickname` |
| organizations | `contacts.company`, `contacts.job_title` |
| photos[primary] | `contacts.avatar_url` |
| source metadata | `contacts.metadata` (namespaced under `sources.contacts.*`) |
| emails | `contact_info` rows (`type='email'`) |
| phones | `contact_info` rows (`type='phone'`) |
| urls/usernames | `contact_info` rows (`type='website'`/`other`) |
| addresses | `addresses` rows |
| birthdays/events | `important_dates` rows (`label='birthday'`, `label='anniversary'`, etc.) |
| contact groups | `labels` + `contact_labels` (default mapping) |

### 7.3 Conflict policy

- Source fields do not blindly overwrite user-edited local fields.
- Track per-field provenance in metadata and apply "source wins" only for
  previously source-owned fields.
- Local manual edits win unless user explicitly requests a refresh overwrite.
- Ambiguous merges emit activity feed entries for review.

### 7.4 Activity and audit

Backfill must create `activity_feed` entries:

- `contact_synced` (new from provider)
- `contact_sync_updated` (field changes applied)
- `contact_sync_conflict` (manual resolution needed)
- `contact_sync_deleted_source` (source tombstone observed)

Each entry includes provider/account/external ID references in details.

## 8. Scheduling and Operational Semantics

Module-vs-connector decision (v1 contract):

- Incremental contacts sync runs inside the Butler module runtime as an internal
  poll loop.
- There is no standalone contacts connector process.
- Local dev bootstrap remains unchanged: `scripts/dev.sh` starts `butlers up`,
  and that process owns contacts sync polling when `[modules.contacts]` is
  configured.

Default schedule:

- Startup: run incremental sync immediately; full sync if no cursor.
- Incremental polling: every 15 minutes.
- Forced full refresh: every 6 days (before token expiry window).

Failure handling:

- Retry transient provider errors with exponential backoff + jitter.
- Preserve last good cursor until full page chain completes.
- On repeated failures, set module health to degraded with actionable status.

## 9. Tooling Surface (Module)

Target-state module tools:

- `contacts_sync_now(provider='google', mode='incremental|full')`
- `contacts_sync_status(provider='google')`
- `contacts_source_list(provider?)`
- `contacts_source_reconcile(contact_id?)`

Relationship role tools remain the primary user-facing CRUD; contacts module
tools are operational/sync controls.

## 10. Security and Privacy

- Reuse existing OAuth credential storage (`google_oauth_credentials`).
- Store least-privilege scopes for enabled features only.
- Never log access/refresh tokens or raw secret payloads.
- Treat imported raw payload snapshots as sensitive PII.
- All synced contact data remains in user-owned butler DB; no cross-butler
  sharing except explicit routing paths.

## 11. Rollout Plan (Target State)

1. Implement read-only Google provider with full + incremental sync.
2. Enable backfill into Relationship tables with provenance metadata.
3. Add operational tools and dashboard sync status visibility.
4. Add optional `otherContacts` import.
5. Add CardDAV provider prototype (Apple/iCloud path) using the same canonical
   contracts and link/state tables.

## 12. References (Primary Sources)

- Google People API `people.connections.list`:
  https://developers.google.com/people/api/rest/v1/people.connections/list
- Google People API `people.updateContact`:
  https://developers.google.com/people/api/rest/v1/people/updateContact
- Google People API `otherContacts.list`:
  https://developers.google.com/people/api/rest/v1/otherContacts/list
- Google People API `contactGroups.list`:
  https://developers.google.com/people/api/rest/v1/contactGroups/list
- Google Contacts API migration guide (People API + CardDAV):
  https://developers.google.com/people/contacts-api-migration
- Google "Manage your contacts with CardDAV":
  https://developers.google.com/people/carddav
- Apple Support: 3rd-party app access with iCloud:
  https://support.apple.com/en-ae/guide/icloud/mm6b1a490a/icloud
- Apple Support: app-specific passwords:
  https://support.apple.com/en-us/102654
- Apple Platform Deployment (CardDAV-compliant server account payload):
  https://support.apple.com/en-lb/guide/deployment/depdc4ba8db9/web
