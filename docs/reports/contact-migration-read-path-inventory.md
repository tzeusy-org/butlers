# Contact Migration Read-Path Inventory

**Issue:** bu-wkjc2
**Date:** 2026-05-18
**Scope:** Every reader of `public.contacts` and `public.contact_info` in production code paths
**Related:** Brief §6b Amendment 13 + Amendment 1.1.C bead 4.5; blocks bead 7 (read-path cut-over)

---

## Summary

| Stat | Value |
|---|---|
| Total reader entries | 62 |
| Readers of `public.contacts` | 40 |
| Readers of `public.contact_info` | 28 |
| Readers of both (JOIN) | 6 |
| Owning butler: `relationship` | 31 |
| Owning butler: `core/identity` | 6 |
| Owning butler: `core/daemon` | 3 |
| Owning butler: `core/memory-api` | 5 |
| Owning butler: `core/briefing-api` | 4 |
| Owning butler: `core/approvals` | 4 |
| Owning butler: `contacts-module` | 5 |
| Owning butler: `core/chronicler` | 1 |
| Owning butler: `core/connectors` | 3 |

---

## Inventory Table

### Group A — Core Identity Layer (`src/butlers/identity.py`)

| File:Line | Butler | Read Shape (what fields, what filter) | Target Table | Re-pointing Plan |
|---|---|---|---|---|
| `src/butlers/identity.py:113` | core/identity (all butlers via Switchboard) | `SELECT c.id, c.name, e.roles, c.entity_id WHERE ci.type=$1 AND ci.value=$2 LIMIT 1` — primary channel-to-contact resolution path | `public.contact_info JOIN public.contacts LEFT JOIN public.entities` | Re-point to entity-based lookup: `SELECT entity_id, canonical_name, roles FROM public.entities WHERE EXISTS (SELECT 1 FROM ... triple WHERE predicate='has-<type>' AND object_value=$value)`; use `resolve_contact_by_channel()` replacement that queries triples instead of `contact_info` |
| `src/butlers/identity.py:141` | core/identity (all butlers via Switchboard) | Same shape as :113 — telegram_user_client fallback: re-queries with `type='telegram_user_id'` | `public.contact_info JOIN public.contacts LEFT JOIN public.entities` | Same re-point as :113; telegram fallback chain encoded in triple lookup |
| `src/butlers/identity.py:172` | core/identity (all butlers via Switchboard) | Same shape as :113 — WhatsApp phone-number fallback: `type='phone' AND value=$1` | `public.contact_info JOIN public.contacts LEFT JOIN public.entities` | Same re-point as :113; phone fallback reads `has-phone` triple on entity |
| `src/butlers/identity.py:263` | core/identity (all butlers via Switchboard) | Same shape as :113 — re-check inside `create_temp_contact()` transaction to avoid double-creation race | `public.contact_info JOIN public.contacts LEFT JOIN public.entities` | Same re-point as :113; race guard moves into entity-triple creation path |

---

### Group B — Switchboard Identity Injection (`roster/switchboard/tools/identity/inject.py`)

| File:Line | Butler | Read Shape (what fields, what filter) | Target Table | Re-pointing Plan |
|---|---|---|---|---|
| `roster/switchboard/tools/identity/inject.py:134` | switchboard | Delegates to `resolve_contact_by_channel()` — no direct SQL; SQL is Group A | `public.contact_info JOIN public.contacts` | No direct re-point needed; re-point lives in Group A. This call site switches when Group A switches |

> **Note:** `inject.py` contains no direct SQL; it calls `resolve_contact_by_channel()` from `src/butlers/identity.py`. Included for traceability.

---

### Group C — Core Daemon (`src/butlers/daemon.py`)

| File:Line | Butler | Read Shape (what fields, what filter) | Target Table | Re-pointing Plan |
|---|---|---|---|---|
| `src/butlers/daemon.py:852` | core/daemon (`_resolve_contact_channel_identifier`) | `SELECT ci.value FROM public.contact_info WHERE ci.contact_id=$1 AND ci.type=$2 ORDER BY CASE WHEN ci.context=$3 THEN 0 ... END LIMIT 1` — context-aware delivery address lookup for `notify()` | `public.contact_info` | Re-point to `public.entity_info` triple lookup: `SELECT value FROM relationship.facts WHERE entity_id=$entity_id AND predicate='has-<type>'` ordered by `metadata->>'context'`; or query new `entity_info` table once it's in place |
| `src/butlers/daemon.py:873` | core/daemon (`_resolve_contact_channel_identifier`) | Same as :852 without context-aware ordering — fallback path when no `msg_context` provided: `WHERE ci.contact_id=$1 AND ci.type=$2 ORDER BY ci.is_primary DESC LIMIT 1` | `public.contact_info` | Same re-point as :852; select primary triple when no context filter |

---

### Group D — Approvals Module (`src/butlers/modules/approvals/`)

| File:Line | Butler | Read Shape (what fields, what filter) | Target Table | Re-pointing Plan |
|---|---|---|---|---|
| `src/butlers/modules/approvals/_shared.py:61` | core/approvals (`is_primary_contact`) | `SELECT is_primary FROM public.contact_info WHERE contact_id=$1 AND type=$2 AND value=$3` — primacy check for approval gate | `public.contact_info` | Post-cut-over: `is_primary` becomes metadata on the triple; re-point to `SELECT metadata->>'is_primary' FROM relationship.facts WHERE entity_id=$entity_id AND predicate='has-<type>' AND content=$value` |
| `src/butlers/modules/approvals/email_guard.py:56` | core/approvals (`_get_email_context`) | `SELECT context FROM public.contact_info WHERE type='email' AND value=$1` — fetch context tag for outbound email guard | `public.contact_info` | Re-point to `SELECT metadata->>'context' FROM relationship.facts WHERE predicate='has-email' AND content=$email`; or promote context to a separate triple attribute |
| `src/butlers/modules/approvals/gate.py:207` | core/approvals (`_resolve_target_contact`) | `SELECT id AS contact_id, name, roles, entity_id FROM public.contacts WHERE id=$1::uuid` — direct UUID lookup for approval gate when caller provides contact_id | `public.contacts` | Re-point to entity lookup: `SELECT id, canonical_name, roles FROM public.entities WHERE id = (SELECT entity_id FROM public.contacts WHERE id=$1)` until bead 8; post-bead-8 pass entity_id directly |
| `src/butlers/modules/approvals/gate.py:248` | core/approvals | Delegates to `resolve_contact_by_channel()` — channel-based fallback; SQL is Group A | `public.contact_info JOIN public.contacts` | No direct re-point; see Group A |

