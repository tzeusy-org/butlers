## ADDED Requirements

### Requirement: Contact routes are compatibility aliases

The routes `/contacts` and `/contacts/:contactId` SHALL remain compatibility aliases, not
canonical pages. Both MUST replace-navigate to `/entities/index?has=contact`. Because the retired
`public.contacts` identity and its per-contact resolver no longer exist, the detail alias MUST NOT
invent an entity ID, render a not-found claim about the legacy ID, or revive the retired contact
detail page. Owners reach canonical `/entities/:entityId` details from the entity index.

#### Scenario: Contact index URL redirects to the filtered entity index

- **WHEN** a user navigates to `/contacts`
- **THEN** the client MUST replace-navigate to `/entities/index?has=contact`

#### Scenario: Legacy contact detail URL falls back to the filtered entity index

- **WHEN** a user navigates to `/contacts/abc-123-uuid`
- **THEN** the client MUST replace-navigate to `/entities/index?has=contact`
- **AND** it MUST NOT claim that `abc-123-uuid` resolved to an entity

### Requirement: Pending identities queue on the entity index

The entity index page (`/entities/index?has=contact`) SHALL display a "Pending
Identities" section listing all contacts with
`metadata.needs_disambiguation = true`. This section MUST appear above the
main entity table when pending contacts exist.

#### Scenario: Pending identities displayed

- **WHEN** a user navigates to `/entities/index?has=contact` and 2 temporary
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

## MODIFIED Requirements

### Requirement: Entity index page (`/entities/index`)

The frontend SHALL render an entity index at `/entities/index` (NOT
`/butlers/relationship/entities`). The `/entities` landing itself is the Plex (see
Requirement: Entity Plex view); the Index is the tabular curation surface one tab away,
home for list-filter-and-curate workflows over the same population. The Index MUST
consist of:

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
4. **SubpageTabs** — horizontal nav strip linking Plex / Index / Concentration.
   Active tab is `/entities/index`.
5. **Cmd-K affordance** — visible mono kbd capsule (`⌘K`) in the header.

The Index page MUST render inside `<Page archetype="overview">` (per the in-flight
`page-primitive-spec-sync` change) with breadcrumb `Entities`.

#### Scenario: Index renders with neutral rows and queue rail
- **WHEN** a user navigates to `/entities/index` with at least one entity in `public.entities`
- **THEN** the rows MUST render neutral hairline-on-neutral (no amber/red fills)
- **AND** the curation queue rail MUST be present (collapsed to single serif italic line if empty)
- **AND** the SubpageTabs strip MUST mark Index as active

#### Scenario: has=contact filter chip lists every entity with a contact triple
- **WHEN** `?has=contact` query is applied
- **THEN** the result set MUST be exactly the entities with at least one triple in
  `relationship.entity_facts` whose predicate matches `has-email | has-phone | has-handle |
  has-address | has-birthday | has-website`

#### Scenario: `/contacts` index redirects to `/entities/index?has=contact`
- **WHEN** a request reaches the contacts INDEX path `/contacts` (no `:contactId` param)
- **THEN** the client MUST replace-navigate to `/entities/index?has=contact`
- **AND** no functional regression MUST occur for any prior `/contacts` index workflow
- **AND** the contact-detail compatibility path `/contacts/:contactId` MUST also
  replace-navigate to `/entities/index?has=contact`, as defined by Requirement: Contact
  routes are compatibility aliases
- **AND** canonical single-record navigation MUST start from the entity index and target
  `/entities/:entityId`

## REMOVED Requirements

### Requirement: Contact detail page canonical route is /contacts/:contactId

**Reason**: The retired contact identity can no longer be resolved at this compatibility boundary.

**Migration**: Both `/contacts` and `/contacts/:contactId` replace-navigate to `/entities/index?has=contact`; canonical details use `/entities/:entityId`.

### Requirement: Contact detail page conforms to the detail-page archetype

**Reason**: The standalone contact detail page was decommissioned, so it no longer owns a detail-page shell.

**Migration**: The entity detail page owns the canonical detail archetype and contact-channel composition.

### Requirement: Pending identities queue on contacts page

**Reason**: The standalone contacts page and pre-Plex entity route are retired.

**Migration**: Preserve the queue at `/entities/index?has=contact`.
