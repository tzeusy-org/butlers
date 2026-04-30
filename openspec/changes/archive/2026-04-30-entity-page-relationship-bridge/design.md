## Context

`relationship-tabs-to-entities` (now closed and merged across `bu-x7fdu.1`–`.8`) split entity activity onto a relationship-butler-owned page at `/butlers/relationship/entities/:id`. It is reachable today from one inbound path (`ContactDetailView` "View entity activity →" link) plus direct URL entry. The memory butler's entity surface at `/entities/:id` (rendered by `EntityDetailPage.tsx`, ~1615 lines) is identity-focused: header card, info/credentials, facts table, telegram session setup, linked contact section. The two pages exist by design; doctrine does not collapse them.

A user who navigated to `/butlers-dev/entities/<id>` after the cutover saw the identity page (no tabs) and asked where the activity went. The discoverability hole is concrete and small: add an inbound link.

## Goals / Non-Goals

**Goals**
- Close the bidirectional bridge between the two entity pages so the URL a user types deterministically reaches the data they expect.
- Make the bridge discoverable from both the entity listing (`/entities`) and the entity detail page (`/entities/:id`).
- Mirror the existing `ContactDetailView → /butlers/relationship/entities/:id` link styling so the pattern is consistent.

**Non-Goals**
- Embed relationship tabs inside `EntityDetailPage`. Out of scope; doctrine permits but does not require it, and the audiences differ.
- Add a "Relationship Entities" sidebar entry. The Contacts page plus this bridge cover the discovery surface.
- Repoint `/entities/:id` to `/butlers/entities/:id` per `entity-identity/spec.md:529`. Pre-existing URL drift; leave for a separate cleanup.

## Decisions

### D1. Predicate is `entity_type === 'person'` only

The bridge link MUST be active only when `entity_type === 'person'`. **Why:** every relationship-domain predicate in `predicate-taxonomy.md` Part 2 — `interaction_*`, `contact_note`, `gift`, `loan`, `life_event`, `dunbar_tier_override` — is anchored to a `person` entity (the predicate-taxonomy table at lines 78–186 and 231–236 explicitly columns the target entity type as `person` for every relationship row). The CHECK constraint at `entity-identity/spec.md:19-20` allows `entity_type IN ('person', 'organization', 'place', 'other')`; the relationship butler writes facts only for `person`. Organizations/places/others would render with all five tabs empty. **Alternative considered:** include `organization` (mirror image of how a contact relationship to a company might feel natural). Rejected — the predicate taxonomy gives no path for an interaction or note to land on an organization entity, and pretending otherwise would set wrong user expectations. **Alternative considered:** always show; rely on the empty-state rendering of `EntityDetailView`. Rejected — empty states are for the absence of *expected* data, not for "this surface doesn't apply to this entity type".

### D2. Link target uses `entity.id` directly

The link target is `/butlers/relationship/entities/${entity.id}`. **Why:** the relationship-scoped page accepts any entity UUID and 404s if the entity is unknown to `public.entities`. We pass the UUID we already have on the page; no contact resolution needed. **Alternative:** route through `linked_contact_id` first. Rejected — the relationship-scoped page is entity-keyed, not contact-keyed; the previous architecture moved away from contact-keyed surfacing.

### D3. Header placement on `EntityDetailPage`

The link is rendered inside the main entity card's `CardContent` block (`EntityDetailPage.tsx` lines 1254–1466). Specifically: position immediately below the **Roles** block (which closes at line 1380) and above the **Source Provenance** block (line 1382), styled as `text-primary text-sm font-medium hover:underline` with the `→` arrow suffix. The link is rendered conditionally on `entity.entity_type === 'person'`; it is omitted for any other type (mirroring the conditional render of "Mark as confirmed" at line 1462, which only appears for unidentified entities). **Why:** the Aliases→Roles row sequence already groups identity badges; the link sits naturally as a CTA after the badges and before provenance metadata. The styling mirrors `ContactDetailView`'s "View entity activity →" link verbatim so the visual language is uniform across the contact↔entity↔activity triangle. **Alternative:** floating CTA button at top-right of the card header. Rejected — doesn't match the existing pattern; introduces new component noise.