---

### Group E — Memory Module Owner-Resolution (`src/butlers/modules/memory/tools/preferences.py`)

| File:Line | Butler | Read Shape (what fields, what filter) | Target Table | Re-pointing Plan |
|---|---|---|---|---|
| `src/butlers/modules/memory/tools/preferences.py:42` | core/memory | `SELECT e.id, e.canonical_name FROM public.contacts c JOIN public.entities e ON c.entity_id=e.id WHERE 'owner'=ANY(e.roles) LIMIT 1` — owner entity resolution via contacts JOIN | `public.contacts JOIN public.entities` | Pre-cut-over compatible: owner entity can be found directly via `SELECT id, canonical_name FROM public.entities WHERE 'owner'=ANY(roles) LIMIT 1`; remove the contacts JOIN (the fallback path at line :56 already does this) |

---

### Group F — Core Briefing APIs

| File:Line | Butler | Read Shape (what fields, what filter) | Target Table | Re-pointing Plan |
|---|---|---|---|---|
| `src/butlers/api/briefing/cache.py:117` | core/briefing-cache | `SELECT c.id FROM public.contacts c JOIN public.entities e ON c.entity_id=e.id WHERE 'owner'=ANY(e.roles) LIMIT 1` — owner contact_id for cache invalidation | `public.contacts JOIN public.entities` | Remove contacts JOIN: use `SELECT id FROM public.entities WHERE 'owner'=ANY(roles) LIMIT 1`; cache invalidation only needs entity_id post-cut-over |
| `src/butlers/api/routers/dashboard_briefing.py:135` | core/briefing-api | `SELECT c.id FROM public.contacts c JOIN public.entities e ON c.entity_id=e.id WHERE 'owner'=ANY(e.roles) LIMIT 1` — owner assertion for dashboard auth gate | `public.contacts JOIN public.entities` | Remove contacts JOIN: `SELECT id FROM public.entities WHERE 'owner'=ANY(roles) LIMIT 1`; assertion only needs entity exists |
| `src/butlers/api/routers/system.py:491` | core/system-api | `SELECT e.id FROM public.contacts c JOIN public.entities e ON c.entity_id=e.id WHERE 'owner'=ANY(e.roles) LIMIT 1` — owner existence check for system auth | `public.contacts JOIN public.entities` | Same as :135; remove contacts JOIN |
| `src/butlers/api/routers/preferences.py:88` | core/preferences-api | `SELECT e.id FROM public.contacts c JOIN public.entities e ON c.entity_id=e.id WHERE 'owner'=ANY(e.roles) LIMIT 1` — owner entity_id resolution for preferences lookup | `public.contacts JOIN public.entities` | Remove contacts JOIN; query entities directly |

---

### Group G — Core Approvals Dashboard API (`src/butlers/api/routers/approvals.py`)

| File:Line | Butler | Read Shape (what fields, what filter) | Target Table | Re-pointing Plan |
|---|---|---|---|---|
| `src/butlers/api/routers/approvals.py:321` | core/approvals-api | `SELECT id, name, COALESCE(roles, '{}') AS roles FROM public.contacts WHERE id=$1` — resolve target contact display info when pending_action has contact_id in tool_args | `public.contacts` | Re-point: post-cut-over contacts row may not exist; look up entity instead: `SELECT canonical_name, roles FROM public.entities WHERE id=(SELECT entity_id FROM public.contacts WHERE id=$1)`; keep until bead 8 |

---

### Group H — Core Memory API (`src/butlers/api/routers/memory.py`)

| File:Line | Butler | Read Shape (what fields, what filter) | Target Table | Re-pointing Plan |
|---|---|---|---|---|
| `src/butlers/api/routers/memory.py:862` | core/memory-api (entity list) | `(SELECT c.id FROM public.contacts c WHERE c.entity_id=e.id LIMIT 1) AS linked_contact_id` — correlated subquery in entity list to include linked contact_id | `public.contacts` | Post-cut-over: `linked_contact_id` is available via the entity row itself; remove subquery or replace with entity-native FK |
| `src/butlers/api/routers/memory.py:957` | core/memory-api (entity get) | `(SELECT c.id FROM public.contacts c WHERE c.entity_id=e.id LIMIT 1) AS linked_contact_id`, `(SELECT c.name FROM public.contacts c WHERE c.entity_id=e.id LIMIT 1) AS linked_contact_name` — two correlated subqueries in entity detail | `public.contacts` | Same as :862; `linked_contact_name` → use `entities.canonical_name` directly |
| `src/butlers/api/routers/memory.py:1194` | core/memory-api (link-contact endpoint) | `SELECT id FROM public.contacts WHERE id=$1` — verify contact exists before linking | `public.contacts` | Keep until bead 8; entity already verified separately |
| `src/butlers/api/routers/memory.py:1258` (write guard context) | core/memory-api (entity-merge) | No direct SELECT beyond the write (see write-path inventory); reads are described in write-path inventory bead | — | See write-path inventory |

---

### Group I — Core OAuth Router (`src/butlers/api/routers/oauth.py`)

| File:Line | Butler | Read Shape (what fields, what filter) | Target Table | Re-pointing Plan |
|---|---|---|---|---|
| `src/butlers/api/routers/oauth.py:1005` | core/oauth | `SELECT id FROM public.contacts WHERE entity_id=$1 ORDER BY created_at ASC LIMIT 1` — find owner contact for google_health contact_info upsert | `public.contacts` | After cut-over: query entity_info table directly (channel lives there); keep contact lookup until bead 8 |

---

### Group J — Core Search Router (`src/butlers/api/routers/search.py`)

| File:Line | Butler | Read Shape (what fields, what filter) | Target Table | Re-pointing Plan |
|---|---|---|---|---|
| `src/butlers/api/routers/search.py:154` | core/search-api | `SELECT DISTINCT ON (c.id) c.id, c.name, (SELECT ci.value ... email), (SELECT ci.value ... phone) FROM public.contacts c LEFT JOIN public.contact_info ci WHERE c.archived_at IS NULL AND (c.name ILIKE $1 OR ci.value ILIKE $1) ORDER BY c.id LIMIT $2` — full-text contacts+contact_info search | `public.contacts LEFT JOIN public.contact_info` | Post-cut-over: search against `entities` + `entity_info` tables; name search via `entities.canonical_name`; channel value search via `entity_info.value` |

