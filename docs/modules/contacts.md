# Contacts Module: Unified Identity Model and Address-Book Sync

Status: Normative (Contacts-as-Identity Model)
Last updated: 2026-02-25
Primary owner: Platform/Modules + Relationship Domain
Depends on: `src/butlers/modules/base.py`, `src/butlers/identity.py`, `src/butlers/credential_store.py`, `docs/roles/switchboard_butler.md`, `docs/roles/relationship_butler.md`

---

## 1. Overview

The Contacts module serves two distinct but deeply related purposes:

1. **Identity model** — `shared.contacts` and `shared.contact_info` are the canonical identity store for every person (or system actor) who interacts with the butler system. All channels (Telegram, Email, etc.) resolve to a contact record before any routing or delivery decision is made.
2. **Address-book sync** — A reusable module that synchronizes external address-book sources (Google Contacts, future: Apple/CardDAV) into the canonical contact model and backfills the Relationship Butler's CRM schema.

These two purposes are intentionally unified: the sync module enriches the same `shared.contacts` records that the identity resolution path reads at runtime.

---

## 2. Contacts-as-Identity Model

### 2.1 Shared Schema Tables

Both tables live in the `shared` PostgreSQL schema and are accessible to all butler roles (read for most; insert by Switchboard and daemon bootstrap).

#### `shared.contacts`

The canonical contact registry. One row per known person or system actor.

| Column | Type | Notes |
|---|---|---|
| `id` | `uuid` (PK) | Generated, stable identifier |
| `name` | `text` | Display name (may be `null` for unknown senders) |
| `roles` | `text[]` | Role tags (e.g. `['owner']`) |
| `entity_id` | `uuid` | FK to memory butler's entity (cross-schema reference; `null` until linked) |
| `metadata` | `jsonb` | Unstructured metadata (e.g. `needs_disambiguation`, `source_channel`) |
| `created_at` | `timestamptz` | Auto-populated |

#### `shared.contact_info`

One row per channel identifier associated with a contact. A contact may have multiple `contact_info` rows across different channel types.

| Column | Type | Notes |
|---|---|---|
| `id` | `uuid` (PK) | Generated |
| `contact_id` | `uuid` | FK to `shared.contacts.id` |
| `type` | `text` | Channel type (e.g. `'telegram'`, `'email'`, `'telegram_bot_token'`) |
| `value` | `text` | Channel-specific identifier (chat ID, email address, token) |
| `is_primary` | `bool` | Primary entry for this (contact, type) pair |
| `secured` | `bool` | Marks credential values requiring elevated read guard |
| `created_at` | `timestamptz` | Auto-populated |

UNIQUE constraint on `(type, value)` guarantees at most one contact per channel identifier.

### 2.2 Roles

The `roles` array on `shared.contacts` encodes the contact's relationship to the system.

| Role value | Meaning |
|---|---|
| `owner` | The human operator who owns and controls this butler system |
| _(none)_ | A known non-owner contact (e.g. a person messaging in, an external recipient) |

The `owner` singleton is enforced by a partial unique index (`ix_contacts_owner_singleton`): only one contact may have `'owner' = ANY(roles)`.

Future roles (not yet implemented): `trusted`, `blocked`, `service`.

### 2.3 Owner Bootstrap

On every butler daemon startup, `_ensure_owner_contact(pool)` (`src/butlers/daemon.py`) ensures exactly one owner contact exists in `shared.contacts`. This is idempotent and safe under concurrent restarts:

```sql
INSERT INTO shared.contacts (name, roles)
VALUES ('Owner', '{owner}')
ON CONFLICT DO NOTHING
```

The owner contact has no `contact_info` rows by default. Channel identifiers (Telegram chat ID, email address, bot token) are added when the butler first interacts on that channel, or by provisioning scripts.

### 2.4 Reverse-Lookup

The canonical reverse-lookup path maps a channel identifier to a contact:

```
(channel_type, channel_value) → ResolvedContact
```

Implemented by `resolve_contact_by_channel(pool, channel_type, channel_value)` in `src/butlers/identity.py`.

```sql
SELECT c.id, c.name, c.roles, c.entity_id
FROM shared.contact_info ci
JOIN shared.contacts c ON c.id = ci.contact_id
WHERE ci.type = $1 AND ci.value = $2
LIMIT 1
```

Returns a `ResolvedContact` dataclass with `contact_id`, `name`, `roles`, and `entity_id`, or `None` when no match is found.

### 2.5 Secured Entries

`contact_info.secured = true` marks entries that contain credential material (e.g. `type='telegram_bot_token'`). Secured entries:

- Are filtered out from default list/read MCP tools.
- Require the `include_secured=true` flag to access.
- Must not appear in dashboard API responses without an explicit secured-data permission check.

### 2.6 Temporary Contacts for Unknown Senders

When Switchboard receives a message from a sender not found in `shared.contact_info`, it calls `create_temp_contact(pool, channel_type, channel_value)` (`src/butlers/identity.py`):

