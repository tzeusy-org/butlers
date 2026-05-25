## Scope Note

> **FRONTEND-ONLY CHANGE.** This change modifies frontend route prose, navigation
> contracts, and component-level requirements only. The
> `/api/butlers/relationship/entities/*` API namespace is NOT affected by this
> change; all existing backend endpoints under that namespace remain in effect and
> MUST NOT be altered as part of this change.

---

## MODIFIED Requirements

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

### Requirement: Owner identity and credential management via contact detail page

The entity detail contact-channel card at `/entities/:entityId` SHALL be the
primary mechanism for configuring owner identity fields and credentials. The
"Add contact info" form on the contact-channel card MUST support all identity
and credential types, including secured types (`email_password`,
`telegram_api_id`, `telegram_api_hash`).

When a secured type is selected, the form MUST:
- Use a password input field to mask the value during entry
- Automatically set `secured = true` on the created `contact_info` entry
- Hide the "Primary" checkbox (not applicable to credential entries)

The form MUST display human-friendly labels for all types (e.g., "Email
password", "Telegram API ID", "Telegram API hash", "Telegram chat ID").

#### Scenario: Add a secured credential from the entity detail contact-channel card

- **WHEN** a user opens the owner entity's detail page at `/entities/:entityId`
  and clicks "Add contact info" in the contact-channel card
- **AND** selects "Email password" from the type dropdown and enters a value
- **THEN** the input field MUST be a password field (masked)
- **AND** the created `contact_info` entry MUST have `secured = true`
- **AND** the entry MUST appear in the contact info list with a masked value
  and a "Reveal" button

#### Scenario: Add a non-secured identity field from the entity detail contact-channel card

- **WHEN** a user adds a `telegram` or `email` entry via the contact-channel
  card form on the entity detail page
- **THEN** the input field MUST be a text field (not masked)
- **AND** the created `contact_info` entry MUST have `secured = false`

---

### Requirement: Owner identity setup banner

The dashboard SHALL display a persistent banner on the entity index page
(`/entities?has=contact`) when the owner contact is missing key identity
fields (name, email, telegram handle, or telegram chat ID). The banner
provides a one-time onboarding dialog as a convenience; the entity detail
contact-channel card at `/entities/:entityId` is the canonical location for
ongoing identity and credential management.

#### Scenario: Banner shown when owner has missing identity fields

- **WHEN** a user navigates to `/entities?has=contact` and the owner contact
  is missing any of: name, email, telegram handle, or telegram chat ID
- **THEN** a banner MUST be displayed indicating which fields are missing
- **AND** a "Set Up Identity" button MUST open a dialog for filling in missing
  fields

#### Scenario: Banner hidden when all identity fields are configured

- **WHEN** the owner contact has name, email, telegram handle, and telegram
  chat ID configured
- **THEN** the setup banner MUST NOT be displayed

#### Scenario: Banner dialog includes credentials section

- **WHEN** the owner setup dialog is opened
- **THEN** a collapsible "Credentials" section MUST be available for
  optionally setting email password, Telegram API ID, and Telegram API hash
- **AND** these credential fields MUST create secured `contact_info` entries

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
