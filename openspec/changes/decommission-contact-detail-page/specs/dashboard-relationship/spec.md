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
