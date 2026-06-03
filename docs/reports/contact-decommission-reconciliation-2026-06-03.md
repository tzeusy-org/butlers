# Contact Decommission — Terminal Reconciliation Report

**Epic:** bu-m8gb6 — Decommission contact detail page into entity detail  
**Date:** 2026-06-03  
**Author:** Beads Worker (agent/bu-m8gb6.7)  
**Scope:** Terminal reconciliation after all implementation children (.1–.6) are merged to main.

---

## 1. Child Bead Status

All implementation children are CLOSED and merged to main.

| Bead | PR | Description | Status |
|------|----|-------------|--------|
| bu-m8gb6.1 | #1926 | OpenSpec ratification review | MERGED |
| bu-m8gb6.2 | (no PR) | ContactDetailPage parity inventory | MERGED |
| bu-m8gb6.3 | #1938 | Entity detail contact-channel card | MERGED |
| bu-m8gb6.4 | #1937 | Contact-to-entity redirect resolver | MERGED |
| bu-m8gb6.5 | #2000 | /contacts/:id → /entities/:id redirect | MERGED |
| bu-m8gb6.6 | #2081 | Delete ContactDetailPage/ContactDetailView | MERGED |
| bu-m8gb6.7 | this PR | Terminal reconciliation report | IN PROGRESS |

---

## 2. OpenSpec Requirement → Evidence Mapping

The governing OpenSpec change is at
`openspec/changes/decommission-contact-detail-page/`.
The delta spec provides MODIFIED requirements for 8 requirements in
`openspec/specs/dashboard-relationship/spec.md`.

### 2.1 "Contact detail page" (decommission + contact-channel card)

**Requirement (delta):** The standalone contact detail page SHALL be decommissioned.
Contact-channel data SHALL be rendered on the canonical entity detail page at
`/entities/:entityId` as a contact-channel card.

**Evidence:**

- `ContactDetailPage.tsx` and `ContactDetailView.tsx` deleted in bu-m8gb6.6 (PR #2081,
  commit e3efa5eec). Zero occurrences in any active source file.
- `ContactChannelCard` component implemented at
  `frontend/src/components/relationship/ContactChannelCard.tsx` (905 lines).
- `ContactChannelCard` rendered inside `EntityDetailPage` at
  `frontend/src/pages/EntityDetailPage.tsx:2381`.
- Card fetches linked contacts via
  `GET /relationship/entities/{entityId}/linked-contacts`
  (`frontend/src/api/client.ts:1914`, backend at
  `roster/relationship/api/router.py:4478`).

**Spec scenarios:**

| Scenario | Evidence |
|----------|----------|
| Entity detail renders contact-channel card | `ContactChannelCard.tsx:841` (main export), `EntityDetailPage.tsx:2381` |
| Entity detail renders sparse contact data | `ContactChannelCard.tsx:882–904` (empty state w/ compact messaging) |
| Activity stays entity-scoped | `EntityDetailPage.tsx:2380–2398` — ContactChannelCard is beside, not inside, ActivityTimeline |

**RESULT: PASS**

---

### 2.2 "Contact detail page canonical route is /contacts/:contactId"

**Requirement (delta):** `/contacts/:contactId` SHALL be a compatibility redirect,
not a canonical page. MUST resolve contact by `contactId`, read linked `entity_id`,
and redirect to `/entities/:entityId`. If contact has no linked entity, MUST render
a recovery state linking back to `/entities?has=contact`.

**Evidence:**

- Route registered in `frontend/src/router-config.tsx:96`:
  `{ path: '/contacts/:contactId', element: <ContactEntityRedirect /> }`
- `ContactEntityRedirect` implemented at `frontend/src/router.tsx:44–77`.
  Uses `resolveContactEntity(contactId)` → API call → redirect or recovery UI.
- API function `resolveContactEntity` at `frontend/src/api/client.ts:1074`.
  Calls `GET /relationship/contacts/{contactId}/entity`.
- Backend endpoint `resolve_contact_entity` at
  `roster/relationship/api/router.py:1313–1349`.
  Returns `{entity_id, status}` or HTTP 404.
- Recovery state (EmptyState with "Browse entities" link to `/entities?has=contact`)
  at `frontend/src/router.tsx:66–76`.
- `/contacts` → `/entities?has=contact` redirect at `frontend/src/router-config.tsx:91`.

