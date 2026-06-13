## 1. Spec Authoring

- [x] 1.1 Scaffold `openspec/changes/redesign-butler-detail-no-hero/`.
- [x] 1.2 Write `proposal.md` citing Gate A bead bu-rx6c2 and chosen option A2.
- [x] 1.3 Write `design.md` documenting why A2 does not require a
      `detail-page-archetype` delta.
- [x] 1.4 Write delta spec `specs/dashboard-butler-management/spec.md` modifying
      `Requirement: Butler detail page tab body vocabulary`.
- [x] 1.5 Preserve unrelated clauses in the modified requirement: primary slot,
      no drawer slot, tab-expansion rule, and base-tabs scenario.
- [x] 1.6 Run `openspec validate redesign-butler-detail-no-hero --strict`.

## 2. Epic 01 Implementation Checklist: Page Detail Shell Primitives

- [ ] 2.1 Verify `<Page archetype="detail">` renders title, breadcrumbs,
      status/action controls, and primary body content without invented wrapper
      components.
- [ ] 2.2 Confirm Butler detail renders no standalone `<Breadcrumbs>` component;
      breadcrumbs are supplied through the Page shell.
- [ ] 2.3 Confirm `<ChatPanel />` remains in the Page `actions` slot and is not
      duplicated elsewhere in Butler detail.
- [ ] 2.4 For Gate A A2, render the Butler detail action cluster through the Page
      `actions` slot: ChatPanel, status pill, force-run action, pause action, and
      future prompt action affordance.
- [ ] 2.5 Add or update RTL/snapshot coverage for the chosen A2 shape and confirm
      sibling detail-page consumers are not regressed.
- [ ] 2.6 Add Storybook/Ladle shell states for Butler detail covering loading,
      error, and status/action variants.

## 3. Epic 04 Implementation Checklist: Overview as Identity Surface

- [ ] 3.1 Keep the Overview tab identity card as the butler identity surface.
- [ ] 3.2 Preserve existing identity-card clauses: name/status, description,
      port, eligibility state, quarantine reason, and 24h eligibility timeline.
- [ ] 3.3 Add process facts only via the sibling process-facts spec: container
      name, port, uptime, and config path; do not surface `pid`.
- [ ] 3.4 Preserve module-health rendering using the existing module-health data
      path and empty state.
- [ ] 3.5 Preserve heartbeat display from existing system heartbeat data.
- [ ] 3.6 Preserve cost telemetry and add recent-sessions card using existing
      data hooks.
- [ ] 3.7 Verify there is no Tier 2 Hero block above the tabs after the Overview
      redesign lands.

## 4. Reconciliation

- [ ] 4.1 Reconcile Epic 01 implementation against this A2 no-hero contract.
- [ ] 4.2 Reconcile Epic 04 implementation against the Overview identity-surface
      clauses and this no-hero contract.
- [ ] 4.3 Run `/opsx:sync` or the project-approved OpenSpec sync flow after owner
      review when this delta is ready to merge into canonical specs.
