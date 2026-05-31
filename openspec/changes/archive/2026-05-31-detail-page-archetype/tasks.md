## 1. Spec Authoring

- [x] 1.1 Create `openspec/changes/detail-page-archetype/` via `openspec new change`.
- [x] 1.2 Write `proposal.md` with Why / What Changes / Capabilities / Impact.
- [x] 1.3 Write `design.md` with Context / Goals / Decisions / Risks.
- [x] 1.4 Write delta spec: `specs/dashboard-domain-pages.md` (Fact, Rule, Episode
      archetype conformance + title-quality fix).
- [x] 1.5 Write delta spec: `specs/dashboard-relationship.md` (Contact archetype
      conformance + route duplication resolution).
- [x] 1.6 Write delta spec: `specs/dashboard-butler-management.md` (Butler outer
      chrome adoption of `<Page archetype="detail">`; tabs as primary slot).
- [x] 1.7 Write `detail-page-archetype` new capability spec in `specs/`.
- [x] 1.8 Run `openspec validate detail-page-archetype` and confirm pass.

## 2. Route Duplication Resolution (Contact)

- [ ] 2.1 Confirm `/contacts/:contactId` is the only active frontend route for
      `ContactDetailPage` (verified: `frontend/src/router.tsx` line 84).
- [ ] 2.2 Confirm `/butlers/relationship/contacts/:id` is NOT registered in the
      router (verified: absent from `router.tsx`; only the entity redirect at line 105
      covers the `/butlers/relationship/` prefix).
- [ ] 2.3 Add a permanent redirect entry for `/butlers/relationship/contacts/:id`
      → `/contacts/:id` in `frontend/src/router.tsx`, following the
      `RelationshipEntityRedirect` pattern already present at lines 57–64.
      File a follow-up bead if the PR author decides to fold this into the
      ContactDetailPage migration PR.

## 3. Implementation Bead Tracking

- [ ] 3.1 Fact / Rule detail pages — `<Page archetype="detail">` adoption (bu-rqfil.x,
      PR #1392). Verify merged.
- [ ] 3.2 Contact detail page — `<Page archetype="detail">` adoption (bu-rqfil.x,
      PR #1393). Verify merged. Confirm route redirect from step 2.3 is included.
- [ ] 3.3 Episode detail page — title lifted to record-identity. Confirm the
      spec change from task 1.4 is reflected in the implementation.
- [x] 3.4 Butler detail page outer chrome — DESCOPED. Superseded by the
      status-board lineage (`extend-butler-detail-status-board-chrome`, archived
      2026-05-13; shipped PR #1614). `/butlers/:name` now uses
      `<Page archetype="status-board">`, not `detail`. The `dashboard-butler-management`
      delta has been removed from this change. Remaining canonical-spec reconciliation
      is tracked in a separate follow-up bead.
- [ ] 3.5 ConnectorDetailPage — implementation migrated (PR #1397, out of this
      spec scope). File sibling bead to delta `connector-base-spec` when
      ConnectorDetailPage spec home is decided.

## 4. Spec Sync and Reconciliation

- [ ] 4.1 Run `openspec sync detail-page-archetype` after all delta specs are
      reviewed and owner-confirmed to merge delta content into the canonical spec tree.
- [ ] 4.2 Verify that `openspec/specs/dashboard-domain-pages/spec.md` reflects the
      updated Fact, Rule, and Episode requirements (archetype conformance, record-identity
      titles).
- [ ] 4.3 Verify that `openspec/specs/dashboard-relationship/spec.md` reflects the
      canonical `/contacts/:id` route and removes the stale `/butlers/relationship/contacts/:id`
      requirement.
- [x] 4.4 N/A — `dashboard-butler-management` delta removed from this change (see
      task 3.4). The canonical spec's butler-detail requirement is reconciled to
      `<Page archetype="status-board">` under a separate follow-up bead, not this change.
- [ ] 4.5 Verify that `openspec/specs/detail-page-archetype/spec.md` is created
      and matches the new capability spec authored in task 1.7.

## 5. Open Items

- [ ] 5.1 ConnectorDetailPage spec home — decide whether it lives in `connector-base-spec`,
      a new `dashboard-connector-detail` spec, or an existing spec. File a follow-up bead
      once decided.
- [ ] 5.2 `permanenceBadge` / `maturityBadge` deduplication — `detail-page-audit.md`
      §4.7 names `components/memory/badges.tsx` as the extraction target. This is not
      in scope for this change; file a follow-up bead when Fact/Rule migration PRs close.
- [ ] 5.3 `PracticalDrawer` extraction — audit §6.6 recommends extracting
      `PracticalDrawer` to `components/ui/practical-drawer.tsx`. Not in scope here;
      file a follow-up bead when EntityDetailPage is updated.