**Spec scenarios:**

| Scenario | Evidence |
|----------|----------|
| Contact detail URL redirects to entity detail | `router.tsx:53–58` (redirect on linked contact) |
| Contact detail URL handles missing entity link | `router.tsx:63–76` (recovery EmptyState, unlinked/404) |
| Contact index still redirects to entity index filter | `router-config.tsx:91` (Navigate to `/entities?has=contact`) |

**RESULT: PASS**

---

### 2.3 "Memory entity page links to relationship activity"

**Requirement (delta):** Entity surfaces SHALL use `/entities/:entityId` as
canonical entity detail route. MUST NOT link to `/butlers/relationship/entities/:entityId`
as a product route. Legacy route MAY remain as compatibility redirect only.

**Evidence:**

- `RelationshipEntityRedirect` registered at `frontend/src/router-config.tsx:122–126`
  as redirect-only (`/butlers/relationship/entities/:entityId` → `/entities/:entityId`).
- `RelationshipEntityRedirect` implementation at `frontend/src/router.tsx:25–31`:
  `<Navigate to={/entities/${entityId}} replace />`.
- No active `<Link to="/butlers/relationship/entities/...">` found in any non-router,
  non-test source file (rg audit: zero results).

**Spec scenarios:**

| Scenario | Evidence |
|----------|----------|
| Internal entity links target canonical entity route | rg audit: no primary nav to `/butlers/relationship/entities/` in src/ |
| Legacy relationship entity URL redirects | `router-config.tsx:122–126` + `router.tsx:25–31` |

**RESULT: PASS**

---

### 2.4 "Entity detail page"

**Requirement (delta):** Frontend SHALL render canonical entity detail page at
`/entities/:entityId` with header, contact-channel card, unified ActivityTimeline,
Gifts/Loans panels, and Workbench/Provenance mode.

**Evidence:**

- Route: `frontend/src/router-config.tsx:110`:
  `{ path: '/entities/:entityId', element: <EntityDetailPage /> }`.
- `EntityDetailPage.tsx` (~2434 lines) renders all required sections.
- Header at `EntityDetailPage.tsx:2180–2220` (canonical_name, entity_type, aliases, roles).
- Contact-channel card at `EntityDetailPage.tsx:2380–2386`.
- ActivityTimeline from `EntityDetailPage.tsx:2320–2370` (editorial mode).
- Gifts/Loans panels: `EntityDetailPage.tsx` renders `GiftsSection` and `LoansSection`.
- Workbench mode: `EntityDetailPage.tsx:2400–2403` (`<ProvenanceGrid>`).
- Mode toggle persisted to `localStorage` under key `entities.detail.mode`
  (`EntityDetailPage.tsx:104`).

**Spec scenarios:**

| Scenario | Evidence |
|----------|----------|
| Entity detail is canonical at /entities | `router-config.tsx:110` |
| Entity not found | `EntityDetailPage.tsx:2090–2110` (NotFound state) |

**RESULT: PASS**

---

### 2.5 "Owner identity and credential management via contact detail page"

**Requirement (delta, F2 resolution):** The entity detail contact-channel card at
`/entities/:entityId` SHALL be the primary mechanism for configuring owner identity
fields and credentials, including secured types.

**Evidence:**

- Credentials are referenced by a link to `/secrets` (Secrets → User tab) rather than
  inline in the contact-channel card (`EntityDetailPage.tsx:2411–2422`). The card header
  comment reads: "Credentials and identity-bound secrets are managed in Secrets → User."
- TelegramSessionSetup (owner-specific credential workflow) is rendered in the
  PracticalDrawer at `EntityDetailPage.tsx:2424–2429`.
- `ContactChannelCard` AddChannelInfoForm supports email, phone, telegram, website, other
  (`ContactChannelCard.tsx:87–93`). Secured types (`email_password`, `telegram_api_id`,
  `telegram_api_hash`) are NOT currently addable via this form — they are managed via
  the Secrets page.

**GAP NOTE:** The delta spec requires the "Add contact info" form to support secured
types including `email_password`, `telegram_api_id`, `telegram_api_hash`. The
`AddChannelInfoForm` (`ContactChannelCard.tsx:496–593`) only exposes types from
`CONTACT_INFO_TYPES = ["email", "phone", "telegram", "website", "other"]`. Secured
credential types are NOT in this list. The implementation intentionally routes
credentials to the Secrets page instead. This is a partial divergence from the spec's
literal requirement. However, the parity inventory (bu-m8gb6.2) specifically notes
these were migrated to `entity_info` and managed via the Secrets surface — this
represents a deliberate design choice. See Discovered-Follow-Ups.

