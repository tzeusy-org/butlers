## ADDED Requirements

### Requirement: Memory entity page links to relationship activity

The memory butler entity surfaces (`/entities` and `/entities/:id`, rendered by `EntitiesPage.tsx` and `EntityDetailPage.tsx`) SHALL surface a navigation link to `/butlers/relationship/entities/:id` for every entity whose `entity_type` is `'person'`. This closes the bidirectional bridge with the existing `EntityDetailView → /entities/:id` "View identity →" link mandated for the relationship-scoped entity page. The relationship butler writes facts only against `person` entities (every relationship-domain row in `predicate-taxonomy.md` Part 2 columns its target entity type as `person`); for `organization`, `place`, and `other` entity types the bridge MUST NOT direct users to a page that will render with all five tabs empty.

The pages MUST render the link as follows:

1. **On `/entities/:id` (entity detail page)** — the link MUST appear inside the main entity card, positioned after the Roles block and before the Source Provenance block. The link text MUST be "View relationship activity →" and MUST point to `/butlers/relationship/entities/:entityId`. Styling MUST mirror the `ContactDetailView` "View entity activity →" link (`text-primary text-sm font-medium hover:underline`). The link MUST be rendered only when `entity.entity_type === 'person'`; for other entity types the link MUST be omitted (matching the conditional-render convention used by the "Mark as confirmed" CTA on the same card).

2. **On `/entities` (entity listing page)** — every table row MUST include an icon button in the row-actions cluster, positioned immediately adjacent to the existing Edit action. The button MUST be enabled with tooltip "Open relationship activity" when the row's `entity_type === 'person'`, and rendered disabled with tooltip "Not a person entity" otherwise. Activating the enabled button MUST navigate to `/butlers/relationship/entities/:id` for that row's entity. This always-rendered, conditionally-disabled pattern MUST mirror the existing "View contact" UserIcon button on the same row.

#### Scenario: Entity detail page surfaces relationship activity link for a person

- **WHEN** a user navigates to `/entities/ent-456-uuid` and the entity has `entity_type = 'person'`
- **THEN** the main entity card MUST include a "View relationship activity →" link
- **AND** the link target MUST be `/butlers/relationship/entities/ent-456-uuid`
- **AND** the link MUST be positioned after the Roles block and before the Source Provenance block

#### Scenario: Entity detail page omits link for organization entity

- **WHEN** a user navigates to `/entities/org-789-uuid` and the entity has `entity_type = 'organization'`
- **THEN** the main entity card MUST NOT contain a "View relationship activity →" link
- **AND** the rest of the page MUST render normally

#### Scenario: Entity detail page omits link for place entity

- **WHEN** a user navigates to `/entities/loc-111-uuid` and the entity has `entity_type = 'place'`
- **THEN** the main entity card MUST NOT contain a "View relationship activity →" link
- **AND** the rest of the page MUST render normally

#### Scenario: Entity detail page omits link for other entity

- **WHEN** a user navigates to `/entities/oth-222-uuid` and the entity has `entity_type = 'other'`
- **THEN** the main entity card MUST NOT contain a "View relationship activity →" link
- **AND** the rest of the page MUST render normally

#### Scenario: Entities listing renders enabled action for person row

- **WHEN** a user navigates to `/entities` and a row in the table has `entity_type = 'person'`
- **THEN** the row's actions cluster MUST contain an enabled icon button
- **AND** the button's tooltip MUST read "Open relationship activity"
- **AND** clicking the button MUST navigate to `/butlers/relationship/entities/:id` for that row's entity

#### Scenario: Entities listing renders disabled action for non-person row

- **WHEN** a user navigates to `/entities` and a row in the table has `entity_type` other than `'person'` (e.g., `'organization'`, `'place'`, `'other'`)
- **THEN** the row's actions cluster MUST still contain the icon button (rendered, not omitted)
- **AND** the button MUST be disabled
- **AND** the button's tooltip MUST read "Not a person entity"
- **AND** clicking the disabled button MUST NOT navigate
