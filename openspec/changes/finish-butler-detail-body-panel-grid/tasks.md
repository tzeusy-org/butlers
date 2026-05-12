## Backend contracts (gate on all frontend beads)

- [ ] B.1 Implement `GET /api/butlers/{name}/memory/stats` endpoint returning
      per-butler episode/fact/entity/rule counts plus 24h deltas. Register route
      in `src/butlers/api/routers/memory.py`. Response model:
      `ButlerMemoryStats { total_episodes, episodes_24h, total_facts, facts_24h,
      total_entities, entities_24h, total_rules, rules_24h }`.
- [ ] B.2 Add `getButlerActivityFeed(name, limit?)` function to
      `frontend/src/api/client.ts` wrapping
      `GET /api/butlers/{name}/activity-feed`. Add `useButlerActivityFeed` hook
      in `frontend/src/hooks/use-butlers.ts`.

## Frontend: Overview tab

- [ ] F.1 Restyle `ButlerOverviewTab` to a Panel-grid layout. Layout: identity
      (span=2), process (span=2), heartbeat+eligibility (span=2), modules
      (span=2), cost (span=1), recent sessions (span=3), activity feed (span=4).
      All panels use `<Panel>` atom. Activity feed uses `useButlerActivityFeed`.
      No `pid` field anywhere. All timestamps via `<Time>`.
- [ ] F.2 Remove Recent Notifications card from Overview tab. Fold notification
      content into activity-feed panel events. Update unit tests.

## Frontend: Config tab

- [ ] F.3 Restyle `ButlerConfigTab` to the 2x2 Panel-grid layout: process
      (span=2), schedule (span=2), scopes-oauth (span=2), integrations (span=2),
      followed by a collapsed accordion for butler.toml / CLAUDE.md / AGENTS.md /
      MANIFESTO.md. Remove `RuntimeConfigCard` from the Config tab layout.
      Preserve the Formatted/Raw toggle inside the butler.toml accordion item.
      All timestamps via `<Time>`.

## Frontend: Memory tab

- [ ] F.4 Restyle `ButlerMemoryTab` KPI quartet to use `<Panel>` atoms.
      Replace `useMemoryStats()` with the new per-butler `useButlerMemoryStats(name)`
      hook (wrapping `GET /api/butlers/{name}/memory/stats`). Wire "+N today"
      sub-lines from the 24h delta fields. Update tests.

## Frontend: Switchboard Routing Log tab

- [ ] F.5 Replace the `<Card>` wrapper in `ButlerRoutingLogTab` with
      `<Panel title="routing log" span={4} scroll={true} height="480px">`.
      The `<RoutingLogTable>` component is unchanged.

## Frontend: Switchboard Registry tab

- [ ] F.6 Replace the `<Card>` wrapper in `ButlerRegistryTab` with
      `<Panel title="butler registry" span={4}>`.
      The `<RegistryTable>` component is unchanged.

## Out of scope (follow-up bead)

- [ ] OOS.1 Sessions tab and CRM tab inline helpers (`ButlerSessionsTab` and
      `ButlerCrmTab` in `ButlerDetailPage.tsx`) are operator-mode tabs not
      covered by this spec. File a follow-up bead `operator-tab-panel-restyle`
      after merge.

## Tests

- [ ] T.1 Unit tests for `ButlerOverviewTab`: assert Panel atoms present, no Card
      wrapper, no `pid` in DOM, all timestamps via `<Time>`, activity-feed panel
      renders event rows.
- [ ] T.2 Unit tests for `ButlerConfigTab`: assert Panel atoms present, 2x2
      layout, accordion collapsed by default, markdown content renders on expand.
- [ ] T.3 Unit tests for `ButlerMemoryTab`: assert per-butler stats hook called,
      Panel atoms present, "+N today" sub-lines rendered, global `useMemoryStats`
      not called.
- [ ] T.4 Unit tests for `ButlerRoutingLogTab`: assert no Card wrapper, Panel
      atom present.
- [ ] T.5 Unit tests for `ButlerRegistryTab`: assert no Card wrapper, Panel
      atom present.
- [ ] T.6 Backend unit tests for `GET /api/butlers/{name}/memory/stats`: per-butler
      scoping, 24h delta, graceful empty when schema missing.

## Doctrine audit

- [ ] DA.1 Grep all restyled component files for `pid` outside of test files: must
      return zero matches.
- [ ] DA.2 Grep all restyled component files for hex/oklch/rgb literals: must
      return zero matches.
- [ ] DA.3 Grep all restyled component files for em-dash (U+2014): must return
      zero matches.
- [ ] DA.4 Grep all restyled component files for hardcoded butler names
      (calendar, household, etc.): must return zero matches.
- [ ] DA.5 Grep `ButlerMemoryTab` for `useMemoryStats` after restyle: must return
      zero matches.

## Reconciliation

- [ ] R.1 After all F-beads and B-beads close, author reconciliation report
      confirming all five surfaces are on Panel-grid vocabulary with no Card
      wrappers remaining in the tab body.