**Spec scenarios:**

| Scenario | Evidence |
|----------|----------|
| Add a secured credential from entity detail contact-channel card | NOT implemented in AddChannelInfoForm — intentionally deferred to Secrets page |
| Add a non-secured identity field from entity detail | Implemented (email/phone/telegram via AddChannelInfoForm) |

**RESULT: PARTIAL — Secured credential add via contact-channel card deferred to Secrets page**

---

### 2.6 "Owner identity setup banner"

**Requirement (delta, F3 resolution):** Dashboard SHALL display a persistent banner on
the entity index page (`/entities?has=contact`) when owner contact is missing key
identity fields. Banner links to "Set Up Identity" dialog.

**Evidence:**

- `OwnerSetupBanner` component exists at
  `frontend/src/components/relationship/OwnerSetupBanner.tsx`.
- Rendered inside `PracticalDrawer` on `EntityDetailPage` at `EntityDetailPage.tsx:2408`.
- `EntityDetailPage.tsx:2045`: `ownerNeedsSetup = isOwner && entity && !entity.linked_contact_id`.
- The banner is rendered on the ENTITY DETAIL page (`/entities/:entityId`), not the
  entity index page (`/entities?has=contact`).

**GAP NOTE:** The delta spec requires the banner on `/entities?has=contact` (entity index
page). The implementation places it on `/entities/:entityId` (entity detail page) inside
the PracticalDrawer. The entity index page (`EntitiesIndexPage`) is a separate component;
verifying whether it also has a banner requires checking `EntitiesIndexPage.tsx`.

**Evidence (secondary check):**
```
rg "OwnerSetupBanner" frontend/src/components/relationship/EntitiesIndexPage.tsx
```
Result: not found. `OwnerSetupBanner` is only rendered on the entity detail page.
The entity index page does not display the setup banner.

**RESULT: PARTIAL — Banner placement diverges from spec (entity detail, not entity index)**

---

### 2.7 "Pending identities queue on contacts page"

**Requirement (delta, F3 resolution):** Entity index page (`/entities?has=contact`) SHALL
display a "Pending Identities" section for contacts with `needs_disambiguation = true`.

**Evidence:**

- Pending identities queue is implemented in `EntitiesIndexPage` (relationship component).
  The parity inventory (bu-m8gb6.2) references this as a pre-existing feature.
- `frontend/src/api/client.ts:1084`: `getPendingContacts()` function exists.
- This capability was present in the entity list view; the delta spec confirmed its
  location should be `/entities?has=contact` (which it already was).

**RESULT: PASS (pre-existing, location correct)**

---

### 2.8 "Entity detail Editorial / Workbench mode toggle"

**Requirement (delta, F1 resolution):** Entity detail page at `/entities/:entityId` SHALL
render in Editorial (default) or Workbench mode. Mode persists in
`localStorage["entities.detail.mode"]`.

**Evidence:**

- Mode toggle implementation at `EntityDetailPage.tsx:100–133`.
- localStorage key `entities.detail.mode` at `EntityDetailPage.tsx:104`.
- Editorial mode renders ActivityTimeline; Workbench renders ProvenanceGrid.
- `?mode=workbench` URL param override at `EntityDetailPage.tsx:107`.

**RESULT: PASS**

---

### 2.9 "Dispatch design language token discipline"

**Requirement (delta, F1 resolution):** All six entity routes SHALL conform to Dispatch
design language; sixth route is `/entities/:entityId` (not `/butlers/relationship/entities/:id`).

**Evidence:**

- `router-config.tsx` registers 6 entity routes: `/entities`, `/entities/hop`,
  `/entities/columns`, `/entities/concentration`, `/entities/social-map`,
  `/entities/:entityId`.
- Token audit: `frontend/src/index.css:145` — stale comment referencing
  `ContactDetailView.tsx` was cleaned up in bu-m8gb6.6 (commit e3efa5eec).

**RESULT: PASS (route name corrected; token discipline is ongoing per spec)**

---

## 3. rg Audit Results

### 3.1 ContactDetailPage