---

### Group K — Core Data-Ops Router (`src/butlers/api/routers/data_ops.py`)

| File:Line | Butler | Read Shape (what fields, what filter) | Target Table | Re-pointing Plan |
|---|---|---|---|---|
| `src/butlers/api/routers/data_ops.py:69` | core/data-ops-api | `SELECT * FROM public.contacts ORDER BY id` — full table export for data download | `public.contacts` | Post-bead-8: remove contacts from export scope; add entity export; keep until tables are dropped |
| `src/butlers/api/routers/data_ops.py:70` | core/data-ops-api | `SELECT * FROM public.contact_info ORDER BY id` — full table export | `public.contact_info` | Same as :69 |

---

### Group L — Core Priority Contacts API (`src/butlers/api/routers/priority_contacts.py`)

| File:Line | Butler | Read Shape (what fields, what filter) | Target Table | Re-pointing Plan |
|---|---|---|---|---|
| `src/butlers/api/routers/priority_contacts.py:84` | core/ingestion-api | `SELECT pc.contact_id, c.name AS contact_name, array_agg(ci.value) AS contact_info_values FROM priority_contacts pc LEFT JOIN public.contacts c ON c.id=pc.contact_id LEFT JOIN public.contact_info ci ON ci.contact_id=pc.contact_id WHERE ci.secured=false GROUP BY...` — list priority contacts with name + non-sensitive identifiers | `public.contacts LEFT JOIN public.contact_info` | Re-point to `entities`/`entity_info` JOIN: use entity_id on priority_contacts row, then JOIN entities for canonical_name and entity_info for identifiers |
| `src/butlers/api/routers/priority_contacts.py:174` | core/ingestion-api | `SELECT EXISTS(SELECT 1 FROM public.contacts WHERE id=$1)` — verify contact_id exists before adding to priority_contacts | `public.contacts` | Keep until bead 8; validate entity_id instead post-cut-over |

---

### Group M — Core Chronicler / Sessions Adapter (`src/butlers/chronicler/adapters/sessions.py`)

| File:Line | Butler | Read Shape (what fields, what filter) | Target Table | Re-pointing Plan |
|---|---|---|---|---|
| `src/butlers/chronicler/adapters/sessions.py:309` | core/chronicler | `SELECT ie.id AS event_id, ie.source_channel AS channel, c.name AS display_name FROM public.ingestion_events ie LEFT JOIN public.contact_info ci ON ci.type=ie.source_channel AND ci.value=ie.source_sender_identity LEFT JOIN public.contacts c ON c.id=ci.contact_id WHERE ie.id=ANY($1)` — resolve contact display names for session episode titles | `public.contact_info LEFT JOIN public.contacts` | Re-point: use `resolve_contact_by_channel()` replacement (Group A); or add entity-based sender resolution directly here |

---

### Group N — Core Ingestion Events Router (`src/butlers/api/routers/ingestion_events.py`)

| File:Line | Butler | Read Shape (what fields, what filter) | Target Table | Re-pointing Plan |
|---|---|---|---|---|
| `src/butlers/api/routers/ingestion_events.py:604` | core/ingestion-api | Delegates to `resolve_contact_by_channel()` — no direct SQL; SQL is Group A | `public.contact_info JOIN public.contacts` | No direct re-point; see Group A |

---

### Group O — Core Briefing Job (`src/butlers/jobs/briefing.py`)

| File:Line | Butler | Read Shape (what fields, what filter) | Target Table | Re-pointing Plan |
|---|---|---|---|---|
| `src/butlers/jobs/briefing.py:543` | core/briefing-job | `SELECT c.name, id.label, id.month, id.day, id.year FROM important_dates id JOIN contacts c ON c.id=id.contact_id WHERE LOWER(id.label) LIKE '%birthday%' AND ...` — birthday highlights for weekly briefing | `contacts` | Post-cut-over: JOIN through entity_id; `contacts.name` → `entities.canonical_name`; important_dates keeps `contact_id` until cut-over complete |
| `src/butlers/jobs/briefing.py:584` | core/briefing-job | `SELECT c.name, f.content, f.metadata->>'...' FROM facts f JOIN contacts c ON c.id=(split_part(f.subject, ':', 2))::uuid WHERE f.predicate='reminder'...` — reminders from SPO facts joined to contacts for display name | `contacts` | Re-point: JOIN to entities instead: `join entities e on e.id = f.entity_id` and use `e.canonical_name`; predicate-scoped facts already anchor to entity_id |
| `src/butlers/jobs/briefing.py:644` | core/briefing-job | `SELECT c.stay_in_touch_days, c.first_name, c.last_name, ... FROM contacts c JOIN facts f ...WHERE c.stay_in_touch_days IS NOT NULL AND c.listed=true GROUP BY ... HAVING ...` — interaction-gap highlights | `contacts` | `stay_in_touch_days` is CRM preference; post-cut-over encode as triple `(entity_id, stay-in-touch-cadence, days)`; full query restructured around entity |

---

### Group P — Core Connectors

| File:Line | Butler | Read Shape (what fields, what filter) | Target Table | Re-pointing Plan |
|---|---|---|---|---|
| `src/butlers/connectors/discretion.py:220` | core/connectors (discretion/routing weight) | `SELECT COALESCE(e.roles, '{}') AS roles FROM public.contact_info ci JOIN public.contacts c ON c.id=ci.contact_id LEFT JOIN public.entities e ON e.id=c.entity_id WHERE ci.type=$1 AND ci.value=$2 LIMIT 1` — resolve sender role weight for routing triage | `public.contact_info JOIN public.contacts LEFT JOIN public.entities` | Re-point: same triple-based lookup as Group A identity resolution; `resolve_contact_by_channel()` replacement returns roles from entity |
| `src/butlers/connectors/gmail_policy.py:544` | core/connectors (GmailPolicyEvaluator) | `SELECT DISTINCT ci.value FROM public.priority_contacts pc JOIN public.contact_info ci ON ci.contact_id=pc.contact_id WHERE pc.butler='gmail' AND ci.type='email' AND ci.secured=false` — load gmail priority contact emails for inbound policy gate | `public.contact_info` (via priority_contacts JOIN) | Re-point: add `entity_id` FK to `priority_contacts`; then lookup `entity_info` for email addresses instead of `contact_info` |
| `src/butlers/connectors/google_health.py:570` | core/connectors (google_health) | `SELECT id FROM public.contacts WHERE entity_id=$1 ORDER BY created_at ASC LIMIT 1` — find owner contact to upsert google_health channel entry | `public.contacts` | After cut-over: lookup entity_info directly; keep contact lookup until bead 8 |

