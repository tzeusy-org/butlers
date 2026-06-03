## Why

The entity redesign has made the split between `/entities/:entityId` and
`/contacts/:contactId` increasingly artificial. The entity detail page is now
the user's canonical surface for relationship activity, provenance, linked
contacts, credential/entity info, and practical relationship operations. The
remaining contact detail page is a channel/admin island: useful data, but not a
separate destination the user should have to remember.

There is also a route-contract wrinkle in the current spec. Several shipped
requirements still name `/butlers/relationship/entities/:id` as the relationship
activity detail page, while the frontend already redirects that route to
`/entities/:id` and the user-facing design intent is that `/entities/:id` is the
correct canonical route. Keeping both as normative routes creates duplicate
navigation expectations and keeps reviving contact-vs-entity confusion.

This change decommissions the standalone contact detail page by moving its
remaining contact-channel capability into the entity detail page as a card, then
turns `/contacts/:contactId` into a compatibility redirect to the linked entity.

## What Changes

- **Canonical route correction:** `/entities/:entityId` is the canonical entity
  detail route. `/butlers/relationship/entities/:entityId` remains a legacy
  client-side redirect to `/entities/:entityId`; it MUST NOT be described as the
  canonical surface.
- **Contact detail decommission:** `/contacts/:contactId` no longer renders
  `ContactDetailPage` after the entity page reaches parity. It resolves the
  contact's `entity_id` and redirects to `/entities/:entityId`.
- **Entity detail contact card:** `EntityDetailPage` gains a post-redesign
  contact-channel card that contains the current contact-page-only concerns:
  contact methods, secured reveal affordances, preferred channel, linked contact
  rows, important dates, quick facts, contact-to-contact relationships, labels,
  and unlink/archive/delete actions where still valid.
- **Index behavior preserved:** `/contacts` continues to redirect to
  `/entities?has=contact`.
- **API decommission path:** contact-detail API/read hooks remain available
  only until the entity-detail card consumes entity-keyed relationship APIs.
  Any contact-keyed endpoint retained during the transition must be explicitly
  marked as compatibility-only and removed or demoted once the contact-card
  parity bead closes.

## Capabilities

### Modified Capabilities

- `dashboard-relationship`: canonical entity route, entity detail composition,
  contact detail route behavior, and entity/contact navigation contracts.

## Impact

- **Frontend:** update route config, tests, entity detail layout, contact table
  row navigation, approval links, command palette/entity finder targets, and any
  breadcrumb or CTA still pointing to `/contacts/:contactId` for primary
  navigation.
- **Backend/API:** add or confirm a lightweight contact-to-entity resolver for
  redirects; move any missing contact-detail card data to entity-keyed
  relationship endpoints rather than extending contact-keyed detail responses.
- **Specs:** sync `dashboard-relationship` so every entity detail reference says
  `/entities/:entityId`, not `/butlers/relationship/entities/:entityId`.
- **Beads:** create a new execution epic with child beads for spec sync,
  entity-card parity, redirect/router cleanup, API compatibility cleanup,
  tests, and final reconciliation.
- **Dependencies:** full contact-detail decommission is gated on the
  contacts-to-triples migration epic (`bu-uhjxr`) reaching read-path cut-over
  and secured/contact-info migration readiness. Route-contract spec correction
  and frontend target cleanup can proceed earlier.