```
rg "ContactDetailPage" frontend/
```
Files with matches (as of origin/main HEAD e3efa5eec):
- `frontend/README.md:187` — stale file tree listing (documentation artifact, no impact)
- `frontend/tests/e2e/entity-redesign.spec.ts:269` — comment: "The old ContactDetailPage (with tabs) is no longer rendered."

**Active source files:** ZERO. ContactDetailPage is fully deleted.

### 3.2 ContactDetailView

```
rg "ContactDetailView" frontend/
```
Files with matches:
- `frontend/README.md:187` — stale file tree listing (same doc artifact as above)

**Active source files:** ZERO. ContactDetailView is fully deleted.

### 3.3 /butlers/relationship/entities/ (frontend navigation links)

```
rg 'to="/butlers/relationship/entities|href="/butlers/relationship/entities|navigate.*butlers/relationship/entities' frontend/src/
```
**Result:** ZERO matches. No active navigation links to the legacy route prefix.

The string `/butlers/relationship/entities` appears only in:
- API client comments/doc strings (correct — the API namespace is unaffected per Scope Note)
- hooks/use-entities.ts doc comments (correct)
- api/types.ts type comments (correct)

### 3.4 /contacts/:id as primary navigation

```
rg 'to="/contacts/|href="/contacts/' frontend/src/
```
**Result:** ZERO matches outside router/router-config/test files. No component issues
primary navigation links to `/contacts/:contactId`.

### 3.5 "View relationship activity"

```
rg '"View relationship activity"' frontend/
```
**Result:** ZERO matches. Stale string no longer present.

### 3.6 Summary

| Artifact | Active references | Status |
|----------|-------------------|--------|
| ContactDetailPage | 0 (comments/README only) | CLEAN |
| ContactDetailView | 0 (README only) | CLEAN |
| /butlers/relationship/entities/ (nav link) | 0 | CLEAN |
| /contacts/:id (primary nav) | 0 | CLEAN |
| "View relationship activity" | 0 | CLEAN |

**README file tree is stale** — `frontend/README.md:187` still lists
`ContactDetailPage.tsx` in the directory tree. This is a documentation artifact
with no functional impact. See Discovered-Follow-Ups.

---

## 4. FE → BE Wiring Audit

Each interactive control in the ContactChannelCard is verified against its backend
endpoint.

### 4.1 Read path — linked contacts list

| Control | FE hook | API call | Backend endpoint | Status |
|---------|---------|----------|-----------------|--------|
| Channel card render | `useEntityLinkedContacts` | `getEntityLinkedContacts(entityId)` | `GET /relationship/entities/{entityId}/linked-contacts` (router.py:4478) | **LIVE** |

### 4.2 Contact info mutations

| Control | FE hook | API call | Backend endpoint | Status |
|---------|---------|----------|-----------------|--------|
| Add channel info | `useAddEntityContact` | `addEntityContact(entityId, req)` | `POST /relationship/entities/{entityId}/contacts` (router.py:5206) | **LIVE** |
| Edit channel info (entity_facts row) | `useUpdateEntityContact` | `updateEntityContact(entityId, pred, hash, req)` | `PUT /relationship/entities/{entityId}/contacts/{pred}/{hash}` (router.py:5409) | **LIVE** |
| Delete channel info (entity_facts row) | `useDeleteEntityContact` | `deleteEntityContact(entityId, pred, hash)` | `DELETE /relationship/entities/{entityId}/contacts/{pred}/{hash}` (router.py:5313) | **LIVE** |
| Legacy contact_info row | Read-only marker shown | N/A (write-blocked) | Write-blocked by `contact_info_write_guard.py` | **READ-ONLY by design** |

### 4.3 Secured reveal

| Control | FE hook | API call | Backend endpoint | Status |
|---------|---------|----------|-----------------|--------|
| Reveal (entity_facts entry) | `useRevealEntityContactSecret` | `revealEntitySecret(entityId, infoId)` | `GET /relationship/entities/{entityId}/secrets/{infoId}` (router.py:4054) | **LIVE** |
| Reveal (legacy contact_info entry) | `useRevealContactSecret` | `revealContactSecret(contactId, infoId)` | `GET /relationship/contacts/{contactId}/secrets/{infoId}` (router.py:1517) | **LIVE (COMPAT-ONLY)** |

