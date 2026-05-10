## 1. Spec Authoring

- [x] 1.1 Create `openspec/changes/add-bespoke-butler-tab-capability/` with
      proposal.md, tasks.md, and spec delta.
- [x] 1.2 Write `proposal.md` citing Gate B bead bu-41p8z, the settled parent
      change `redesign-detail-page-tab-vocabulary`, and the existing per-butler
      tab pattern in ButlerDetailPage.tsx.
- [x] 1.3 Write delta spec `specs/dashboard-butler-management/spec.md` adding
      the nine bespoke-tab rules enumerated in bu-iuol4.2 and scenarios.
- [x] 1.4 Explicitly prohibit switchboard from carrying a resident bespoke tab
      in the spec.
- [x] 1.5 Add bespoke-tab-label scenario to the resident-tab list.
- [x] 1.6 Run `openspec validate add-bespoke-butler-tab-capability --strict`.

## 2. Per-Butler Implementation Checklists (owned by sub-beads under bu-iuol4)

Each butler with a bespoke tab requires its own implementation bead. This
checklist tracks the skeleton; actual panel content is per-butler scope.

- [ ] 2.1 chronicler / Timelines tab: implement Panel grid conforming to
      bu-iuol4.1 visual contract.
- [ ] 2.2 education / Reviews tab: implement Panel grid conforming to
      bu-iuol4.1 visual contract.
- [ ] 2.3 finance / Finances tab: implement Panel grid conforming to
      bu-iuol4.1 visual contract.
- [ ] 2.4 health / Health tab: implement Panel grid conforming to
      bu-iuol4.1 visual contract.
- [ ] 2.5 home / Devices tab: implement Panel grid conforming to
      bu-iuol4.1 visual contract.
- [ ] 2.6 relationship / Contacts tab: implement Panel grid conforming to
      bu-iuol4.1 visual contract.
- [ ] 2.7 travel / Trips tab: implement Panel grid conforming to
      bu-iuol4.1 visual contract.

## 3. Verification

- [ ] 3.1 Confirm bespoke tab appears after Memory and before any
      operator-only tabs in both resident and operator modes for each
      domain butler.
- [ ] 3.2 Confirm switchboard still renders only Routing Log and Registry
      beyond base tabs; no resident bespoke added.
- [ ] 3.3 Confirm bespoke tab renders appropriate empty state when butler is
      paused or quarantined.
- [ ] 3.4 Confirm bespoke tab component is wrapped in `<Suspense>` with
      `<TabFallback>`.
- [ ] 3.5 Run `openspec validate add-bespoke-butler-tab-capability --strict`
      after any spec edits.

## 4. Reconciliation

- [ ] 4.1 After all per-butler panel implementations land, reconcile against
      the nine bespoke-tab rules in this spec.
- [ ] 4.2 Run `/opsx:sync` or the project-approved OpenSpec sync flow when
      this delta is ready to merge into canonical specs.
