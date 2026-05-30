# ContactDetailPage Parity Inventory — 2026-05-25

**Purpose:** Pre-implementation capability inventory for moving `ContactDetailPage`
into the entity detail contact-channel card on `/entities/:entityId`.
Required by OpenSpec change `decommission-contact-detail-page` (PR #1926) task 2.1–2.3.

**Scope:** Frontend-only migration; API namespace `/api/butlers/relationship/entities/*`
is unchanged. The destination surface is the entity-detail contact-channel card on
`/entities/:entityId` (not the legacy `/butlers/relationship/entities/:entityId` route).

**Source files audited:**

- `frontend/src/pages/ContactDetailPage.tsx`
- `frontend/src/components/relationship/ContactDetailView.tsx`
- `frontend/src/hooks/use-contacts.ts`
- `frontend/src/api/client.ts` (contact functions, lines 1007–1230)
- `frontend/src/api/client.ts` (entity functions, lines 1749–2100)
- `frontend/src/hooks/use-memory.ts`
- `frontend/src/hooks/use-entities.ts`
- `frontend/src/pages/EntityDetailPage.tsx`
- `roster/relationship/api/router.py`

---

## 1. Capability Parity Table

Each row maps a visible capability from `ContactDetailView` to its entity-card
destination and decommission decision.

| # | Capability | Current endpoint (contact-keyed) | Entity-keyed endpoint | Decommission decision |
|---|---|---|---|---|
| 1 | **Read contact header** — full_name, nickname, company, job_title | `GET /relationship/contacts/{id}` (ContactDetail) | Entity name + aliases already on `GET /memory/entities/{entityId}` | **Temporary compat endpoint** — full_name/company/job_title still live in `public.contacts`. When bead `bu-akads` (read-path cut-over, closed) is complete these become entity facts. Header editing moves to entity name edit + entity_facts. |
| 2 | **Edit contact header** — inline first/last/nickname/company/job_title | `PATCH /relationship/contacts/{id}` (`patchContact`) | No direct entity equivalent for CRM fields | **Temporary compat** — keep `PATCH /relationship/contacts/{id}` as compatibility writer. After contacts-to-triples writes migrate (bu-k9ylx), CRM fields become fact mutations via relationship.entity_facts. |
| 3 | **Role badges** — display of `contact.roles[]` | `GET /relationship/contacts/{id}` (roles field) | `entity.roles` already on `GET /memory/entities/{entityId}` | **Entity-keyed endpoint already available** — `entity.roles` is present; entity edit already supports add/remove role. No gap. |
| 4 | **Contact labels** — colored label badges per contact | `GET /relationship/contacts/{id}` (labels[]) | No entity-keyed label endpoint exists yet | **Blocked by bu-uhjxr** — labels are rows in `contact_labels` joined to `labels`; they are NOT yet triples. Until migration bead 8 (bu-k9ylx) makes label facts available, labels must read from `GET /relationship/contacts/{id}`. The entity-card must call `getContact(contactId)` for the linked contact's labels as a compatibility read. |
| 5 | **Contact info rows** — email/phone/telegram/website/other grouped by account | `GET /relationship/contacts/{id}` (contact_info[]) | Entity-keyed: `GET /relationship/entities/{entityId}/linked-contacts` returns `LinkedContactSummary`; does NOT include contact_info rows | **Blocked by bu-uhjxr** — `contact_info` rows (non-secured) are mid-migration to `relationship.facts` (bu-akads read-path is closed but `contact_info` table is not dropped yet). Contact-info display must still read via `getContact(contactId)` compatibility endpoint. |
| 6 | **Secured contact_info reveal** — click-to-reveal with `••••••••` placeholder | `GET /relationship/contacts/{id}/secrets/{infoId}` (`revealContactSecret`) | Secured rows: `GET /relationship/entities/{entityId}/secrets/{infoId}` (`revealEntitySecret`) | **Partially available** — `revealEntitySecret` exists and works for entity_info secured rows (API: `GET /relationship/entities/{entityId}/secrets/{infoId}`). However, secured=true `contact_info` rows that have NOT yet been migrated to `public.entity_info` (bu-pl8fy, OPEN P2) still require `revealContactSecret`. The entity-card must dispatch to the correct reveal endpoint based on whether the entry lives in contact_info or entity_info. This is **blocked by bu-pl8fy** for full parity. |
| 7 | **Add contact_info entry** — inline form for email/phone/telegram/website/home_assistant_url/other | `POST /relationship/contacts/{id}/contact-info` (`createContactInfo`) | No entity-keyed contact_info create endpoint | **Temporary compat** — keep using `createContactInfo` compatibility write. Becomes a fact write after bu-k9ylx (write-path cut-over). New types blocked from being added to contact-only surface. |
| 8 | **Edit contact_info entry** (non-secured) — inline value edit | `PATCH /relationship/contacts/{id}/contact-info/{infoId}` (`patchContactInfo`) | No entity-keyed equivalent | **Temporary compat** — same migration dependency as add. |
| 9 | **Delete contact_info entry** | `DELETE /relationship/contacts/{id}/contact-info/{infoId}` (`deleteContactInfo`) | No entity-keyed equivalent | **Temporary compat** — same migration dependency. |
| 10 | **Preferred channel selector** — none/telegram/email dropdown | `PATCH /relationship/contacts/{id}` with `preferred_channel` | No entity-keyed preferred_channel field | **Blocked by bu-uhjxr** — `preferred_channel` lives in `contacts.preferred_channel`. Until contacts-to-triples cut-over writes this as a fact, keep `patchContact` compat write. Post-migration: becomes a `preferred_channel` entity fact. |
| 11 | **Link contact to entity** — from ContactDetailView, "View entity activity →" link + unlink | `contacts/{id}/link-entity`, `PATCH /memory/entities/{entityId}/linked-contact`, `DELETE /memory/entities/{entityId}/linked-contact` | Entity-keyed: `setEntityLinkedContact`, `unlinkEntityContact` already call `/memory/entities/{entityId}/linked-contact` | **Entity-keyed endpoint already available** — `LinkedContactSection` on `EntityDetailPage` already handles link/unlink. `ContactDetailView`'s unlink calls `useUnlinkContact` which hits the entity-keyed endpoint. No gap. |
| 12 | **Link / create entity from contact** — for unlinked contacts | `POST /relationship/contacts/{id}/link-entity`, `POST /relationship/contacts/{id}/create-entity` | Entity-keyed: not applicable post-redesign (contact list → entity index) | **Removed by spec** — this workflow lives on `/entities?has=contact` (unlinked contacts queue). The entity-card shows contacts already linked to an entity; the unlinked resolution workflow stays at the entity list/queue surface. |
| 13 | **Delete contact** (hard-delete) | `DELETE /relationship/contacts/{id}` (`deleteContact`) | `DELETE /relationship/entities/{entityId}` (forget/tombstone) | **Partial compat + entity alternative** — the entity-level "Forget" (hard-delete with tombstone) is already available on `EntityDetailPage`. `deleteContact` (contact-row only) can remain as a compat lifecycle action on the entity card for as long as the contact row exists; once bu-e2ja9 drops `contact_info` and contacts are fully fact-backed, contact-row delete merges into entity forget. |
| 14 | **Archive contact** (soft-delete) | `POST /relationship/contacts/{id}/archive` (`archiveContact`) | `POST /relationship/entities/{entityId}/archive` | **Partial compat + entity alternative** — entity archive (`archiveRelationshipEntity`) already exists on the entity surface. Contact archive (soft-deletes the contact row, preserves source links) is distinct from entity archive. Keep as compat action on entity card during migration; rationalize post bu-k9ylx. |
| 15 | **Important dates** — from `important_dates` table, birthday + anniversaries | `GET /relationship/contacts/{id}` (includes birthday from `important_dates` table), `GET /relationship/entities/{entityId}/dates` | `getEntityDates(entityId)` — entity-keyed, hits `GET /relationship/entities/{entityId}/dates` | **Entity-keyed endpoint already available** — `useEntityDates` and `getEntityDates` are implemented. `EntityDetailPage` already renders upcoming dates in `ProfileSnapshot`. Birthday is extracted from dates. Full parity for read. Mutations (add/edit/remove date) are still MCP-only; no dashboard mutation endpoint exists yet. |
| 16 | **Quick facts / profile snapshot** — birthday, lives_in, works_at, family extracted from entity.recent_facts | Entity facts from `GET /memory/entities/{entityId}?facts_limit=N` | Already on `EntityDetailPage` as `ProfileSnapshot` + `FactsSection` | **Entity-keyed endpoint already available** — `ProfileSnapshot` already renders birthday, place, work, family from `entity.recent_facts`. Workbench provenance grid already shows entity_facts. No gap for read. |
| 17 | **Contact-to-contact relationships** — predicates like married_to, sibling_of, parent_of, etc. | These are entity-graph edges stored as facts (predicate, subject_entity_id, object_entity_id) | `GET /relationship/entities/{entityId}/neighbours` (`getEntityNeighbours`) — entity-keyed | **Entity-keyed endpoint already available** — `useEntityNeighbours` and `NeighboursSection` are implemented on `EntityDetailPage`. Entity-graph relationships (knows, married_to, works_at, etc.) are already rendered. The deprecated contact-to-contact display in `ContactDetailView` is not present; relationships live on the entity graph. |
| 18 | **PulseStrip** — closeness + open loops at-a-glance | `ContactDetailPage` renders `PulseStrip` only when `contact.entity_id` is set | `EntityDetailPage` always renders `PulseStrip` with `entityId` | **Entity-keyed endpoint already available** — `PulseStrip` on the entity page is already the primary surface. No gap. |
| 19 | **Breadcrumbs / subtitle** — "Contacts → contact name" with email/telegram subtitle | Contact page breadcrumbs from contact CRM fields | Entity page breadcrumbs from `entity.canonical_name` | **Entity-keyed endpoint already available** — entity page already has proper breadcrumbs. |
| 20 | **Unlinked entity warning banner** — yellow warning when `contact.entity_id` is null | Shown in `ContactDetailView` when no entity link | Not applicable on entity page (page IS the entity) | **Removed by spec** — entity page always has an entity. The recovery state for contacts without an entity link is handled by the redirect route's narrow recovery UI (D2 per design.md). |
| 21 | **Entity_info / credentials (owner entity)** — telegram_api_id, telegram_api_hash, telegram_user_session, home_assistant_token | Owner-specific: these were removed from contact_info in a prior commit (code comment in ContactDetailView confirms: "managed as entity_info entries on the owner entity") | `GET /relationship/entities/{entityId}/info`, `POST/PATCH/DELETE /relationship/entities/{entityId}/info/{infoId}`, `GET /relationship/entities/{entityId}/secrets/{infoId}` | **Entity-keyed endpoint already available** — `TelegramSessionSetup` on `EntityDetailPage` uses entity_info endpoints exclusively. Credentials are fully migrated off contact_info. No gap. |

---

## 2. Endpoint Summary

### Entity-keyed endpoints already available (no gap)

| Capability | Endpoint |
|---|---|
| Entity name, type, aliases | `GET /memory/entities/{entityId}` |
| Entity roles | `GET /memory/entities/{entityId}` (`entity.roles`) |
| Entity-graph relationships (neighbours) | `GET /relationship/entities/{entityId}/neighbours` |
| Entity facts / profile snapshot | `GET /memory/entities/{entityId}?facts_limit=N` |
| Important dates (read) | `GET /relationship/entities/{entityId}/dates` |
| Entity archive | `POST /relationship/entities/{entityId}/archive` |
| Entity forget (hard-delete) | `DELETE /relationship/entities/{entityId}` |
| Link/unlink contact ↔ entity | `PUT/DELETE /memory/entities/{entityId}/linked-contact` |
| Entity_info CRUD + secured reveal | `GET/POST/PATCH/DELETE /relationship/entities/{entityId}/info[/{infoId}]`, `GET /relationship/entities/{entityId}/secrets/{infoId}` |
| Linked contacts list | `GET /relationship/entities/{entityId}/linked-contacts` |
| PulseStrip | Derived from entity facts |
| Activity timeline | `GET /relationship/entities/{entityId}/timeline` |

### Temporary contact-keyed compatibility endpoints (retained during migration)

These endpoints must be used by the entity-card during the migration window and
MUST be marked compatibility-only in code comments. They are expected to be
removed or re-pointed after bu-uhjxr beads reach cut-over and drop stages.

| Capability | Compat endpoint | Migration gate |
|---|---|---|
| Contact header read (full_name, company, job_title) | `GET /relationship/contacts/{id}` | bu-akads (closed); needs entity-fact migration complete for full parity |
| Contact header edit | `PATCH /relationship/contacts/{id}` | bu-k9ylx (write-path cut-over, OPEN P0) |
| Labels read | `GET /relationship/contacts/{id}` (labels[]) | bu-uhjxr bead 8 / bu-k9ylx |
| Contact info rows read (non-secured) | `GET /relationship/contacts/{id}` (contact_info[]) | bu-akads (read-path done), but contact_info table not yet dropped |
| Contact info add/edit/delete | `POST/PATCH/DELETE /relationship/contacts/{id}/contact-info[/{infoId}]` | bu-k9ylx (write-path cut-over) |
| Preferred channel read/write | `GET/PATCH /relationship/contacts/{id}` (preferred_channel) | bu-k9ylx |
| Contact archive (soft-delete) | `POST /relationship/contacts/{id}/archive` | bu-k9ylx + bu-e2ja9 |
| Contact hard-delete | `DELETE /relationship/contacts/{id}` | bu-e2ja9 (drop, OPEN P0) |

### Capabilities removed by spec

| Capability | Rationale |
|---|---|
| Link/create entity from unlinked contact (within contact detail) | Workflow lives on `/entities?has=contact` queue; entity-card shows already-linked contacts only |
| Unlinked entity warning banner | Not applicable on entity page; redirect recovery state handles the no-entity case |

### Capabilities blocked by bu-uhjxr children

| Capability | Blocking bead | Description |
|---|---|---|
| Secured contact_info reveal (full parity) | **bu-pl8fy** (OPEN P2) | Secured=true rows migrate to `public.entity_info` per RFC 0004 Amendment 2; until then, the entity-card must dispatch to `revealContactSecret` for contact-info secured rows and `revealEntitySecret` for entity_info rows. The entity-card needs conditional routing logic. |
| Post-migration secured reveal endpoint design | **bu-fa5ex** (OPEN P2) | After bead 8 (contact_info write-blocked), a new reveal endpoint for credentials in `relationship.credentials` is needed. Entity-card must wire to this once available. |
| Labels full migration | **bu-k9ylx** (OPEN P0) | Contact labels are not yet triples; entity-keyed label endpoint does not exist. |
| Preferred channel as entity fact | **bu-k9ylx** (OPEN P0) | `preferred_channel` is a column on `contacts`, not a fact. |
| Full contact_info table drop / compat cleanup | **bu-e2ja9** (OPEN P0) | Hard drop gated on 30-day soak after bu-hpv4u verification. |

---

## 3. Sections Not Yet in EntityDetailPage (Gaps Requiring Implementation)

The following sections of `ContactDetailView` are NOT currently rendered in
`EntityDetailPage` and need to be added as part of the entity-card contact-channel
implementation bead:

1. **Contact channel card** — a new card component on `EntityDetailPage` that
   renders: contact info rows (email/phone/telegram), secured reveal/hide,
   add/edit/delete contact_info mutations (via compat endpoints), preferred
   channel selector, and contact lifecycle actions (archive, delete).

2. **Contact labels display** — labels are shown in `ContactDetailView` alongside
   role badges. Currently `EntityDetailPage` only has entity roles; contact labels
   (from `contact_labels`) need to be pulled from the linked contact and displayed
   in the channel card.

3. **Edit contact CRM fields** — inline edit of first/last/nickname/company/job_title
   is available in `ContactDetailView` via `EditHeaderForm` → `patchContact`.
   The entity page only edits canonical_name. The CRM fields need a compatibility
   edit form in the channel card (writing to `patchContact`) during the migration
   window.

4. **Contact lifecycle actions on entity page** — Delete contact (hard-delete) and
   Archive contact are in `ContactDetailView` but not in `EntityDetailPage`. Entity
   page has "Forget entity" (tombstone) but not the contact-row lifecycle actions.
   These need to be added to the channel card as compat actions.

5. **Preferred channel selector** — exists in `ContactDetailView` via `PreferredChannelRow`;
   not rendered on `EntityDetailPage`. Needs compat implementation in channel card.

---

## 4. Key Findings (Surprising or Noteworthy)

1. **Credentials were already migrated off contact_info.** The `ContactDetailView`
   code explicitly comments that `telegram_api_id`, `telegram_api_hash`,
   `telegram_user_session`, and `home_assistant_token` "are now managed as
   entity_info entries on the owner entity." `TelegramSessionSetup` on `EntityDetailPage`
   already uses entity_info endpoints. This is a non-issue.

2. **Secured reveal needs dual-dispatch logic.** During the migration window, the
   entity-card will need to determine whether a secured entry lives in `contact_info`
   (use `revealContactSecret`) or `entity_info` (use `revealEntitySecret`). The
   entity-keyed `LinkedContactSummary` does not expose whether each channel entry
   is contact-side or entity-side. This may require either an extended
   `linked-contacts` response payload or a type discriminator on the entry. This
   is a non-trivial implementation detail that should be raised in the implementation
   bead. **Consider creating a new bead for this.**

3. **Contact labels have no entity-keyed read path.** `contact_labels` is a join
   table with no entity-keyed endpoint. The entity-card must call `getContact(contactId)`
   for labels until migration completes. Exposing labels in `GET /relationship/entities/{entityId}/linked-contacts`
   is the natural fix, but that is an API extension that may be out of scope for
   the frontend-only entity-card bead.

4. **`linked-contacts` endpoint returns summaries only, not channel rows.** 
   `GET /relationship/entities/{entityId}/linked-contacts` returns `LinkedContactSummary[]`.
   That type does NOT include `contact_info[]`, `preferred_channel`, or `labels`.
   The entity-card will need either: (a) a fanout call to `getContact(contactId)`
   for each linked contact (current approach, expensive), or (b) an enriched
   linked-contacts endpoint. This is the primary API gap for the entity-card and
   should be a new bead or sub-task.

5. **`getContact` is still the most complete read.** Despite the migration work,
   `GET /relationship/contacts/{id}` is still the single endpoint that returns
   contact_info[], labels, preferred_channel, and CRM fields together.
   The entity-card will call this as a compatibility read for each linked contact,
   which is functionally correct but should be documented as compat-only.

6. **Important dates have a read path, but no mutation UI on entity page.**
   `useEntityDates` and `getEntityDates` exist and render in `ProfileSnapshot`.
   However, there are no add/edit/remove date endpoints accessible from the
   dashboard — date mutations are MCP-tool-only. This was pre-existing and is not
   a regression from the decommission.

7. **Contact-to-contact relationships are a non-issue.** The task description
   asked about "contact-to-contact relationships" specifically, but in code these
   are entity-graph edges (facts with predicates like `married_to`, `sibling_of`).
   `ContactDetailView` does NOT render a contact-relationship section directly —
   those are rendered via `useEntityNeighbours` / `NeighboursSection` on the entity
   page. The entity page already has full parity here.

8. **Archive vs. forget are semantically different.** Contact archive (`archived_at`)
   preserves source links so sync won't recreate the contact; entity archive hides
   from list views. They are not equivalent. Both actions need to be available on
   the entity card during the transition (as separate affordances or a single
   lifecycle menu).

---

## 5. Recommended New Beads

The following work items were discovered during this inventory and are not yet tracked.
They should be filed before implementation beads start.

| # | Recommended bead | Priority | Rationale |
|---|---|---|---|
| R1 | Enrich `GET /relationship/entities/{entityId}/linked-contacts` to include contact_info[], labels, preferred_channel | P1 | Avoids N fanout calls from entity-card to getContact(); enables entity-keyed read for channel card data |
| R2 | Secured reveal dual-dispatch design in entity-card | P2 | contact_info vs. entity_info secured entries require different reveal endpoints during migration; needs explicit design decision |
| R3 | Important dates write surface (dashboard UI for add/edit/remove) | P3 | Pre-existing gap; MCP-only today; out of scope for this decommission but worth tracking |

---

## 6. Decommission Dependencies

Full `/contacts/:contactId` → `/entities/:entityId` redirect rollout is blocked
until all of the following close:

- **bu-uhjxr** (parent migration epic, 52% complete) — specifically:
  - **bu-k9ylx** (write-path cut-over, P0 OPEN) — labels, preferred_channel, contact header edit still write to contact tables
  - **bu-pl8fy** (secured credential migration to entity_info, P2 OPEN) — secured reveal needs single endpoint post-migration
  - **bu-e2ja9** (drop public.contact_info, P0 OPEN) — final cleanup gate
  - **bu-hpv4u** (30-day post-cut-over verification, P0 OPEN)

Route-contract and link cleanup can proceed before these close (per D5 in design.md).
The entity-card implementation bead can proceed once this inventory is accepted.

---

*Produced for bu-m8gb6.2 — Inventory ContactDetailPage parity for entity card*
*Date: 2026-05-25*