---

### Group Q — Contacts Module — Backfill Matchers (`src/butlers/modules/contacts/backfill.py`)

| File:Line | Butler | Read Shape (what fields, what filter) | Target Table | Re-pointing Plan |
|---|---|---|---|---|
| `src/butlers/modules/contacts/backfill.py:136` | contacts-module | `SELECT sl.local_contact_id FROM contacts_source_links sl JOIN public.contacts c ON c.id=sl.local_contact_id WHERE sl.provider=$1 AND sl.account_id=$2 AND sl.external_contact_id=$3 AND sl.deleted_at IS NULL` — match by source link | `public.contacts` (via contacts_source_links JOIN) | After cut-over: contacts_source_links may pivot to entity_id; keep JOIN until bead 8 |
| `src/butlers/modules/contacts/backfill.py:154` | contacts-module | `SELECT ci.contact_id FROM public.contact_info ci WHERE ci.type='email' AND lower(ci.value)=$1 LIMIT 1` — match existing contact by email | `public.contact_info` | Re-point: `SELECT entity_id FROM relationship.facts WHERE predicate='has-email' AND lower(content)=$1 LIMIT 1`; return entity_id not contact_id |
| `src/butlers/modules/contacts/backfill.py:169` | contacts-module | `SELECT ci.contact_id FROM public.contact_info ci WHERE ci.type='phone' AND (ci.value=$1 OR ci.value=$2) LIMIT 1` — match existing contact by phone | `public.contact_info` | Same re-point as :154; query `has-phone` triple |
| `src/butlers/modules/contacts/backfill.py:188` | contacts-module | `SELECT id FROM contacts WHERE (name ILIKE $1 OR ...) AND (archived_at IS NULL OR archived_at>now())` — fuzzy name match | `contacts` | Post-cut-over: query `public.entities` by `canonical_name ILIKE` or aliases |
| `src/butlers/modules/contacts/backfill.py:392` | contacts-module | `SELECT * FROM public.contacts WHERE id=$1` — full row fetch before update | `public.contacts` | Keep until bead 8; post-cut-over switch to entity fetch |
| `src/butlers/modules/contacts/backfill.py:535` | contacts-module | `SELECT id, is_primary FROM public.contact_info WHERE contact_id=$1 AND type=$2 AND lower(value)=lower($3)` — check if channel value already exists before upsert | `public.contact_info` | Re-point: check triple existence via `SELECT 1 FROM relationship.facts WHERE entity_id=$eid AND predicate='has-<type>' AND lower(content)=lower($value)` |
| `src/butlers/modules/contacts/backfill.py:875` | contacts-module | `SELECT 1 FROM public.contacts WHERE id=$1` — verify contact still exists before update | `public.contacts` | Keep until bead 8; verify entity exists instead |

---

### Group R — Relationship Butler — MCP Contact Tools (`roster/relationship/tools/contacts.py`)

| File:Line | Butler | Read Shape (what fields, what filter) | Target Table | Re-pointing Plan |
|---|---|---|---|---|
| `roster/relationship/tools/contacts.py:312` | relationship | `SELECT * FROM contacts WHERE id=$1` — full contact row fetch in `contact_update()` before dynamic SET | `contacts` | Keep until bead 8; fetch from entities after cut-over |
| `roster/relationship/tools/contacts.py:409` | relationship | `SELECT * FROM contacts WHERE id=$1` — full row fetch in `contact_get()` | `contacts` | Post-cut-over: `contact_get()` transitions to entity-centric view |
| `roster/relationship/tools/contacts.py:471` | relationship | `SELECT * FROM contacts WHERE {conditions} ORDER BY {order} LIMIT $2 OFFSET $3` — `contact_search()` full-text search | `contacts` | Post-cut-over: search `entities` by `canonical_name/aliases`; supplementary channel matches via `entity_info` |
| `roster/relationship/tools/contacts.py:555` | relationship | `SELECT * FROM contacts WHERE id=$1` — source contact fetch in `contact_merge()` | `contacts` | Keep until bead 8 |
| `roster/relationship/tools/contacts.py:561` | relationship | `SELECT * FROM contacts WHERE id=$1` — target contact fetch in `contact_merge()` | `contacts` | Keep until bead 8 |
| `roster/relationship/tools/contacts.py:645` | relationship | `SELECT * FROM contacts WHERE id=$1` — re-fetch after merge to return updated row | `contacts` | Keep until bead 8 |

---

### Group S — Relationship Butler — MCP Contact-Info Tools (`roster/relationship/tools/contact_info.py`)

| File:Line | Butler | Read Shape (what fields, what filter) | Target Table | Re-pointing Plan |
|---|---|---|---|---|
| `roster/relationship/tools/contact_info.py:74` | relationship (`_is_owner_contact`) | `SELECT 1 FROM public.contacts c LEFT JOIN public.entities e ON e.id=c.entity_id WHERE c.id=$1 AND 'owner'=ANY(COALESCE(e.roles,'{}'))` — owner check before contact_info mutation | `public.contacts LEFT JOIN public.entities` | Remove contacts JOIN post-cut-over; check `SELECT 1 FROM public.entities WHERE id=$entity_id AND 'owner'=ANY(roles)` |
| `roster/relationship/tools/contact_info.py:173` | relationship | `SELECT id FROM contacts WHERE id=$1` — verify contact exists in `contact_info_add()` | `contacts` | Keep until bead 8; verify entity exists instead |
| `roster/relationship/tools/contact_info.py:273` | relationship | `SELECT * FROM public.contact_info WHERE id=$1` — fetch contact_info row before update | `public.contact_info` | Post-cut-over: fetch triple by fact_id |
| `roster/relationship/tools/contact_info.py:370` | relationship | `SELECT * FROM public.contact_info WHERE contact_id=$1 AND type=$2 ORDER BY is_primary DESC, created_at` or `WHERE contact_id=$1 ORDER BY type, is_primary DESC, created_at` — `contact_info_list()` | `public.contact_info` | Re-point: `SELECT * FROM relationship.facts WHERE entity_id=$entity_id AND predicate LIKE 'has-%' ORDER BY metadata->>'is_primary' DESC` |
| `roster/relationship/tools/contact_info.py:395` | relationship | `SELECT * FROM public.contact_info WHERE id=$1` — fetch before delete in `contact_info_remove()` | `public.contact_info` | Re-point: fetch triple by fact_id |
| `roster/relationship/tools/contact_info.py:418` | relationship | `SELECT DISTINCT c.*, ci.type AS matched_type, ci.value AS matched_value FROM contacts c JOIN public.contact_info ci ON c.id=ci.contact_id WHERE ci.type=$1 AND ci.value ILIKE ...` or without type filter — `contact_search_by_info()` | `contacts JOIN public.contact_info` | Re-point: `SELECT entity_id, canonical_name FROM relationship.facts WHERE predicate='has-<type>' AND content ILIKE ...` |