### D4. EntitiesPage row action — always rendered, conditionally enabled

Add an icon button (lucide `Activity` is preferred; substitute if already imported and clearer) in the row actions cluster (`EntitiesPage.tsx:808–920`), positioned immediately adjacent to the existing `EditIcon` button (currently at line 838). The button MUST follow the **always-rendered, conditionally-disabled** pattern used by the existing `UserIcon` "View contact" button at lines 811–832 of the same file: enabled when `entity.entity_type === 'person'` (tooltip "Open relationship activity"); rendered disabled otherwise (tooltip "Not a person entity"). **Why:** the row actions cluster has a deliberate visual rhythm — UserIcon, EditIcon, GitMergeIcon, ArchiveIcon, TrashIcon all render in every row whether enabled or not. Conditionally hiding the new icon would break alignment between rows and create a flickery scan as the user moves down the table. The detail page does not have this constraint (its CTAs already render conditionally per the "Mark as confirmed" precedent at line 1462), so D3 omits and D4 disables — same UX intent, two visual conventions. **Alternative:** conditional render. Rejected for the listing page; accepted for the detail page (D3).

### D5. No backend changes

No new endpoints, no schema changes, no migrations. The link target and conditional logic are pure frontend concerns. The existing `useEntity()` hook already returns `entity_type`; the existing `EntitySummary` row already includes `entity_type`. **Why:** keeps the change blast radius minimal; aligns with /cruft-cleanup principles (no compat shims, no speculative scaffolding).

### D6. Spec home: `dashboard-relationship`

The new requirement lives in `dashboard-relationship/spec.md`, not in `entity-identity` or `dashboard-domain-pages`. **Why:** the existing inbound (`ContactDetailView → /butlers/relationship/entities/:id`) and outbound (`EntityDetailView → /entities/:id`) link mandates already live in `dashboard-relationship`. Co-locating the third leg of the triangle in the same spec keeps the bridge contract auditable in one place. **Alternative:** add to `entity-identity` (which mentions the entity detail page abstractly). Rejected — `entity-identity` describes data-model invariants, not navigation contracts.

### D7. No `useButlers()` gate on the link

The link is rendered solely on the `entity_type === 'person'` predicate; it is not additionally gated on whether the relationship butler is present in the active roster (`useButlers()`). **Why:** the existing `ContactDetailView` "View entity activity →" link follows the same pattern (no butler-presence check at `ContactDetailView.tsx:908`), and in practice ContactDetailView is only reachable via API data served by the relationship butler — its presence is implied. `EntityDetailPage` does not have that implicit gate (its data comes from memory butler APIs), so a deployment without the relationship butler would render a dead link. We accept this hypothetical dead-link risk because: (a) the relationship butler is present in the user's actual deployment, and (b) per /cruft-cleanup we don't add features for hypothetical scenarios. If a multi-deployment configuration arises, gate the link on `useButlers()` data and amend this decision; do not pre-build for it.

## Risks / Trade-offs

- **Risk:** A future entity type added to the entity-type taxonomy might warrant relationship facts (e.g., a hypothetical `pet` type). The hard-coded `entity_type IN ('person','organization')` check would need to be updated. **Mitigation:** the predicate is documented in this design and in the spec scenarios; the check is in two places (detail page + listing page); both are mechanical to amend. Not worth abstracting until a third entity type is added.
- **Risk:** The conditional rendering creates an asymmetry — users with non-person entities don't see the link, even though the page exists at the URL. **Mitigation:** acceptable; the page renders empty for those entities and the rendering rule is documented.
- **Trade-off:** No sidebar entry for "Relationship Entities". Users browsing solely via sidebar must go via Contacts. Acceptable for now; revisit if the user reports the gap.

## Migration Plan

None. Frontend-only change; no data, no schema, no API. Ship behind no flag — the link's mere presence is purely additive UX.

## Open Questions

None. The spec scenarios cover the four user paths (person, organization, other entity types, missing entity).
