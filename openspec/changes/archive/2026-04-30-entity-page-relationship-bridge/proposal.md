## Why

The `relationship-tabs-to-entities` change shipped a relationship-scoped entity activity page at `/butlers/relationship/entities/:id` rendering five tabs (Notes, Interactions, Gifts, Loans, Timeline). It is reachable from `ContactDetailView` via a "View entity activity →" link and is mandated by `dashboard-relationship/spec.md` to expose a "View identity →" link back to the memory butler entity page at `/entities/:id`.

The reverse bridge is missing. A user who lands on `/entities/:id` (the memory butler identity page rendered by `EntityDetailPage.tsx`) — either by typing the URL, or by clicking a row on `/entities` (`EntitiesPage.tsx`) — has no link to the relationship activity. The only inbound paths to `/butlers/relationship/entities/:id` are: (a) the contact detail page, and (b) direct URL entry. A user who knows the entity but not a specific linked contact has no discoverable path. The sidebar's "Relationships" group exposes Contacts and Groups, not Entities.

This is a discoverability hole, not a doctrine gap. `dashboard-relationship/spec.md` mandates the contact→entity-activity link and the entity-activity→identity link; it does not mandate the identity→entity-activity link. We close the loop now so the bridge is bidirectional and the URL the user typed surfaces the data they expected.

## What Changes

- Add a prominent "View relationship activity →" link to the main entity card of `frontend/src/pages/EntityDetailPage.tsx`, deep-linking to `/butlers/relationship/entities/:entityId`. The link MUST render only when `entity.entity_type === 'person'` — every relationship-domain predicate in `predicate-taxonomy.md` Part 2 (`interaction_*`, `contact_note`, `gift`, `loan`, `life_event`, `dunbar_tier_override`) targets person entities. For non-person entity types (`organization`, `place`, `other` per the `chk_entities_entity_type` CHECK constraint at `entity-identity/spec.md:19-20`) the link MUST be omitted; the relationship-scoped page would render with all five tabs empty.
- Add a row-level icon button on `frontend/src/pages/EntitiesPage.tsx` ("Open relationship activity") that navigates to `/butlers/relationship/entities/:id`. The button MUST mirror the existing `UserIcon` convention (`EntitiesPage.tsx:811-832`): always rendered, enabled when `entity_type === 'person'`, disabled with a "Not a person entity" tooltip otherwise. Position adjacent to the existing Edit action.
- Update the `dashboard-relationship` spec to mandate the inbound bridge as a new requirement ("Memory entity page links to relationship activity"). The existing "View identity →" requirement stays unchanged.

## Capabilities

### New Capabilities

None. The relationship-scoped entity page already exists; this change only mandates and implements the inbound link.

### Modified Capabilities

- `dashboard-relationship`: ADDED requirement that the memory butler `EntityDetailPage` (`/entities/:id`) and the entity listing page (`/entities`) MUST surface a navigation link to `/butlers/relationship/entities/:id` for person entities. The contact-page link mandate and the entity-page outbound "View identity →" mandate are unchanged.

## Impact

**Frontend (`frontend/src/`)**
- Modified: `pages/EntityDetailPage.tsx` — add header link.
- Modified: `pages/EntitiesPage.tsx` — add row action.
- No new components, hooks, or routes.

**Backend / database / API contract**
- No changes. The relationship-scoped entity endpoints already exist and are tested.

**Specs**
- Modified: `openspec/specs/dashboard-relationship/spec.md` (delta authored under this change).
- Unchanged: `entity-identity`, `predicate-taxonomy.md`, `dashboard-domain-pages`, `dashboard-shell`.

**Out of scope (deferred)**
- Adding a sidebar entry for "Relationship Entities" under the Relationships nav group. Discoverability via Contacts and the new entity-page bridge is sufficient; a dedicated relationship-scoped entities listing would duplicate `/entities` filter capabilities for marginal benefit.
- Embedding the relationship tabs inline inside `EntityDetailPage`. Doctrine does not prohibit it, but the existing two-page split keeps memory butler (identity admin) and relationship butler (activity browsing) audiences cleanly separated.
- Re-anchoring the URL of the memory entity page (`/entities/:id` vs the `/butlers/entities/:id` reference at `entity-identity/spec.md:529`). This is a separate, pre-existing drift unrelated to the bridge.