---

### Group T — Relationship Butler — Entity Resolve & Facts Helpers

| File:Line | Butler | Read Shape (what fields, what filter) | Target Table | Re-pointing Plan |
|---|---|---|---|---|
| `roster/relationship/tools/_entity_resolve.py:29` | relationship | `SELECT entity_id FROM contacts WHERE id=$1` — resolve contact to entity_id | `contacts` | Post-cut-over: entity_id becomes the primary key; this helper becomes a pass-through or is removed |
| `roster/relationship/tools/facts.py:22` | relationship | `SELECT entity_id FROM contacts WHERE id=$1` — resolve contact_id → entity_id for `fact_set()` | `contacts` | Same as `_entity_resolve.py:29`; remove indirection after cut-over |

---

### Group U — Relationship Butler — Resolve (Contact Resolution Engine) (`roster/relationship/tools/resolve.py`)

| File:Line | Butler | Read Shape (what fields, what filter) | Target Table | Re-pointing Plan |
|---|---|---|---|---|
| `roster/relationship/tools/resolve.py:143` | relationship | `SELECT id, first_name, last_name, nickname, company, job_title, metadata, entity_id FROM contacts WHERE listed=true AND (LOWER(COALESCE(first_name,...))=LOWER($1) OR ...)` — exact name match in `contact_resolve()` | `contacts` | Post-cut-over: query `entities` by canonical_name and aliases |
| `roster/relationship/tools/resolve.py:214` | relationship | Same fields, ILIKE partial match | `contacts` | Same re-point as :143 |
| `roster/relationship/tools/resolve.py:249` | relationship | Same fields, multi-word partial match | `contacts` | Same re-point as :143 |
| `roster/relationship/tools/resolve.py:481` | relationship | `SELECT id, stay_in_touch_days FROM contacts WHERE id=ANY($1)` — batch fetch cadence data for salience scoring | `contacts` | `stay_in_touch_days` → triple `(entity_id, stay-in-touch-cadence, days)`; after cut-over query facts for this predicate |
| `roster/relationship/tools/resolve.py:516` | relationship | `SELECT c.id AS contact_id, COUNT(*) FILTER (...) AS count_90d, MAX(f.valid_at) AS most_recent FROM contacts c LEFT JOIN facts f ON f.entity_id=c.entity_id WHERE c.id=ANY($1) AND c.entity_id IS NOT NULL GROUP BY c.id` — interaction count/recency for salience | `contacts LEFT JOIN facts` | Post-cut-over: query facts directly by entity_id (no contacts JOIN needed) |
| `roster/relationship/tools/resolve.py:669` | relationship | `SELECT metadata, company, job_title, first_name, last_name, nickname FROM contacts WHERE id=$1` — context boost from profile fields | `contacts` | Post-cut-over: read from entity metadata/facts |
| `roster/relationship/tools/resolve.py:699` | relationship | `SELECT entity_id FROM contacts WHERE id=$1` — entity resolution for SPO fact lookup in context boost | `contacts` | Remove indirection after cut-over; entity_id is the primary key |

---

### Group V — Relationship Butler — Dunbar Engine (`roster/relationship/tools/dunbar.py`)

| File:Line | Butler | Read Shape (what fields, what filter) | Target Table | Re-pointing Plan |
|---|---|---|---|---|
| `roster/relationship/tools/dunbar.py:234` | relationship | `SELECT c.id AS contact_id, c.entity_id, SUM(...) AS score, MAX(f.valid_at) FROM contacts c LEFT JOIN facts f ON f.subject='contact:' || c.id::text AND f.predicate LIKE 'interaction_%' WHERE c.listed=true AND c.entity_id IS NOT NULL GROUP BY c.id, c.entity_id ORDER BY score DESC` — Dunbar decay scoring | `contacts LEFT JOIN facts` | Post-cut-over: anchor on entity_id directly; `contacts` JOIN removed since entity_id is the primary key; `listed` → entity-level flag or triple |
| `roster/relationship/tools/dunbar.py:445` | relationship | `SELECT id, stay_in_touch_days FROM contacts WHERE id=ANY($1::uuid[])` — batch cadence fetch for urgency scoring | `contacts` | `stay_in_touch_days` → triple; query facts predicate after cut-over |
| `roster/relationship/tools/dunbar.py:621` | relationship | `ARRAY(SELECT id::text FROM contacts WHERE id=ANY($1::uuid[]))` — subquery inside larger fact query to build subject pattern | `contacts` | Post-cut-over: build subject pattern from entity_ids directly (no contacts subquery) |
| `roster/relationship/tools/dunbar.py:707` | relationship | `SELECT id, entity_id FROM contacts WHERE id=$1` — verify contact + get entity_id in `dunbar_tier_set()` | `contacts` | Post-cut-over: contact_id → entity_id is direct; remove contacts lookup |
| `roster/relationship/tools/dunbar.py:803` | relationship | `SELECT id, entity_id FROM contacts WHERE id=$1` — in `get_contact_dunbar()` | `contacts` | Same as :707 |
| `roster/relationship/tools/dunbar.py:839` | relationship | `SELECT id, entity_id, listed FROM contacts WHERE id=$1` — in `get_contact_dunbar_with_stale_flag()` | `contacts` | Same as :707 |

---

### Group W — Relationship Butler — vCard Tool (`roster/relationship/tools/vcard.py`)

