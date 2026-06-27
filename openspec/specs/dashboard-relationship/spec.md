# Dashboard Relationship

## Purpose

Defines the dashboard surfaces for the Relationship butler: the contact detail API, contact detail page, secured credential reveal, owner identity setup, pending identity disambiguation queue, roles management, and the bidirectional bridge between the memory entity pages and the relationship-scoped entity activity page. Together these form the complete operator-facing contract for viewing, managing, and navigating relationship data through the Butlers dashboard.
## Requirements
### Requirement: Contact detail API

The retired `public.contacts` / `public.contact_info` tables were dropped (core_134 / core_115) and there is no `GET /api/relationship/contacts/:id` endpoint. The canonical single-record read is `GET /api/relationship/entities/:id` (roster/relationship/api/router.py), which joins `public.entities` with contact-fact triples from `relationship.entity_facts` and secured rows from `public.entity_info`.

The response MUST include:
- The core entity record from `public.entities` (`id`, `canonical_name`, `entity_type`, `aliases`, `roles`, `metadata`, `state`, `created_at`, `updated_at`)
- `entity_info` (array) -- contact-channel entries projected from `relationship.entity_facts` contact-fact triples and the backing `public.entity_info` rows, each containing `id`, `type`, `value` (masked as `"********"` when the backing row has `secured = true`), `label`, `secured`, `created_at`
- Activity, gifts, loans, life events, and related-entity data are NOT inlined on this read. They are served by the entity-level tab and aggregator endpoints (see Requirement: Entity-level tab APIs and Requirement: Entity activity aggregator).

#### Scenario: Fetch an existing entity with full detail

- **WHEN** `GET /api/relationship/entities/ent-456-uuid` is called and the entity exists
- **THEN** the API MUST return the complete entity record including `roles` and `aliases` fields
- **AND** the `entity_info` array MUST contain the entity's contact-channel entries with secured values masked
- **AND** the response status MUST be 200

#### Scenario: Secured entity_info values are masked

- **WHEN** an entity has an `entity_info` entry with `secured = true` and `value = 'secret-token-123'`
- **THEN** the `entity_info` array entry MUST have `value = "********"` and `secured = true`

#### Scenario: Entity does not exist

- **WHEN** `GET /api/relationship/entities/nonexistent-uuid` is called and no entity with that ID exists
- **THEN** the API MUST return a 404 response with an error message indicating the entity was not found

#### Scenario: Entity with no contact data

- **WHEN** `GET /api/relationship/entities/ent-456-uuid` is called for an entity that has no contact-channel entries
- **THEN** the API MUST return the entity record with `entity_info` as an empty array (`[]`)
- **AND** the response status MUST be 200

---

### Requirement: Contact detail page

The standalone contact detail page SHALL be decommissioned. Contact-channel data
that remains useful to the user SHALL be rendered on the canonical entity detail
page at `/entities/:entityId` as a post-redesign contact-channel card.

The entity detail contact-channel card MUST contain, for each linked contact:

1. Contact methods grouped by type, including secured reveal/hide affordances for
   secured entries.
2. Preferred channel and primary contact summaries.
3. Important dates and quick facts until their triple-backed replacements are
   fully cut over.
4. Contact-to-contact relationships while relationship edge-facts remain out of
   scope.
5. Labels and supported lifecycle actions where those actions still exist in the
   backend.

The page MUST NOT reintroduce the legacy tabbed contact layout. Activity history
continues to live in the entity detail activity stream.

#### Scenario: Entity detail renders contact-channel card

- **WHEN** a user navigates to `/entities/ent-456-uuid` for an entity with one or
  more linked contacts
- **THEN** the entity detail page MUST render a contact-channel card
- **AND** the card MUST list each linked contact with its contact methods
- **AND** secured entries MUST be masked until explicitly revealed

#### Scenario: Entity detail renders sparse contact data

- **WHEN** an entity has a linked contact with no contact methods, dates, quick
  facts, labels, or relationships
- **THEN** the contact-channel card MUST render without errors
- **AND** empty subsections MUST use compact empty states, not blank dead space

#### Scenario: Activity stays entity-scoped

- **WHEN** the contact-channel card renders
- **THEN** notes, interactions, gifts, loans, and life events MUST remain in the
  entity ActivityTimeline and structured entity panels
- **AND** the card MUST NOT render separate activity tabs

---

### Requirement: Contact detail page canonical route is /contacts/:contactId

The route `/contacts/:contactId` SHALL be a compatibility route, not a canonical
page. It MUST resolve the contact by `contactId`, read the linked `entity_id`, and
redirect to `/entities/:entityId`.

If the contact does not exist, the route MUST render a not-found state. If the
contact exists but has no linked entity, the route MUST render a recovery state
that links back to `/entities?has=contact` and does not claim activity history is
available.

The route `/contacts` without a `contactId` continues to redirect to
`/entities?has=contact`.

#### Scenario: Contact detail URL redirects to entity detail

- **WHEN** a user navigates to `/contacts/abc-123-uuid`
- **AND** contact `abc-123-uuid` has `entity_id = ent-456-uuid`
- **THEN** the client MUST redirect to `/entities/ent-456-uuid`
- **AND** the entity detail page MUST render the contact-channel card

#### Scenario: Contact detail URL handles missing entity link

- **WHEN** a user navigates to `/contacts/abc-123-uuid`
- **AND** the contact exists but has `entity_id IS NULL`
- **THEN** the route MUST not redirect to a broken entity URL
- **AND** it MUST render a compact recovery state linking to `/entities?has=contact`

#### Scenario: Contact index still redirects to entity index filter

- **WHEN** a user navigates to `/contacts`
- **THEN** the client MUST redirect to `/entities?has=contact`

---

### Requirement: Contact detail page conforms to the detail-page archetype

The contact detail page at `/contacts/:contactId` SHALL conform to the detail-page archetype
defined in the `detail-page-archetype` spec.

**Changes from the existing requirement (§Requirement: Contact detail page):**

1. **Shell adoption.** The page MUST use `<Page archetype="detail">` as its outer
   shell. The existing breadcrumbs block MUST be passed via the `breadcrumbs` prop.
   The inline three-skeleton loading block and the inline destructive-text error block
   MUST be removed from the page body and delegated to the `loading` and `error` props
   on `<Page>`.

2. **Title.** The `title` prop on `<Page>` MUST be the contact's full name
   (`first_name + " " + last_name`), consistent with the H1 already rendered inside
   `ContactDetailView`. If the contact has a `nickname`, it MUST be appended in
   parentheses: `"Alice Johnson (Allie)"`.

3. **Actions.** The edit and delete buttons currently inside `ContactDetailView`'s
   header (`ContactDetailView.tsx` lines 864–898) MUST be migrated to the `actions`
   prop on `<Page>` so they appear in the page header row. The `ContactDetailView`
   component body retains all other content.