**Note:** The dual-dispatch is currently dormant because `list_entity_linked_contacts`
excludes `secured=true` rows (`WHERE secured = false`). Secured entries will flow
through when bu-pl8fy completes.

### 4.4 Preferred channel (COMPAT-ONLY)

| Control | FE hook | API call | Backend endpoint | Status |
|---------|---------|----------|-----------------|--------|
| Preferred channel selector | `usePatchContact` | `patchContact(contactId, req)` | `PATCH /relationship/contacts/{contactId}` (router.py:1574) | **LIVE (COMPAT-ONLY)** |

The preferred channel selector renders and fires correctly. The backend COMPAT-ONLY
endpoint is active. Blocked on bu-uhjxr to migrate `preferred_channel` to entity_facts.

### 4.5 Contact entity redirect

| Control | FE component | API call | Backend endpoint | Status |
|---------|--------------|----------|-----------------|--------|
| /contacts/:contactId redirect | `ContactEntityRedirect` | `resolveContactEntity(contactId)` | `GET /relationship/contacts/{contactId}/entity` (router.py:1313) | **LIVE** |

### 4.6 Dead controls audit

**No dead onClick handlers found.** All interactive controls in the ContactChannelCard
(expand/collapse rows, Reveal/Hide buttons, Edit/Delete affordances, Add contact info
form, Preferred channel selector, Link contact CTA) are wired to working mutations or
query hooks. The entity-keyed write paths (add, edit, delete, reveal) are confirmed live.
The COMPAT-ONLY paths (preferred_channel patch, legacy secured reveal) are also live.

One nuance: the "Add contact info" form intentionally excludes secured credential types
(`email_password`, `telegram_api_id`, `telegram_api_hash`). This is not a dead control —
it is a deliberate scoping decision routing credentials to the Secrets page. It is a
spec divergence (see §2.5 GAP NOTE).

---

## 5. Compatibility Shims — Intentionally Retained

The following COMPAT-ONLY elements remain in production. Each has an explicit owner/
migration gate.

| Shim | Location | Purpose | Owner/Gate |
|------|----------|---------|------------|
| `PATCH /relationship/contacts/{id}` | `roster/relationship/api/router.py:1574` | Write preferred_channel, full_name, CRM fields | bu-k9ylx (write-path cut-over) + bu-uhjxr |
| `GET /relationship/contacts/{id}` | `roster/relationship/api/router.py:1357` | Read labels, preferred_channel, legacy contact_info | bu-uhjxr (contacts-to-triples) |
| `GET /relationship/contacts/{id}/secrets/{infoId}` | `roster/relationship/api/router.py:1517` | Reveal legacy contact_info secured rows | bu-pl8fy (secured migration) + bu-uhjxr |
| `useRevealContactSecret` (FE) | `frontend/src/hooks/use-contacts.ts:119` | Legacy reveal path (dual-dispatch compat) | bu-pl8fy + bu-uhjxr |
| `usePatchContact` (FE) | `frontend/src/hooks/use-contacts.ts:102` | Write preferred_channel from PreferredChannelSelector | bu-k9ylx + bu-uhjxr |
| `GET /relationship/contacts/{id}/entity` | `roster/relationship/api/router.py:1313` | Resolve contact_id → entity_id for redirect | Permanent until /contacts/:id redirect is fully removed |

All shims are marked `COMPAT-ONLY` in code comments. None are new; all pre-dated this
epic or were added as intentional migration bridges.

---

## 6. Test Coverage

### 6.1 Frontend tests

| Area | Test file | Coverage |
|------|-----------|---------|
| ContactChannelCard (static/unit) | `ContactChannelCard.test.tsx` | Populated/sparse/multi-contact/empty/secured/edit/delete/add/dual-dispatch |
| ContactChannelCard (interaction) | `ContactChannelCard.reveal.test.tsx` | Reveal click routing (entity_facts vs legacy) |
| /contacts/:contactId redirect | `router.test.tsx:163–245` | Linked → redirect, unlinked → recovery, 404 → recovery |
| /contacts → /entities?has=contact | `router.test.tsx:127–161` | Redirect verified |
| /butlers/relationship/entities/:id redirect | `router.test.tsx:248–310` | Legacy redirect verified |
| EntityDetailPage | `EntityDetailPage.test.tsx` | General page tests |

