## 1. Spec Authoring

- [x] 1.1 Scaffold `openspec/changes/extend-butler-detail-status-board-chrome/`.
- [x] 1.2 Write `proposal.md` citing epic bu-ja5bt doctrine gates and Gates A2
      and B2.
- [x] 1.3 Write `design.md` documenting archetype swap rationale, header/footer
      slot placement decisions, heartbeat-tile removal decision, mode-aware tab
      rail overflow decision, and chrome token policy.
- [x] 1.4 Write delta spec `specs/dashboard-butler-management/spec.md` with
      MODIFIED outer chrome requirement and ADDED requirements for sibling-butler
      nav, footer KPI band, heartbeat-tile placement, mode-aware tab rail, and
      chrome token policy.
- [x] 1.5 Ensure Gate A A2 no-Tier-2-hero rule is referenced (not redefined);
      sibling-nav header slot placement is explicit.
- [x] 1.6 Run `openspec validate extend-butler-detail-status-board-chrome --strict`.

## 2. Frontend Primitives Checklist: Epic bu-ja5bt children .2/.3/.4

- [ ] 2.1 Implement `<SiblingButlerNav>` (bu-ja5bt.2): horizontal nav strip
      listing all real-roster butlers from `useButlers()`, sorted by sessions_24h
      desc then name asc. Each entry: `<ButlerMark size="sm">` + butler name +
      activity-tone dot. Active butler: `aria-current="page"`. Role=navigation,
      aria-label="Navigate to butler". Overflow: horizontal scroll + scroll-snap.
      No butler-hue tokens on any chrome state.
- [ ] 2.2 Implement `<ButlerDetailHeader>` (bu-ja5bt.3): composes the status-board
      header slot. Renders H1 title + description + status pill +
      `<ButlerDetailActions>` + `<SiblingButlerNav>`. All chrome via neutral CSS
      variables. No Tier 2 identity card below the header strip.
- [ ] 2.3 Implement `<ButlerDetailFooter>` KPI band (bu-ja5bt.4): four-cell band
      (sessions 24h, spend today, load%, last activity) scoped to the active
      butler via `useButlerStatusBoard()`. Reuses `<KpiCell>` atom from
      bu-iuol4.13. Tolerates partial-failure data. Last activity via `<Time relative>`.

## 3. Page Wiring Checklist: Epic bu-ja5bt child .5

- [ ] 3.1 Swap `<DetailPage>` for `<Page archetype="status-board">` in
      `ButlerDetailPage.tsx` (bu-ja5bt.5). Pass title, description, breadcrumbs,
      actions, header (`<ButlerDetailHeader />`), footer (`<ButlerDetailFooter />`),
      and primary (`<Tabs>`) through the shell props.
- [ ] 3.2 Remove `pulse={<ButlerHeartbeatTile />}` from `ButlerDetailPage.tsx`.
      Do NOT touch `SystemPage.tsx`.
- [ ] 3.3 Preserve `<ButlerDetailActions>` in the Page actions slot unchanged.
- [ ] 3.4 Preserve all existing mode toggle, tab persistence, and URL logic
      without modification.

## 4. A11y and Responsive Checklist: Epic bu-ja5bt children .6/.7

- [ ] 4.1 Verify keyboard focus order (bu-ja5bt.6): H1 title > status pill >
      actions block (ButlerDetailActions) > sibling-nav > tab rail.
- [ ] 4.2 Verify each sibling-nav entry is focusable with visible focus ring and
      activates via Enter key.
- [ ] 4.3 Verify `aria-current="page"` is on the active entry and
      `aria-label="Navigate to butler"` is on the wrapper.
- [ ] 4.4 Verify operator-mode tab rail (10 base + Models + bespoke) scrolls
      horizontally and all triggers are keyboard-reachable (bu-ja5bt.7).
- [ ] 4.5 Verify resident-mode tab rail (7 base + bespoke) shows no horizontal
      scrollbar at md+ breakpoints.

## 5. Test Harness Checklist: Epic bu-ja5bt child .8

- [ ] 5.1 Author integration tests in `ButlerDetailPage.test.tsx` covering all
      spec scenarios (bu-ja5bt.8). Each test labeled with its scenario name.
- [ ] 5.2 Assert `<Page archetype="status-board">` resolves.
- [ ] 5.3 Assert no Tier 2 hero block between Page header and Tabs body.
- [ ] 5.4 Assert sibling nav lists all butlers from `useButlers()` with
      `aria-current` on active entry.
- [ ] 5.5 Assert sibling nav renders skeleton while `useButlerStatusBoard()` loads.
- [ ] 5.6 Assert paused/quarantined sibling remains navigable.
- [ ] 5.7 Assert footer KPI band is butler-scoped.
- [ ] 5.8 Assert footer partial-failure placeholder renders.
- [ ] 5.9 Assert `<ButlerHeartbeatTile />` absent from detail-page DOM.
- [ ] 5.10 Assert `<ButlerHeartbeatTile />` present on SystemPage DOM.
- [ ] 5.11 Assert operator-mode 10+ tabs present.
- [ ] 5.12 Assert resident-mode 7-tab + bespoke present.
- [ ] 5.13 Assert mode toggle round-trips.

## 6. Doctrine Audit Checklist: Epic bu-ja5bt child .9

- [ ] 6.1 Grep `ButlerDetailPage.tsx` for `ButlerHeartbeatTile` returns zero
      matches.
- [ ] 6.2 Grep `SystemPage.tsx` for `ButlerHeartbeatTile` returns non-zero.
- [ ] 6.3 Grep new component files for hex/oklch/rgb literals returns zero.
- [ ] 6.4 Grep new JSX strings for em-dashes (`--`) returns zero.
- [ ] 6.5 Grep new component files for butler-hue tokens on non-ButlerMark
      elements returns zero.
- [ ] 6.6 Grep grid render paths for hardcoded butler names returns zero.

## 7. Reconciliation

- [ ] 7.1 Reconcile all epic bu-ja5bt children against this spec contract.
- [ ] 7.2 Author reconciliation report at `docs/reports/butler-detail-status-board-chrome.md`
      (bu-ja5bt.10) after all other children close.
- [ ] 7.3 Run `/opsx:sync` or the project-approved OpenSpec sync flow once the
      reconciliation report is approved by the owner.
