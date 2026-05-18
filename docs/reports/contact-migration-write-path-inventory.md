# Contact Migration Write-Path Inventory

**Issue:** bu-j1t5n  
**Date:** 2026-05-18  
**Scope:** Every writer of `public.contacts` and `public.contact_info` in production code paths  
**Related:** Brief §6b Amendment 1.1.B + Amendment 1.1.C bead 2; blocks bead 3 (central writer) and bead 4 (per-writer dual-write shims)

---

## Summary

| Stat | Value |
|---|---|
| Total writer entries | 32 |
| Writers of `public.contacts` | 16 |
| Writers of `public.contact_info` | 16 |
| Owning butler: `relationship` | 15 |
| Owning butler: `core/identity` | 4 |
| Owning butler: `core/oauth` | 4 |
| Owning butler: `contacts-module` | 6 |
| Owning butler: `core/memory-api` | 3 |

---

## Inventory Table

### Group A — Core Identity Layer (`src/butlers/identity.py`)

| File:Line | Butler | Operation | Target Table | Current Write Shape | Re-pointing Plan Stub |
|---|---|---|---|---|---|
| `src/butlers/identity.py:325` | core/identity (all butlers via Switchboard) | INSERT | `public.contacts` | `(name, entity_id, metadata)` — creates a temporary "unknown sender" contact with `metadata.needs_disambiguation=true`; atomically follows INSERT into `public.contact_info` below | Create entity via `entities` INSERT + emit triple `(entity_id, has-channel, channel_value)` via `relationship_assert_fact()` in one transaction; rename function `create_temp_entity()` per RFC 0004 Amendment 2 |
| `src/butlers/identity.py:338` | core/identity (all butlers via Switchboard) | INSERT + ON CONFLICT DO NOTHING | `public.contact_info` | `(contact_id, type=<channel_type>, value=<channel_value>, is_primary=true)` — links new temp contact to its originating channel; race-safe via `ON CONFLICT (type, value) DO NOTHING` | Emit `(entity_id, has-<channel_type>, channel_value)` triple via `relationship_assert_fact()` instead; no separate `contact_info` row needed |

---

### Group B — Switchboard Ingestion Path (`roster/switchboard/tools/identity/inject.py`)

| File:Line | Butler | Operation | Target Table | Current Write Shape | Re-pointing Plan Stub |
|---|---|---|---|---|---|
| `roster/switchboard/tools/identity/inject.py:152` | switchboard | — (delegates to `create_temp_contact()`) | `public.contacts` + `public.contact_info` | Calls `identity.create_temp_contact()` — all writes covered by Group A entries above | Delegate to `create_temp_entity()` after re-point; no direct SQL here |

> **Note:** `inject.py` itself contains no SQL; it calls `create_temp_contact()` from `src/butlers/identity.py`. The write-path entry is Group A. This row is included for traceability but does not add new SQL writers.

---

### Group C — OAuth Callback (`src/butlers/api/routers/oauth.py`)

| File:Line | Butler | Operation | Target Table | Current Write Shape | Re-pointing Plan Stub |
|---|---|---|---|---|---|
| `src/butlers/api/routers/oauth.py:1017` | core/oauth | INSERT (fallback) | `public.contacts` | `(name='Owner', entity_id, metadata='{}')` — creates minimal owner contact only when `oauth._register_google_health_contact_info()` finds no existing contact linked to the owner entity | Post-migration: owner contact row still needed (triples do not replace contacts entirely until cut-over); add dual-write shim for `contact_info` row below; contact INSERT can remain until bead 8 |
| `src/butlers/api/routers/oauth.py:1026` | core/oauth | INSERT + ON CONFLICT (type, value) DO NOTHING | `public.contact_info` | `(contact_id=owner_contact_id, type='google_health', value=<google_user_id>, secured=false)` — idempotent owner channel registration on Google Health OAuth success | Emit `(owner_entity_id, has-google-health, google_user_id)` triple via `relationship_assert_fact()` in dual-write shim; both paths live until bead 8 |

---

### Group D — Google Health Connector (`src/butlers/connectors/google_health.py`)

| File:Line | Butler | Operation | Target Table | Current Write Shape | Re-pointing Plan Stub |
|---|---|---|---|---|---|
| `src/butlers/connectors/google_health.py:585` | core/connectors | INSERT (fallback) | `public.contacts` | `(name='Owner', entity_id, metadata='{}')` — identical fallback to `oauth.py:1017`; ensures owner contact exists before registering `google_health` channel | Same as oauth.py:1017; contact INSERT stays until bead 8 |
| `src/butlers/connectors/google_health.py:596` | core/connectors | INSERT + ON CONFLICT (type, value) DO NOTHING | `public.contact_info` | `(contact_id=owner_contact_id, type='google_health', value=<google_user_id>, secured=false)` — called from `upsert_google_health_contact_info()` which is invoked by the OAuth callback | Dual-write shim: also emit triple `(owner_entity_id, has-google-health, google_user_id)` via `relationship_assert_fact()` |

