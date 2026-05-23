## Context

The canonical `dashboard-relationship` spec currently contains mixed route
language:

- `/entities` is already specified as the top-level entity index.
- `/entities/:entityId` is already registered in `frontend/src/router-config.tsx`
  and renders `EntityDetailPage`.
- `/butlers/relationship/entities/:entityId` is still named in several
  requirements, but the router redirects it to `/entities/:entityId`.
- `/contacts` already redirects to `/entities?has=contact`.
- `/contacts/:contactId` still renders a standalone detail page.

The backend fact model is entity-first. Notes, interactions, gifts, loans, life
events, provenance, neighbours, concentration, queue, and search are already
entity-keyed. Keeping contact detail as an independent destination now mostly
preserves old CRM topology rather than the post-redesign mental model.

## Goals

- Make `/entities/:entityId` the single canonical entity detail page in spec,
  router, links, tests, and prose.
- Move the useful contact detail content into a card on `EntityDetailPage`.
- Preserve deep links to existing contact URLs through redirects, not broken
  routes.
- Avoid adding new contact-keyed feature surfaces while the contacts-to-triples
  migration is still active.
- Keep the visual language consistent with the entity redesign: no old card
  stack, no tab resurrection, no raw connector/source IDs in user-facing prose.

## Non-Goals

- Dropping `public.contacts` or `public.contact_info` directly in this change.
  That remains owned by the contacts-to-triples migration epic.
- Reworking the entity fact migration sequencing.
- Reintroducing separate Notes/Interactions/Gifts/Loans tabs.
- Removing contact-list style workflows from `/entities?has=contact`; that route
  remains the contact-oriented index.

## Decisions

### D1. Canonical detail route is `/entities/:entityId`

`/entities/:entityId` is the canonical entity detail route. All internal links
must target it. `/butlers/relationship/entities/:entityId` remains only as a
legacy redirect so old links keep working.

Rationale: the frontend already behaves this way, the entity index is
top-level, and the user's intent is explicit. Keeping the relationship-prefixed
route normative recreates a second product surface.

### D2. Contact detail becomes a compatibility redirect

After parity lands, `/contacts/:contactId` resolves the contact's linked
`entity_id` and redirects to `/entities/:entityId`. If the contact is missing
or has no entity link, the redirect route renders a narrow recovery state with
an actionable path back to `/entities?has=contact`.

Rationale: bookmarks should continue to work, but users should land where the
relationship history and identity state actually live.

### D3. Entity page owns a contact-channel card

The entity detail page gains one contact-channel card under the existing detail
layout. It lists all linked contacts for that entity and exposes contact methods
and metadata that are still contact-scoped during the transition.

The card must cover:

- contact info values grouped by type, including secured reveal/hide;
- preferred channel and linked contact summaries;
- important dates and quick facts until their triple-backed replacements are
  fully cut over;
- contact-to-contact relationships while edge-fact migration remains out of
  scope;
- labels and lifecycle actions where the backend still supports them.

Rationale: this preserves current functionality while removing the page split.

### D4. API work should prefer entity-keyed reads

If the entity page needs data currently only available via
`GET /api/relationship/contacts/{id}`, the preferred implementation is an
entity-keyed endpoint or an extension to existing entity endpoints, not deeper
frontend fanout to contact detail endpoints. Contact-keyed reads may be used
temporarily for the redirect and migration bridge, but must be labeled
compatibility-only.

Rationale: the active contacts-to-triples migration is already moving readers
away from contact tables. This feature should not create new contact-table
commitment.

### D5. Decommission is gated but not blocked wholesale

Route-contract cleanup, spec sync, and link-target cleanup can proceed before
the P0 migration finishes. Full redirecting of `/contacts/:contactId` depends
on entity-card parity and on the contact-info/secured-row migration state being
safe enough that no user capability disappears.

## Risks

- **Capability regression:** contact detail currently has mutation affordances
  that may not all exist in entity-keyed APIs. Mitigation: parity inventory bead
  first; implementation bead cannot close until each existing affordance is
  either moved, intentionally removed in spec, or deferred with an explicit bead.
- **Migration conflict:** the P0 contacts-to-triples migration is active.
  Mitigation: do not drop contact APIs/tables here; depend on migration cut-over
  for final decommission.
- **Redirect ambiguity:** a contact without `entity_id` cannot resolve to an
  entity. Mitigation: recovery state plus backend/QA bead to quantify and fix
  orphan contacts before redirect rollout.
- **Spec churn:** existing canonical specs mention both routes in many places.
  Mitigation: one spec-sync bead dedicated to replacing entity-detail route
  language and preserving API namespace language separately.