No ContactDetailPage or ContactDetailView tests remain (all deleted in bu-m8gb6.6).
No skipped-but-relevant test suites found in active files.

### 6.2 Backend tests

| Area | Test file | Coverage |
|------|-----------|---------|
| `GET /entities/{id}/linked-contacts` | `test_relationship_entities_linked_contacts.py` | 20+ cases: empty, labels, entity_facts, secured |
| `PUT /entities/{id}/contacts/{pred}/{hash}` | `test_relationship_entities_update_contact.py` | 15+ cases: success/404/400/owner approval |
| `GET /entities/{id}/secrets/{infoId}` | `test_relationship_entity_info_reveal.py` | Reveal endpoint coverage |
| `POST /entities/{id}/contacts` | `test_relationship_entities_contacts.py` | Add contact fact |

**GAP:** The `GET /relationship/contacts/{contact_id}/entity` resolver endpoint
(the redirect backbone) has NO dedicated integration test. The frontend router tests
cover the FE path (mocking `resolveContactEntity`). The backend SQL query (2 lines,
simple SELECT + 404) is trivially correct, but a backend API test would be better hygiene.
See Discovered-Follow-Ups.

---

## 7. OpenSpec Fast-Forward Status

**The decommission-contact-detail-page change has NOT been fast-forwarded (applied)
to the canonical `openspec/specs/dashboard-relationship/spec.md`.**

The change directory `openspec/changes/decommission-contact-detail-page/` is NOT in
`openspec/archive/`, confirming the ff step is pending.

The canonical spec still contains:
- Line 52: `ContactDetailPage` at `/contacts/:contactId` described as canonical.
- Line 264: "Owner identity and credential management via contact detail page"
  references `/butlers/relationship/contacts/:id`.
- Line 291: "Owner identity setup banner" references `/butlers/relationship/contacts`.
- Line 425: Entity detail page described as `/butlers/relationship/entities/:id`.

These are EXACTLY the requirements that the delta spec's MODIFIED blocks replace.
The code is correct and the delta spec is correct; only the spec archive step is pending.

See Discovered-Follow-Ups — this is a required post-epic cleanup.

---

## 8. Summary

### 8.1 Requirements coverage

| Req # | Requirement | Result |
|-------|-------------|--------|
| R1 | Contact detail page decommissioned + contact-channel card on entity detail | PASS |
| R2 | /contacts/:contactId → compatibility redirect | PASS |
| R3 | /contacts → /entities?has=contact | PASS |
| R4 | /butlers/relationship/entities/:id → legacy redirect only | PASS |
| R5 | /entities/:entityId canonical entity detail page | PASS |
| R6 | Owner credential management via entity detail contact-channel card | PARTIAL (deferred to Secrets page) |
| R7 | Owner identity setup banner on /entities?has=contact | PARTIAL (on /entities/:entityId, not index) |
| R8 | Pending identities queue on /entities?has=contact | PASS (pre-existing) |
| R9 | Editorial/Workbench mode toggle | PASS |
| R10 | Dispatch token discipline (route name correction) | PASS |

### 8.2 rg audit: clean

All five target strings (ContactDetailPage, ContactDetailView, /butlers/relationship/entities/
nav links, /contacts/:id primary nav links, "View relationship activity") return zero
active source matches.

### 8.3 FE → BE wiring: no dead controls

All interactive controls in the ContactChannelCard are wired to live backend endpoints.
COMPAT-ONLY paths are explicitly labeled and live.

### 8.4 Compatibility shims: 6 retained, all labeled

Six COMPAT-ONLY shims remain with migration gates (bu-k9ylx, bu-uhjxr, bu-pl8fy).

---

## 9. Discovered Follow-Up Items

The following gaps were found during reconciliation. They are reported here for the
coordinator to file as beads. They are NOT blockers for epic closure.

### FU-1: OpenSpec fast-forward — apply delta to canonical spec

**Title:** Apply decommission-contact-detail-page delta to canonical dashboard-relationship spec  
**Type:** task  
**Priority:** P2  
**Rationale:** The `openspec/changes/decommission-contact-detail-page/` change has been
ratified, implemented, and all code is merged. The `openspec ff` step to apply MODIFIED
blocks to `openspec/specs/dashboard-relationship/spec.md` and archive the change has not
been run. The canonical spec still shows old route/component names (ContactDetailPage,
/butlers/relationship/contacts/:id, /contacts/:contactId as canonical).
**File:** `openspec/specs/dashboard-relationship/spec.md`