---

### Group E — Contacts Module — Backfill Engine (`src/butlers/modules/contacts/backfill.py`)

| File:Line | Butler | Operation | Target Table | Current Write Shape | Re-pointing Plan Stub |
|---|---|---|---|---|---|
| `src/butlers/modules/contacts/backfill.py:278` | contacts-module | INSERT | `public.contacts` | Full CRM row: `(name, first_name, last_name, nickname, company, job_title, avatar_url, metadata, entity_id)` — happy path when entity resolution succeeds | After bead 3 lands: emit entity-identifying facts (`has-name`, `works-at`, etc.) via `relationship_assert_fact()` as dual-write; contact row itself remains until bead 8 |
| `src/butlers/modules/contacts/backfill.py:301` | contacts-module | INSERT | `public.contacts` | Same fields minus `entity_id` — fallback when entity resolution fails (`entity_id=NULL`) | Same dual-write approach; entity creation should be guaranteed before bead 8 cut-over |
| `src/butlers/modules/contacts/backfill.py:484` | contacts-module | UPDATE | `public.contacts` | Dynamic SET clause over any subset of CRM columns (name, company, job_title, avatar_url, metadata, etc.) via `_update_existing_contact()` | Dual-write shim: propagate changed facts (name, company, etc.) to `relationship_assert_fact()` with supersession |
| `src/butlers/modules/contacts/backfill.py:549` | contacts-module | UPDATE | `public.contact_info` | `UPDATE contact_info SET is_primary=false WHERE contact_id=$1 AND type=$2` — demotes existing primaries before promoting a new one | No triple equivalent; `is_primary` becomes a fact attribute on the triple itself; shim: update triple metadata |
| `src/butlers/modules/contacts/backfill.py:556` | contacts-module | UPDATE | `public.contact_info` | `UPDATE contact_info SET is_primary=true WHERE id=$1` — marks the winning primary | Same as above; encode into triple's `metadata.is_primary` field |
| `src/butlers/modules/contacts/backfill.py:566` | contacts-module | UPDATE | `public.contact_info` | `UPDATE contact_info SET is_primary=false WHERE contact_id=$1 AND type=$2` — pre-insert demote on is_primary path | Same shim as :549 |
| `src/butlers/modules/contacts/backfill.py:579` | contacts-module | INSERT + ON CONFLICT DO NOTHING | `public.contact_info` | `(contact_id, type, value, label, is_primary)` — bulk upsert from `CanonicalContact` for all email/phone/url/telegram entries; `secured` never touched | Emit `(entity_id, has-<type>, value)` triple per entry via `relationship_assert_fact()` in dual-write; label + is_primary encoded in triple metadata |

---

### Group F — Contacts Module — Telegram Enrichment (`src/butlers/modules/contacts/__init__.py`)

| File:Line | Butler | Operation | Target Table | Current Write Shape | Re-pointing Plan Stub |
|---|---|---|---|---|---|
| `src/butlers/modules/contacts/__init__.py:995` | contacts-module | INSERT + ON CONFLICT DO NOTHING | `public.contact_info` | `(contact_id, type='telegram_chat_id', value=<chat_id>, label=NULL, is_primary=false)` — post-sync enrichment that resolves private Telegram chat IDs from dialogs | Emit `(entity_id, has-telegram-chat-id, chat_id)` triple via `relationship_assert_fact()`; ON CONFLICT idempotency preserved by triple dedup |

---

### Group G — Relationship Butler — MCP Contact Tools (`roster/relationship/tools/contacts.py`)