4. **Body layout.** The `<ContactDetailView>` component output (minus the header
   card's edit/delete buttons) becomes the `primary` body slot inside the shell.

5. **Token cleanup status.** The hex-literal color palettes previously at
   `ContactDetailView.tsx` lines 53–62 and 69–77 have already been replaced with
   CSS custom properties (`var(--category-*)` and `var(--role-*)`) as of the migration
   in ce185209 (role badge hex → CSS tokens). No token-cleanup prerequisite remains
   for this migration step. Implementers should verify no new hex literals were
   introduced during the archetype migration.

#### Scenario: Contact detail uses shell loading state

- **WHEN** `GET /api/relationship/contacts/:id` is in flight
- **THEN** the `<Page>` shell MUST show `DetailSkeleton`
- **AND** no inline `<Skeleton>` blocks MUST be rendered by the page at the page layer

#### Scenario: Contact detail uses shell error state

- **WHEN** the contact fetch fails
- **THEN** the `<Page>` shell MUST render the destructive error card
- **AND** no inline destructive-text block MUST be rendered at the page layer

#### Scenario: Contact detail title shows full name with nickname

- **WHEN** a contact has `first_name = "Alice"`, `last_name = "Johnson"`, and
  `nickname = "Allie"`
- **THEN** the `<h1>` rendered by the shell MUST read "Alice Johnson (Allie)"

#### Scenario: Contact detail title shows full name without nickname

- **WHEN** a contact has `first_name = "Bob"`, `last_name = "Smith"`, and no nickname
- **THEN** the `<h1>` rendered by the shell MUST read "Bob Smith"

#### Scenario: Contact edit and delete actions in page header

- **WHEN** a contact detail page renders a resolved contact
- **THEN** the edit button and the delete button MUST appear in the page header row
  (via the `actions` prop), visible without scrolling
- **AND** they MUST NOT appear only inside the `<ContactDetailView>` card body

#### Scenario: No hex literals for role badge colors

- **WHEN** a contact has a role badge (e.g., "owner") rendered on the detail page
- **THEN** the badge color MUST use a CSS custom property or Tailwind semantic token
- **AND** the badge MUST NOT be styled with an inline `style={{ backgroundColor: "#..." }}`

---

### Requirement: Secured contact info reveal API

The dashboard API SHALL expose `GET /api/relationship/entities/{entity_id}/secrets/{info_id}` which returns the unmasked value of a secured `public.entity_info` entry (rejecting non-secured rows with HTTP 400).

#### Scenario: Reveal a secured value

- **WHEN** `GET /api/relationship/entities/ent-456/secrets/info-456` is called for an `entity_info` entry with `secured = true`
- **THEN** the API MUST return the actual `value` of the entry
- **AND** the response status MUST be 200

#### Scenario: Non-secured entry is rejected

- **WHEN** `GET /api/relationship/entities/ent-456/secrets/info-456` is called for an `entity_info` entry with `secured = false`
- **THEN** the API MUST return a 400 response
- **AND** the response MUST NOT include the unmasked `value`

#### Scenario: Entry does not exist

- **WHEN** `GET /api/relationship/entities/ent-456/secrets/nonexistent-id` is called
- **THEN** the API MUST return a 404 response

#### Scenario: Entry belongs to different entity

- **WHEN** `GET /api/relationship/entities/ent-456/secrets/info-456` is called but `info-456` belongs to entity `ent-789`
- **THEN** the API MUST return a 404 response

---

### Requirement: Owner identity and credential management

The Secrets page (`/secrets`) SHALL be the primary mechanism for configuring
owner identity credentials. The entity detail page (`/entities/:entityId`) SHALL
surface identity-bound credentials by displaying a prominent link to the Secrets
page (`/secrets` → User tab) where the owner entity's credentials can be viewed,
entered, and managed.

The entity detail contact-channel card MUST NOT include secured credential types
(`email_password`, `telegram_api_id`, `telegram_api_hash`,
`home_assistant_token`) in its "Add contact info" type dropdown. The
`AddChannelInfoForm` MUST support the following non-secured contact types only:
`email`, `phone`, `telegram`, `website`, `other`. The form MUST display
human-friendly labels for all supported types.

The User tab on the Secrets page MUST support all secured credential types for
the owner entity (`email_password`, `telegram_api_id`, `telegram_api_hash`,
`home_assistant_token`).

> **Design rationale (deliberate product decision):** Secured credentials are
> intentionally managed on the dedicated Secrets page, not through the "Add
> contact info" form on the entity detail contact-channel card. Separating
> credential entry from contact-channel entry is an explicit security/UX
> boundary: the Secrets page is purpose-built for masked entry, reveal
> affordances, and per-entity identity projection. This is the shipped
> implementation as of the entity detail redesign (bu-m8gb6 reconciliation,
> 2026-05-25, bu-x1zql spec alignment).

#### Scenario: Add a non-secured channel entry from the entity detail contact-channel card

- **WHEN** a user opens the owner entity's detail page at `/entities/:entityId`
  and clicks "Add contact info" in the contact-channel card
- **AND** selects "Email" or "Telegram" from the type dropdown and enters a
  value
- **THEN** the input field MUST be a text field (not masked)
- **AND** the created entry MUST have `secured = false`

#### Scenario: Secured credentials are managed via the Secrets page

- **WHEN** a user needs to enter or update a secured credential (e.g., email
  password, Telegram API ID/hash)
- **THEN** the user MUST navigate to the Secrets page at `/secrets` (linked
  from the entity detail page)
- **AND** the User tab MUST display and allow management of the owner entity's
  secured credentials
- **AND** the entity detail contact-channel card MUST NOT offer secured
  credential types in its add form

---

### Requirement: Owner identity setup banner

The dashboard SHALL display a persistent banner on the entity detail page
(`/entities/:entityId`) when the owner entity is missing key identity
fields (name, telegram handle, or telegram chat ID). The banner appears
inside the practical drawer, which is forced open when the owner has not
completed identity setup. The entity detail contact-channel card at
`/entities/:entityId` is the canonical location for ongoing identity and
credential management.

> **Placement rationale (deliberate product decision):** An earlier revision of
> this spec placed the banner on the entity index page (`/entities?has=contact`)
> as a "convenience" onboarding shortcut. The shipped implementation places it on
> the entity detail page inside the practical drawer (forced open when setup is
> incomplete), co-located with the canonical identity management surface. This is
> the correct placement because: (1) the detail page is the spec's own canonical
> location for identity management, (2) `forceOpen` ensures the banner is
> prominently surfaced without requiring a separate index-level data fetch, and
> (3) the reconciliation of bu-m8gb6 explicitly recommended updating the spec
> rather than changing the UI code.

#### Scenario: Banner shown when owner has missing identity fields

- **WHEN** a user navigates to `/entities/:entityId` for the owner entity and
  the owner is missing any of: name, telegram handle, or telegram chat ID
- **THEN** a banner MUST be displayed inside the practical drawer indicating
  which fields are missing
- **AND** the practical drawer MUST be forced open when the banner is active
- **AND** a "Set Up Identity" button MUST open a dialog for filling in missing
  fields

#### Scenario: Banner hidden when all identity fields are configured

- **WHEN** the owner entity has name, telegram handle, and telegram chat ID
  configured
- **THEN** the setup banner MUST NOT be displayed

#### Scenario: Banner dialog includes credentials section

- **WHEN** the owner setup dialog is opened
- **THEN** a collapsible "Credentials" section MUST be available for
  optionally setting Telegram API ID, Telegram API hash, Home Assistant URL,
  and Home Assistant token
- **AND** credential fields (API hash, API ID, Home Assistant token) MUST
  create secured `entity_info` entries

---

### Requirement: Pending identities queue on contacts page

The entity index page (`/entities?has=contact`) SHALL display a "Pending
Identities" section listing all contacts with
`metadata.needs_disambiguation = true`. This section MUST appear above the
main entity table when pending contacts exist.

#### Scenario: Pending identities displayed

- **WHEN** a user navigates to `/entities?has=contact` and 2 temporary
  contacts exist with `metadata.needs_disambiguation = true`
- **THEN** a "Pending Identities" section MUST appear above the entity table
- **AND** each pending contact MUST display the contact's name, source
  channel, source value, and creation date

#### Scenario: Merge action on pending identity

- **WHEN** the user clicks "Merge" on a pending identity
- **THEN** a dialog MUST open with a contact search/select input
- **AND** the user MUST be able to search existing contacts by name
- **AND** selecting a contact and confirming MUST call the merge API
- **AND** the pending identity MUST disappear from the queue after successful
  merge

#### Scenario: Confirm as new action on pending identity

- **WHEN** the user clicks "Confirm as new" on a pending identity
- **THEN** the `needs_disambiguation` flag MUST be removed from the contact's
  metadata
- **AND** the contact MUST move to the main entity table

#### Scenario: Archive action on pending identity

- **WHEN** the user clicks "Archive" on a pending identity
- **THEN** the contact's `listed` MUST be set to `false`
- **AND** the pending identity MUST disappear from the queue

#### Scenario: No pending identities

- **WHEN** no contacts have `metadata.needs_disambiguation = true`
- **THEN** the "Pending Identities" section MUST NOT be displayed

---

### Requirement: Dashboard roles management API

Roles live on the `public.entities.roles` column and are managed through the entity surface, not a contacts endpoint. The retired `public.contacts` table was dropped in core_134, so there is no `PATCH /api/contacts/{id}` endpoint.

#### Scenario: Update entity roles

- **WHEN** `POST /api/relationship/entities` is called with `{"id": "ent-456", "roles": ["owner"]}` for an existing entity
- **THEN** the entity's `roles` column MUST be updated to `['owner']`
- **AND** the response MUST include the updated entity with the new roles
- **AND** the response status MUST be 200

#### Scenario: Roles update is owner-gated

- **WHEN** the caller does not resolve to an owner-role entity and `POST /api/relationship/entities` is called with a `roles` change
- **THEN** the API MUST return a 403 response with `{ code: 'owner_required' }`
- **AND** the `roles` column MUST NOT be modified

---

### Requirement: Memory entity page links to relationship activity

The entity surfaces (`/entities` and `/entities/:entityId`) SHALL use
`/entities/:entityId` as the canonical entity detail route. They MUST NOT link to
`/butlers/relationship/entities/:entityId` as a product route.

The legacy route `/butlers/relationship/entities/:entityId` MAY remain registered
only as a compatibility redirect to `/entities/:entityId`.

#### Scenario: Internal entity links target canonical entity route

- **WHEN** a user activates an entity row, entity finder result, contact-channel
  card entity link, or relationship activity affordance
- **THEN** the navigation target MUST be `/entities/:entityId`
- **AND** no new internal link MUST target `/butlers/relationship/entities/:entityId`

#### Scenario: Legacy relationship entity URL redirects

- **WHEN** a user navigates to `/butlers/relationship/entities/ent-456-uuid`
- **THEN** the client MUST redirect to `/entities/ent-456-uuid`

---

### Requirement: Entity detail page

The frontend SHALL render the canonical entity detail page at `/entities/:entityId`
displaying the entity's identity header, contact-channel card, unified activity
stream, and supporting entity panels. This is the canonical surface for browsing
notes, interactions, gifts, loans, life events, contact methods, and practical
relationship context for any entity in `public.entities`.

The page MUST contain:

1. **Header** — displaying `canonical_name`, `entity_type`, aliases, roles, entity
   state, and mode controls.
2. **Contact-channel card** — displaying linked-contact channel data as specified
   in Requirement: Contact detail page.
3. **Unified ActivityTimeline** — a single vertically-scrolling event stream
   sourced from the entity timeline endpoint.
4. **Gifts and Loans panels** — structured displays for those fact families when
   non-empty.
5. **Workbench/Provenance mode** — the dense provenance view already specified for
   entity detail.

#### Scenario: Entity detail is canonical at /entities

- **WHEN** a user navigates to `/entities/ent-456-uuid`
- **THEN** the entity detail page MUST render for entity `ent-456-uuid`
- **AND** the page MUST include activity and contact-channel context in the same
  detail surface

#### Scenario: Entity not found

- **WHEN** a user navigates to `/entities/nonexistent-uuid`
- **THEN** the page MUST display an entity not-found state

---

### Requirement: Entity-level tab APIs

The dashboard API SHALL expose five entity-keyed endpoints for tab data, each reading from `facts` filtered by predicate. All five endpoints MUST scope queries to `validity = 'active' AND scope = 'relationship'`. All five endpoints MUST support pagination via query parameters `?limit=` (default 50, max 200) and `?offset=` (default 0). All five endpoints MUST return 404 if the requested entity UUID does not exist in `public.entities`.

The endpoints are:

| Endpoint | Predicate filter | Sort order |
|---|---|---|
| `GET /api/relationship/entities/{id}/notes` | `predicate = 'contact_note'` | `valid_at DESC` |
| `GET /api/relationship/entities/{id}/interactions` | `predicate LIKE 'interaction_%'` | `valid_at DESC` |
| `GET /api/relationship/entities/{id}/gifts` | `predicate = 'gift'` | `created_at DESC` |
| `GET /api/relationship/entities/{id}/loans` | `predicate = 'loan'` | `created_at DESC` |
| `GET /api/relationship/entities/{id}/timeline` | `predicate IN ('contact_note','life_event','gift','loan','dunbar_tier_override') OR predicate LIKE 'interaction_%'` | `valid_at DESC NULLS LAST, created_at DESC` |

The Timeline endpoint excludes the legacy `activity` predicate. The `_log_activity()` write path is removed in this change; historical `activity` facts (if any survive) are not surfaced on Timeline (they are duplicates of primary facts already included via their own predicates) but remain queryable via the `feed_get` MCP tool.

Response field shapes MUST follow the wrapper mappings in `predicate-taxonomy.md` §5.2 with the following per-tab shapes:

- **notes** entries: `{ id: fact.id, content: fact.content, emotion: fact.metadata->>'emotion', created_at: fact.valid_at }`
- **interactions** entries: `{ id: fact.id, type: <predicate suffix>, summary: fact.content, occurred_at: fact.valid_at, direction: fact.metadata->>'direction', group_size: fact.metadata->>'group_size' }`. The `type` field is extracted from the predicate suffix: `predicate='interaction_meeting'` yields `type='meeting'`. The `direction` and `group_size` fields are populated by the passive interaction sync job (`passive-interaction-sync` spec) and may be null for facts written via direct `interaction_log()` calls without those metadata keys.
- **gifts** entries: `{ id: fact.id, description: fact.content, occasion: fact.metadata->>'occasion', status: fact.metadata->>'status', created_at: fact.created_at }`
- **loans** entries: `{ id: fact.id, description: fact.content, amount_cents: fact.metadata->>'amount_cents', currency: fact.metadata->>'currency', direction: fact.metadata->>'direction', settled: fact.metadata->>'settled', settled_at: fact.metadata->>'settled_at', created_at: fact.created_at }`
- **timeline** entries: `{ kind: <predicate-family>, id: fact.id, content: fact.content, valid_at: fact.valid_at, predicate: fact.predicate, metadata: fact.metadata }` where `kind` is one of `note`, `interaction`, `gift`, `loan`, `life_event`, `dunbar_tier_override`.

When a metadata field referenced above is absent from a fact's JSONB, the response value MUST be `null` (not omitted; not a default). Clients MUST be able to render rows with missing metadata fields without errors.

#### Scenario: Notes endpoint returns facts for entity

- **WHEN** `GET /api/relationship/entities/ent-456/notes` is called and three `contact_note` facts exist with `entity_id = ent-456`, `validity = 'active'`, `scope = 'relationship'`
- **THEN** the response status MUST be 200
- **AND** the response body MUST be a list of three entries shaped per the notes mapping
- **AND** entries MUST be ordered by `valid_at DESC`

#### Scenario: Interactions endpoint merges interaction subtypes

- **WHEN** `GET /api/relationship/entities/ent-456/interactions` is called and the entity has interaction facts with predicates `interaction_meeting`, `interaction_message`, and `interaction_call`
- **THEN** the response MUST include all three with `type` field set to `"meeting"`, `"message"`, and `"call"` respectively (the predicate suffix)
- **AND** entries MUST be ordered by `valid_at DESC` regardless of subtype

#### Scenario: Mixed-channel interactions are merged across linked contacts

- **WHEN** an entity has two contacts (one Telegram, one email) and interaction facts exist via both channels
- **THEN** `GET /api/relationship/entities/{id}/interactions` MUST return all facts where `entity_id = $1` regardless of which contact's tools created them
- **AND** the response MUST NOT deduplicate by `(predicate, valid_at)` — facts from different channels are surfaced separately

#### Scenario: Timeline orders by valid_at across all six predicate families

- **WHEN** `GET /api/relationship/entities/{id}/timeline` is called and the entity has facts of every supported predicate family
- **THEN** the response MUST include facts from `interaction_*`, `contact_note`, `life_event`, `gift`, `loan`, and `dunbar_tier_override`
- **AND** entries MUST be ordered by `valid_at DESC` with `NULLS LAST` semantics, falling back to `created_at DESC` for property facts (gift, loan, dunbar_tier_override)
- **AND** each entry MUST include a `kind` field identifying the predicate family

#### Scenario: Timeline excludes legacy activity facts

- **WHEN** `GET /api/relationship/entities/{id}/timeline` is called and the entity has facts with `predicate = 'activity'`
- **THEN** those facts MUST NOT appear in the response

#### Scenario: Empty entity returns empty arrays

- **WHEN** any of the five endpoints is called for an entity that has zero matching facts
- **THEN** the response status MUST be 200
- **AND** the response body MUST be `[]`

#### Scenario: Entity does not exist

- **WHEN** any of the five endpoints is called with an entity UUID that does not exist in `public.entities`
- **THEN** the response status MUST be 404
- **AND** the response body MUST contain an error message indicating the entity was not found

#### Scenario: Retracted facts are excluded

- **WHEN** an entity has facts with `validity = 'retracted'` or `validity = 'superseded'`
- **THEN** none of the five endpoints MUST include those facts in their responses
- **AND** only `validity = 'active'` facts MUST be returned

#### Scenario: Pagination defaults and limits

- **WHEN** any of the five endpoints is called without `limit` or `offset` query parameters
- **THEN** the response MUST return at most 50 entries
- **AND** the offset MUST be 0

#### Scenario: Pagination max enforced

- **WHEN** any of the five endpoints is called with `?limit=500`
- **THEN** the response MUST be limited to 200 entries (the maximum)

#### Scenario: Cross-scope facts excluded

- **WHEN** an entity has facts with `scope = 'health'` or `scope = 'finance'`
- **THEN** the relationship-domain endpoints MUST NOT include those facts in any response
- **AND** only facts with `scope = 'relationship'` MUST be returned

#### Scenario: Sparse metadata fields render as null

- **WHEN** a fact has `metadata = '{}'` or is missing one of the documented metadata fields
- **THEN** the response entry MUST include the field with value `null`
- **AND** the endpoint MUST NOT raise an error

---

**Phase 2 Extension: Entity Redesign**

> Added 2026-05-17 via `/project-direction` Phase 2 for the entity-redesign feature.
> Drives the brief at `docs/redesigns/2026-05-17-entity-brief.md` (binding §0 design intent,
> binding §6b Phase 1 amendments). Layered on top of the contact-tabs scope above.

> **Phase 1 / Phase 2 table reconciliation:** Phase 1's tab endpoints (§Notes/§Interactions/§Gifts/§Loans/§Timeline above) currently read the legacy shared `facts` table where the relationship butler stores relational and contact facts under `scope='relationship'`. Phase 2 introduces `relationship.entity_facts` as the canonical RDF triple store (per `specs/relationship-facts/spec.md`). During the 10-step migration (Brief §6b Amendment 1.1.C), Phase 1 endpoints MUST be re-pointed to `relationship.entity_facts` no later than Migration bead 7 (read-path cut-over). Until cut-over, Phase 1 endpoints read the legacy table; from cut-over, they read `relationship.entity_facts`. Both reads return identical data during the dual-write window. Phase 2 endpoints (§§added below) read `relationship.entity_facts` from day one — they ship after Migration bead 5 (backfill) completes.

### Requirement: Owner-only authorization for entity endpoints

The entity endpoints under `/api/relationship/entities/*` MUST enforce owner-only authorization.
They expose both mutation surfaces that mint, merge, archive, or forget entities AND read
surfaces that return raw contact-fact `object` values (emails, phone numbers, social handles,
addresses) — which are PII. The owner-only authorization gate from
`about/heart-and-soul/security.md:18-22` and `rfcs/0007:309` (`'owner' = ANY(e.roles)`) MUST
apply to both write and PII-bearing read surfaces; one without the other leaves a leak hole.

**Clause 12a — Writes (mutations).** Every `POST/PATCH/DELETE` under
`/api/relationship/entities/*` MUST resolve the caller to an owner-role entity
per the `'owner' = ANY(e.roles)` pattern and return HTTP 403 with the envelope
`{ code: 'owner_required' }` otherwise. The gate applies to the exact endpoint set:

- `POST /api/relationship/entities`
- `POST /api/relationship/entities/{id}/merge`
- `POST /api/relationship/entities/{id}/archive`
- `POST /api/relationship/entities/{id}/promote-tier`
- `DELETE /api/relationship/entities/{id}`
- `POST /api/relationship/entities/queue/dismiss`
- `POST /api/relationship/entities/{id}/contacts`
- `DELETE /api/relationship/entities/{id}/contacts/{pred}/{valueHash}`

**Clause 12b — Reads (PII-bearing).** The same owner-only gate MUST apply to the following
GET endpoints because they return raw contact-fact `object` values (emails / phones /
handles / addresses) or aliased identity links whose exposure through the shared
`DASHBOARD_API_KEY` would leak PII to any caller reaching the API surface:

- `GET /api/relationship/entities/queue`
- `GET /api/relationship/entities/search`
- `GET /api/relationship/entities/{id}/contacts`
- `GET /api/relationship/entities/{id}/neighbours`
- `GET /api/relationship/entities/{id}/activity`

The list-only `GET /api/relationship/entities` and per-entity timeline / notes /
interactions / gifts / loans endpoints (which do NOT surface raw contact-fact `object`
values) inherit the existing dashboard session boundary and are not within scope of this
gate. Any future change that adds raw contact-fact values to those responses MUST extend
the gate to the affected endpoint.

**Clause 12c — Deploy gate.** In any non-`dev` environment, daemon startup MUST fail with
a fatal error if `DASHBOARD_API_KEY` is unset. The dev-time "no API key → auth disabled"
shortcut at `src/butlers/api/app.py:246` is incompatible with shipping the entity endpoints.
A guardrail test (tasks.md §12.8) MUST exercise this invariant.

#### Scenario: Owner request to mutate entity succeeds
- **WHEN** an authenticated request resolves to an entity with `'owner' = ANY(e.roles)` and
  calls `POST /api/relationship/entities/{id}/promote-tier`
- **THEN** the response status MUST be 2xx (per the endpoint's own contract)
- **AND** the gate MUST NOT reject the request

#### Scenario: Non-owner request is rejected with `owner_required`
- **WHEN** an authenticated request resolves to an entity whose `roles` does NOT contain
  `'owner'` and calls any endpoint in clause 12a or 12b
- **THEN** the response status MUST be 403
- **AND** the response body MUST contain `{ code: 'owner_required' }` (envelope form per
  `rfcs/0007:75-87` or unwrapped per relationship-domain convention; the `code` string is
  binding)
- **AND** no mutation MUST be applied
- **AND** no PII MUST be returned

#### Scenario: Missing `DASHBOARD_API_KEY` in production refuses startup
- **WHEN** the daemon starts with `BUTLERS_ENV != 'dev'` and `DASHBOARD_API_KEY` unset
- **THEN** startup MUST fail with a fatal error referencing the missing key
- **AND** no entity endpoint MUST become reachable

---

### Requirement: Entity index page (`/entities`)

The frontend SHALL render an entity index at `/entities` (NOT `/butlers/relationship/entities`)
as the canonical landing surface for "people and things I care about." The route is owned
by the relationship butler's frontend tree but exposed at the top-level path because the
Index is the home for every entity-related workflow (Hop, Columns, Concentration, Social-map
are alternate views of the same population). The Index MUST consist of:

1. **Tabular list (left/main column)** — one row per entity, neutral hairline-on-neutral.
   Columns: entity-mark glyph (type indicator: `P / O / L / X / @ / E / G`), canonical_name +
   nicknames, tier badge (Dunbar), `last_seen`, contact-fact count pill, aliases. Rows MUST NOT
   carry state colour; the EntityMark glyph carries type, not hue (Brief §0 "No hue from entity type").
   Row vertical padding is 10px (not 24px — no card thinking).
2. **Filter chips** — type pills (`person/organization/location/product/...`), `has=contact`
   chip (replaces legacy `/contacts` page), state chips (`unidentified`, `duplicate-candidate`,
   `stale`), tier chips. The `has=contact` chip MUST surface all entities with at least one
   `has-email | has-phone | has-handle | has-address` triple.
3. **Curation queue (right rail)** — see Requirement: Entity curation queue.
4. **SubpageTabs** — horizontal nav strip linking Index / Hop / Columns / Concentration /
   Social-map. Active tab is `/entities`.
5. **Cmd-K affordance** — visible mono kbd capsule (`⌘K`) in the header.

The Index page MUST render inside `<Page archetype="overview">` (per the in-flight
`page-primitive-spec-sync` change) with breadcrumb `Entities`.

#### Scenario: Index renders with neutral rows and queue rail
- **WHEN** a user navigates to `/entities` with at least one entity in `public.entities`
- **THEN** the rows MUST render neutral hairline-on-neutral (no amber/red fills)
- **AND** the curation queue rail MUST be present (collapsed to single serif italic line if empty)
- **AND** the SubpageTabs strip MUST mark Index as active

#### Scenario: has=contact filter chip lists every entity with a contact triple
- **WHEN** `?has=contact` query is applied
- **THEN** the result set MUST be exactly the entities with at least one triple in
  `relationship.entity_facts` whose predicate matches `has-email | has-phone | has-handle |
  has-address | has-birthday | has-website`

#### Scenario: `/contacts` index redirects to `/entities?has=contact`
- **WHEN** a request reaches the contacts INDEX path `/contacts` (no `:contactId` param)
- **THEN** the response MUST be a 301 redirect to `/entities?has=contact`
- **AND** no functional regression MUST occur for any prior `/contacts` index workflow
- **AND** the contact-detail path `/contacts/:contactId` MUST NOT be redirected; it
  continues to serve the canonical contact detail page per Requirement: Contact detail
  page canonical route in the shipped `dashboard-relationship` spec.

### Requirement: Entity Hop view (`/entities/hop`)

The frontend SHALL render a re-centre graph explorer at `/entities/hop` with predicate-grouped
neighbour fan-out. The page MUST:

1. Accept `?center=<entity_id>` to seed the centre node (defaults to owner if absent).
2. Render the centre entity card plus predicate-grouped neighbour rows (`knows` group,
   `family-of` group, `co-attended` group, etc.). Each neighbour shows EntityMark + name +
   tier + edge weight + `last_seen`.
3. Allow re-centring on any neighbour with one click. Re-centring MUST update `?center=`
   and remain on `/entities/hop` (NOT navigate away to a different product surface).
4. Render inside `<Page archetype="overview">` with SubpageTabs strip marking Hop active.

Data source: `GET /api/relationship/entities/{id}/neighbours` (Requirement: Entity
neighbours endpoint below).

#### Scenario: Re-centre keeps user on /entities/hop
- **WHEN** a user clicks a neighbour from the centre fan-out
- **THEN** the URL MUST change to `/entities/hop?center=<new_entity_id>`
- **AND** the page MUST remain `/entities/hop` (NOT navigate to `/entities/<id>` detail)

### Requirement: Entity Columns view (`/entities/columns`)

The frontend SHALL render a Finder-style cascading column drill at `/entities/columns`.
Each column shows one entity's predicate-grouped neighbours; clicking a neighbour pushes
a new column to the right. Column 0 is the owner unless `?path=` overrides it. Each column
MUST be reachable via either (a) chained client-side calls to
`GET /api/relationship/entities/{id}/neighbours` or (b) a server-side
`GET /api/relationship/entities/{id}/columns?path=<csv>` helper. Phase 2 picks
**option (a)**: client-side chaining is sufficient for v1; no new server endpoint required
(resolves Phase 1 Open Question 15).

Render inside `<Page archetype="overview">` with SubpageTabs Columns active.

#### Scenario: Clicking a neighbour pushes a new column
- **WHEN** the user is on `/entities/columns` viewing column 0 (owner) and clicks a neighbour
  of the owner in column 0
- **THEN** a new column MUST be appended to the right showing that neighbour's
  predicate-grouped neighbours
- **AND** the URL MUST reflect the new path (e.g. `?path=ent-1,ent-2`)
- **AND** no new server endpoint MUST be called (per option (a)); only chained calls to
  `/api/relationship/entities/{id}/neighbours`

#### Scenario: Column 0 defaults to owner
- **WHEN** the user navigates to `/entities/columns` without a `?path=` query
- **THEN** column 0 MUST render the owner entity's predicate-grouped neighbours
- **AND** the SubpageTabs strip MUST mark Columns active

### Requirement: Entity Concentration view (`/entities/concentration`)

The frontend SHALL render a balance-sheet view of weight aggregation per predicate at
`/entities/concentration`. The page MUST:

1. Accept `?pred=<predicate>` (default: `knows`). Tabs flip the active predicate.
2. Render a sorted list: rows are entities, columns are `weight` (sum of edge weights for
   that predicate), `share` (weight / total), `last_seen`. Tabular nums; no count-up animation.
3. Render a header rollup: `total`, `top3Share`.
4. Tabs are NOT hardcoded to four predicates — the predicate set is enumerated from the
   `predicate_registry` filtered to relational predicates (resolves Phase 1 Open Question 8).
5. Render inside `<Page archetype="overview">` with SubpageTabs Concentration active.

Data source: `GET /api/relationship/entities/concentration?pred=<predicate>`.

#### Scenario: Predicate tabs are enumerated from registry
- **WHEN** the page loads with `relationship.entity_predicate_registry` containing five relational
  predicates (`knows`, `family-of`, `partner-of`, `colleague-of`, `co-attended`)
- **THEN** the Concentration page MUST render five tabs (NOT a hardcoded four)
- **AND** the active tab MUST be `knows` if no `?pred=` is supplied

#### Scenario: top3Share and total render in tabular nums
- **WHEN** the page renders with at least three entities holding non-zero `weight` for
  `predicate='knows'`
- **THEN** the header rollup MUST show `total` (sum of all weights) and `top3Share`
  (top-3-sum / total) using `font-variant-numeric: tabular-nums`
- **AND** no count-up animation MUST be applied to the values

### Requirement: Social Map preservation

The existing `/entities/social-map` route MUST remain unchanged in this redesign pass.
SocialMapPage is refactored into a `SocialMapView` component so the SubpageTabs chrome
can wrap it without duplicating layout, but its visual behaviour and data sources are
preserved. Any refresh to the Dunbar circles UI is explicitly out of scope (resolves
Phase 1 Open Question 1).

#### Scenario: SocialMapView renders inside SubpageTabs chrome unchanged
- **WHEN** the user navigates to `/entities/social-map` after the refactor
- **THEN** the Dunbar-circles visualisation MUST render with identical visual behaviour
  to the pre-refactor `SocialMapPage`
- **AND** the SubpageTabs strip MUST wrap the view with Social-map marked active
- **AND** the data sources powering the circles MUST be unchanged from the prior spec

#### Scenario: Dunbar circles UI is not modified
- **WHEN** code review compares the post-refactor `SocialMapView` rendering against the
  pre-refactor `SocialMapPage` rendering
- **THEN** the visual output (circle layout, sizing, labels, colours) MUST be identical
- **AND** any change to the circles UI MUST be explicitly out of scope and rejected at
  review

### Requirement: Entity detail Editorial / Workbench mode toggle

The entity detail page at `/entities/:entityId` SHALL render in one of two modes: **Editorial** (default) or **Workbench**.
The unified ActivityTimeline is present in Editorial mode. In Workbench mode
it is replaced by the ProvenanceGrid (see `bu-r6vft`), which surfaces every
provenance column in a dense, sortable grid. The toggle also changes how the
header and contact facts are rendered.

**Editorial mode** is the default and MUST:
- Use `<Page archetype="detail">` (per the in-flight `detail-page-archetype`
  change) with Display 44px headline for the entity canonical_name (editorial
  archetype, per `about/heart-and-soul/design-language.md:218-246`
  Non-Negotiable 2 + Gate A A2). The 44px Display tier is permitted per the
  editorial-archetype carve-out at
  `about/heart-and-soul/design-language.md:225-232`; the 1.2 type-ratio
  doctrine at `:243-246` is a floor (values ≥1.2 satisfy it), not a target —
  Display-tier headlines are exempt by archetype.
- Hide provenance metadata (`conf`, `src`, `weight`, `verified`, `primary`)
  from row chrome. Provenance is still loaded into the response; only the
  visual rendering hides it.
- Render contacts grouped by predicate (`has-email`, `has-phone`, ...). A
  person with three emails MUST render three rows, primary first; never
  collapsed to "the email."
- Render the voice gloss in `Source Serif 4` italic 16px (one line under the
  canonical name). **The gloss text MUST be a canned string** selected by
  `(tier, state, category)` from `frontend/src/lib/entity-glosses.ts` — see
  Requirement: Detail-page voice gloss source.

**Workbench mode** MUST:
- Use `<Page archetype="overview">` with `text-2xl` H1 (per
  `about/heart-and-soul/design-language.md` Non-Negotiable 2 + Gate A A2).
  44px Display is forbidden in this mode. Editorial mode uses
  `<Page archetype="detail">` (per the in-flight `detail-page-archetype`
  change); Workbench reuses the already-defined `archetype="overview"` for
  its dense workspace layout. **Workspace-archetype gap note (R3):** the
  brief originally proposed `<Page archetype="workspace">` but no `workspace`
  archetype is normatively defined in any shipped or in-flight Page spec.
  Rather than block on authoring a sister spec, Workbench reuses
  `archetype="overview"` (which IS defined) for v1; a dedicated `workspace`
  archetype MAY be introduced in a separate change later if needed.
- Surface every provenance column (`conf`, `src`, `lastSeen`, `weight`,
  `verified`, `primary`) on every row. The same data record drives both
  modes.
- Render contacts as a dense predicate+value+provenance grid; sortable by
  any column.

**Mode persistence and toggle UI:**
- The mode toggle lives in the Page shell's actions slot (icon button), per
  Phase 1 Amendment 8.
- The mode persists in `localStorage` under the key `entities.detail.mode`
  (distinct from the `butlers.detail.mode` key used by
  `redesign-detail-page-tab-vocabulary`'s Resident/Operator toggle — Phase 1
  Amendment 10 mandates the distinct key and distinct vocabulary).
- Missing, invalid, or unsupported values in `localStorage` MUST default to
  `editorial`.
- `?mode=workbench` URL parameter overrides `localStorage` for the current
  page load only; toggling via the UI updates both URL and `localStorage`.
  _(Design history: param name reconciled from `?view=` → `?mode=` to match
  shipped code, bu-monvg.)_

**Forget affordance (binding):**
- Both modes MUST surface a "Forget this entity" action in the Page header
  (NOT a kebab menu). Clicking opens a confirm dialog with a one-sentence
  serif gloss (canned text: "Forgetting also tombstones the source. Aliases
  stay.") before the destructive POST.

#### Scenario: Editorial is default, mode persists

- **WHEN** a user lands on `/entities/<uuid>` with no `localStorage` value
- **THEN** Editorial MUST render with Display 44px headline
- **WHEN** the user toggles to Workbench
- **THEN** `localStorage["entities.detail.mode"]` MUST be set to `workbench`
- **AND** subsequent loads MUST render Workbench until toggled back

#### Scenario: Three emails render three rows in both modes

- **WHEN** an entity has three `has-email` triples (primary + two secondary)
- **THEN** Editorial MUST render three rows under the "Email" predicate group,
  primary first
- **AND** Workbench MUST render three rows in the contacts grid, sorted by
  `primary DESC`
- **AND** neither mode MUST collapse to a single "Email" row

---

### Requirement: Entity curation queue (Index right rail)

The Index page (`/entities`) right rail SHALL render the curation queue — a single
union view of entities needing operator attention. The queue MUST source from
`GET /api/relationship/entities/queue` and render three sections:

1. **Unidentified** — entities with `metadata->>'unidentified' = 'true'`. Actions
   per row: promote (give canonical_name), dismiss, merge.
2. **Duplicate candidate** — entity pairs detected via shared triples (e.g. same
   `has-email` value across two entities). Each row shows both entities, the reason
   ("shared email: alice@x" — deterministic string, no LLM), a similarity score.
   Action: merge (`POST /api/relationship/entities/{id}/merge`).
3. **Stale** — entities whose most-recent triple `last_seen` is older than 365 days.
   Action: refresh (re-add a triple) or archive.

The rail MUST:
- Be the ONLY surface where state colour (amber for unidentified/duplicate, dim for
  stale) appears on the Index page. State colour MUST NOT leak into Index rows
  (per Brief §0 success criterion).
- Collapse to a single serif-italic line ("Nothing waiting.") when all three sections
  are empty (per Brief §0 "right rail never shows a count of zero").
- Update optimistically on action (no full page reload).

Section ordering: Unidentified → Duplicate-candidate → Stale.

#### Scenario: Queue rail is the only source of state colour on Index
- **WHEN** the Index page renders with both unidentified entities AND populated rows
- **THEN** the queue rail MUST render amber accents on the unidentified entries
- **AND** every row in the main tabular list MUST render neutral hairline-on-neutral
- **AND** no amber/red fill MUST appear in the main list rows

#### Scenario: Empty queue collapses to serif gloss
- **WHEN** all three queue sections are empty
- **THEN** the rail MUST render exactly one serif italic line "Nothing waiting."
- **AND** no zero-count badges MUST be rendered

### Requirement: App-wide Cmd-K Finder

The dashboard SHALL expose an app-wide command palette opened via `⌘K` (macOS) /
`Ctrl-K` (other platforms) on any page. The Finder MUST:

1. Hit exactly one endpoint per keystroke: `GET /api/relationship/entities/search?q=<query>`.
   No other surface MUST call this endpoint; conversely the Finder MUST NOT call any other
   relationship endpoint to assemble results.
2. Resolve entities first, then other record kinds (per Phase 1 Open Question 14).
3. Show results in <300ms for local datasets (Brief §0 success criterion).
4. Search across: entity canonical_name, aliases, contact-fact values
   (`has-email | has-phone | has-handle | has-address`), and predicate labels.
5. Render keyboard-driven (arrow keys navigate; Enter opens detail; Esc closes).
6. Render kbd capsules in mono (KbMono primitive).

**Ranking is rule-based per `prompts/07-finder.md §7.5`:**
- Exact prefix match on canonical_name → score 100
- Substring match on canonical_name → score 80
- Exact match on alias → score 70
- Match on contact-fact value (email/phone/handle/address) → score 70
- Substring match on predicate label → score 30
- Tie-break by `lastSeen DESC`, then `tier ASC`.

**No embedding service, no reranker LLM, no model call at any stage of Finder
ranking in v1** — see Requirement: Finder is deterministic.

**Reconciliation against existing `/api/search`:** the top-level `/api/search` (RFC 0007:122)
returns a grouped `SearchResults` shape covering sessions/state/contacts. The entity Finder
endpoint is intentionally separate — scoping ranking logic to the relationship butler
preserves schema isolation. The top-level `/api/search` MAY later add an `entities` group
that fans out to this endpoint, but that is out of scope here.

#### Scenario: Finder returns ranked entities within 300ms
- **WHEN** a user presses ⌘K from any page and types "alice"
- **THEN** the Finder MUST call `GET /api/relationship/entities/search?q=alice` exactly once per keystroke
- **AND** results MUST render in <300ms for a local dataset of <10000 entities
- **AND** entities MUST appear before other result kinds

#### Scenario: Finder matches contact-fact values
- **WHEN** the query is "alice@example.com" and a triple
  `(entity=X, has-email, "alice@example.com")` exists
- **THEN** entity X MUST appear in the results with `matchedOn: "has-email"` populated

### Requirement: Dispatch design language token discipline

All six entity routes (`/entities`, `/entities/hop`, `/entities/columns`, `/entities/concentration`, `/entities/social-map`, `/entities/:entityId`) SHALL conform to the Dispatch design language with the following token rules (per Phase 1 Amendment 9 + Brief §1 binding tokens).

Note: the sixth route in this list replaces the legacy `/butlers/relationship/entities/:id` route name that appeared in the original version of this requirement. The route `/entities/:entityId` is the canonical entity detail route per the "Entity detail page" requirement.

1. **No new tokens** outside `frontend/src/index.css`. The redesign reuses
   `--bg`, `--bg-elev`, `--bg-deep`, `--fg`, `--mfg`, `--dim`, `--border`,
   `--border-soft`, `--border-strong`, `--red`, `--amber`, `--green`,
   `--category-1..8` (butler hues, EntityMark glyph only), `--tier-1..6`
   (Dunbar ramp, six layers: 5/15/50/150/500/1500), and `--severity-*` (per
   in-flight `token-system-spec-sync`).

   **Token namespace bridging (R3 gap note):** the Dispatch tokens (`--bg`,
   `--fg`, `--mfg`, `--dim`, `--border-soft`, `--border-strong`) are NOT
   present in shipped `frontend/src/index.css` (which today defines the
   shadcn ramp: `--foreground`, `--background`, `--border`,
   `--muted-foreground`, …) and they are NOT part of any in-flight token
   change. Phase 3 task 8.x (frontend foundation) MUST resolve this by
   EITHER (a) adding the Dispatch tokens to `frontend/src/index.css` mapped
   1:1 to the shadcn tokens they replace, OR (b) rewriting component classes
   to use the existing shadcn token names. The choice is deferred to
   implementation; this spec is shape-only. `--tier-1..6` already ships in
   `frontend/src/index.css` and is not part of this gap.
2. **No hex literals** anywhere in
   `frontend/src/components/relationship/*`,
   `frontend/src/pages/entities/*`, or
   `frontend/src/pages/butlers/relationship/*` EXCEPT in
   `frontend/src/lib/entity-model.ts` and the predicate-catalog UI.
3. **Fonts:** `Inter Tight` (UI), `Source Serif 4` (voice/gloss),
   `JetBrains Mono` (numerals, IDs, eyebrows, kbd). Font loading MUST be
   verified in `frontend/index.html` or equivalent before merge.

#### Scenario: Token discipline applies to canonical entity detail route

- **WHEN** code review compares any component rendered at `/entities/:entityId`
- **THEN** the component MUST NOT introduce new CSS custom properties outside `frontend/src/index.css`
- **AND** the component MUST NOT use hex color literals in `frontend/src/components/relationship/*` or `frontend/src/pages/entities/*`

### Requirement: Provenance contract — every fact carries its origin

Every entity-scoped endpoint MUST include provenance fields on every triple it returns.
The affected endpoints are: `/api/relationship/entities/{id}/contacts`,
`/api/relationship/entities/{id}/neighbours`,
`/api/relationship/entities/concentration`,
`/api/relationship/entities/queue`,
`/api/relationship/entities/{id}/{notes,interactions,gifts,loans,timeline}`,
and `/api/relationship/entities/search`. Provenance fields are defined in the
`relationship-facts` capability spec:

- `src` (TEXT, NOT NULL): butler that wrote the fact.
- `conf` (FLOAT 0..1, NOT NULL): confidence score, default 1.0 for owner-authored.
- `last_seen` (TIMESTAMP, NULLABLE): most recent observation of the triple.
- `weight` (INT, NULLABLE): aggregation weight for relational predicates.
- `verified` (BOOL, NOT NULL, default false): owner-confirmed flag.
- `primary` (BOOL, NULLABLE): primary-of-kind flag (for multi-valued contact predicates).

UI rendering MAY hide these fields (Editorial mode); the API MUST NOT silently drop
or omit them. Omission is a contract violation (per Brief §0 binding intent).

**Envelope reconciliation (R1 D2):** success responses from all new entity endpoints are
unwrapped per the relationship-domain convention (`rfcs/0007:88-91` exemption from the
default envelope). Error responses MUST be wrapped per `rfcs/0007:75-87` so the `code`
discriminator (`owner_required`, `entity_not_found`, etc.) is uniformly available to
clients regardless of which endpoint raised the error.

#### Scenario: Provenance fields are present on every triple response
- **WHEN** any of the listed entity-scoped endpoints returns at least one triple-derived
  row to the client
- **THEN** every row MUST include the keys `src`, `conf`, `last_seen`, `weight`,
  `verified`, and `primary` (nullable values are explicit, never omitted)
- **AND** the API response MUST NOT silently drop any provenance field
- **AND** UI code MAY choose not to render the fields in Editorial mode, but the API
  contract is unaffected by render choice

#### Scenario: Error envelope carries `code` discriminator
- **WHEN** an entity-scoped endpoint returns a non-2xx response (for example 403
  `owner_required` or 404 `entity_not_found`)
- **THEN** the error payload MUST be wrapped per `rfcs/0007:75-87` with a `code` field
- **AND** clients MUST be able to dispatch on `code` uniformly across all entity endpoints

### Requirement: Entity activity aggregator (cross-butler read surface)

The dashboard API SHALL expose `GET /api/relationship/entities/{id}/activity` as
a relationship-owned aggregator that returns a unified activity stream merging:

1. Relationship-domain rows from `relationship.entity_facts` (notes, interactions, life events,
   gifts, loans, dunbar_tier_override) — tagged `src: 'relationship'`.
2. Chronicler-domain rows tagged with `src: 'chronicler'` (kind: `episode`).

The chronicler rows MUST be fetched **via chronicler's MCP tools** —
`chronicler_list_episodes` per RFC 0014:255-258 (the brief named `chronicler_list_events`
but RFC 0014 lists only `list_episodes` / `get_episode` / `submit_correction`; Phase 2
chooses to use only currently-listed MCP tools rather than propose a new tool).

**Hard invariant (mirror `rfcs/0014:178` "Tests MUST exercise the no-LLM invariant for
every adapter"):** the relationship butler MUST NOT issue direct SQL into `chronicler.*`
schemas. A guardrail test in `roster/relationship/tests/test_chronicler_boundary.py` MUST
assert that the `activity` aggregator implementation does not import any
`chronicler.*` ORM model and does not contain the substring `FROM chronicler.` or
`JOIN chronicler.` in any SQL string.

The Timeline tab (defined above) and the activity aggregator coexist; the Timeline tab
renders the aggregator output as the merged stream.

#### Scenario: Activity aggregator merges via MCP only
- **WHEN** `GET /api/relationship/entities/<id>/activity` is called and chronicler
  episodes mention the entity
- **THEN** the aggregator MUST call `chronicler_list_episodes` via MCP with an entity filter
- **AND** chronicler rows MUST appear in the response with `src: 'chronicler'`
- **AND** the response MUST NOT include any row sourced via direct SQL from `chronicler.*`

#### Scenario: Boundary guardrail test passes
- **WHEN** the test suite runs `tests/test_chronicler_boundary.py::test_no_direct_chronicler_sql`
- **THEN** the test MUST scan the relationship router for `FROM chronicler.` / `JOIN chronicler.`
- **AND** the test MUST fail if any such string is found

### Requirement: Detail-page voice gloss source — canned strings only

Detail-page voice glosses SHALL be canned strings selected by `(tier, state, category)`,
with no LLM call per page load. The gloss types are: the serif italic one-liner under the
canonical name in Editorial mode, and the forget-confirm gloss in both modes. The source of
truth lives at `frontend/src/lib/entity-glosses.ts` as a strict enum keyed on
`(tier, state, category)`.

The enum MUST be exhaustive — every `(tier ∈ {0..5}, state ∈ {active, unidentified,
duplicate-candidate, stale, archived}, category ∈ {person, organization, location, product,
group, email, other})` combination MUST resolve to a non-empty string. Build-time validation
MUST fail if any combination is missing.

#### Scenario: No LLM call during Editorial render
- **WHEN** Editorial detail renders for any entity
- **THEN** zero requests MUST be issued to any LLM provider during the render
- **AND** the gloss text MUST be looked up from `entity-glosses.ts` via a pure function

### Requirement: Finder is deterministic — no LLM ranking

`GET /api/relationship/entities/search` MUST use rule-based ranking only (no embedding service,
no reranker LLM in v1). The rule set is defined in
`pr/overview/entity-redesign/prompts/07-finder.md §7.5` and also reproduced in
Requirement: App-wide Cmd-K Finder above. No model call MAY appear in the request handler path
of `/api/relationship/entities/search`.

#### Scenario: Finder handler issues zero LLM calls
- **WHEN** a Finder query is processed
- **THEN** the handler MUST NOT call any LLM provider
- **AND** the handler MUST NOT call any embedding service
- **AND** ranking MUST be computed purely from string-matching and `last_seen / tier` tie-breaks