1. Creates a new `shared.contacts` row with `metadata.needs_disambiguation = true`.
2. Links a `shared.contact_info` row for `(channel_type, channel_value)`.
3. Uses `ON CONFLICT (type, value) DO NOTHING` to survive concurrent creation races.

The temp contact is returned as a `ResolvedContact` with `roles = []`. The owner is notified once per new unknown sender (tracked via `butler_state` KV: `identity:unknown_notified:{type}:{value}`).

### 2.7 Identity Preamble Injection

Before any routing prompt is sent to the LLM, Switchboard prepends a structured identity preamble built by `build_identity_preamble(resolved, channel, ...)` (`src/butlers/identity.py`):

| Sender type | Preamble format |
|---|---|
| Owner | `[Source: Owner, via {channel}]` |
| Known non-owner | `[Source: {name} (contact_id: {cid}, entity_id: {eid}), via {channel}]` |
| Unknown (temp contact created) | `[Source: Unknown sender (contact_id: {cid}), via {channel} -- pending disambiguation]` |

The preamble `contact_id`, `entity_id`, and `sender_roles` are also written to `routing_log` columns so every routed message carries full identity lineage.

---

## 3. Contact-Based notify() Resolution

When a butler calls `notify(contact_id=..., channel=..., ...)`, the daemon resolves the channel identifier via `_resolve_contact_channel_identifier(contact_id, channel)` (`src/butlers/daemon.py`):

```sql
SELECT ci.value
FROM shared.contact_info ci
WHERE ci.contact_id = $1 AND ci.type = $2
ORDER BY ci.is_primary DESC NULLS LAST, ci.created_at ASC
LIMIT 1
```

Priority order for recipient resolution in `notify()`:

1. `contact_id` provided → query `shared.contact_info WHERE contact_id=X AND type=channel`.
2. `recipient` string provided → use as-is (backwards-compatible explicit addressing).
3. Neither → resolve owner contact channel identifier (default path for scheduled/proactive sends).

When `contact_id` resolves but no matching `contact_info` entry exists for the requested channel, `notify()` parks a `pending_action` and notifies the owner that the identifier is missing.

---

## 4. Owner Contact Info Lookup

`resolve_owner_contact_info(pool, info_type)` (`src/butlers/credential_store.py`) is the DB-side counterpart to `CredentialStore.resolve()` for identity-bound credentials that have been migrated to `contact_info`.

```sql
SELECT ci.value
FROM shared.contact_info ci
JOIN shared.contacts c ON c.id = ci.contact_id
WHERE 'owner' = ANY(c.roles) AND ci.type = $1
ORDER BY ci.is_primary DESC NULLS LAST, ci.created_at ASC
LIMIT 1
```

Used by `_resolve_default_notify_recipient` (daemon.py) to look up the owner's Telegram chat ID (`type='telegram'`) when no explicit `recipient` or `contact_id` is provided in `notify(channel="telegram", intent="send")`.

Legacy fallback: if no `contact_info` row exists, the daemon falls back to `TELEGRAM_CHAT_ID` in `butler_secrets`.

---

## 5. Address-Book Sync Module

The module syncs external address-book sources into the canonical contact model described above, and additionally backfills the Relationship Butler's CRM schema.

### 5.1 Design Goals

- Google-first delivery: reliably load and continuously sync all Google account contacts.
- Provider-agnostic architecture: normalize provider payloads into one canonical model.
- Safe backfill into existing CRM schema: upsert without destructive overwrites.
- Deterministic incremental sync: cursor/token-based ingestion with explicit recovery on token expiry.
- Provenance and auditability: every synced field remains traceable to source account/provider metadata.

### 5.2 Scope and Boundaries

In scope:
- OAuth-backed sync from Google People API.
- Full bootstrap sync + recurring incremental sync.
- Optional secondary import stream for Google "Other contacts".
- Provider abstraction for future Apple/CardDAV adapters.
- Backfill/upsert pipeline into Relationship Butler tables.
- Sync metadata persistence (cursor/token/account mapping/provenance).

Out of scope (v1):
- Bi-directional write-back to Google or Apple.
- Real-time push/webhook contact change subscriptions.
- Automatic destructive deletes of local records when source deletes.
- Enterprise directory ingestion defaults (Google Workspace directory).

### 5.3 Provider-Agnostic Runtime Contract

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

### 5.4 Canonical Contact Model

Provider adapters map payloads into:
- `external_id`, `etag`, `display_name`, `first_name`, `last_name`, `middle_name`, `nickname`
- `emails[]`, `phones[]`, `addresses[]`, `organizations[]`
- `birthdays[]`, `anniversaries[]`, `urls[]`, `usernames[]`, `photos[]`
- `group_memberships[]`, `deleted` (tombstone flag), `raw` (provider snapshot)