| File:Line | Butler | Operation | Target Table | Current Write Shape | Re-pointing Plan Stub |
|---|---|---|---|---|---|
| `roster/relationship/tools/contacts.py:282` | relationship | INSERT | `public.contacts` (via `contact_create()`) | Full CRM row built dynamically from schema introspection: at minimum `(name/first_name/last_name)`, optionally `(company, job_title, entity_id, metadata, etc.)` | Dual-write shim in `contact_create()`: after creating contact row, emit `(entity_id, has-name, display_name)` and other name facts via `relationship_assert_fact()` |
| `roster/relationship/tools/contacts.py:378` | relationship | UPDATE | `public.contacts` (via `contact_update()`) | Dynamic SET over any CRM field (name, first_name, last_name, nickname, company, job_title, pronouns, gender, avatar_url, metadata, listed) excluding `roles` | Dual-write: for changed name/company fields, emit superseding fact via `relationship_assert_fact()` |
| `roster/relationship/tools/contacts.py:518` | relationship | UPDATE | `public.contacts` (via `contact_archive()`) | `SET archived_at=now(), listed=false` — soft-archive | Emit state-marker triple `(entity_id, contact-archived, timestamp)` via `relationship_assert_fact()` in shim |
| `roster/relationship/tools/contacts.py:619` | relationship | UPDATE | `public.contacts` (via `contact_merge()`) | `SET archived_at=now(), listed=false WHERE id=source_id` — archives source after re-pointing child tables including `contact_info.contact_id` | Dual-write: add triple tombstone for merged contact; merge via entity_merge already handles fact re-pointing |

---

### Group H — Relationship Butler — MCP Contact-Info Tools (`roster/relationship/tools/contact_info.py`)

| File:Line | Butler | Operation | Target Table | Current Write Shape | Re-pointing Plan Stub |
|---|---|---|---|---|---|
| `roster/relationship/tools/contact_info.py:228` | relationship | UPDATE | `public.contact_info` (in `contact_info_add()`) | `SET is_primary=false WHERE contact_id=$1 AND type=$2` — demotes existing primaries (non-owner path only; owner path queued as pending_action) | Shim: update `is_primary` metadata on existing triples of same type |
| `roster/relationship/tools/contact_info.py:237` | relationship | INSERT | `public.contact_info` (in `contact_info_add()`) | `(contact_id, type, value, label, is_primary, context)` — MCP-level add for non-owner contacts; owner mutations queued to `pending_actions` | Emit `(entity_id, has-<type>, value)` triple with metadata `{label, is_primary, context}` via `relationship_assert_fact()`; retain pending_actions gate for owner |
| `roster/relationship/tools/contact_info.py:337` | relationship | UPDATE | `public.contact_info` (in `contact_info_update()`) | `SET is_primary=false WHERE contact_id=$1 AND type=$2 AND is_primary=true AND id!=$3` — demote-sibling step on primary toggle | Shim: update metadata on sibling triples |
| `roster/relationship/tools/contact_info.py:349` | relationship | UPDATE | `public.contact_info` (in `contact_info_update()`) | Dynamic SET over `(value, label, is_primary)` — owner mutations queued; non-owner written immediately | Update triple field values via `relationship_assert_fact()` supersession |
| `roster/relationship/tools/contact_info.py:404` | relationship | DELETE | `public.contact_info` (in `contact_info_remove()`) | `DELETE WHERE id=$1` — hard delete by ID | Emit superseding triple with `validity='retracted'` via `relationship_assert_fact()` (or add a `contact_info_remove` shim that retract-flags the triple) |

---

### Group I — Relationship Butler — Dashboard API (`roster/relationship/api/router.py`)

