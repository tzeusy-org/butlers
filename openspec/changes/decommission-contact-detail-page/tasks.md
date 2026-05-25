## 0. Ratification

- [x] 0.1 Review delta spec and issue REVIEW.md (bu-m8gb6.1, 2026-05-24)
- [x] 0.2 Operator approved fixing F1–F4 findings inline (2026-05-25)
- [x] 0.3 All MODIFIED blocks added; strict validation passes; REVIEW.md updated

## 1. Spec And Route Contract

- [x] 1.1 Update `openspec/specs/dashboard-relationship/spec.md` via this change so
      `/entities/:entityId` is the canonical entity detail page everywhere.
      Preserve `/api/butlers/relationship/entities/*` as the API namespace; only
      frontend route prose changes.
- [ ] 1.2 Define `/butlers/relationship/entities/:entityId` as a legacy redirect to
      `/entities/:entityId`, not a normative product route.
- [ ] 1.3 Replace the contact-detail canonical-route requirement with a
      decommissioned compatibility route requirement for `/contacts/:contactId`.
- [x] 1.4 Run OpenSpec validation for this change.

## 2. Contact Capability Parity Inventory

- [ ] 2.1 Inventory every visible capability in `ContactDetailPage` and
      `ContactDetailView`: read fields, mutations, secured reveal, delete/archive,
      link/unlink, labels, relationships, dates, quick facts, and navigation.
- [ ] 2.2 Map each capability to an entity-keyed endpoint, an existing contact-keyed
      compatibility endpoint, or an explicit removal/defer decision.
- [ ] 2.3 Identify dependencies on the contacts-to-triples migration epic
      (`bu-uhjxr`) and mark final redirect/removal tasks blocked where needed.

## 3. Entity Detail Contact Card

- [ ] 3.1 Add the post-redesign contact-channel card to `EntityDetailPage`.
- [ ] 3.2 Render linked contacts and contact methods from entity-keyed data where
      available.
- [ ] 3.3 Preserve secured reveal/hide affordances without exposing secret values
      in logs, tests, or snapshots.
- [ ] 3.4 Preserve or replace current contact lifecycle actions according to the
      parity inventory.
- [ ] 3.5 Add focused frontend tests for populated, sparse, multi-contact, and
      secured-value states.

## 4. Route And Link Decommission

- [ ] 4.1 Change `/contacts/:contactId` from `ContactDetailPage` rendering to a
      contact-to-entity redirect/recovery route after entity-card parity lands.
- [ ] 4.2 Update internal links that point to `/contacts/:contactId` as primary
      navigation to target `/entities/:entityId` when the entity id is available.
- [ ] 4.3 Keep `/contacts` index redirecting to `/entities?has=contact`.
- [ ] 4.4 Keep `/butlers/relationship/entities/:entityId` redirecting to
      `/entities/:entityId`; update tests to assert that this is legacy behavior.
- [ ] 4.5 Remove or retire `ContactDetailPage` and `ContactDetailView` once no route
      renders them and all tests have moved.

## 5. API Compatibility Cleanup

- [ ] 5.1 Add or confirm a minimal contact-to-entity resolver for redirect use.
- [ ] 5.2 Avoid adding new contact-keyed detail payloads; prefer entity-keyed
      endpoint extensions for entity-card data.
- [ ] 5.3 Mark any remaining contact-keyed route/hook/type as compatibility-only.
- [ ] 5.4 After migration gates close, remove obsolete contact-detail hooks/types and
      API client functions that no longer have a caller.

## 6. Reconciliation

- [ ] 6.1 Run targeted frontend tests for router redirects, entity detail, entity
      index, contact table/navigation, and secured reveal.
- [ ] 6.2 Run targeted backend/API tests for contact-to-entity resolver behavior and
      entity-card data endpoints.
- [ ] 6.3 Run `rg` audits for stale route strings:
      `/butlers/relationship/entities/`, `/contacts/:contactId`,
      `ContactDetailPage`, `ContactDetailView`, and "View relationship activity".
- [ ] 6.4 Produce a final reconciliation note mapping each decommission requirement
      to code/tests and listing any remaining compatibility shims.