### 5.5 Sync State Persistence (Module-Owned Tables)

- `contacts_source_accounts` — provider, account_id, subject_email, connected_at, last_success_at
- `contacts_sync_state` — provider, account_id, sync_cursor, cursor_issued_at, last_full/incremental_sync_at, last_error
- `contacts_source_links` — provider, account_id, external_contact_id, local_contact_id, source_etag, first_seen_at, last_seen_at, deleted_at

These tables are provider-neutral and live in the hosting butler DB (not in `shared`).

### 5.6 Google Provider (v1 Target)

APIs used:
- Google People API `people.connections.list` for "My Contacts" sync.
- Google People API `otherContacts.list` (optional secondary source).
- Google People API `contactGroups.list` for group/label sync.

OAuth scopes:
- `https://www.googleapis.com/auth/contacts.readonly` (minimum v1)
- `https://www.googleapis.com/auth/contacts.other.readonly` (optional)

Incremental sync behavior:
- Sync tokens expire (~7 days). Module schedules forced full refresh every 6 days.
- On `EXPIRED_SYNC_TOKEN`, drop cursor and run full sync immediately.
- Respect pagination (`nextPageToken`) until exhausted before committing cursor.

Required `personFields`:
- `names,emailAddresses,phoneNumbers,addresses,birthdays,events,organizations,biographies,urls,memberships,photos,userDefined,metadata`

### 5.7 Scheduling and Operational Semantics

Default schedule:
- Startup: run incremental sync immediately; full sync if no cursor.
- Incremental polling: every 15 minutes.
- Forced full refresh: every 6 days (before token expiry window).

Failure handling:
- Retry transient provider errors with exponential backoff + jitter.
- Preserve last good cursor until full page chain completes.
- On repeated failures, set module health to degraded with actionable status.

### 5.8 Roster Enablement Contract

Enabled by default in:
- `roster/general/butler.toml`
- `roster/health/butler.toml`
- `roster/relationship/butler.toml`

Intentionally excluded:
- `roster/switchboard/butler.toml` (routing/control plane; does not own CRM sync execution)
- `roster/messenger/butler.toml` (delivery plane only)

### 5.9 Tooling Surface (Module)

Target-state module tools:
- `contacts_sync_now(provider='google', mode='incremental|full')`
- `contacts_sync_status(provider='google')`
- `contacts_source_list(provider?)`
- `contacts_source_reconcile(contact_id?)`

### 5.10 Backfill Contract into Relationship Butler

Upsert identity resolver order:
1. Existing `contacts_source_links` match (provider + account + external_id)
2. Primary email exact match in `contact_info` (type='email')
3. Phone exact/e164 match in `contact_info` (type='phone')
4. Conservative name match fallback (manual-review flag when ambiguous)

No hard deletes. Source deletes set `deleted_at` on link and mark contact as candidate for archival review.

Table mapping:

| Canonical field | Relationship destination |
|---|---|
| names/display name | `contacts.name`, `contacts.first_name`, `contacts.last_name`, `contacts.nickname` |
| organizations | `contacts.company`, `contacts.job_title` |
| photos[primary] | `contacts.avatar_url` |
| emails | `contact_info` rows (`type='email'`) |
| phones | `contact_info` rows (`type='phone'`) |
| addresses | `addresses` rows |
| birthdays/events | `important_dates` rows |
| contact groups | `labels` + `contact_labels` |

Conflict policy:
- Source fields do not blindly overwrite user-edited local fields.
- Per-field provenance tracked in metadata under `sources.contacts.*`.
- Local manual edits win unless explicitly overridden.

Activity feed events: `contact_synced`, `contact_sync_updated`, `contact_sync_conflict`, `contact_sync_deleted_source`.

---

## 6. Security and Privacy

- OAuth credentials stored via existing `google_oauth_credentials` store.
- Secured `contact_info` entries (`secured=true`) are filtered from default read paths.
- Never log credential values or raw secret payloads.
- All synced contact data remains in user-owned butler DB; no cross-butler sharing except explicit routing paths.
- `shared.contacts` and `shared.contact_info` are read-only for most butler schemas (write access scoped to Switchboard and daemon bootstrap).

---

## 7. Implementation References

| Component | Location |
|---|---|
| Identity resolution | `src/butlers/identity.py` |
| Switchboard identity injection | `roster/switchboard/tools/identity/inject.py` |
| Owner bootstrap | `src/butlers/daemon.py:_ensure_owner_contact` |
| Owner contact_info lookup | `src/butlers/credential_store.py:resolve_owner_contact_info` |
| notify() contact_id resolution | `src/butlers/daemon.py:_resolve_contact_channel_identifier` |
| Shared schema migration (core_007) | `src/butlers/migrations/versions/` |
| Contacts sync runtime | `src/butlers/modules/contacts/sync.py:ContactsSyncRuntime` |
| CRM backfill engine | `src/butlers/modules/contacts/backfill.py` |