| File:Line | Butler | Operation | Target Table | Current Write Shape | Re-pointing Plan Stub |
|---|---|---|---|---|---|
| `roster/relationship/api/router.py:898` | relationship | UPDATE | `public.contacts` (`POST /contacts/{id}/link-entity`) | `SET entity_id=$1 WHERE id=$2` — assigns entity FK to a contact | After cut-over this becomes a no-op (entity_id on the triple is already the subject); until bead 8: keep |
| `roster/relationship/api/router.py:968` | relationship | UPDATE | `public.contacts` (`POST /contacts/{id}/create-entity`) | `SET entity_id=$1 WHERE id=$2` — same as above, after auto-creating a new entity | Same stub as :898 |
| `roster/relationship/api/router.py:1502` | relationship | UPDATE | `public.contacts` (`PATCH /contacts/{id}`) | Dynamic SET over `(first_name, last_name, name, nickname, company, job_title, preferred_channel)` | Dual-write: propagate changed name/company fields as superseding facts via `relationship_assert_fact()` |
| `roster/relationship/api/router.py:1558` | relationship | DELETE | `public.contacts` (`DELETE /contacts/{id}`) | `DELETE WHERE id=$1` (CASCADE drops `contact_info` rows); also removes `contacts_source_links` | Tombstone: emit retracted triples for all facts anchored to this entity; post-bead-8: entity is the anchor so contact DELETE will be a tombstone-only operation |
| `roster/relationship/api/router.py:1586` | relationship | UPDATE | `public.contacts` (`POST /contacts/{id}/archive`) | `SET archived_at=now(), updated_at=now()` | Emit state-marker triple via `relationship_assert_fact()` |
| `roster/relationship/api/router.py:1612` | relationship | UPDATE | `public.contacts` (`POST /contacts/{id}/unarchive`) | `SET archived_at=NULL, updated_at=now()` | Emit superseding state-marker triple (unarchived) via `relationship_assert_fact()` |
| `roster/relationship/api/router.py:1647` | relationship | UPDATE | `public.contacts` (`POST /contacts/{id}/confirm`) | `SET metadata=$1 (pop needs_disambiguation)` | Emit `(entity_id, contact-confirmed, timestamp)` triple via `relationship_assert_fact()` |
| `roster/relationship/api/router.py:1704` | relationship | DELETE | `public.contact_info` (inside `POST /contacts/{id}/merge`) | `DELETE WHERE contact_id=source AND (type,value) IN target` — removes duplicate contact_info rows from source before moving remainder | Shim: retract duplicate triples from source entity before entity_merge handles the rest |
| `roster/relationship/api/router.py:1717` | relationship | UPDATE | `public.contact_info` (inside `POST /contacts/{id}/merge`) | `SET contact_id=target WHERE contact_id=source` — migrates surviving contact_info rows to target contact | Post-cut-over: triples already anchor to entity_id; entity_merge re-points fact subjects — this UPDATE becomes a no-op |
| `roster/relationship/api/router.py:1755` | relationship | DELETE | `public.contacts` (inside `POST /contacts/{id}/merge`) | `DELETE WHERE id=source_id` — removes merged-away contact | Tombstone source entity triples; keep until bead 8 |
| `roster/relationship/api/router.py:1919` | relationship | INSERT | `public.contact_info` (`POST /contacts/{id}/contact-info`) | `(contact_id, type, value, is_primary, secured, parent_id, context)` — full dashboard manual-entry for any contact_info type including `secured=true` credentials | Dual-write shim: also emit `(entity_id, has-<type>, value)` triple via `relationship_assert_fact()` (non-credential rows only; `secured=true` rows stay in `public.entity_info` per RFC 0004 Amendment 2 carve-out) |
| `roster/relationship/api/router.py:1988` | relationship | DELETE | `public.contact_info` (`DELETE /contacts/{id}/contact-info/{info_id}`) | `DELETE WHERE id=$1` — hard delete by ID | Emit retraction triple via `relationship_assert_fact()` |
| `roster/relationship/api/router.py:2059` | relationship | UPDATE | `public.contact_info` (`PATCH /contacts/{id}/contact-info/{info_id}`) | Dynamic SET over `(type, value, is_primary, context)` | Update triple via superseding `relationship_assert_fact()` call |
| `roster/relationship/api/router.py:2072` | relationship | UPDATE | `public.contact_info` (inside PATCH, sibling demote) | `SET is_primary=false WHERE contact_id=$1 AND type=$2 AND parent_id IS NULL AND id!=$3` — clears sibling primaries when toggling `is_primary=true` | Shim: update `is_primary` metadata on sibling triples |

---

### Group J — Relationship Butler — Stay-in-Touch Tool (`roster/relationship/tools/stay_in_touch.py`)

| File:Line | Butler | Operation | Target Table | Current Write Shape | Re-pointing Plan Stub |
|---|---|---|---|---|---|
| `roster/relationship/tools/stay_in_touch.py:24` | relationship | UPDATE | `public.contacts` (via `stay_in_touch_set()`) | `SET stay_in_touch_days=$2, updated_at=now() WHERE id=$1` — sets/clears cadence override | `stay_in_touch_days` is a CRM preference, not a channel fact; post-cut-over encode as `(entity_id, stay-in-touch-cadence, days)` triple with supersession |

---

### Group K — Core Memory API (`src/butlers/api/routers/memory.py`)