| File:Line | Butler | Read Shape (what fields, what filter) | Target Table | Re-pointing Plan |
|---|---|---|---|---|
| `roster/relationship/tools/vcard.py:48` | relationship | `SELECT * FROM contacts WHERE listed=true ORDER BY first_name, last_name, nickname` — export all listed contacts to vCard | `contacts` | Post-cut-over: export from entity graph; contact_info channel values via entity_info |
| `roster/relationship/tools/vcard.py:73` | relationship (via `contact_info_list()`) | Delegates to `contact_info_list()` (see Group S `:370`) | `public.contact_info` | See Group S `:370` |

---

### Group X — Relationship Butler — Dashboard API (`roster/relationship/api/router.py`)

| File:Line | Butler | Read Shape (what fields, what filter) | Target Table | Re-pointing Plan |
|---|---|---|---|---|
| `roster/relationship/api/router.py:300` | relationship | `SELECT count(DISTINCT c.id) FROM contacts c{joins}{where}` — count for `GET /contacts` list | `contacts` | Post-cut-over: count from entities |
| `roster/relationship/api/router.py:304` | relationship | `SELECT c.id, c.name, c.first_name, c.last_name, c.nickname FROM contacts c{joins}{where} ORDER BY c.name OFFSET $n LIMIT $m` — paginated contact list | `contacts` | Post-cut-over: paginated entity list |
| `roster/relationship/api/router.py:337` | relationship | `SELECT DISTINCT ON (ci.contact_id, ci.type) ci.contact_id, ci.type, ci.value FROM public.contact_info ci WHERE ci.contact_id=ANY($1) AND ci.type IN ('email','phone') ORDER BY ...` — batch-fetch primary email/phone for contact list | `public.contact_info` | Re-point: batch fetch from entity_info/facts by entity_id |
| `roster/relationship/api/router.py:352` | relationship | `SELECT c.id AS contact_id, MAX(f.valid_at) AS last_at FROM contacts c JOIN facts f ON f.entity_id=c.entity_id WHERE c.id=ANY($1) AND f.predicate LIKE 'interaction_%' ... GROUP BY c.id` — last interaction time for list | `contacts JOIN facts` | Post-cut-over: remove contacts JOIN; query facts by entity_id |
| `roster/relationship/api/router.py:522` | relationship | `SELECT c.id, c.name, c.first_name, ... FROM contacts c LEFT JOIN public.entities e WHERE c.archived_at IS NULL AND (c.metadata->>'needs_disambiguation')::boolean=true` — list pending disambiguation contacts | `contacts LEFT JOIN public.entities` | Post-cut-over: `needs_disambiguation` flag moves to entity metadata |
| `roster/relationship/api/router.py:550` | relationship | `SELECT id, type, value, is_primary, secured, parent_id, context FROM public.contact_info WHERE contact_id=$1 ORDER BY is_primary DESC NULLS LAST, type, id` — contact_info per pending contact | `public.contact_info` | Re-point: fetch triples by entity_id |
| `roster/relationship/api/router.py:673` | relationship | `SELECT type, value FROM public.contact_info WHERE contact_id=$1` — contact_info for entity-suggestion scoring (layer 2 email/phone matching) | `public.contact_info` | Re-point: `SELECT predicate, content FROM relationship.facts WHERE entity_id=$entity_id AND predicate IN ('has-email','has-phone')` |
| `roster/relationship/api/router.py:741` | relationship | `SELECT count(*) FROM contacts c WHERE c.entity_id IS NULL AND c.archived_at IS NULL AND (metadata->>'needs_disambiguation')::boolean IS NOT TRUE {filter}` — count unlinked contacts | `contacts` | Post-cut-over: contacts without entity_id will not exist; this endpoint can be removed |
| `roster/relationship/api/router.py:763` | relationship | `SELECT c.id, c.name, ..., (SELECT ci.value ... email), (SELECT ci.value ... phone) FROM contacts c WHERE c.entity_id IS NULL AND c.archived_at IS NULL...` — list unlinked contacts with primary channels | `contacts + correlated contact_info subqueries` | Same as :741; post-cut-over endpoint becomes no-op |
| `roster/relationship/api/router.py:841` | relationship | `SELECT id, name AS full_name, first_name, last_name, company FROM contacts WHERE id=$1` — contact row fetch for `GET /contacts/{id}/entity-suggestions` | `contacts` | Keep until bead 8 |
| `roster/relationship/api/router.py:880` | relationship | `SELECT id FROM contacts WHERE id=$1 AND archived_at IS NULL` — contact existence check in `link_entity` | `contacts` | Keep until bead 8 |
| `roster/relationship/api/router.py:925` | relationship | `SELECT id, name AS full_name, first_name, nickname, company FROM contacts WHERE id=$1 AND archived_at IS NULL` — in `create_and_link_entity` | `contacts` | Keep until bead 8 |
| `roster/relationship/api/router.py:1127` | relationship | `SELECT id, entity_id FROM contacts WHERE id=$1` — contact detail for interactions endpoint | `contacts` | Remove indirection after cut-over; entity_id becomes primary key |
| `roster/relationship/api/router.py:1196` | relationship | `SELECT c.id, ..., (SELECT ci.value ... email), (SELECT ci.value ... phone), MAX(f.valid_at) AS last_interaction FROM contacts c LEFT JOIN public.entities e WHERE c.id=$1 AND c.archived_at IS NULL` — full contact detail | `contacts + correlated contact_info subqueries` | Post-cut-over: entity detail endpoint replaces this |
| `roster/relationship/api/router.py:1277` | relationship | `SELECT id, type, value, is_primary, secured, parent_id, context FROM public.contact_info WHERE contact_id=$1 ORDER BY ...` — contact_info for detail view | `public.contact_info` | Re-point: triples by entity_id |
| `roster/relationship/api/router.py:1373` | relationship | `SELECT id, type, value, secured FROM public.contact_info WHERE id=$1 AND contact_id=$2` — reveal secured entry for `GET /contacts/{id}/secrets/{info_id}` | `public.contact_info` | Post-cut-over: secured values live in `public.entity_info` (RFC 0004 Amendment 2 carve-out); re-point to `entity_info` |
| `roster/relationship/api/router.py:1432` | relationship | `SELECT id FROM contacts WHERE id=$1 AND archived_at IS NULL` — existence check in `patch_contact` | `contacts` | Keep until bead 8 |
| `roster/relationship/api/router.py:1462` | relationship | `SELECT first_name, last_name FROM contacts WHERE id=$1` — fetch current name fields before composing update | `contacts` | Keep until bead 8 |
| `roster/relationship/api/router.py:1509` | relationship | `SELECT entity_id FROM contacts WHERE id=$1` — get entity_id for role update in `patch_contact` | `contacts` | Remove after cut-over; entity_id is primary key |
| `roster/relationship/api/router.py:1542` | relationship | `SELECT id FROM contacts WHERE id=$1` — existence check in `delete_contact` | `contacts` | Keep until bead 8 |
| `roster/relationship/api/router.py:1579` | relationship | `SELECT id FROM contacts WHERE id=$1 AND archived_at IS NULL` — existence check in `archive_contact` | `contacts` | Keep until bead 8 |
| `roster/relationship/api/router.py:1605` | relationship | `SELECT id FROM contacts WHERE id=$1 AND archived_at IS NOT NULL` — existence check in `unarchive_contact` | `contacts` | Keep until bead 8 |
| `roster/relationship/api/router.py:1635` | relationship | `SELECT id, metadata FROM contacts WHERE id=$1 AND archived_at IS NULL` — metadata fetch in `confirm_contact` | `contacts` | Keep until bead 8 |
| `roster/relationship/api/router.py:1681` | relationship | `SELECT id, entity_id FROM contacts WHERE id=$1 AND archived_at IS NULL` — fetch target in `merge_contact` | `contacts` | Keep until bead 8 |
| `roster/relationship/api/router.py:1689` | relationship | `SELECT id, entity_id FROM contacts WHERE id=$1` — fetch source in `merge_contact` | `contacts` | Keep until bead 8 |
| `roster/relationship/api/router.py:1981` | relationship | `SELECT id FROM public.contact_info WHERE id=$1 AND contact_id=$2` — existence check before delete | `public.contact_info` | Re-point: check triple by fact_id |
| `roster/relationship/api/router.py:2023` | relationship | `SELECT id FROM public.contact_info WHERE id=$1 AND contact_id=$2` — existence check in `patch_contact_info` | `public.contact_info` | Re-point: check triple by fact_id |
| `roster/relationship/api/router.py:2066` | relationship | `SELECT contact_id, type FROM public.contact_info WHERE id=$1` — fetch entry metadata when toggling is_primary | `public.contact_info` | Re-point: fetch triple metadata by fact_id |
| `roster/relationship/api/router.py:2082` | relationship | `SELECT id, type, value, is_primary, secured, parent_id, context FROM public.contact_info WHERE id=$1` — return updated entry after patch | `public.contact_info` | Re-point: fetch triple by fact_id |
| `roster/relationship/api/router.py:2260` | relationship | `JOIN contacts c ON c.id=id.contact_id ... WHERE c.archived_at IS NULL` — important-dates list via contacts JOIN | `contacts` | Post-cut-over: JOIN entities instead |
| `roster/relationship/api/router.py:2916` | relationship | `(SELECT ci.value FROM public.contact_info ci WHERE ci.contact_id=c.id AND ci.type='email' AND ci.secured=false ...) AS email, (...phone...) FROM public.contacts c WHERE c.entity_id=$1 AND c.archived_at IS NULL` — `GET /entities/{entity_id}/linked-contacts` | `public.contacts + correlated contact_info subqueries` | Post-cut-over: list entity_info entries by entity_id; no contacts JOIN needed |
| `roster/relationship/api/router.py:2984` | relationship | `SELECT DISTINCT ci.value FROM public.contact_info ci JOIN public.contacts c ON c.id=ci.contact_id WHERE c.entity_id=$1 AND c.archived_at IS NULL AND ci.secured=false UNION SELECT DISTINCT ei.value FROM public.entity_info ei WHERE ei.entity_id=$1 AND ei.secured=false` — collect sender identifiers for message-thread matching | `public.contact_info JOIN public.contacts UNION public.entity_info` | Post-cut-over: only `entity_info` side of UNION remains; contact_info UNION arm is dropped |
| `roster/relationship/api/router.py:3113` | relationship | `JOIN contacts c ON c.id=id.contact_id WHERE c.entity_id=$1 AND c.archived_at IS NULL` — important dates filtered by entity | `contacts` | Same as :2260 |
| `roster/relationship/api/router.py:3184` | relationship | `SELECT id FROM contacts WHERE entity_id=$1 AND archived_at IS NULL ORDER BY id LIMIT 1` — find any linked contact for dunbar-tier endpoint | `contacts` | Post-cut-over: dunbar tier stored on entity; remove contacts lookup |
| `roster/relationship/api/router.py:3260` | relationship | `SELECT id, avatar_url FROM public.contacts WHERE id=ANY($1::uuid[])` — batch fetch avatar_url for dunbar ranking map | `public.contacts` | Post-cut-over: avatar_url lives on entity or entity_info; re-point to entity-level attribute |