---

### FU-2: Backend test for GET /relationship/contacts/{contact_id}/entity

**Title:** Add API integration test for contact-to-entity resolver endpoint  
**Type:** task  
**Priority:** P3  
**Rationale:** `GET /relationship/contacts/{contact_id}/entity` (router.py:1313) has no
dedicated backend test. Frontend router tests mock the endpoint. The backend logic is
simple (2-line SQL SELECT) and correct, but lacks regression coverage for the 404 path,
the linked/unlinked response shapes, and UUID coercion.
**File:** would go in `tests/api/test_relationship_contacts_entity_resolver.py`

---

### FU-3: Stale file tree in frontend/README.md

**Title:** Remove deleted files from frontend/README.md directory tree  
**Type:** chore  
**Priority:** P4  
**Rationale:** `frontend/README.md:187` still lists `ContactDetailPage.tsx` (and likely
`ContactsPage.tsx`, `EntitiesPage.tsx`) in the pages directory tree. These files were
deleted. The README is auto-generated documentation; the tree should be updated.

---

### FU-4: OwnerSetupBanner location diverges from spec

**Title:** Evaluate OwnerSetupBanner placement: entity detail vs entity index page  
**Type:** task  
**Priority:** P3  
**Rationale:** The delta spec (F3 resolution) requires the owner setup banner on
`/entities?has=contact` (entity index page). The implementation places it on
`/entities/:entityId` (entity detail) inside PracticalDrawer, visible only when the
owner entity is open. The entity index page has no banner. If the spec intent is
that the banner nudges first-time users at the list level, this is a functional gap.
A decision is needed: either update the spec to match the implementation (entity
detail placement is defensible), or add the banner to EntitiesIndexPage.

---

### FU-5: Secured credential types not in AddChannelInfoForm

**Title:** Decide: add secured credential types to AddChannelInfoForm or update spec  
**Type:** task  
**Priority:** P3  
**Rationale:** The delta spec (F2 resolution) requires the "Add contact info" form on the
contact-channel card to support secured types (`email_password`, `telegram_api_id`,
`telegram_api_hash`). The AddChannelInfoForm (`ContactChannelCard.tsx:496–593`) only
exposes `["email", "phone", "telegram", "website", "other"]`. Secured credentials are
intentionally routed to the Secrets page instead. A spec amendment is needed to either
(a) keep credentials on the Secrets page and update the spec to reflect this, or (b)
add secured-type support to the form.

---

### FU-6: Remove COMPAT-ONLY contact-keyed hooks after bu-uhjxr migration

**Title:** Remove contact-keyed COMPAT shims post bu-uhjxr  
**Type:** task  
**Priority:** P2 (gated on bu-uhjxr)  
**Rationale:** 5 COMPAT-ONLY hooks/endpoints remain active: `usePatchContact`,
`useRevealContactSecret`, `GET /relationship/contacts/{id}`, `PATCH /relationship/contacts/{id}`,
`GET /relationship/contacts/{id}/secrets/{infoId}`. All are properly labeled. After
bu-uhjxr (contacts-to-triples) + bu-k9ylx (write-path) + bu-pl8fy (secured migration)
reach cut-over, these must be removed in a single sweep.
**Depends on:** bu-uhjxr, bu-k9ylx, bu-pl8fy

---

## 10. Epic Closure Recommendation

**Recommendation: bu-m8gb6 CAN be closed.**

The epic's primary implementation goals are complete:
1. `ContactDetailPage` and `ContactDetailView` are deleted from the codebase.
2. `/contacts/:contactId` redirects to `/entities/:entityId` via a working resolver.
3. `/entities/:entityId` has a functional contact-channel card with all primary
   read/write operations wired to entity-keyed backends.
4. All COMPAT-ONLY shims are labeled, live, and have documented migration gates.
5. No dead controls found in the FE → BE wiring audit.

The residual gaps (FU-1 through FU-6) are hygiene tasks and spec alignment work,
none of which block user-facing functionality. The two partial requirements (owner
setup banner placement, secured credential form types) represent deliberate design
decisions that should be reconciled in the spec rather than code changes.

The COMPAT-ONLY shims (FU-6) are owned by the contacts-to-triples migration epic
(bu-uhjxr), not this epic.