| File:Line | Butler | Operation | Target Table | Current Write Shape | Re-pointing Plan Stub |
|---|---|---|---|---|---|
| `src/butlers/api/routers/memory.py:1199` | core/memory-api | UPDATE | `public.contacts` (`POST /api/memory/entities/{id}/linked-contact`) | `SET entity_id=$1 WHERE id=$2` — links an existing contact to an entity | Post-cut-over: contact row still tracks entity FK; shim is minimal — keep until bead 8 |
| `src/butlers/api/routers/memory.py:1304` | core/memory-api | UPDATE | `public.contacts` (`POST /api/memory/entities/{id}/merge`) | `SET entity_id=target WHERE entity_id=source` — re-links all contacts from merged source entity | After cut-over: triples anchor to entity; re-point facts via entity_merge already; this UPDATE on contacts remains for backward compat until bead 8 |
| `src/butlers/api/routers/memory.py:1404` | core/memory-api | UPDATE | `public.contacts` (`DELETE /api/memory/entities/{id}`) | `SET entity_id=NULL WHERE entity_id=$1` — unlinks contacts when their entity is deleted | Post-cut-over: entity deletion retracts all triples; contact unlink is a tombstone side-effect; keep until bead 8 |
| `src/butlers/api/routers/memory.py:1506` | core/memory-api | UPDATE | `public.contacts` (`DELETE /api/memory/entities/{id}/linked-contact`) | `SET entity_id=NULL WHERE entity_id=$1` — explicit contact unlink endpoint | Same as :1404 |

---

### Group L — Relationship Butler — vCard Import (`roster/relationship/tools/vcard.py`)

| File:Line | Butler | Operation | Target Table | Current Write Shape | Re-pointing Plan Stub |
|---|---|---|---|---|---|
| `roster/relationship/tools/vcard.py:219` | relationship | INSERT (via `contact_create()`) | `public.contacts` | Creates contact from vCard FN/N fields; delegates to `roster/relationship/tools/contacts.py:contact_create` — see Group G entry | Shim lives in `contact_create()`; vcard.py itself needs no change |
| `roster/relationship/tools/vcard.py:233` | relationship | INSERT (via `contact_info_add()`) | `public.contact_info` | Phone entries from vCard TEL fields: `(contact_id, type='phone', value, label)` | Shim lives in `contact_info_add()`; vcard.py itself needs no change |
| `roster/relationship/tools/vcard.py:253` | relationship | INSERT (via `contact_info_add()`) | `public.contact_info` | Email entries from vCard EMAIL fields: `(contact_id, type='email', value, label)` | Same shim in `contact_info_add()` |

---

## Notes and Open Questions

1. **`secured=true` carve-out:** `roster/relationship/api/router.py:1919` is the only write path that can insert `secured=true` rows into `public.contact_info`. Per RFC 0004 Amendment 2 §"public.entity_info", credential rows (`secured=true`) are **out of scope** for the triples migration and will move to `public.entity_info` instead of `relationship.facts`. The re-pointing plan stub for that row reflects this.

2. **`contact_merge()` re-points `contact_info.contact_id`:** `roster/relationship/tools/contacts.py` line ~581 includes `contact_info` in its `_child_tables` list and does `UPDATE contact_info SET contact_id=$1 WHERE contact_id=$2` inside the transaction. This is effectively a bulk UPDATE (the operation is implicit, not a named line in the inventory above because it is generated by a loop). The dual-write shim for `contact_merge` must also handle fact-subject re-pointing for all triples of the source entity.

3. **No dedicated contact-creation endpoint in `relationship/api/router.py`:** Contact creation from the dashboard goes exclusively through the MCP `contact_create` tool (Group G), not through a REST endpoint. Only MCP-authenticated sessions can create contacts.

4. **`src/butlers/modules/contacts/backfill.py` UPDATE at line 484** updates CRM metadata columns only (name, company, avatar, provenance metadata), not channel identifiers — so the dual-write shim only needs to propagate changed name/company facts, not re-emit all channel triples on each sync cycle.

5. **Migrations excluded:** `roster/relationship/migrations/003_consolidate_contacts_to_public.py:69` contains a one-shot schema-migration INSERT. This is not a production write path and is not in scope for dual-write shims.

6. **`roster/home/modules/__init__.py`:** The Home butler references `contact_info` only in comments/docstrings (checking for `home_assistant_token` type). No writes found.

7. **`src/butlers/chronicler/adapters/sessions.py`:** Reads `contact_info` via JOIN for session display-name resolution. No writes.

8. **`src/butlers/api/routers/ingestion_events.py`:** Reads `contact_info` to resolve sender identity. No writes.

---

## Discovered Follow-ups

- **Follow-up 1 (low priority):** `contact_merge()` in `roster/relationship/tools/contacts.py` uses a table loop to re-point `contact_info.contact_id`. This implicit UPDATE is not separately callable and will need a dedicated dual-write shim step when bead 4 is implemented for the merge path.
- **Follow-up 2 (informational):** The `secured=true` migration to `public.entity_info` (Group I, line 1919) is a separate sub-task not covered by the triples migration; it should be tracked separately as it may need its own bead between bead 8 (contact_info deprecation) and bead 10 (drop table).