---

### Group Y — Relationship Butler — Jobs (`roster/relationship/jobs/relationship_jobs.py`)

| File:Line | Butler | Read Shape (what fields, what filter) | Target Table | Re-pointing Plan |
|---|---|---|---|---|
| `roster/relationship/jobs/relationship_jobs.py:185` | relationship | `JOIN contacts c ON d.contact_id=c.id WHERE c.listed=true ORDER BY d.month, d.day` — important dates JOIN contacts for upcoming-dates briefing | `contacts` | Post-cut-over: JOIN entities; `contacts.listed` → entity-level flag |
| `roster/relationship/jobs/relationship_jobs.py:304` | relationship | `SELECT c.id, c.entity_id, c.stay_in_touch_days, ... FROM contacts c LEFT JOIN facts f ... WHERE c.listed=true GROUP BY ...` — stale contacts scan | `contacts LEFT JOIN facts` | Post-cut-over: `stay_in_touch_days` as triple; `listed` as entity flag; full query restructured |
| `roster/relationship/jobs/relationship_jobs.py:419` | relationship | `SELECT c.id, COALESCE(...) AS contact_name FROM contacts c WHERE c.id=ANY($1::uuid[])` — name lookup for pending gifts | `contacts` | Post-cut-over: `canonical_name` from entities |
| `roster/relationship/jobs/relationship_jobs.py:431` | relationship | `SELECT DISTINCT d.contact_id, d.month, d.day, d.label FROM important_dates d JOIN contacts c ON d.contact_id=c.id WHERE c.listed=true AND d.contact_id=ANY($1)` — upcoming dates for gift contacts | `contacts` | Same as :185 |
| `roster/relationship/jobs/relationship_jobs.py:521` | relationship | `SELECT c.id AS contact_id, ..., COUNT(f.id) AS interaction_count, MIN(f.valid_at) AS first_interaction FROM contacts c LEFT JOIN facts f ON f.subject='contact:' || c.id::text WHERE c.listed=true GROUP BY c.id HAVING COUNT(f.id)>0` — interaction milestones | `contacts LEFT JOIN facts` | Post-cut-over: anchor on entity_id; `facts.entity_id` already present |
| `roster/relationship/jobs/relationship_jobs.py:873` | relationship | `SELECT ci.type, ci.value, ci.contact_id, COALESCE(e.roles,'{}') AS roles FROM public.contact_info ci JOIN public.contacts c ON c.id=ci.contact_id LEFT JOIN public.entities e ON e.id=c.entity_id JOIN (UNNEST(...)) ON ...` — bulk channel-to-contact resolution for interaction sync | `public.contact_info JOIN public.contacts LEFT JOIN public.entities` | Re-point: use `resolve_contact_by_channel()` batch variant (Group A replacement) |
| `roster/relationship/jobs/relationship_jobs.py:1203` | relationship | `SELECT ci.contact_id, LOWER(ci.value) AS email, COALESCE(e.roles,'{}') AS roles FROM public.contact_info ci JOIN public.contacts c ON c.id=ci.contact_id LEFT JOIN public.entities e ON e.id=c.entity_id WHERE ci.type='email' AND LOWER(ci.value)=ANY($1)` — resolve calendar attendee emails to contacts | `public.contact_info JOIN public.contacts LEFT JOIN public.entities` | Re-point: email-to-entity resolution via `has-email` triple; roles from entity |

