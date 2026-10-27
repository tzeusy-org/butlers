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
enabled = true        # default false; only the Relationship butler sets true
run_on_startup = true
interval_minutes = 15
full_sync_interval_days = 6
```

Sync is off unless `sync.enabled = true`. Only the Relationship butler enables it, because it owns
entities and sync takes no cross-process lock; every other butler's `butler.toml` sets
`enabled = false` explicitly, and a roster test pins that exactly one butler syncs.

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

Butlers with a `[modules.contacts]` table in `roster/*/butler.toml` load the module, but only
Relationship runs the sync poller. The Switchboard (routing plane) and Messenger (delivery plane) intentionally omit it.

### Credentials

- **Google**: `GOOGLE_OAUTH_CLIENT_ID` and `GOOGLE_OAUTH_CLIENT_SECRET` from the shared credential
  store; the per-account refresh token is an owner `entity_info` entry of type
  `google_oauth_refresh` (`src/butlers/google_credentials.py`), written by the OAuth flow — see
  [OAuth Flows](../identity_and_secrets/oauth-flows.md).
- **Telegram**: API ID, API hash, and user session string from owner `entity_info` entries.

## Dependencies

None. The contacts module is a leaf module.

## Implementation Notes

- Contacts sync runs as an in-process poll loop inside `butlers up`, not a standalone connector: an
  immediate incremental run on startup, then polling every 15 minutes
  (`ContactsSyncRuntime`; `trigger_immediate_sync()` is the poller trigger).
- Rollout: `[modules.contacts]` with `provider = "google"` on general, health and relationship;
  deliberately absent from switchboard (routing plane) and messenger (delivery plane).
- Telegram absence detection is part of the shared sync engine's completion path, so polling and
  `contacts_sync_now` use the same guard before successful state persistence. It requires an explicit
  complete-full-fetch receipt after all pages and local contact applications finish. Incremental sync
  never infers absence, including an empty unchanged-hash result; the current Telegram incremental
  implementation still returns the full list even when its hash is unchanged.
- A full empty list refuses deletion even if its hash matches. A removed fraction strictly greater than
  half also refuses, where removed is the set of pre-cycle active Telegram links absent from the
  snapshot, `|active - present| / |active|`, so contacts added in the same snapshot cannot offset
  removals; an empty active set deletes nothing. `max_shrink_fraction` defaults to `0.5`, is finite in
  `[0, 1)`, and accepts equality. This allows ordinary individual removals while parking substantial
  list loss; it is an implementation sanity parameter, not permission to delete entities or facts.
  Refusals log `contact_sync_absence_refused` with a bounded reason and counts. Safe positive work
  and an otherwise successful cursor may persist when only absence inference is refused.
- A reappearing Telegram contact whose inactive local anchor entity was merged away or deleted fails
  closed every cycle until an operator repairs the link. The failed cycle records the content-free
  `contacts_sync_state.last_error` value `contacts source anchor unavailable` (instead of the generic
  `contacts sync failed`) and logs `contact_sync_anchor_stalled` with a bounded reason, no ids.
- Accepted absence marks only the configured account's active Telegram source links, preserving
  entities, Telegram handles and Google/other-provider links. `contact_sync_deleted_source` records
  new transitions after commit. Absence changes, affected version-cache eviction and successful
  cursor/state save share one hosting transaction. Earlier per-contact positive commits survive a
  later failure and are replayable; remote owner calls remain outside that transaction. Ordinary
  logs are not a durable outbox and do not promise exactly-once emission after a crash.
- Same-account manual/poll cycles serialize. Persistent state and absent-source baseline checks
  refuse stale competing-engine finalization or links seen again during a fetch. Other accounts
  progress independently. Reappearance bypasses an unchanged etag/fingerprint cache when its exact
  source anchor is inactive, retains that still-live entity anchor and uses the existing upsert to
  clear `deleted_at`; inactive links do not become generally routable. No schema/grant change or
  automatic retry is introduced. See the canonical [completeness guard](../../openspec/specs/module-contacts/spec.md).
- Backfill never auto-merges an ambiguous name match. A provider overwrites only the fields it owns
  in the provenance map; locally edited fields are preserved.
- Migration `contacts_001` adds the `contacts_source_links.local_contact_id` FK only when
  `contacts` exists in the current schema (`to_regclass(format('%I.contacts', current_schema()))`),
  because schemas such as `general` and `health` enable the module without owning CRM `contacts`.

## Related Pages

- [Module System](module-system.md)
- [Identity Model](../concepts/identity-model.md) -- entity anchor, channel facts, resolution


### Relationship ownership and bounded channel diagnostics

Contacts keeps entity/profile/source-link work on its hosting pool. Channel reads
and reports use the daemon's configured Switchboard client, a fixed Relationship
destination, and the registered `contacts_resolve_channel` / `contacts_report_channel`
operations in the production-enabled `contacts_ingress` group. Existing explicitly
selected legacy groups keep their original members. The target uses its ordinary pool and the existing central writer,
selecting `contacts-backfill` and a fresh third-party report itself. Subject UUIDs,
provider locators and transport trace context supply no owner or approval authority.
No foreign SQL fallback, extra grant, role switch or automatic retry is added.

Source-link matching remains first. Email uses strip/lower normalization; phones
retain the stripped exact and digits/plus alternatives. Only a completed empty
owning lookup permits fallback to the next channel or local name/new-person path.
Ambiguous matches refuse automatic merge, and unavailable or malformed responses
leave the contact apply incomplete. Initial startup before runtime transport is
wired follows the same unavailable rule. Generic identity resolution is unchanged.

An applied contact counts local entity/source-link processing, not active channel
facts. Candidate and pending-approval channel results remain explicitly incomplete
and retain the central writer's ordinary adoption/decision rules. A timeout after
a possible remote commit is uncertain; ordinary later replay uses the same central
idempotency rules. Child calls remain outside the local provenance transaction;
there is no distributed atomicity promise.

Channel failures emit `contacts_channel_report_failed` with only entity UUID and
internally selected channel type/predicate. They omit values, exception strings,
tracebacks, chains, payloads and arbitrary failure-class labels. Failed entries
continue to the next channel and restore temporary report context. Unrelated
mandatory CRM sync events retain their existing fields. Historical rollout cause
and historical log exposure remain unverified; this repair neither erases logs
nor changes credentials, provider behavior or temporal cutover ownership.