---

## Notes and Open Questions

1. **Owner-lookup pattern is duplicated 7 times** — `SELECT c.id FROM public.contacts c JOIN public.entities e ON c.entity_id=e.id WHERE 'owner'=ANY(e.roles) LIMIT 1` appears in briefing-cache, dashboard-briefing, system, preferences-api, memory-tools/preferences, oauth, and google_health. All should be consolidated into a shared `resolve_owner_entity()` helper that queries `public.entities` directly (the contacts JOIN is already redundant: roles live on entities since migration core_016). This simplification can be done before bead 7.

2. **`listed` flag on contacts** — many readers filter on `contacts.listed=true`. Post-cut-over this flag must be encoded on the entity (as a boolean field or triple). Its re-pointing is implicitly covered in each entry above but the entity schema change should be confirmed in bead 7.

3. **`stay_in_touch_days` on contacts** — used in 4 read paths (briefing job, relationship jobs, dunbar engine, resolve salience). This CRM preference is scheduled to become a triple `(entity_id, stay-in-touch-cadence, days)`. All 4 sites must be re-pointed before cut-over.

4. **`avatar_url` on contacts** — used in `router.py:3260`. Not a channel identifier; it is a CRM display field. Post-cut-over: attach to entity metadata or a dedicated `entity_info` row.

5. **`secured=true` carve-out on contact_info** — `router.py:1373` is the only read path that reveals secured entries (via `GET /contacts/{id}/secrets/{info_id}`). Per RFC 0004 Amendment 2, secured rows move to `public.entity_info` not triples. This endpoint must be re-pointed to `entity_info`.

6. **`needs_disambiguation` flag** — encoded in `contacts.metadata`. Two read paths filter on this (`router.py:522`, `:741`). Post-cut-over: move to entity metadata.

7. **`contacts.details->>'notes'` usage** — `router.py:530` reads `c.details->>'notes'`. This CRM field is not in scope for the triples migration and should be confirmed as staying on the entity or migrated to a fact.

8. **`roster/home/modules/__init__.py`** — references `contact_info` in comments/docstrings only (checking for `home_assistant_token` type). Actual credential lookup uses `resolve_owner_entity_info()` which queries `public.entity_info`, not `public.contact_info`. No reads from contacts/contact_info. Confirmed no-op.

9. **`src/butlers/connectors/gmail.py`, `telegram_user_client.py`** — reference `contact_info` in string literals/comments only (connector type naming). No SQL reads. Confirmed no-op.

10. **`roster/relationship/tools/dunbar.py:621`** — the subquery `ARRAY(SELECT id::text FROM contacts WHERE id=ANY($1::uuid[]))` is used only to build a LIKE pattern for the facts `subject` column (e.g., `contact:{uuid}`). After cut-over, facts use `entity_id` directly and this subject-pattern lookup becomes unnecessary.

11. **Migration scripts excluded** — `roster/relationship/migrations/007_reminders_to_calendar_events.py:303` and `roster/relationship/migrations/010_drop_legacy_contact_tables.py:211` contain one-shot schema-migration SELECTs. Not production read paths; not in scope.

12. **Backfill scripts excluded** — `src/butlers/scripts/backfill_facts.py` contains multiple contact reads used only by one-shot migration scripts. Not in scope for dual-read shims but should be updated when contacts table is dropped.

---

## Discovered Follow-ups

- **Follow-up 1 (pre-bead-7, low-risk quick win):** The owner-lookup JOIN pattern (`contacts JOIN entities WHERE 'owner'=ANY(e.roles)`) is repeated 7+ times across briefing-cache, system, dashboard-briefing, preferences, memory/preferences, oauth, and google_health. All can be simplified to `SELECT FROM public.entities WHERE 'owner'=ANY(roles)` immediately (roles moved to entities in core_016). This reduces the migration surface before bead 7 fires.

- **Follow-up 2 (bead 7 prerequisite):** `contacts.listed` flag is read by 10+ paths. Its post-cut-over encoding on entities must be finalized in bead 5 (schema) before bead 7 can cut over those readers.

- **Follow-up 3 (informational — separate sub-task):** `GET /contacts/{id}/secrets/{info_id}` (`:1373`) reads secured `contact_info` rows. Per RFC 0004 Amendment 2, secured rows go to `public.entity_info` not triples. This endpoint needs a dedicated re-point bead (post-bead-8) — not covered by the standard read-path cut-over.

- **Follow-up 4 (informational):** `src/butlers/scripts/backfill_facts.py` (5 contact reads, lines ~137, ~534, ~589, ~638, ~683, ~731) should be updated or retired when `public.contacts` is dropped in bead 10.
